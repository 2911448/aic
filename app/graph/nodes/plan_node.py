"""
Plan Node - 中央路由协调器
分析当前状态，决定下一步执行哪个节点
支持迭代式上下文构建和影响扩散控制
"""

from typing import Literal

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command

from app.core.logger_config import logger
from app.core.prompt_manager import prompt_manager
from app.graph.state import IssueProcessState, NodeName, ProcessStage
from app.llms.llm_factory import get_gpt_model
from app.schemas.issue_analysis import PlanDecision
from app.utils.common_function import parse_json_response


class PlanAgentNode:
    """Plan调度Agent节点"""

    def __init__(self):
        """初始化Plan节点"""
        self.prompt_manager = prompt_manager

    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[
        Literal[
            NodeName.ISSUE_INSIGHT.value,
            NodeName.CODE_RETRIEVER.value,
            NodeName.ENTRY_SELECTOR.value,
            NodeName.CONTEXT_ASSEMBLER.value,
            NodeName.PATCH_GENERATOR.value,
            NodeName.IMPACT_ANALYZER.value,
            NodeName.VERIFY.value,
            NodeName.REFINE.value,
            NodeName.REVIEWER.value,
            NodeName.END.value,
        ]
    ]:
        """
        决策下一步路由

        Args:
            state: 当前状态

        Returns:
            Command对象，指定下一步路由到哪个节点
        """
        goto = NodeName.END.value
        update_dict = {}

        try:
            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": NodeName.PLAN.value,
                    "progress": "规划下一步",
                    "think_chain_item": {
                        "type": NodeName.PLAN.value,
                        "title": "任务规划",
                        "desc": "分析当前状态，决定下一步操作",
                        "urls": [],
                    },
                },
            )

            goto = await self._make_decision(state)

        except Exception as e:
            logger.error(f"Plan节点执行失败: {e}")
            update_dict = {"error": f"Plan节点错误: {str(e)}", "completed": True}
            goto = NodeName.END.value

        return Command(goto=goto, update=update_dict)

    async def _make_decision(self, state: IssueProcessState) -> str:
        """
        根据当前状态做出路由决策

        Args:
            state: 当前状态

        Returns:
            下一个节点名称
        """
        logger.info("Plan节点启动 - 分析当前状态并决策下一步")

        if state.get("completed"):
            logger.info("任务已完成，结束流程")
            return NodeName.END.value

        if state.get("error"):
            error_msg = state.get("error")
            logger.error(f"检测到错误: {error_msg}，结束流程")
            return NodeName.END.value

        # 提取扩散控制信息
        impact_report = state.get("impact_report")
        current_target = state.get("current_target")
        target_queue = state.get("target_queue", [])
        current_depth = state.get("current_expansion_depth", 0)
        max_depth = state.get("max_expansion_depth", 3)

        llm = await get_gpt_model()

        prompt = self.prompt_manager.render(
            "plan_decision",
            issue_type=state.get("issue_type"),
            error=state.get("error"),
            search_queries=state.get("search_queries", []),
            retrieved_code=state.get("retrieved_code", []),
            current_target=current_target,
            target_queue_size=len(target_queue),
            editable_context=state.get("editable_context"),
            current_patch=state.get("current_patch"),
            impact_report=impact_report,
            current_expansion_depth=current_depth,
            max_expansion_depth=max_depth,
            verification_result=state.get("verification_result"),
            patch_retry_count=state.get("patch_retry_count", 0),
            diagnosis_result=state.get("diagnosis_result"),
            review_report=state.get("review_report"),
        )

        response = await llm.ainvoke(prompt)
        response_data = parse_json_response(response.content)
        decision = PlanDecision(**response_data)

        logger.info(
            f"Plan决策: 下一步 → {decision.next_node} | 原因: {decision.reason}"
        )

        # Route to next node
        if decision.next_node == "END":
            logger.info("Plan决定结束流程")
            return NodeName.END.value

        node_mapping = {
            "issue_insight": NodeName.ISSUE_INSIGHT.value,
            "code_retriever": NodeName.CODE_RETRIEVER.value,
            # 新节点
            "entry_selector": NodeName.ENTRY_SELECTOR.value,
            "context_assembler": NodeName.CONTEXT_ASSEMBLER.value,
            "patch_generator": NodeName.PATCH_GENERATOR.value,
            "impact_analyzer": NodeName.IMPACT_ANALYZER.value,
            # 验证与评审节点
            "verify": NodeName.VERIFY.value,
            "refine": NodeName.REFINE.value,
            "reviewer": NodeName.REVIEWER.value,
        }

        return node_mapping.get(decision.next_node, NodeName.END.value)
