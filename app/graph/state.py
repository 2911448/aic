"""
LangGraph State definitions for Issue processing workflow
"""

from collections.abc import Sequence
from enum import Enum
from typing import Annotated, Any, Optional, TypedDict

from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field


def reduce_messages(
    left: list[BaseMessage] | Sequence[BaseMessage],
    right: list[BaseMessage] | Sequence[BaseMessage],
) -> list[BaseMessage]:
    """Merge message lists for Annotated reduce operation"""
    return list(left) + list(right)


class NodeName(str, Enum):
    """工作流节点名称枚举"""

    PLAN = "plan"
    ISSUE_INSIGHT = "issue_insight"
    CODE_RETRIEVER = "code_retriever"
    # 新节点（替换原 CODE_SCOPE 和 PATCH_SMITH）
    ENTRY_SELECTOR = "entry_selector"
    CONTEXT_ASSEMBLER = "context_assembler"
    PATCH_GENERATOR = "patch_generator"
    IMPACT_ANALYZER = "impact_analyzer"
    # 验证与评审节点
    VERIFY = "verify"
    REFINE = "refine"
    REVIEWER = "reviewer"
    # MR 提交节点
    MR_SUBMITTER = "mr_submitter"
    END = "__end__"


class ProcessStage(str, Enum):
    """处理阶段枚举"""

    ISSUE_ANALYSIS = "issue_analysis"
    CODE_SEARCH = "code_search"
    ENTRY_SELECTION = "entry_selection"
    CONTEXT_ASSEMBLY = "context_assembly"
    PATCH_GENERATION = "patch_generation"
    IMPACT_ANALYSIS = "impact_analysis"
    VERIFICATION = "verification"
    DIAGNOSIS = "diagnosis"
    REVIEW = "review"
    THINK_CHAIN = "think_chain"


class IssueProcessState(TypedDict, total=False):
    """
    State for Issue processing workflow with Command-based routing

    This state is passed between nodes and updated as the workflow progresses.
    Each node reads the current state and returns an updated state.
    """

    # Message History
    messages: Annotated[Sequence[BaseMessage], reduce_messages]  # Conversation messages

    # Original Input
    issue_data: dict  # GitLab Issue raw data from webhook
    project_info: dict  # Project information

    # Issue Insight Results
    issue_type: Optional[str]  # bug or feature
    branch_name_suggestion: Optional[str]  # LLM生成的分支名建议
    search_queries: list[str]  # Generated RAG search queries

    # Code Retrieval Results
    retrieved_code: list[dict]  # RAG retrieved code snippets

    # Entry Selection Results
    current_target: Optional[dict]  # 当前聚焦的目标符号 (TargetContext)
    target_queue: list[dict]  # 待处理的符号队列 (list[TargetContext])

    # Context Assembly Results
    editable_context: Optional[dict]  # 可编辑上下文切片 (EditableContextSlice)

    # Patch Generation Results
    generated_patches: dict[str, str]  # 已生成的补丁 {file_path: patch_content}
    current_patch: Optional[str]  # 当前生成的补丁 (unified diff)
    current_modified_code: Optional[str]  # 当前补丁对应的修改后的完整代码

    # Impact Analysis Results
    impact_report: Optional[dict]  # 影响分析报告 (ImpactReport)

    # Expansion Control
    max_expansion_depth: int  # 最大扩散深度，默认3
    current_expansion_depth: int  # 当前扩散深度

    # Verification Results
    verification_result: Optional[dict]  # Verification result (syntax, linter, semantic)
    
    # Diagnosis & Retry Control
    patch_retry_count: int  # 补丁重试次数
    retry_history: list[dict]  # 重试历史记录
    diagnosis_result: Optional[dict]  # 失败诊断结果 (DiagnosisResult)
    
    # Review Results
    review_report: Optional[str]  # Markdown 格式的评审报告

    # MR Submission Results
    mr_url: Optional[str]  # Merge Request URL
    mr_iid: Optional[int]  # Merge Request IID
    branch_name: Optional[str]  # 创建的分支名称

    # Execution Metadata
    executed_nodes: list[str]  # History of executed nodes for tracking
    current_step: str  # Current step description
    error: Optional[str]  # Error message if any
    completed: bool  # Whether the workflow is completed

    # Sandbox Inf
    sandbox_id: Optional[str]  # Sandbox ID
    repo_path: Optional[str]  # Git repository path in sandbox (e.g., "project-name")
