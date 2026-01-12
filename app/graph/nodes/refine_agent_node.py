"""
Refine Agent Node - 批处理修复循环节点（彻底重写版）

当验证失败时，接收错误列表并批量修复所有 error 级别问题。
使用 LangChain Agent + tools + structured output 生成修复方案。
"""

import difflib
from typing import Literal

from langchain.agents import create_agent
from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.config.app_config import app_config
from app.core.logger_config import logger
from app.core.prompt_manager import prompt_manager
from app.graph.state import IssueProcessState, NodeName, ProcessStage
from app.llms.llm_factory import get_gpt_model
from app.sandbox.file_service import FileService
from app.sandbox.git_service import GitService
from app.sandbox.manager import get_sandbox_manager
from app.tools.registry import get_tools_for_agent


class FileFix(BaseModel):
    """单个文件的修复方案"""
    file_path: str = Field(description="文件路径")
    fixed_code: str = Field(description="修复后的完整代码")
    reasoning: str = Field(description="修复推理过程")
    fixed_error_lines: list[int] = Field(description="已修复的错误行号列表")


class RefineOutput(BaseModel):
    """RefineAgent 的输出格式"""
    file_fixes: list[FileFix] = Field(description="每个文件的修复方案")
    confidence: float = Field(description="修复置信度", ge=0.0, le=1.0)
    unfixable_errors: list[str] = Field(
        default=[],
        description="无法自动修复的错误（将触发熔断）"
    )


