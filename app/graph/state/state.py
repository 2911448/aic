"""
LangGraph State definitions for Issue processing workflow (Refactored with Domain Grouping)

State 重构说明：
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
    """Sandbox 生命周期信息（由 SandboxBootstrap 填充）"""

    sandbox_id: Optional[str]  # Sandbox ID
    repo_path: Optional[str]  # Git repository path in sandbox
    default_branch: Optional[str]  # Default branch name (e.g., main, master)
    ignore_patterns: list[str]  # 从 .gitignore 读取的忽略规则


class AnalysisInfo(TypedDict, total=False):
    """Issue 分析结果（IssueAnalyst 产出）"""

    issue_type: Optional[str]  # bug or feature
    branch_name_suggestion: Optional[str]  # LLM生成的分支名建议
    search_queries: list[str]  # Generated RAG search queries


class RetrievalInfo(TypedDict, total=False):
    """代码检索结果（CodeRetriever 产出）"""

    retrieved_code: list[dict]  # RAG retrieved code snippets
    retrieval_meta: Optional[dict]  # 检索元信息：score、rerank等


class TargetingInfo(TypedDict, total=False):
    """切入点选择（EntrySelector）"""

    current_target: Optional[dict]  # 当前聚焦的目标符号 (TargetContext)


class ContextInfo(TypedDict, total=False):
    """可编辑上下文切片（ContextSliceBuilder 产出）"""

    editable_context: Optional[dict]  # EditableContextSlice：可编辑代码 + 依赖签名
    batch_contexts: list[dict]  # 批量上下文（BatchContextBuilder 产出）


class PatchingInfo(TypedDict, total=False):
    """补丁生成与候选管理（PatchFlow 产出）"""

    patch_candidates: list[dict]  # 候选补丁列表
    selected_patch: Optional[dict]  # 选中的补丁（PatchJudge 产出）
    generated_patches: dict[str, str]  # 已生成的补丁 {file_path: patch_content}
    current_patch: Optional[str]  # 当前生成的补丁 (unified diff)
    current_modified_code: Optional[str]  # 当前补丁对应的修改后的完整代码
    patch_retry_count: int  # 补丁重试次数
    retry_history: list[dict]  # 重试历史记录
    applied_history: list[dict]  # 补丁应用历史（记录每轮apply结果/失败原因）


class VerificationInfo(TypedDict, total=False):
    """验证结果（VerificationFlow 产出）"""

    verification_results_by_candidate: dict[str, dict]  # 各候选补丁的验证结果
    final_verification: Optional[dict]  # 最终验证结果：mypy + ruff 全量检查
    light_results: list[dict]  # 轻量验证结果（每轮批次自检结果）
    
    # RefineAgent 修复循环相关字段
    refine_retry_count: int  # RefineAgent 修复循环次数
    refine_history: list[dict]  # RefineAgent 修复历史记录


class ImpactInfo(TypedDict, total=False):
    """影响分析（ImpactAnalyzer 产出）"""

    impact_report: Optional[dict]  # ImpactReport：影响范围、建议扩散目标


class RippleInfo(TypedDict, total=False):
    """涟漪递归队列管理（Ripple Loop 产出）"""

    pending_file_tasks: list[dict]  # 待处理的文件任务队列（每项包含 file_path, reason, symbols, priority 等）
    inflight_batch: list[dict]  # 当前处理的批次（最多5个文件任务）
    seen_files: list[str]  # 已处理/已入队的文件路径（仅用于历史追踪，不再用于过滤）
    iteration: int  # 当前循环轮次
    max_iterations: int  # 最大循环次数（防止无穷涟漪）
    last_applied_files: list[str]  # 上一批次成功应用补丁的文件路径列表（用于增量扫描）
    last_signature_changes: dict[str, list[dict]]  # 上一批次的签名变更指纹 {file_path: [{"symbol_name": str, "symbol_type": str, "change_type": str}]}


class ReviewInfo(TypedDict, total=False):
    """代码评审结果（Reviewer 产出）"""

    review_report: Optional[str]  # Markdown 格式的评审报告


class DeliveryInfo(TypedDict, total=False):
    """MR 提交结果（MRSubmitter 产出）"""

    mr_url: Optional[str]  # Merge Request URL
    mr_iid: Optional[int]  # Merge Request IID
    branch_name: Optional[str]  # 创建的分支名称


class RuntimeInfo(TypedDict, total=False):
    """执行元数据与流程控制"""

    executed_nodes: list[str]  # History of executed nodes for tracking
    current_step: str  # Current step description
    error: Optional[str]  # Error message if any
    completed: bool  # Whether the workflow is completed
    trace_id: Optional[str]  # 可选：分布式追踪 ID


# ============================================================================
# Main IssueProcessState (Top-Level State)
# ============================================================================


class IssueProcessState(TypedDict, total=False):
    """
    Issue Processing Workflow State (Domain-Grouped Structure)

    分域结构说明：
    - sandbox: SandboxBootstrap 填充，其他节点只读
    - analysis: IssueAnalyst 产出
    - retrieval: CodeRetriever 产出
    - targeting: EntrySelector & ExpansionController 管理
    - context: ContextSliceBuilder 产出
    - patching: PatchFlow (PatchWriter + PatchJudge) 产出
    - verification: VerificationFlow 产出
    - impact: ImpactAnalyzer 产出
    - ripple: Ripple Loop 队列管理（全局扫描、批量处理、增量涟漪）
    - review: Reviewer 产出
    - delivery: MRSubmitter 产出
    - runtime: 流程控制元数据
    """

    # Message History (for LLM conversation)
    messages: Annotated[Sequence[BaseMessage], reduce_messages]

    # Original Input
    issue_data: dict  # GitLab Issue raw data from webhook
    project_info: dict  # Project information

    # Domain-Specific Groups
    sandbox: SandboxInfo  # Sandbox 生命周期信息
    analysis: AnalysisInfo  # Issue 分析结果
    retrieval: RetrievalInfo  # 代码检索结果
    targeting: TargetingInfo  # 切入点与扩散控制
    context: ContextInfo  # 可编辑上下文切片
    patching: PatchingInfo  # 补丁生成与管理
    verification: VerificationInfo  # 验证结果
    impact: ImpactInfo  # 影响分析
    ripple: RippleInfo  # 涟漪递归队列管理
    review: ReviewInfo  # 代码评审结果
    delivery: DeliveryInfo  # MR 提交结果
    runtime: RuntimeInfo  # 执行元数据与流程控制


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
    if "analysis" not in state:
        state["analysis"] = {"search_queries": []}
    if "retrieval" not in state:
        state["retrieval"] = {"retrieved_code": []}
    if "targeting" not in state:
        state["targeting"] = {}
    if "context" not in state:
        state["context"] = {}
    if "patching" not in state:
        state["patching"] = {
            "patch_candidates": [],
            "generated_patches": {},
            "patch_retry_count": 0,
            "retry_history": [],
            "applied_history": [],
        }
    if "verification" not in state:
        state["verification"] = {
            "verification_results_by_candidate": {},
            "light_results": [],
            "refine_retry_count": 0,
            "refine_history": [],
        }
    if "impact" not in state:
        state["impact"] = {}
    if "ripple" not in state:
        state["ripple"] = {
            "pending_file_tasks": [],
            "inflight_batch": [],
            "seen_files": [],
            "iteration": 0,
            "max_iterations": 10,
            "last_applied_files": [],
            "last_signature_changes": {},
        }
    if "review" not in state:
        state["review"] = {}
    if "delivery" not in state:
        state["delivery"] = {}
    if "runtime" not in state:
        state["runtime"] = {
            "executed_nodes": [],
            "current_step": "",
            "error": None,
            "completed": False,
        }

    return state

