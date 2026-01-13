"""
Reviewer Agent Node - 代码评审节点
生成详细的代码变更评审报告，类似 PR Description
"""

from typing import Literal, Optional

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command

from app.core.logger_config import logger
from app.core.prompt_manager import prompt_manager
from app.graph.state import IssueProcessState, NodeName, ProcessStage
from app.llms.llm_factory import get_llm_model


class ReviewerAgentNode:
    """评审 Agent 节点"""

    def __init__(self):
        """初始化节点"""
        self.prompt_manager = prompt_manager

    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal["main_router", "sandbox_teardown"]]:
        """
        生成代码评审报告

        Args:
            state: 当前工作流状态

        Returns:
            Command 对象，返回 main_router 节点
        """
        update_dict = {}

        try:
            # 发送进度事件
            await adispatch_custom_event(
                ProcessStage.REVIEW.value,
                {
                    "status": NodeName.REVIEWER.value,
                    "progress": "正在生成评审报告...",
                    "think_chain_item": {
                        "type": NodeName.REVIEWER.value,
                        "title": "代码评审",
                        "desc": "生成变更说明和评审建议",
                        "urls": [],
                    },
                },
            )

            # 从分域结构获取所需信息
            patching = state.get("patching", {})
            generated_patches = patching.get("generated_patches", {})
            verification = state.get("verification", {})
            verification_result = verification.get("final_verification")
            impact = state.get("impact", {})
            impact_report = impact.get("impact_report")

            if not generated_patches:
                logger.warning("没有生成的补丁，跳过评审")
                runtime = state.get("runtime", {})
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.REVIEWER.value,
                            ],
                            "current_step": NodeName.REVIEWER.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.MAIN_ROUTER.value)

            # 生成评审报告（Markdown 格式）
            markdown_report = await self._generate_review_report(
                state,
                generated_patches,
                verification_result,
                impact_report,
            )

            runtime = state.get("runtime", {})
            update_dict.update(
                {
                    "review": {
                        "review_report": markdown_report,
                    },
                    "runtime": {
                        **runtime,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.REVIEWER.value,
                        ],
                        "current_step": NodeName.REVIEWER.value,
                    },
                }
            )

            logger.info("评审报告生成完成")

            # 发送完成事件
            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": NodeName.REVIEWER.value,
                    "progress": "评审报告生成完成",
                    "think_chain_item": {
                        "type": NodeName.REVIEWER.value,
                        "title": "代码评审",
                        "desc": "已生成 Markdown 格式的评审报告",
                        "urls": [],
                    },
                },
            )

            return Command(update=update_dict, goto=NodeName.MAIN_ROUTER.value)

        except Exception as e:
            logger.error(f"评审报告生成失败: {e}", exc_info=True)
            runtime = state.get("runtime", {})
            update_dict.update(
                {
                    "runtime": {
                        **runtime,
                        "error": f"评审报告生成失败: {str(e)}",
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.REVIEWER.value,
                        ],
                        "current_step": NodeName.REVIEWER.value,
                    },
                }
            )

            return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

    async def _generate_review_report(
        self,
        state: IssueProcessState,
        generated_patches: dict[str, str],
        verification_result: Optional[dict],
        impact_report: Optional[dict],
    ) -> str:
        """
        生成评审报告（Markdown 格式）

        Args:
            state: 当前状态
            generated_patches: 生成的补丁字典
            verification_result: 验证结果
            impact_report: 影响分析报告

        Returns:
            Markdown 格式的评审报告
        """
        issue_data = state.get("issue_data", {})
        issue_title = issue_data.get("title", "")
        issue_description = issue_data.get("description", "")
        # 从分域结构读取 issue_type
        analysis = state.get("analysis", {})
        issue_type = analysis.get("issue_type", "unknown")

        # 构建补丁信息列表
        patches_info = []

        for file_path, diff in generated_patches.items():
            patches_info.append(
                {
                    "file_path": file_path,
                    "unified_diff": diff,
                }
            )

        # 变更摘要
        changes_summary = f"修改了 {len(patches_info)} 个文件"

        # 验证结果摘要
        verification_summary = "未执行验证"
        if verification_result:
            passed = verification_result.get("passed", False)
            issues_count = len(verification_result.get("issues", []))
            verification_summary = (
                f"验证状态: {'通过' if passed else '失败'}"
                + (f", 发现 {issues_count} 个问题" if issues_count > 0 else "")
            )

        # 影响分析摘要
        impact_summary = "未执行影响分析"
        if impact_report:
            affected_count = len(impact_report.get("affected_callers", []))
            risk_level = impact_report.get("risk_level", "unknown")
            impact_summary = f"受影响调用方: {affected_count} 个, 风险级别: {risk_level}"

        # 渲染 Prompt
        prompt = self.prompt_manager.render(
            "change_review",
            issue_title=issue_title,
            issue_description=issue_description or "无描述",
            issue_type=issue_type,
            changes_summary=changes_summary,
            patches=patches_info,
            verification_results=verification_summary,
            impact_analysis=impact_summary,
        )

        # 调用 LLM 生成 Markdown 报告
        llm = await get_llm_model(model_name="gpt-5-2025-08-07")
        response = await llm.ainvoke(prompt)

        # 直接返回 Markdown 内容
        markdown_report = response.content.strip()

        return markdown_report


