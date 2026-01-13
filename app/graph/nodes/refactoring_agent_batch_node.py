"""
Refactoring Agent Batch Node - 批量重构代理节点

自治修复闭环设计：
- 逐文件处理：为每个文件独立生成 patch → 立即应用到沙箱 → 自检通过后继续下一个
- Agent 自主自检：在一次调用中自主调用工具（check_syntax、run_command_in_sandbox）进行质量检查
- 原地纠错：将检查失败视为输入反馈，在节点内部重试修正（最多3次/文件）
- 沙箱真实性：每个 patch 通过自检后立即 apply，确保后续文件看到最新状态
"""

import difflib
from typing import Literal, Optional

from langchain.agents import create_agent
from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.core.logger_config import logger
from app.core.prompt_manager import prompt_manager
from app.graph.state import IssueProcessState, NodeName, ProcessStage
from app.llms.llm_factory import get_llm_model
from app.sandbox.git_service import GitService
from app.sandbox.manager import get_sandbox_manager
from app.tools.registry import get_tools_for_agent


class FilePatchOutput(BaseModel):
    """单个文件的补丁输出"""
    file_path: str = Field(description="文件路径")
    modified_content: str = Field(description="修改后的完整文件内容")
    reasoning: str = Field(description="修改推理")
    confidence: float = Field(description="置信度 0.0-1.0", ge=0.0, le=1.0)
    self_check_passed: bool = Field(default=False, description="Agent自检是否通过")
    check_details: Optional[dict] = Field(default=None, description="检查详情（语法、Ruff 等）")