class RefineAgentNode:
    """批处理修复循环节点（使用 LangChain Agent）"""

    def __init__(self):
        """初始化节点"""
        self.prompt_manager = prompt_manager
        # 获取 refine agent 可用的工具
        self.tools = get_tools_for_agent("refine")
        self.sandbox_manager = get_sandbox_manager()

    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal["main_router", "sandbox_teardown"]]:
        """
        批量修复验证失败的文件
        
        流程：
        1. 获取 verification.final_verification.all_issues
        2. 过滤出 severity="error" 的问题
        3. 按文件分组
        4. 调用 Agent 生成修复方案
        5. 应用修正补丁到 sandbox
        6. 更新 state 并返回 main_router

        Args:
            state: 当前工作流状态

        Returns:
            Command 对象，返回 main_router 或 sandbox_teardown
        """
        update_dict = {}

        try:
            # 发送进度事件
            await adispatch_custom_event(
                ProcessStage.DIAGNOSIS.value,
                {
                    "status": NodeName.REFINE_AGENT.value,
                    "progress": "正在批量修复错误...",
                    "think_chain_item": {
                        "type": NodeName.REFINE_AGENT.value,
                        "title": "批量修复",
                        "desc": "修复静态检查发现的错误",
                        "urls": [],
                    },
                },
            )

            # 检查重试次数
            verification = state.get("verification", {})
            refine_retry_count = verification.get("refine_retry_count", 0)
            
            # 从配置中获取最大重试次数
            max_retry_count = app_config.workflow.max_refine_retry_count
            
            if refine_retry_count >= max_retry_count:
                error_msg = f"已达到最大修复次数 ({max_retry_count})"
                logger.warning(error_msg)
                return self._abort_with_error(state, error_msg)
            
            # 获取 error 列表
            final_verification = verification.get("final_verification", {})
            all_issues = final_verification.get("all_issues", [])
            error_issues = [i for i in all_issues if i.get("severity") == "error"]
            
            if not error_issues:
                # 没有 error，直接通过
                logger.info("没有 error 需要修复，直接通过")
                runtime = state.get("runtime", {})
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.REFINE_AGENT.value,
                            ],
                            "current_step": NodeName.REFINE_AGENT.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.MAIN_ROUTER.value)

            # 按文件分组
            issues_by_file = self._group_issues_by_file(error_issues)
            
            # 从配置中获取最大重试次数（用于日志显示）
            max_retry_count = app_config.workflow.max_refine_retry_count
            
            logger.info(
                f"开始修复 {len(issues_by_file)} 个文件的 {len(error_issues)} 个错误 "
                f"(第 {refine_retry_count + 1}/{max_retry_count} 轮)"
            )
            
            # 批量生成修复
            refine_output = await self._generate_fixes(state, issues_by_file)
            
            # 检查是否有无法修复的错误
            if refine_output.unfixable_errors:
                error_msg = f"存在无法自动修复的错误: {refine_output.unfixable_errors}"
                logger.error(error_msg)
                return self._abort_with_error(state, error_msg)
            
            # 应用修复补丁
            await self._apply_fixes(state, refine_output)
            
            # 更新状态并返回
            return self._update_state_and_retry(state, refine_output)

        except Exception as e:
            logger.error(f"批量修复失败: {e}", exc_info=True)
            runtime = state.get("runtime", {})
            update_dict.update(
                {
                    "runtime": {
                        **runtime,
                        "error": f"批量修复失败: {str(e)}",
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.REFINE_AGENT.value,
                        ],
                        "current_step": NodeName.REFINE_AGENT.value,
                    },
                }
            )

            return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

    def _group_issues_by_file(self, error_issues: list[dict]) -> dict[str, list[dict]]:
        """
        按文件分组错误
        
        Args:
            error_issues: 错误列表
        
        Returns:
            {file_path: [issue1, issue2, ...], ...}
        """
        issues_by_file = {}
        for issue in error_issues:
            file_path = issue.get("file_path", "")
            if file_path not in issues_by_file:
                issues_by_file[file_path] = []
            issues_by_file[file_path].append(issue)
        
        return issues_by_file

    async def _generate_fixes(
        self,
        state: IssueProcessState,
        issues_by_file: dict[str, list[dict]]
    ) -> RefineOutput:
        """
        使用 Agent 批量生成修复方案
        
        对每个文件：
        1. 从 sandbox 读取当前内容
        2. 渲染 prompt（包含错误列表）
        3. 调用 Agent 生成修复后的代码
        4. 收集所有 FileFix

        Args:
            state: 当前状态
            issues_by_file: 按文件分组的错误列表

        Returns:
            RefineOutput: 修复方案
        """
        sandbox = state.get("sandbox", {})
        sandbox_id = sandbox.get("sandbox_id", "")
        
        # 为每个文件读取当前内容
        file_contents = {}
        file_service = FileService(self.sandbox_manager, sandbox_id)
        
        for file_path in issues_by_file.keys():
            try:
                content = await file_service.read_file(path=file_path)
                file_contents[file_path] = content
            except Exception as e:
                logger.error(f"读取文件 {file_path} 失败: {e}", exc_info=True)
                file_contents[file_path] = ""
        
        # 构建 prompt（包含所有文件的错误和内容）
        prompt = self.prompt_manager.render(
            "batch_fix",
            issues_by_file=issues_by_file,
            file_contents=file_contents,
            sandbox_id=sandbox_id,
        )

        try:
            # 使用官方推荐的 create_agent + response_format
            llm = await get_gpt_model(temperature=0.1)
            agent = create_agent(
                model=llm,
                tools=self.tools,
                response_format=RefineOutput
            )
            
            # 调用 Agent
            result = await agent.ainvoke({
                "messages": [{"role": "user", "content": prompt}]
            })
            
            # 从 structured_response 获取验证后的 Pydantic 模型实例
            output: RefineOutput = result["structured_response"]
            
            logger.info(
                f"[Refine Agent] 修复完成, "
                f"confidence={output.confidence:.2f}, "
                f"fixed_files={len(output.file_fixes)}, "
                f"unfixable={len(output.unfixable_errors)}"
            )

            return output

        except Exception as e:
            logger.error(f"修复生成失败: {e}", exc_info=True)
            
            # 返回失败结果
            return RefineOutput(
                file_fixes=[],
                confidence=0.0,
                unfixable_errors=[f"修复生成失败: {str(e)}"]
            )

    async def _apply_fixes(
        self,
        state: IssueProcessState,
        refine_output: RefineOutput
    ):
        """
        应用修复补丁到 sandbox
        
        对每个文件：
        1. 生成 unified diff
        2. 调用 GitService.apply_patch()
        3. 更新 patching.generated_patches
        
        Args:
            state: 当前状态
            refine_output: 修复方案
        """
        sandbox = state.get("sandbox", {})
        sandbox_id = sandbox.get("sandbox_id")
        
        git_service = GitService(self.sandbox_manager, sandbox_id)
        file_service = FileService(self.sandbox_manager, sandbox_id)
        
        patching = state.get("patching", {})
        generated_patches = patching.get("generated_patches", {})
        
        for file_fix in refine_output.file_fixes:
            try:
                # 从 sandbox 读取当前内容
                current_content = await file_service.read_file(file_fix.file_path)
                
                # 生成 unified diff
                diff = self._generate_diff(
                    current_content,
                    file_fix.fixed_code,
                    file_fix.file_path
                )
                
                if not diff:
                    logger.warning(f"文件 {file_fix.file_path} 没有变化，跳过应用")
                    continue
                
                # 应用补丁
                logger.info(f"应用修复补丁: {file_fix.file_path}")
                await git_service.apply_patch(diff)
                
                # 更新 patching.generated_patches（覆盖旧补丁）
                generated_patches[file_fix.file_path] = diff
                
                logger.info(f"修复补丁应用成功: {file_fix.file_path}")
                
            except Exception as e:
                logger.error(f"应用修复补丁失败 {file_fix.file_path}: {e}", exc_info=True)
                raise

    def _generate_diff(
        self,
        original_content: str,
        modified_content: str,
        file_path: str
    ) -> str:
        """
        生成 unified diff
        
        Args:
            original_content: 原始内容
            modified_content: 修改后内容
            file_path: 文件路径
        
        Returns:
            unified diff 字符串
        """
        original_lines = original_content.splitlines()
        modified_lines = modified_content.splitlines()
        
        diff_lines = list(difflib.unified_diff(
            original_lines,
            modified_lines,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            lineterm="",
        ))
        
        if not diff_lines:
            return ""
        
        return "\n".join(diff_lines) + "\n"

    def _update_state_and_retry(
        self,
        state: IssueProcessState,
        refine_output: RefineOutput
    ) -> Command:
        """
        更新状态并准备重新验证
        
        清空验证结果，增加重试计数，记录修复历史。
        """
        verification = state.get("verification", {})
        runtime = state.get("runtime", {})
        
        refine_retry_count = verification.get("refine_retry_count", 0)
        refine_history = verification.get("refine_history", [])
        
        # 记录修复历史
        refine_history.append({
            "round": refine_retry_count + 1,
            "fixed_files": [fix.file_path for fix in refine_output.file_fixes],
            "fixed_error_lines": {
                fix.file_path: fix.fixed_error_lines
                for fix in refine_output.file_fixes
            },
            "confidence": refine_output.confidence,
            "reasoning": {
                fix.file_path: fix.reasoning
                for fix in refine_output.file_fixes
            },
        })
        
        update_dict = {
            "verification": {
                **verification,
                "refine_retry_count": refine_retry_count + 1,
                "refine_history": refine_history,
                # 清空 final_verification 以触发重新验证
                "final_verification": None,
            },
            "runtime": {
                **runtime,
                "executed_nodes": [
                    *runtime.get("executed_nodes", []),
                    NodeName.REFINE_AGENT.value,
                ],
                "current_step": NodeName.REFINE_AGENT.value,
            },
        }
        
        logger.info(
            f"修复完成，清空验证结果以触发重新验证 "
            f"(第 {refine_retry_count + 1} 轮)"
        )
        
        # 发送完成事件
        asyncio.create_task(adispatch_custom_event(
            ProcessStage.THINK_CHAIN.value,
            {
                "status": NodeName.REFINE_AGENT.value,
                "progress": f"修复完成（第 {refine_retry_count + 1} 轮）",
                "think_chain_item": {
                    "type": NodeName.REFINE_AGENT.value,
                    "title": "批量修复",
                    "desc": f"修复了 {len(refine_output.file_fixes)} 个文件",
                    "urls": [],
                },
            },
        ))
        
        return Command(update=update_dict, goto=NodeName.MAIN_ROUTER.value)

    def _abort_with_error(
        self,
        state: IssueProcessState,
        error_msg: str
    ) -> Command:
        """
        中止修复流程，返回 sandbox_teardown
        """
        runtime = state.get("runtime", {})
        verification = state.get("verification", {})
        
        update_dict = {
            "runtime": {
                **runtime,
                "error": error_msg,
                "executed_nodes": [
                    *runtime.get("executed_nodes", []),
                    NodeName.REFINE_AGENT.value,
                ],
                "current_step": NodeName.REFINE_AGENT.value,
            },
            "verification": {
                **verification,
                # 保持当前的 refine_retry_count
            }
        }
        
        # 发送中止事件
        asyncio.create_task(adispatch_custom_event(
            ProcessStage.THINK_CHAIN.value,
            {
                "status": NodeName.REFINE_AGENT.value,
                "progress": "修复失败，停止重试",
                "think_chain_item": {
                    "type": NodeName.REFINE_AGENT.value,
                    "title": "批量修复",
                    "desc": error_msg,
                    "urls": [],
                },
            },
        ))
        
        return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)


# 需要导入 asyncio 用于 create_task
import asyncio


# 导出
__all__ = ["RefineAgentNode"]
