"""
LangGraph State definitions for Issue processing workflow

State 说明：
- 从平铺字段改为分域结构，提升可维护性和清晰度
- 每个域（sandbox、analysis、retrieval等）独立管理相关字段
- 使用 TypedDict 嵌套结构，保持与 LangGraph 兼容性
"""

from collections.abc import Sequence
from typing import Annotated, Any, Optional, TypedDict

from langchain_core.messages import BaseMessage


def reduce_messages(
    left: list[BaseMessage] | Sequence[BaseMessage],
    right: list[BaseMessage] | Sequence[BaseMessage],
) -> list[BaseMessage]:
    """Merge message lists for Annotated reduce operation"""
    return list(left) + list(right)


# ============================================================================
# Domain-Specific State Groups
# ============================================================================


class SandboxInfo(TypedDict, total=False):
    """Sandbox 生命周期信息（SandboxBootstrap 产出）"""

    sandbox_id: Optional[str]  
    repo_path: Optional[str]  # Git repository path in sandbox
    default_branch: Optional[str]  
    ignore_patterns: list[str]  # 从 .gitignore 读取的忽略规则


class AnalysisInfo(TypedDict, total=False):
    """Issue 分析结果（PlannerAgent 产出 - Issue 理解部分 + OmniExplorer 产出）"""

    search_queries: list[str]  # RAG 搜索查询列表（用于代码检索）
    
    # OmniExplorer 产出
    semantic_hits: list[dict]  # 语义检索结果
    anchor_symbols: list[dict]  # 锚定符号列表
    ripple_graph: dict  # 调用涟漪图
    signature_contracts: dict[str, str]  # 函数签名契约  


class RetrievalInfo(TypedDict, total=False):
    """代码检索结果（CodeRetriever 产出）"""

    retrieved_code: list[dict]  
    retrieval_meta: Optional[dict]  


class TargetingInfo(TypedDict, total=False):
    """切入点选择（EntrySelector）"""

    current_target: Optional[dict]  # 当前聚焦的目标符号 (TargetContext)


class PatchingInfo(TypedDict, total=False):
    """补丁生成与管理（CodeAgent 产出）- 结构化版本"""

    patches: list[dict]  # 补丁产物列表（PatchArtifact 的 dict 表示）
    applied_history: list[dict]  # 补丁应用历史（记录每轮apply结果/失败原因）


class VerificationInfo(TypedDict, total=False):
    """验证结果（VerificationFlow 产出）"""

    final_verification: Optional[dict]  # 最终验证结果：mypy + ruff 全量检查（包含 passed, error_count, warnings, errors 等）


class DeliveryInfo(TypedDict, total=False):
    """MR 提交结果（MRPublisher 产出）- 包含评审报告"""

    mr_url: Optional[str]  # Merge Request URL
    mr_iid: Optional[int]  # Merge Request IID
    branch_name: Optional[str]  # 创建的分支名称
    review_artifact: Optional[dict]  # 结构化评审产物（ReviewArtifact 的 dict 表示）


class PlanningInfo(TypedDict, total=False):
    """计划与调度信息（Planner 决策 → TaskRunner 执行）"""

    retry_count: int  # 重试计数器（用于熔断验证/修复循环）
    orchestration_history: list[dict]  # 编排历史记录（包含 Planner 决策 + TaskRunner 执行结果）
    next_tasks: list[dict]  # Planner 决策的待执行任务列表（由 TaskRunner 消费）
    last_decision: Optional[dict]  # 上一次 Planner 决策（用于可观测性）
    idle_count: int  # 无进展计数（用于空转熔断）
    round: int  # 当前执行轮次（TaskRunner 每次执行后 +1）
    last_round_summary: str  # 最近一轮执行的汇总摘要（单agent时为该agent的reasoning；多agent时为多行汇总，每行≤300字）


class RuntimeInfo(TypedDict, total=False):
    """执行元数据与流程控制"""

    executed_nodes: list[str]  # History of executed nodes for tracking
    current_step: str  # Current step description
    error: Optional[str]  # Error message if any
    completed: bool  # Whether the workflow is completed
    trace_id: Optional[str]  # 可选：分布式追踪 ID


