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

    # Planner Orchestrator（LLM 调度中心）
    PLANNER_ORCHESTRATOR = "planner_orchestrator"
    
    # TaskRunner（系统派发执行节点）
    TASK_RUNNER = "task_runner"
    
    # 可被调度的 Agent 节点
    OMNI_EXPLORER = "omni_explorer"
    CODE_AGENT = "code_agent"
    MR_PUBLISHER = "mr_publisher"

    # Merge workflow 节点
    MERGE_DIFF_COLLECTOR = "merge_diff_collector"
    VECTOR_INDEX_UPDATE = "vector_index_update"

    # Verification 节点
    VERIFICATION_FLOW = "verification_flow"

    # 特殊节点
    END = "__end__"


class ProcessStage(str, Enum):
    """处理阶段枚举（用于事件上报）"""

    # 生命周期阶段
    SANDBOX_BOOTSTRAP = "sandbox_bootstrap"
    SANDBOX_TEARDOWN = "sandbox_teardown"

    # Plan Agent 阶段
    PLANNING = "planning"
    CODE_GENERATION = "code_generation"

    # 代码检索阶段
    CODE_SEARCH = "code_search"

    # 验证阶段
    VERIFICATION = "verification"

    # MR 提交阶段
    MR_SUBMISSION = "mr_submission"

    # 通用阶段
    THINK_CHAIN = "think_chain"

