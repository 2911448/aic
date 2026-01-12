"""
Issue Workflow - 重构后的 Issue 处理工作流

采用 Multi-Agent 架构：
- Sandbox 生命周期前置（SandboxBootstrap/Teardown）
- 确定性路由（MainRouter）替代 LLM 调度
- 子图封装复杂流程（PatchFlow, VerificationFlow）
- 工具驱动的 Agent
"""

from langgraph.graph import StateGraph

from app.core.logger_config import logger
from app.graph.lifecycle.sandbox_bootstrap import SandboxBootstrapNode
from app.graph.lifecycle.sandbox_teardown import SandboxTeardownNode
from app.graph.nodes.batch_context_builder_node import BatchContextBuilderNode
from app.graph.nodes.code_retriever_agent_node import CodeRetrieverAgentNode
from app.graph.nodes.context_slice_builder_node import ContextSliceBuilderNode
from app.graph.nodes.entry_selector_agent_node import EntrySelectorAgentNode
from app.graph.nodes.global_impact_scan_node import GlobalImpactScanNode
from app.graph.nodes.incremental_impact_scan_node import IncrementalImpactScanNode
from app.graph.nodes.issue_insight_agent_node import IssueInsightAgentNode
from app.graph.nodes.mr_submitter_agent_node import MRSubmitterAgentNode
from app.graph.nodes.queue_manager_node import QueueManagerNode
from app.graph.nodes.refactoring_agent_batch_node import RefactoringAgentBatchNode
from app.graph.nodes.refine_agent_node import RefineAgentNode
from app.graph.nodes.reviewer_agent_node import ReviewerAgentNode
from app.graph.nodes.verification_node import VerificationNode
from app.graph.routers.main_router import MainRouterNode
from app.graph.state import IssueProcessState, NodeName
from app.graph.subgraphs.patch_flow import PatchFlowNode


def create_issue_workflow():
    """
    创建 Issue 处理工作流

    Returns:
        Compiled workflow graph
    """
    logger.info("创建 Issue 处理工作流")

    # 创建节点实例
    # Lifecycle
    sandbox_bootstrap_node = SandboxBootstrapNode()
    sandbox_teardown_node = SandboxTeardownNode()

    # Routers / Controllers
    main_router_node = MainRouterNode(max_patch_retries=3)

    # Core Agents / Nodes
    issue_analyst_node = IssueInsightAgentNode()
    code_retriever_node = CodeRetrieverAgentNode()
    entry_selector_node = EntrySelectorAgentNode()
    context_slice_builder_node = ContextSliceBuilderNode()

    # Subgraphs / Complex Flows
    patch_flow_node = PatchFlowNode()
    
    # Verification Node
    verification_node = VerificationNode()

    # Ripple Loop Nodes
    global_impact_scan_node = GlobalImpactScanNode(max_scan_files=500)
    queue_manager_node = QueueManagerNode(batch_size=5)
    batch_context_builder_node = BatchContextBuilderNode()
    refactoring_agent_batch_node = RefactoringAgentBatchNode(max_retries=3)
    incremental_impact_scan_node = IncrementalImpactScanNode()

    # Other Agents
    refine_agent_node = RefineAgentNode()
    reviewer_node = ReviewerAgentNode()
    mr_submitter_node = MRSubmitterAgentNode()

    # 创建状态图
    graph = StateGraph(IssueProcessState)

    # 添加节点
    # Lifecycle
    graph.add_node(NodeName.SANDBOX_BOOTSTRAP.value, sandbox_bootstrap_node)
    graph.add_node(NodeName.SANDBOX_TEARDOWN.value, sandbox_teardown_node)

    # Routers / Controllers
    graph.add_node(NodeName.MAIN_ROUTER.value, main_router_node)

    # Core Agents / Nodes
    graph.add_node(NodeName.ISSUE_ANALYST.value, issue_analyst_node)
    graph.add_node(NodeName.CODE_RETRIEVER.value, code_retriever_node)
    graph.add_node(NodeName.ENTRY_SELECTOR.value, entry_selector_node)
    graph.add_node(NodeName.CONTEXT_SLICE_BUILDER.value, context_slice_builder_node)

    # Subgraphs / Complex Flows
    graph.add_node(NodeName.PATCH_FLOW.value, patch_flow_node)
    
    # Verification Node
    graph.add_node(NodeName.VERIFICATION_FLOW.value, verification_node)

    # Ripple Loop Nodes
    graph.add_node(NodeName.GLOBAL_IMPACT_SCAN.value, global_impact_scan_node)
    graph.add_node(NodeName.QUEUE_MANAGER.value, queue_manager_node)
    graph.add_node(NodeName.BATCH_CONTEXT_BUILDER.value, batch_context_builder_node)
    graph.add_node(NodeName.REFACTORING_AGENT_BATCH.value, refactoring_agent_batch_node)
    graph.add_node(NodeName.INCREMENTAL_IMPACT_SCAN.value, incremental_impact_scan_node)

    # Other Agents
    graph.add_node(NodeName.REFINE_AGENT.value, refine_agent_node)
    graph.add_node(NodeName.REVIEWER.value, reviewer_node)
    graph.add_node(NodeName.MR_SUBMITTER.value, mr_submitter_node)

    # 设置入口点：START → SandboxBootstrap
    graph.set_entry_point(NodeName.SANDBOX_BOOTSTRAP.value)

    logger.info("工作流创建完成")

    return graph.compile()


# 导出
__all__ = [
    "create_issue_workflow"
]

