"""
Verify Agent Node - 验证节点（占位实现）
在沙箱环境中验证修复方案
"""

from typing import Literal

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command

from app.core.logger_config import logger
from app.graph.state import IssueProcessState, NodeName, ProcessStage


class VerifyAgentNode:
    """验证Agent节点（占位）"""

    def __init__(self):
        """初始化节点"""
        pass

    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal[NodeName.PLAN.value]]:
        """
        执行验证（占位实现）

        Args:
            state: 当前工作流状态

        Returns:
            Command对象，返回plan节点
        """
        logger.info("验证节点（占位）- 暂未实现")

        await adispatch_custom_event(
            ProcessStage.VERIFICATION.value,
            {
                "status": NodeName.VERIFY.value,
                "progress": "验证功能暂未实现",
                "think_chain_item": {
                    "type": NodeName.VERIFY.value,
                    "title": "验证",
                    "desc": "功能开发中...",
                    "urls": [],
                },
            },
        )

        # 占位实现：返回空的验证结果
        update_dict = {
            "verification_result": None,
            "executed_nodes": [
                *state.get("executed_nodes", []),
                NodeName.VERIFY.value,
            ],
            "current_step": NodeName.VERIFY.value,
            "error": "验证功能暂未实现",
            "completed": True,
        }

        return Command(update=update_dict, goto=NodeName.END.value)
