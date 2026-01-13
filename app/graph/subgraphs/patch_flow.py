"""
Patch Flow Subgraph - 补丁生成子图

包含：
1. PatchWriter: 生成候选补丁
2. PatchJudge: 从候选中选择最佳补丁

流程：PatchWriter → PatchJudge → 写入 state.patching
"""

import difflib
from typing import Literal

from langchain.agents import create_agent
from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.core.logger_config import logger
from app.core.prompt_manager import prompt_manager
from app.graph.routers.patch_judge import get_patch_judge
from app.graph.state import IssueProcessState, NodeName, ProcessStage
from app.llms.llm_factory import get_llm_model
from app.sandbox.git_service import GitService
from app.sandbox.manager import get_sandbox_manager
from app.schemas.context_assembly import EditableContextSlice, PatchResult
from app.tools.registry import get_tools_for_agent


class PatchGenerationOutput(BaseModel):
    """PatchWriter Agent 的结构化输出格式"""
    
    modified_code: str = Field(
        description="修改后的完整 editable 代码字符串，将替换原始的 editable 区域"
    )
    reasoning: str = Field(
        description="修改推理过程，简述做了什么改动以及原因"
    )
    confidence: float = Field(
        description="修改置信度，范围 0.0-1.0",
        ge=0.0,
        le=1.0
    )


class PatchWriterStepNode:
    """
    补丁生成步骤节点
    """

    def __init__(self):
        """初始化节点"""
        self.prompt_manager = prompt_manager
        # 获取 PatchWriter 可用的工具（只读）
        self.tools = get_tools_for_agent("patch_writer")

    async def generate_candidates(
        self,
        state: IssueProcessState,
    ) -> list[PatchResult]:
        """
        基于 Issue 和可编辑上下文，生成候选补丁

        Args:
            state: 当前工作流状态

        Returns:
            候选补丁列表（当前只生成 1 个）
        """
        # 获取上下文（从分域结构）
        context = state.get("context", {})
        editable_context_dict = context.get("editable_context")
        
        if not editable_context_dict:
            logger.error("没有可编辑上下文，无法生成补丁")
            return []

        editable_context = EditableContextSlice(**editable_context_dict)

        # 生成补丁
        patch_result = await self._generate_patch_internal(
            state,
            editable_context,
        )

        if patch_result is None:
            logger.error("无法生成补丁")
            return []

        logger.info(
            f"生成候选补丁: {patch_result.file_path}, "
            f"置信度: {patch_result.confidence:.2f}"
        )

        # 返回候选列表
        return [patch_result]

    async def _generate_patch_internal(
        self,
        state: IssueProcessState,
        context: EditableContextSlice,
    ) -> PatchResult:
        """
        生成补丁
        
        Agent 可以在生成补丁前调用工具：
        - read_file_from_sandbox: 读取其他相关文件
        - parse_code_ast: 解析 AST
        - search_symbol_in_code: 搜索符号
        - analyze_dependencies: 分析依赖
        - run_command_in_sandbox: 执行任意命令（如 ruff check, mypy 等）
        """
        issue_data = state.get("issue_data", {})
        issue_title = issue_data.get("title", "")
        issue_description = issue_data.get("description", "")
        
        # 获取 sandbox 上下文
        sandbox = state.get("sandbox", {})
        sandbox_id = sandbox.get("sandbox_id", "")

        # 构建 prompt
        prompt_text = self.prompt_manager.render(
            "patch_generation",
            issue_title=issue_title,
            issue_description=issue_description,
            target_symbol=context.target.symbol_name,
            file_path=context.target.file_path,
            symbol_type=context.target.symbol_type,
            full_code=context.full_code,
            dependency_signatures=[sig.model_dump() for sig in context.dependency_signatures],
            imports=context.imports,
            schema_definitions=context.schema_definitions,
            editable_start_line=context.editable_start_line,
            editable_end_line=context.editable_end_line,
            sandbox_id=sandbox_id,
        )

        # 自动处理 tool_calls 循环和输出验证
        llm = await get_llm_model(model_name="gpt-5-2025-08-07")
        agent = create_agent(
            model=llm,
            tools=self.tools,
            response_format=PatchGenerationOutput
        )
        
        # 调用 Agent
        result = await agent.ainvoke({
            "messages": [{"role": "user", "content": prompt_text}]
        })
        
        output: PatchGenerationOutput = result["structured_response"]

        if not output.modified_code:
            logger.error("Agent 未返回修改后的代码")
            return None

        logger.info(
            f"[PatchWriter Agent] 生成补丁完成, "
            f"推理: {output.reasoning}"
        )

        # 生成 unified diff（基于整文件，而非仅符号代码）
        # 这样生成的 patch 可以被 git apply 正确应用
        file_content = context.file_content
        if not file_content:
            logger.warning("file_content 为空，回退到使用 full_code 生成 diff")
            file_content = context.full_code
        
        # 将 modified_code 回填到整文件
        # 使用 splitlines() 不保留换行符，然后统一添加 \n
        file_lines = file_content.splitlines()
        modified_lines = output.modified_code.splitlines()
        
        # 替换 editable 区域（行号从 1 开始）
        start_idx = context.editable_start_line - 1
        end_idx = context.editable_end_line
        
        # 构建修改后的整文件（每行作为独立的元素）
        modified_file_lines = (
            file_lines[:start_idx] +
            modified_lines +
            file_lines[end_idx:]
        )
        
        # 生成 unified diff
        # unified_diff 会为每行添加换行符
        # 规范化文件路径：去掉开头的 './'
        normalized_path = context.target.file_path.lstrip('./')
        
        diff_lines = list(difflib.unified_diff(
            file_lines,
            modified_file_lines,
            fromfile=f"a/{normalized_path}",
            tofile=f"b/{normalized_path}",
            lineterm="",  # 不在每行末尾添加换行符
        ))
        
        # 如果没有差异，unified_diff 会返回空列表
        if not diff_lines:
            logger.warning("[PatchWriter] 没有生成差异，原文件和修改后文件可能相同")
            unified_diff = ""
        else:
            # 用换行符连接所有行，末尾添加换行符以符合 git apply 的要求
            unified_diff = "\n".join(diff_lines) + "\n"

        return PatchResult(
            file_path=context.target.file_path,
            original_code=context.full_code,
            modified_code=output.modified_code,
            unified_diff=unified_diff,
            change_summary=output.reasoning,
            confidence=output.confidence,
        )


