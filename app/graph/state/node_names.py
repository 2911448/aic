"""
节点名称与事件名称统一枚举
禁止在代码中硬编码字符串
"""

from enum import Enum


class NodeName(str, Enum):
    """工作流节点名称枚举"""

    # 生命周期节点
    SANDBOX_BOOTSTRAP = "sandbox_bootstrap"
    SANDBOX_TEARDOWN = "sandbox_teardown"

    # 主路由
    MAIN_ROUTER = "main_router"

    # 分析与检索节点
    ISSUE_ANALYST = "issue_analyst"
    CODE_RETRIEVER = "code_retriever"
    ENTRY_SELECTOR = "entry_selector"
    CONTEXT_SLICE_BUILDER = "context_slice_builder"

    # 补丁相关节点
    PATCH_WRITER = "patch_writer"
    PATCH_JUDGE = "patch_judge"

    # Ripple 涟漪递归节点
    GLOBAL_IMPACT_SCAN = "global_impact_scan"
    QUEUE_MANAGER = "queue_manager"
    BATCH_CONTEXT_BUILDER = "batch_context_builder"
    REFACTORING_AGENT_BATCH = "refactoring_agent_batch"
    INCREMENTAL_IMPACT_SCAN = "incremental_impact_scan"

    # 验证相关节点
    VERIFIER = "verifier"
    REFINE_AGENT = "refine_agent"

    # 评审与提交
    REVIEWER = "reviewer"
    MR_SUBMITTER = "mr_submitter"

    # 子图名称
    PATCH_FLOW = "patch_flow"
    VERIFICATION_FLOW = "verification_flow"

    # 特殊节点
    END = "__end__"


class ProcessStage(str, Enum):
    """处理阶段枚举（用于事件上报）"""

    # 生命周期阶段
    SANDBOX_BOOTSTRAP = "sandbox_bootstrap"
    SANDBOX_TEARDOWN = "sandbox_teardown"

    # 分析与检索阶段
    ISSUE_ANALYSIS = "issue_analysis"
    CODE_SEARCH = "code_search"
    ENTRY_SELECTION = "entry_selection"
    CONTEXT_BUILDING = "context_building" 

    # 补丁生成阶段
    PATCH_GENERATION = "patch_generation"

    # 影响分析阶段
    IMPACT_ANALYSIS = "impact_analysis"

    # 验证阶段
    VERIFICATION = "verification"
    DIAGNOSIS = "diagnosis"

    # 评审与提交阶段
    REVIEW = "review"
    MR_SUBMISSION = "mr_submission"

    # 通用阶段
    THINK_CHAIN = "think_chain"
    ROUTING = "routing"

