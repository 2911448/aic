"""
Issue Workflow
"""

from langgraph.graph import StateGraph

from app.core.logger_config import logger
from app.graph.lifecycle.sandbox_bootstrap import SandboxBootstrapNode
from app.graph.lifecycle.sandbox_teardown import SandboxTeardownNode
from app.graph.nodes.planner_agent_node import PlannerAgentNode
from app.graph.nodes.task_runner_node import TaskRunnerNode
from app.graph.nodes.omni_explorer_node import OmniExplorerNode
from app.graph.nodes.code_agent_node import CodeAgentNode
from app.graph.nodes.mr_publisher_agent_node import MRPublisherAgentNode
from app.graph.nodes.verification_node import VerificationNode
from app.graph.state import IssueProcessState, NodeName
from app.graph.workflows.opik_tracing import track_langgraph_workflow_with_opik


def create_issue_workflow():
    """
    创建 Issue 处理工作流

    Returns:
        Compiled workflow graph
    """
    logger.info("创建 Issue 处理工作流")

    # Lifecycle (公共前置/后置节点)
    sandbox_bootstrap_node = SandboxBootstrapNode(next_node=NodeName.PLANNER_ORCHESTRATOR.value)
    sandbox_teardown_node = SandboxTeardownNode()

    # Planner Orchestrator (LLM 决策中心)
    planner_orchestrator_node = PlannerAgentNode()
    
    # TaskRunner (系统派发执行节点)
    task_runner_node = TaskRunnerNode()
    
    # 可被调度的 Agent 节点（通过 TaskRunner 调用）
    omni_explorer_node = OmniExplorerNode()
    code_agent_node = CodeAgentNode()
    verification_node = VerificationNode()
    mr_publisher_node = MRPublisherAgentNode()

    # 创建状态图
    graph = StateGraph(IssueProcessState)

    # 添加节点
    graph.add_node(NodeName.SANDBOX_BOOTSTRAP.value, sandbox_bootstrap_node)
    graph.add_node(NodeName.SANDBOX_TEARDOWN.value, sandbox_teardown_node)

    graph.add_node(NodeName.PLANNER_ORCHESTRATOR.value, planner_orchestrator_node)
    graph.add_node(NodeName.TASK_RUNNER.value, task_runner_node)

    graph.add_node(NodeName.OMNI_EXPLORER.value, omni_explorer_node)
    graph.add_node(NodeName.CODE_AGENT.value, code_agent_node)
    graph.add_node(NodeName.VERIFICATION_FLOW.value, verification_node)
    graph.add_node(NodeName.MR_PUBLISHER.value, mr_publisher_node)

    # 设置入口点：START → SandboxBootstrap
    graph.set_entry_point(NodeName.SANDBOX_BOOTSTRAP.value)

    logger.info("工作流创建完成")

    workflow = graph.compile()
    
    # Opik tracing（可选）
    workflow = track_langgraph_workflow_with_opik(
        workflow,
        workflow_label="issue-workflow",
        tags=["issue-workflow", "langgraph"],
        metadata={"workflow_version": "1.0"},
    )
    
    return workflow


# 导出
__all__ = [
    "create_issue_workflow"
]