class PatchFlowNode:
    """
    补丁流程复合节点（整合 PatchWriter + PatchJudge）
    
    流程：
    1. PatchWriter 生成候选补丁列表
    2. PatchJudge 选择最佳补丁
    3. 更新 state.patching（patch_candidates, selected_patch, current_patch, current_modified_code）
    """
    
    def __init__(self):
        """初始化复合节点"""
        self.patch_writer = PatchWriterStepNode()
        self.patch_judge = get_patch_judge()
    
    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal["main_router", "sandbox_teardown"]]:
        """
        执行补丁生成流程
        
        Args:
            state: 当前工作流状态
        
        Returns:
            Command 对象，成功返回 main_router，失败返回 sandbox_teardown
        """
        update_dict = {}
        
        try:
            # 发送进度事件
            await adispatch_custom_event(
                ProcessStage.PATCH_GENERATION.value,
                {
                    "status": NodeName.PATCH_FLOW.value,
                    "progress": "正在生成代码补丁...",
                    "think_chain_item": {
                        "type": NodeName.PATCH_FLOW.value,
                        "title": "补丁生成流程",
                        "desc": "生成候选补丁并选择最佳方案",
                        "urls": [],
                    },
                },
            )
            
            # Step 1: 生成候选补丁
            candidates = await self.patch_writer.generate_candidates(state)
            
            if not candidates:
                error_msg = "无法生成候选补丁"
                logger.error(error_msg)
                runtime = state.get("runtime", {})
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "error": error_msg,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.PATCH_FLOW.value,
                            ],
                            "current_step": NodeName.PATCH_FLOW.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)
            
            # 转换为 dict 列表
            candidate_dicts = [c.model_dump() for c in candidates]
            
            # Step 2: 选择最佳补丁
            targeting = state.get("targeting", {})
            current_target = targeting.get("current_target", {})
            target_file_path = current_target.get("file_path")
            
            best_patch_dict = self.patch_judge.select_best_patch(
                candidate_dicts,
                target_file_path=target_file_path
            )
            
            if not best_patch_dict:
                error_msg = "无法选择最佳补丁"
                logger.error(error_msg)
                runtime = state.get("runtime", {})
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "error": error_msg,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.PATCH_FLOW.value,
                            ],
                            "current_step": NodeName.PATCH_FLOW.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)
            
            # Step 3: 更新 state
            patching = state.get("patching", {})
            runtime = state.get("runtime", {})
            
            # 更新已生成的补丁
            existing_patches = patching.get("generated_patches", {})
            existing_patches[best_patch_dict["file_path"]] = best_patch_dict["unified_diff"]
            
            # 更新当前目标状态
            current_target_dict = targeting.get("current_target", {})
            current_target_dict["status"] = "completed"
            
            # Step 3.5: 立即应用补丁到 sandbox
            sandbox = state.get("sandbox", {})
            sandbox_id = sandbox.get("sandbox_id")
            repo_path = sandbox.get("repo_path", ".")
            
            if not sandbox_id:
                error_msg = "缺少 sandbox_id，无法应用补丁"
                logger.error(error_msg)
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "error": error_msg,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.PATCH_FLOW.value,
                            ],
                            "current_step": NodeName.PATCH_FLOW.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)
            
            try:
                sandbox_manager = get_sandbox_manager()
                git_service = GitService(sandbox_manager, sandbox_id)
                
                logger.info(f"正在应用补丁到 sandbox {sandbox_id}: {best_patch_dict['file_path']}")
                await git_service.apply_patch(
                    patch_content=best_patch_dict["unified_diff"],
                    repo_path=repo_path
                )
                
                apply_record = {
                    "file_path": best_patch_dict["file_path"],
                    "success": True,
                    "timestamp": None,  # 可选：添加时间戳
                }
                logger.info(f"补丁应用成功: {best_patch_dict['file_path']}")
                
            except Exception as e:
                error_msg = f"补丁应用失败: {str(e)}"
                logger.error(error_msg, exc_info=True)
                
                apply_record = {
                    "file_path": best_patch_dict["file_path"],
                    "success": False,
                    "error": str(e),
                    "timestamp": None,
                }
                
                # 记录失败历史并终止
                applied_history = patching.get("applied_history", [])
                applied_history.append(apply_record)
                
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
                                NodeName.PATCH_FLOW.value,
                            ],
                            "current_step": NodeName.PATCH_FLOW.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)
            
            # 记录成功的应用历史
            applied_history = patching.get("applied_history", [])
            applied_history.append(apply_record)
            
            # 生成新补丁后，清理所有基于旧补丁的下游状态
            update_dict.update(
                {
                    "patching": {
                        **patching,
                        "patch_candidates": candidate_dicts,
                        "selected_patch": best_patch_dict,
                        "current_patch": best_patch_dict["unified_diff"],
                        "current_modified_code": best_patch_dict["modified_code"],
                        "generated_patches": existing_patches,
                        "applied_history": applied_history,
                    },
                    "targeting": {
                        **targeting,
                        "current_target": current_target_dict,
                    },
                    # 清理下游状态，强制重新走完整流程
                    "impact": {},
                    "ripple": {},  # 清空 ripple 队列，重新进行全局扫描
                    "verification": {},
                    "review": {},
                    "delivery": {},
                    "runtime": {
                        **runtime,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.PATCH_FLOW.value,
                        ],
                        "current_step": NodeName.PATCH_FLOW.value,
                    },
                }
            )
            
            logger.info(
                f"PatchFlow 完成: {best_patch_dict['file_path']}, "
                f"置信度: {best_patch_dict['confidence']:.2f}, "
                f"候选数: {len(candidate_dicts)}"
            )
            
            # 发送完成事件
            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": NodeName.PATCH_FLOW.value,
                    "progress": "补丁生成完成",
                    "think_chain_item": {
                        "type": NodeName.PATCH_FLOW.value,
                        "title": "补丁生成流程",
                        "desc": f"文件: {best_patch_dict['file_path']}, 置信度: {best_patch_dict['confidence']:.2f}",
                        "urls": [],
                    },
                },
            )
            
            return Command(update=update_dict, goto=NodeName.MAIN_ROUTER.value)
        
        except Exception as e:
            logger.error(f"PatchFlow 执行失败: {e}", exc_info=True)
            runtime = state.get("runtime", {})
            update_dict.update(
                {
                    "runtime": {
                        **runtime,
                        "error": f"补丁生成流程失败: {str(e)}",
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.PATCH_FLOW.value,
                        ],
                        "current_step": NodeName.PATCH_FLOW.value,
                    },
                }
            )
            
            return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)


# 导出
__all__ = ["PatchFlowNode", "PatchWriterStepNode"]