class MergeInfo(TypedDict, total=False):
    """Merge Request 处理信息（MergeDiffCollector / IndexUpdate 产出）"""

    mr_iid: Optional[int]  # Merge Request IID (项目内 ID)
    mr_id: Optional[int]  # Merge Request global ID
    target_branch: Optional[str]  # 目标分支
    source_branch: Optional[str]  # 源分支
    merge_commit_sha: Optional[str]  # 合并后的 commit SHA
    changed_files: list[dict]  # 变更文件列表 [{"status": "added/modified/deleted/renamed", "path": str, "old_path": Optional[str]}]
    indexed_files: list[str]  # 已成功索引的文件路径列表
    failed_files: list[dict]  # 索引失败的文件 [{"path": str, "error": str}]


# ============================================================================
# Main IssueProcessState (Top-Level State)
# ============================================================================


class IssueProcessState(TypedDict, total=False):
    """
    Issue Processing Workflow State (Domain-Grouped Structure)

    分域结构说明：
    - sandbox: SandboxBootstrap 填充，其他节点只读
    - analysis: PlannerAgent 产出 - Issue 理解
    - planning: PlannerAgent + Scheduler 产出 - 任务编排（execution_plan, task_status, retry_count）
    - retrieval: CodeRetriever 产出（检索到的代码片段，基于 analysis.search_queries）
    - targeting: EntrySelector 产出（选定的目标符号）
    - patching: CodeAgent 产出（结构化补丁产物列表 - PatchArtifact[]）
    - verification: VerificationFlow 产出（mypy/ruff 验证结果）
    - delivery: MRPublisher 产出（MR URL, branch_name, review_artifact）
    - merge: Merge Request 处理信息（用于 merge workflow）
    - runtime: 流程控制元数据（executed_nodes, error, trace_id）
    - execution_history: 执行轨迹记录（字符串列表，格式：[Round N] Agent: xxx | Task: "..." | Result: xxx）
    """

    # Message History (for LLM conversation)
    messages: Annotated[Sequence[BaseMessage], reduce_messages]

    # Original Input
    issue_data: dict  # GitLab Issue raw data from webhook
    project_info: dict  # Project information

    # Domain-Specific Groups
    sandbox: SandboxInfo  # Sandbox 生命周期信息
    planning: PlanningInfo  # Planner 与 Scheduler 信息
    analysis: AnalysisInfo  # Issue 分析结果
    retrieval: RetrievalInfo  # 代码检索结果
    targeting: TargetingInfo  # 切入点选择
    patching: PatchingInfo  # 补丁生成与管理（结构化）
    verification: VerificationInfo  # 验证结果
    delivery: DeliveryInfo  # MR 提交结果（含评审报告）
    merge: MergeInfo  # Merge Request 处理信息
    runtime: RuntimeInfo  # 执行元数据与流程控制
    execution_history: list[str]  # 执行轨迹记录（每轮 agent 执行的摘要）


# ============================================================================
# Helper Functions for State Access
# ============================================================================


def init_state_defaults(state: IssueProcessState) -> IssueProcessState:
    """
    初始化 state 默认值（避免 KeyError）

    使用示例：在 workflow 入口调用
    """
    if "sandbox" not in state:
        state["sandbox"] = {}
    if "planning" not in state:
        state["planning"] = {
            "retry_count": 0,
            "orchestration_history": [],
            "next_tasks": [],
            "last_decision": None,
            "idle_count": 0,
            "round": 0,
            "last_round_summary": "",
        }
    if "analysis" not in state:
        state["analysis"] = {
            "search_queries": [],
            "semantic_hits": [],
            "anchor_symbols": [],
            "ripple_graph": {"center": None, "nodes": [], "edges": []},
            "signature_contracts": {},
        }
    if "retrieval" not in state:
        state["retrieval"] = {"retrieved_code": []}
    if "targeting" not in state:
        state["targeting"] = {}
    if "patching" not in state:
        state["patching"] = {
            "patches": [],
            "applied_history": [],
        }
    if "verification" not in state:
        state["verification"] = {}
    if "delivery" not in state:
        state["delivery"] = {
            "review_artifact": None,
        }
    if "merge" not in state:
        state["merge"] = {
            "changed_files": [],
            "indexed_files": [],
            "failed_files": [],
        }
    if "runtime" not in state:
        state["runtime"] = {
            "executed_nodes": [],
            "current_step": "",
            "error": None,
            "completed": False,
        }
    if "execution_history" not in state:
        state["execution_history"] = []

    return state

