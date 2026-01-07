"""
LangGraph Workflow for Issue Processing with Command-based dynamic routing
支持迭代式上下文构建和影响扩散控制
"""

from langgraph.graph import StateGraph

from app.core.logger_config import logger
from app.graph.nodes.code_retriever_agent_node import CodeRetrieverAgentNode
from app.graph.nodes.context_assembler_agent_node import ContextAssemblerAgentNode
from app.graph.nodes.entry_selector_agent_node import EntrySelectorAgentNode
from app.graph.nodes.impact_analyzer_agent_node import ImpactAnalyzerAgentNode
from app.graph.nodes.issue_insight_agent_node import IssueInsightAgentNode
from app.graph.nodes.mr_submitter_agent_node import MRSubmitterAgentNode
from app.graph.nodes.patch_generator_agent_node import PatchGeneratorAgentNode
from app.graph.nodes.plan_node import PlanAgentNode
from app.graph.nodes.refine_agent_node import RefineAgentNode
from app.graph.nodes.reviewer_agent_node import ReviewerAgentNode
from app.graph.nodes.verify_agent_node import VerifyAgentNode
from app.graph.state import IssueProcessState, NodeName


def create_issue_workflow():
    """
    创建Issue处理工作流

    工作流程:
    1. START → plan (中央调度器)
    2. plan → issue_insight → plan (Issue语义理解)
    3. plan → code_retriever → plan (代码检索)
    4. plan → entry_selector → plan (切入点选择)
    5. plan → context_assembler → plan (上下文组装)
    6. plan → patch_generator → plan (补丁生成)
    7. plan → impact_analyzer → plan (影响分析)
    8. 如果需要扩散：plan → context_assembler (迭代)
    9. plan → verify → plan (验证)
    10. plan → END (任务完成)

    Returns:
        Compiled workflow graph
    """
    logger.info("创建Issue处理工作流（迭代式上下文构建）")

    # 创建节点实例
    plan_node = PlanAgentNode()
    issue_insight_node = IssueInsightAgentNode()
    code_retriever_node = CodeRetrieverAgentNode()
    entry_selector_node = EntrySelectorAgentNode()
    context_assembler_node = ContextAssemblerAgentNode()
    patch_generator_node = PatchGeneratorAgentNode()
    impact_analyzer_node = ImpactAnalyzerAgentNode()
    verify_node = VerifyAgentNode()
    refine_node = RefineAgentNode()
    reviewer_node = ReviewerAgentNode()
    mr_submitter_node = MRSubmitterAgentNode()

    # 创建状态图
    graph = StateGraph(IssueProcessState)

    # 添加节点
    graph.add_node(NodeName.PLAN.value, plan_node)
    graph.add_node(NodeName.ISSUE_INSIGHT.value, issue_insight_node)
    graph.add_node(NodeName.CODE_RETRIEVER.value, code_retriever_node)
    graph.add_node(NodeName.ENTRY_SELECTOR.value, entry_selector_node)
    graph.add_node(NodeName.CONTEXT_ASSEMBLER.value, context_assembler_node)
    graph.add_node(NodeName.PATCH_GENERATOR.value, patch_generator_node)
    graph.add_node(NodeName.IMPACT_ANALYZER.value, impact_analyzer_node)
    graph.add_node(NodeName.VERIFY.value, verify_node)
    graph.add_node(NodeName.REFINE.value, refine_node)
    graph.add_node(NodeName.REVIEWER.value, reviewer_node)
    graph.add_node(NodeName.MR_SUBMITTER.value, mr_submitter_node)

    # 设置入口点：START → plan
    graph.set_entry_point(NodeName.PLAN.value)

    logger.info("工作流创建完成")

    return graph.compile()

