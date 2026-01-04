"""
Code Scope Agent Node - 代码定位节点（占位实现）
使用AST/CFG分析定位具体代码区域
"""

from typing import Literal

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command

from app.core.logger_config import logger
from app.graph.state import IssueProcessState, NodeName, ProcessStage


class CodeScopeAgentNode:
    """代码定位Agent节点（占位）"""

    def __init__(self):
        """初始化节点"""
        pass

    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal[NodeName.PLAN.value]]:
        """
        执行代码定位（占位实现）

        Args:
            state: 当前工作流状态

        Returns:
            Command对象，返回plan节点
        """
        logger.info("代码定位节点（占位）- 暂未实现")

        await adispatch_custom_event(
            ProcessStage.CODE_LOCATION.value,
            {
                "status": NodeName.CODE_SCOPE.value,
                "progress": "代码定位功能暂未实现",
                "think_chain_item": {
                    "type": NodeName.CODE_SCOPE.value,
                    "title": "代码定位",
                    "desc": "功能开发中...",
                    "urls": [],
                },
            },
        )

        # 占位实现：返回空的定位结果
        update_dict = {
            "code_scope": None,
            "executed_nodes": [
                *state.get("executed_nodes", []),
                NodeName.CODE_SCOPE.value,
            ],
            "current_step": NodeName.CODE_SCOPE.value,
            "error": "代码定位功能暂未实现",
            "completed": True,
        }

        return Command(update=update_dict, goto=NodeName.END.value)
