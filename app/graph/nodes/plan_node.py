"""
Plan Node - 中央路由协调器
分析当前状态，决定下一步执行哪个节点
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
            NodeName.CODE_SCOPE.value,
            NodeName.PATCH_SMITH.value,
            NodeName.VERIFY.value,
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
        current_step = state.get("current_step", "unknown")
        executed_nodes = state.get("executed_nodes", [])

        logger.info(
            f"Plan节点启动 | 当前步骤: {current_step} | "
            f"已执行: {', '.join(executed_nodes) if executed_nodes else 'None'}"
        )

        # Check for completion or error conditions
        if state.get("completed"):
            logger.info("任务已完成，结束流程")
            return NodeName.END.value

        if state.get("error"):
            error_msg = state.get("error")
            logger.error(f"检测到错误: {error_msg}，结束流程")
            return NodeName.END.value

        llm = await get_gpt_model()

        prompt = self.prompt_manager.render(
            "plan_decision",
            current_step=current_step,
            executed_nodes=executed_nodes,
            issue_type=state.get("issue_type"),
            error=state.get("error"),
            search_queries=state.get("search_queries", []),
            retrieved_code=state.get("retrieved_code", []),
            code_scope=state.get("code_scope"),
            patch=state.get("patch"),
            verification_result=state.get("verification_result"),
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
            "code_scope": NodeName.CODE_SCOPE.value,
            "patch_smith": NodeName.PATCH_SMITH.value,
            "verify": NodeName.VERIFY.value,
        }

        return node_mapping.get(decision.next_node, NodeName.END.value)