class RefactoringAgentBatchNode:
    """
    批量重构代理节点（自治修复闭环）
    
    核心流程：
    1. 从 batch_contexts 获取批次文件（通常5个）
    2. 逐文件处理：生成 → 应用 → 记录
       - Agent 自主调用工具进行自检（syntax + ruff）
       - 自检失败则重试（最多3次/文件）
       - 自检通过后立即应用到沙箱
    3. 收集成功/失败结果，更新状态
    4. 错误容忍：一个文件失败不阻断其他文件
    """

    def __init__(self, max_retries: int = 3):
        """
        初始化节点
        
        Args:
            max_retries: 最大重试次数
        """
        self.max_retries = max_retries
        self.prompt_manager = prompt_manager
        self.tools = get_tools_for_agent("patch_writer")
        self.sandbox_manager = get_sandbox_manager()

    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal["incremental_impact_scan", "sandbox_teardown"]]:
        """
        执行批量重构（逐文件处理）
        
        Args:
            state: 当前工作流状态
        
        Returns:
            Command对象，成功返回 incremental_impact_scan，失败返回 sandbox_teardown
        """
        update_dict = {}

        try:
            # 发送进度事件
            await adispatch_custom_event(
                ProcessStage.PATCH_GENERATION.value,
                {
                    "status": NodeName.REFACTORING_AGENT_BATCH.value,
                    "progress": "正在执行批量重构...",
                    "think_chain_item": {
                        "type": NodeName.REFACTORING_AGENT_BATCH.value,
                        "title": "批量重构代理",
                        "desc": "逐文件生成补丁并自检",
                        "urls": [],
                    },
                },
            )

            # 获取批量上下文
            context = state.get("context", {})
            batch_contexts = context.get("batch_contexts", [])
            
            if not batch_contexts:
                error_msg = "batch_contexts 为空，无法执行重构"
                logger.error(error_msg)
                runtime = state.get("runtime", {})
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "error": error_msg,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.REFACTORING_AGENT_BATCH.value,
                            ],
                            "current_step": NodeName.REFACTORING_AGENT_BATCH.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

            # 获取 sandbox 信息
            sandbox = state.get("sandbox", {})
            sandbox_id = sandbox.get("sandbox_id")
            
            if not sandbox_id:
                error_msg = "缺少 sandbox_id"
                logger.error(error_msg)
                runtime = state.get("runtime", {})
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "error": error_msg,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.REFACTORING_AGENT_BATCH.value,
                            ],
                            "current_step": NodeName.REFACTORING_AGENT_BATCH.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)
            
            successful_patches = []
            failed_files = []
            skip_count = 0
            
            for idx, file_context in enumerate(batch_contexts):
                file_path = file_context.get("file_path", "")
                logger.info(f"处理文件 {idx + 1}/{len(batch_contexts)}: {file_path}")
                
                # 处理单个文件（生成 → 应用 → 记录）
                result = await self._process_single_file(
                    state,
                    file_context,
                    sandbox_id
                )
                
                if result["success"]:
                    if result["patch"]:
                        successful_patches.append(result["patch"])
                        logger.info(f"文件处理成功: {file_path}")
                    else:
                        logger.info(f"文件无需修改: {file_path}")
                        skip_count += 1
                else:
                    failed_files.append({
                        "file_path": file_path,
                        "error": result.get("error", "未知错误"),
                        "attempts": result.get("attempts", 0),
                    })
                    logger.warning(f"文件处理失败: {file_path} - {result.get('error')}")
            
            # 检查是否全部失败
            if not successful_patches and skip_count == 0:
                error_msg = f"批量重构失败：所有 {len(batch_contexts)} 个文件处理均失败"
                logger.error(error_msg)
                runtime = state.get("runtime", {})
                patching = state.get("patching", {})
                applied_history = patching.get("applied_history", [])
                applied_history.append({
                    "batch": True,
                    "success": False,
                    "failed_files": failed_files,
                    "error": error_msg,
                })
                
                update_dict.update(
                    {
                        "patching": {
                            **patching,
                            "applied_history": applied_history,
                        },
                        "runtime": {
                            **runtime,
                            "error": error_msg,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.REFACTORING_AGENT_BATCH.value,
                            ],
                            "current_step": NodeName.REFACTORING_AGENT_BATCH.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

            # 部分或全部成功
            logger.info(
                f"批量重构完成: 成功 {len(successful_patches)}/{len(batch_contexts)} 个文件"
            )
            if failed_files:
                logger.warning(f"失败文件: {[f['file_path'] for f in failed_files]}")

            # 更新状态
            runtime = state.get("runtime", {})
            patching = state.get("patching", {})
            verification = state.get("verification", {})
            
            # 记录应用历史
            applied_history = patching.get("applied_history", [])
            applied_history.append({
                "batch": True,
                "success": True,
                "successful_files": [p["file_path"] for p in successful_patches],
                "failed_files": failed_files,
                "total": len(batch_contexts),
                "succeeded": len(successful_patches),
                "failed": len(failed_files),
            })
            
            # 记录轻量验证结果（内部自检通过）
            light_results = verification.get("light_results", [])
            light_results.append({
                "batch": True,
                "files": [p["file_path"] for p in successful_patches],
                "passed": True,
                "checks": ["syntax", "ruff"],
                "details": [
                    {
                        "file": p["file_path"],
                        "confidence": p["confidence"],
                        "check_details": p.get("check_details"),
                    }
                    for p in successful_patches
                ],
            })
            
            # 更新 generated_patches
            generated_patches = patching.get("generated_patches", {})
            for patch in successful_patches:
                generated_patches[patch["file_path"]] = patch["unified_diff"]
            
            # 获取 ripple 信息并更新 last_applied_files 和 last_signature_changes
            ripple = state.get("ripple", {})
            
            # 收集所有成功补丁的签名变更
            last_signature_changes = {}
            for patch in successful_patches:
                file_path = patch["file_path"]
                signature_changes = patch.get("signature_changes", [])
                if signature_changes:
                    last_signature_changes[file_path] = signature_changes
            
            logger.info(
                f"记录签名变更: {len(last_signature_changes)} 个文件, "
                f"总计 {sum(len(changes) for changes in last_signature_changes.values())} 个符号"
            )
            
            update_dict.update(
                {
                    "patching": {
                        **patching,
                        "generated_patches": generated_patches,
                        "applied_history": applied_history,
                    },
                    "verification": {
                        **verification,
                        "light_results": light_results,
                    },
                    "ripple": {
                        **ripple,
                        "last_applied_files": [p["file_path"] for p in successful_patches],
                        "last_signature_changes": last_signature_changes,  # 新增
                    },
                    "runtime": {
                        **runtime,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.REFACTORING_AGENT_BATCH.value,
                        ],
                        "current_step": NodeName.REFACTORING_AGENT_BATCH.value,
                    },
                }
            )

            # 发送完成事件
            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": NodeName.REFACTORING_AGENT_BATCH.value,
                    "progress": (
                        f"批量重构完成: 成功 {len(successful_patches)}/{len(batch_contexts)}"
                    ),
                    "think_chain_item": {
                        "type": NodeName.REFACTORING_AGENT_BATCH.value,
                        "title": "批量重构代理",
                        "desc": f"成功修复 {len(successful_patches)} 个文件",
                        "urls": [],
                    },
                },
            )

            return Command(update=update_dict, goto=NodeName.INCREMENTAL_IMPACT_SCAN.value)

        except Exception as e:
            logger.error(f"批量重构失败: {e}", exc_info=True)
            runtime = state.get("runtime", {})
            update_dict.update(
                {
                    "runtime": {
                        **runtime,
                        "error": f"批量重构失败: {str(e)}",
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.REFACTORING_AGENT_BATCH.value,
                        ],
                        "current_step": NodeName.REFACTORING_AGENT_BATCH.value,
                    },
                }
            )

            return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

    async def _generate_patch_for_file(
        self,
        state: IssueProcessState,
        file_context: dict,
        sandbox_id: str
    ) -> Optional[FilePatchOutput]:
        """
        为单个文件生成补丁（带 Agent 自检和重试）
        
        Args:
            state: 当前状态
            file_context: 文件上下文（来自 batch_contexts）
            sandbox_id: Sandbox ID
        
        Returns:
            FilePatchOutput 或 None（重试耗尽）
        """
        try:
            # 获取 issue 信息
            issue_data = state.get("issue_data", {})
            issue_title = issue_data.get("title", "")
            issue_description = issue_data.get("description", "")
            
            # 获取初始 patch 信息（作为参考）
            patching = state.get("patching", {})
            initial_patch = patching.get("current_patch", "")
            
            # 提取文件上下文信息
            file_path = file_context.get("file_path", "")
            file_content = file_context.get("file_content", "")
            symbols = file_context.get("symbols", [])
            reasons = file_context.get("reasons", [])
            
            # 在重试循环外只创建一次 agent
            llm = await get_llm_model(model_name="gpt-5-2025-08-07")
            agent = create_agent(
                model=llm,
                tools=self.tools,
                response_format=FilePatchOutput
            )
            
            # 重试循环
            for attempt in range(self.max_retries):
                logger.info(
                    f"为文件 {file_path} 生成补丁 (尝试 {attempt + 1}/{self.max_retries})"
                )
                
                # 渲染 Prompt
                prompt_text = self.prompt_manager.render(
                    "batch_refactoring",
                    issue_title=issue_title,
                    issue_description=issue_description or "无描述",
                    file_path=file_path,
                    file_content=file_content,
                    symbols=symbols,
                    reasons=reasons,
                    initial_patch=initial_patch,
                    sandbox_id=sandbox_id,
                )
                
                # 调用 Agent
                result = await agent.ainvoke({
                    "messages": [{"role": "user", "content": prompt_text}]
                })
                
                # 获取结构化输出
                output: FilePatchOutput = result["structured_response"]
                
                logger.info(
                    f"[Batch Refactoring Agent] 文件 {file_path}: "
                    f"self_check_passed={output.self_check_passed}, "
                    f"confidence={output.confidence}"
                )
                
                # 检查自检是否通过
                if output.self_check_passed:
                    logger.info(f"文件 {file_path} 自检通过 (尝试 {attempt + 1})")
                    return output
                else:
                    # 自检未通过，继续重试
                    logger.warning(
                        f"文件 {file_path} 自检未通过 (尝试 {attempt + 1}): "
                        f"{output.check_details}"
                    )
            
            # 重试耗尽
            logger.error(
                f"文件 {file_path} 生成失败：已达最大重试次数 {self.max_retries}"
            )
            return None
            
        except Exception as e:
            logger.error(f"生成文件补丁异常 ({file_path}): {e}", exc_info=True)
            return None

    async def _process_single_file(
        self,
        state: IssueProcessState,
        file_context: dict,
        sandbox_id: str
    ) -> dict:
        """
        处理单个文件：生成 → 应用 → 记录
        
        Args:
            state: 当前状态
            file_context: 文件上下文
            sandbox_id: Sandbox ID
        
        Returns:
            处理结果字典 {success: bool, patch: dict, attempts: int, error: str}
        """
        file_path = file_context.get("file_path", "")
        
        try:
            # 1. 生成补丁（带内部重试）
            patch_output = await self._generate_patch_for_file(
                state,
                file_context,
                sandbox_id
            )
            
            if not patch_output:
                return {
                    "success": False,
                    "file_path": file_path,
                    "error": f"生成补丁失败（重试次数耗尽）",
                    "attempts": self.max_retries,
                }
            
            # 2. 生成 unified diff
            original_content = file_context.get("file_content", "")
            modified_content = patch_output.modified_content
            
            # 确保内容以换行符结尾
            if original_content and not original_content.endswith('\n'):
                original_content += '\n'
            if modified_content and not modified_content.endswith('\n'):
                modified_content += '\n'
            
            # 使用 keepends=False 避免与 lineterm="" 冲突
            # 这样可以确保生成的 diff 格式正确
            original_lines = original_content.splitlines(keepends=False)
            modified_lines = modified_content.splitlines(keepends=False)
            
            # 规范化文件路径：去掉开头的 './'
            normalized_path = file_path.lstrip('./')
            
            diff_lines = list(difflib.unified_diff(
                original_lines,
                modified_lines,
                fromfile=f"a/{normalized_path}",
                tofile=f"b/{normalized_path}",
                lineterm="",
            ))
            
            # 检查是否有实际的差异
            if not diff_lines:
                logger.info(f"文件 {file_path} 无需修改")
                return {
                    "success": True,
                    "patch": None,  # 空 patch 表示跳过
                    "error": "文件无需修改",
                    "attempts": 0,
                }
            
            # 将 diff 行连接成字符串
            unified_diff = "\n".join(diff_lines) + "\n"
            
            # 记录补丁信息（用于调试）
            diff_line_count = len(diff_lines)
            logger.debug(
                f"生成补丁: {file_path}, "
                f"diff_lines={diff_line_count}, "
                f"patch_size={len(unified_diff)} bytes"
            )
            
            # 3. 立即应用补丁到沙箱
            git_service = GitService(self.sandbox_manager, sandbox_id)
            
            try:
                await git_service.apply_patch(
                    patch_content=unified_diff
                )
                logger.info(f"补丁应用成功: {file_path}")
            except Exception as e:
                logger.error(f"补丁应用失败 ({file_path}): {e}")
                return {
                    "success": False,
                    "error": f"补丁应用失败: {str(e)}",
                    "attempts": 1,  # 应用失败不重试
                }
            
            # 4. 提取签名变更指纹（用于增量影响扫描）
            from app.utils.diff_analyzer import diff_analyzer
            from app.utils.common_function import detect_language
            
            language = detect_language(file_path)
            signature_changes = diff_analyzer.extract_signature_changes(
                unified_diff=unified_diff,
                file_path=file_path,
                old_content=original_content,
                new_content=modified_content,
                language=language
            )
            
            # 转换为简化格式
            signature_changes_list = [
                {
                    "symbol_name": change.symbol_name,
                    "symbol_type": change.symbol_type,
                    "change_type": change.change_type,
                }
                for change in signature_changes
            ]
            
            logger.debug(
                f"提取签名变更: {file_path}, 变更数={len(signature_changes_list)}"
            )
            
            # 5. 返回成功结果
            return {
                "success": True,
                "patch": {
                    "file_path": file_path,
                    "modified_content": modified_content,
                    "unified_diff": unified_diff,
                    "reasoning": patch_output.reasoning,
                    "confidence": patch_output.confidence,
                    "self_check_passed": patch_output.self_check_passed,
                    "check_details": patch_output.check_details,
                    "signature_changes": signature_changes_list,
                },
                "attempts": 1,  # TODO: 可以从 _generate_patch_for_file 返回实际尝试次数
            }
            
        except Exception as e:
            logger.error(f"处理文件异常 ({file_path}): {e}", exc_info=True)
            return {
                "success": False,
                "error": f"处理异常: {str(e)}",
                "attempts": 0,
            }


# 导出
__all__ = ["RefactoringAgentBatchNode"]

