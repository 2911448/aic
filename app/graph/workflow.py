"""
LangGraph Workflow for Issue Processing with Command-based dynamic routing
"""

from langgraph.graph import StateGraph

from app.core.logger_config import logger
from app.graph.nodes.code_retriever_agent_node import CodeRetrieverAgentNode
from app.graph.nodes.code_scope_agent_node import CodeScopeAgentNode
from app.graph.nodes.issue_insight_agent_node import IssueInsightAgentNode
from app.graph.nodes.patch_smith_agent_node import PatchSmithAgentNode
from app.graph.nodes.plan_node import PlanAgentNode
from app.graph.nodes.verify_agent_node import VerifyAgentNode
from app.graph.state import IssueProcessState, NodeName


def create_issue_workflow():
    """
    创建Issue处理工作流

    工作流程:
    1. START → plan (中央调度器)
    2. plan → issue_insight/code_retriever/code_scope/patch_smith/verify (根据状态决定)
    3. 工作节点 → plan (完成后返回plan)
    4. plan → END (任务完成)

    Returns:
        Compiled workflow graph
    """
    logger.info("创建Issue处理工作流")

    # 创建节点实例
    plan_node = PlanAgentNode()
    issue_insight_node = IssueInsightAgentNode()
    code_retriever_node = CodeRetrieverAgentNode()
    code_scope_node = CodeScopeAgentNode()
    patch_smith_node = PatchSmithAgentNode()
    verify_node = VerifyAgentNode()

    # 创建状态图
    graph = StateGraph(IssueProcessState)

    # 添加节点
    graph.add_node(NodeName.PLAN.value, plan_node)
    graph.add_node(NodeName.ISSUE_INSIGHT.value, issue_insight_node)
    graph.add_node(NodeName.CODE_RETRIEVER.value, code_retriever_node)
    graph.add_node(NodeName.CODE_SCOPE.value, code_scope_node)
    graph.add_node(NodeName.PATCH_SMITH.value, patch_smith_node)
    graph.add_node(NodeName.VERIFY.value, verify_node)

    # 设置入口点：START → plan
    graph.set_entry_point(NodeName.PLAN.value)

    logger.info("工作流创建完成")

    return graph.compile()


# Backward compatibility: keep old function name
def create_chat_graph():
    """
    创建聊天工作流图（已废弃，使用create_issue_workflow）

    为了向后兼容保留此函数
    """
    logger.warning("create_chat_graph已废弃，请使用create_issue_workflow")
    return create_issue_workflow()
