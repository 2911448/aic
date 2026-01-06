"""
Refine Agent Node - 失败诊断和修复循环节点
当验证失败时，分析原因并决定是否重试
"""

from typing import Literal, Optional

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command

from app.core.logger_config import logger
from app.core.prompt_manager import prompt_manager
from app.graph.state import IssueProcessState, NodeName, ProcessStage
from app.llms.llm_factory import get_gpt_model
from app.utils.common_function import parse_json_response


class DiagnosisResult:
    """诊断结果"""

    def __init__(self):
        self.root_cause: str = ""
        self.failed_at: dict = {}
        self.fix_suggestions: list[dict] = []
        self.key_points: list[str] = []
        self.decision: str = "abort"  # retry/abort
        self.decision_reasoning: str = ""
        self.retry_strategy: dict = {}

    def to_dict(self) -> dict:
        return {
            "root_cause": self.root_cause,
            "failed_at": self.failed_at,
            "fix_suggestions": self.fix_suggestions,
            "key_points": self.key_points,
            "decision": self.decision,
            "decision_reasoning": self.decision_reasoning,
            "retry_strategy": self.retry_strategy
        }


class RefineAgentNode:
    """失败诊断和修复循环节点"""

    MAX_RETRY_COUNT = 3  # 最大重试次数

    def __init__(self):
        """初始化节点"""
        self.prompt_manager = prompt_manager

    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal[NodeName.PLAN.value, NodeName.PATCH_GENERATOR.value]]:
        """
        诊断验证失败的原因并决定下一步

        Args:
            state: 当前工作流状态

        Returns:
            Command 对象，可能返回 PLAN 或 PATCH_GENERATOR
        """
        update_dict = {}

        try:
            # 发送进度事件
            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": "refine",
                    "progress": "正在诊断验证失败原因...",
                    "think_chain_item": {
                        "type": "refine",
                        "title": "失败诊断",
                        "desc": "分析错误并制定修复策略",
                        "urls": [],
                    },
                },
            )

            # 获取验证结果
            verification_result = state.get("verification_result")
            if not verification_result:
                logger.warning("没有验证结果，无法进行诊断")
                update_dict.update(
                    {
                        "executed_nodes": [
                            *state.get("executed_nodes", []),
                            "refine",
                        ],
                        "current_step": "refine",
                    }
                )
                return Command(update=update_dict, goto=NodeName.PLAN.value)

            # 检查是否验证失败
            if verification_result.get("status") == "pass":
                logger.info("验证通过，无需修复")
                update_dict.update(
                    {
                        "executed_nodes": [
                            *state.get("executed_nodes", []),
                            "refine",
                        ],
                        "current_step": "refine",
                    }
                )
                return Command(update=update_dict, goto=NodeName.PLAN.value)

            # 检查重试次数
            retry_count = state.get("patch_retry_count", 0)
            if retry_count >= self.MAX_RETRY_COUNT:
                logger.warning(f"已达到最大重试次数 ({self.MAX_RETRY_COUNT})，停止重试")

                update_dict.update(
                    {
                        "error": f"验证失败且已达到最大重试次数 ({self.MAX_RETRY_COUNT})",
                        "diagnosis_result": {
                            "decision": "abort",
                            "decision_reasoning": "已达到最大重试次数",
                        },
                        "executed_nodes": [
                            *state.get("executed_nodes", []),
                            "refine",
                        ],
                        "current_step": "refine",
                    }
                )

                await adispatch_custom_event(
                    ProcessStage.THINK_CHAIN.value,
                    {
                        "status": "refine",
                        "progress": "已达到最大重试次数，停止修复",
                        "think_chain_item": {
                            "type": "refine",
                            "title": "失败诊断",
                            "desc": "已达到最大重试次数，建议人工介入",
                            "urls": [],
                        },
                    },
                )

                return Command(update=update_dict, goto=NodeName.PLAN.value)

            # 执行诊断
            diagnosis = await self._diagnose_failure(state, verification_result)

            # 更新重试历史
            retry_history = state.get("retry_history", [])
            retry_history.append(
                {
                    "retry_count": retry_count + 1,
                    "diagnosis": diagnosis.to_dict(),
                    "verification_result": verification_result,
                }
            )

            update_dict.update(
                {
                    "diagnosis_result": diagnosis.to_dict(),
                    "retry_history": retry_history,
                    "patch_retry_count": retry_count + 1,
                    "executed_nodes": [
                        *state.get("executed_nodes", []),
                        "refine",
                    ],
                    "current_step": "refine",
                }
            )

            # 根据决策选择下一步
            if diagnosis.decision == "retry":
                logger.info(
                    f"诊断建议重试 (第 {retry_count + 1} 次)，将重新生成补丁"
                )

                await adispatch_custom_event(
                    ProcessStage.THINK_CHAIN.value,
                    {
                        "status": "refine",
                        "progress": f"准备重试修复 (第 {retry_count + 1} 次)",
                        "think_chain_item": {
                            "type": "refine",
                            "title": "失败诊断",
                            "desc": f"{diagnosis.root_cause}",
                            "urls": [],
                        },
                    },
                )

                # 返回到 Patch Generator 重新生成
                return Command(update=update_dict, goto=NodeName.PATCH_GENERATOR.value)

            else:
                logger.info(f"诊断建议停止重试: {diagnosis.decision_reasoning}")

                await adispatch_custom_event(
                    ProcessStage.THINK_CHAIN.value,
                    {
                        "status": "refine",
                        "progress": "停止自动修复，建议人工介入",
                        "think_chain_item": {
                            "type": "refine",
                            "title": "失败诊断",
                            "desc": diagnosis.decision_reasoning,
                            "urls": [],
                        },
                    },
                )

                # 返回到 PLAN 节点，由 PLAN 决定下一步
                return Command(update=update_dict, goto=NodeName.PLAN.value)

        except Exception as e:
            logger.error(f"失败诊断失败: {e}", exc_info=True)
            update_dict.update(
                {
                    "error": f"失败诊断失败: {str(e)}",
                    "executed_nodes": [
                        *state.get("executed_nodes", []),
                        "refine",
                    ],
                    "current_step": "refine",
                }
            )

            return Command(update=update_dict, goto=NodeName.PLAN.value)

    async def _diagnose_failure(
        self,
        state: IssueProcessState,
        verification_result: dict,
    ) -> DiagnosisResult:
        """
        诊断验证失败的原因

        Args:
            state: 当前状态
            verification_result: 验证结果

        Returns:
            诊断结果
        """
        issue_data = state.get("issue_data", {})
        issue_title = issue_data.get("title", "")
        issue_description = issue_data.get("description", "")

        # 获取代码信息
        editable_context_dict = state.get("editable_context", {})
        original_code = ""
        modified_code = ""

        if editable_context_dict:
            from app.schemas.context_assembly import EditableContextSlice

            context = EditableContextSlice(**editable_context_dict)
            original_code = context.full_code

        # 格式化验证失败信息
        verification_failure = self._format_verification_failure(verification_result)

        # 构建 Prompt
        prompt = self.prompt_manager.render(
            "failure_diagnosis",
            issue_title=issue_title,
            issue_description=issue_description or "无描述",
            original_code=original_code,
            modified_code=modified_code or "未获取到修改后代码",
            verification_failure=verification_failure,
            syntax_check=self._format_check_result(
                verification_result.get("syntax_check", {})
            ),
            linter_check=self._format_check_result(
                verification_result.get("linter_check", {})
            ),
            semantic_check=self._format_check_result(
                verification_result.get("semantic_check", {})
            ),
        )

        try:
            # 调用 LLM
            llm = await get_gpt_model(temperature=0.2)
            response = await llm.ainvoke(prompt)

            # 解析响应
            result = parse_json_response(response.content)

            # 构建诊断结果
            diagnosis = DiagnosisResult()
            diagnosis.root_cause = result.get("root_cause", "")
            diagnosis.failed_at = result.get("failed_at", {})
            diagnosis.fix_suggestions = result.get("fix_suggestions", [])
            diagnosis.key_points = result.get("key_points", [])
            diagnosis.decision = result.get("decision", "abort")
            diagnosis.decision_reasoning = result.get("decision_reasoning", "")
            diagnosis.retry_strategy = result.get("retry_strategy", {})

            return diagnosis

        except Exception as e:
            logger.error(f"诊断失败: {e}", exc_info=True)

            # 返回默认诊断结果（建议停止）
            diagnosis = DiagnosisResult()
            diagnosis.root_cause = f"诊断过程出错: {str(e)}"
            diagnosis.decision = "abort"
            diagnosis.decision_reasoning = "无法完成自动诊断，建议人工介入"

            return diagnosis

    def _format_verification_failure(self, verification_result: dict) -> str:
        """格式化验证失败信息"""
        parts = []

        parts.append(f"**验证状态**: {verification_result.get('status', 'unknown')}")
        parts.append(
            f"**置信度**: {verification_result.get('confidence', 0.0):.0%}"
        )

        issues = verification_result.get("issues", [])
        if issues:
            parts.append(f"\n**发现的问题** ({len(issues)} 个):")
            for idx, issue in enumerate(issues[:5], 1):  # 只显示前5个
                parts.append(
                    f"{idx}. [{issue.get('level', 'unknown')}] "
                    f"Line {issue.get('line', '?')}: {issue.get('message', '')}"
                )

        return "\n".join(parts)

    def _format_check_result(self, check_result: dict) -> str:
        """格式化单项检查结果"""
        if not check_result:
            return "未执行"

        status = check_result.get("status", "unknown")
        message = check_result.get("message", "")

        parts = [f"状态: {status}"]
        if message:
            parts.append(f"消息: {message}")

        issues = check_result.get("issues", [])
        if issues:
            parts.append(f"\n问题列表:")
            for issue in issues[:3]:  # 只显示前3个
                parts.append(
                    f"- Line {issue.get('line', '?')}: {issue.get('message', '')}"
                )

        return "\n".join(parts)
