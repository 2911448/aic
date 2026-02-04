"""
Merge Workflow - Merge Request 合并后的向量库增量更新工作流

流水线：START → sandbox_bootstrap → merge_diff_collector → vector_index_update → sandbox_teardown → END
"""

from langgraph.graph import StateGraph

from app.core.logger_config import logger
from app.graph.lifecycle.sandbox_bootstrap import SandboxBootstrapNode
from app.graph.lifecycle.sandbox_teardown import SandboxTeardownNode
from app.graph.nodes.merge_diff_collector_node import MergeDiffCollectorNode
from app.graph.nodes.vector_index_update_node import VectorIndexUpdateNode
from app.graph.state import IssueProcessState
from app.graph.state.node_names import NodeName
from app.graph.workflows.opik_tracing import track_langgraph_workflow_with_opik


def create_merge_workflow():
    """
    创建 Merge Request 处理工作流

    Returns:
        Compiled workflow graph
    """
    logger.info("创建 Merge Request 处理工作流")

    # 创建节点实例
    # Lifecycle (公共前置/后置节点)
    # SandboxBootstrapNode 是所有 workflow 的公共前置节点
    # 通过 next_node 参数指定本 workflow 的入口节点，实现可扩展性
    sandbox_bootstrap_node = SandboxBootstrapNode(
        next_node=NodeName.MERGE_DIFF_COLLECTOR.value
    )
    sandbox_teardown_node = SandboxTeardownNode()

    # Merge workflow 核心节点
    merge_diff_collector_node = MergeDiffCollectorNode()
    vector_index_update_node = VectorIndexUpdateNode(max_concurrent_files=5)

    # 创建状态图
    graph = StateGraph(IssueProcessState)

    # 添加节点
    # Lifecycle
    graph.add_node(NodeName.SANDBOX_BOOTSTRAP.value, sandbox_bootstrap_node)
    graph.add_node(NodeName.SANDBOX_TEARDOWN.value, sandbox_teardown_node)

    # Merge workflow 节点
    graph.add_node(NodeName.MERGE_DIFF_COLLECTOR.value, merge_diff_collector_node)
    graph.add_node(NodeName.VECTOR_INDEX_UPDATE.value, vector_index_update_node)

    # 设置入口点：START → SandboxBootstrap
    graph.set_entry_point(NodeName.SANDBOX_BOOTSTRAP.value)

    logger.info("Merge workflow 创建完成")

    # 编译工作流
    workflow = graph.compile()

    # Opik tracing（可选）
    workflow = track_langgraph_workflow_with_opik(
        workflow,
        workflow_label="merge-workflow",
        tags=["merge-workflow", "langgraph"],
        metadata={"workflow_version": "1.0"},
    )

    return workflow


# 导出
__all__ = ["create_merge_workflow"]
