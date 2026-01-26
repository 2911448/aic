"""
Merge Workflow - Merge Request 合并后的向量库增量更新工作流

流水线：START → sandbox_bootstrap → merge_diff_collector → vector_index_update → sandbox_teardown → END
"""

import os

from langgraph.graph import StateGraph

from app.config.app_config import app_config
from app.core.logger_config import logger
from app.graph.lifecycle.sandbox_bootstrap import SandboxBootstrapNode
from app.graph.lifecycle.sandbox_teardown import SandboxTeardownNode
from app.graph.nodes.merge_diff_collector_node import MergeDiffCollectorNode
from app.graph.nodes.vector_index_update_node import VectorIndexUpdateNode
from app.graph.state import IssueProcessState
from app.graph.state.node_names import NodeName


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

    # Opik tracing 集成（如果已配置且未被环境变量禁用）
    opik_config = app_config.opik
    opik_disabled = os.getenv("OPIK_TRACK_DISABLE", "").lower() in ("true", "1", "yes")

    if opik_config and opik_config.enabled and not opik_disabled:
        try:
            from opik.integrations.langchain import OpikTracer, track_langgraph

            # 配置 Opik 环境变量（如果还没配置）
            if not os.getenv("OPIK_API_KEY"):
                os.environ["OPIK_API_KEY"] = opik_config.api_key
            if not os.getenv("OPIK_WORKSPACE"):
                os.environ["OPIK_WORKSPACE"] = opik_config.workspace
            if not os.getenv("OPIK_PROJECT_NAME"):
                os.environ["OPIK_PROJECT_NAME"] = opik_config.project_name

            # 创建 OpikTracer 实例
            opik_tracer = OpikTracer(
                project_name=opik_config.project_name,
                tags=["merge-workflow", "langgraph"],
                metadata={"workflow_version": "1.0"},
            )

            # 使用 track_langgraph 包装工作流
            workflow = track_langgraph(workflow, opik_tracer)
            logger.info("Opik tracing 已启用 (merge workflow)")
        except Exception as e:
            logger.warning(
                f"Opik tracing 初始化失败（将继续运行但不记录 traces）: {e}"
            )
    else:
        if opik_disabled:
            logger.info("Opik tracing 已通过 OPIK_TRACK_DISABLE 环境变量禁用")
        elif not opik_config or not opik_config.enabled:
            logger.info("Opik tracing 未配置或已在配置中禁用")

    return workflow


# 导出
__all__ = ["create_merge_workflow"]
