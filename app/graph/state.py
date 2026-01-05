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
    # 保留验证节点
    VERIFY = "verify"
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
    search_queries: list[str]  # Generated RAG search queries

    # Code Retrieval Results
    retrieved_code: list[dict]  # RAG retrieved code snippets

    # Entry Selection Results (新增)
    current_target: Optional[dict]  # 当前聚焦的目标符号 (TargetContext)
    target_queue: list[dict]  # 待处理的符号队列 (list[TargetContext])

    # Context Assembly Results (新增)
    editable_context: Optional[dict]  # 可编辑上下文切片 (EditableContextSlice)

    # Patch Generation Results (更新)
    generated_patches: dict[str, str]  # 已生成的补丁 {file_path: patch_content}
    current_patch: Optional[str]  # 当前生成的补丁

    # Impact Analysis Results (新增)
    impact_report: Optional[dict]  # 影响分析报告 (ImpactReport)

    # Expansion Control (新增)
    max_expansion_depth: int  # 最大扩散深度，默认3
    current_expansion_depth: int  # 当前扩散深度

    # Verification Results
    verification_result: Optional[dict]  # Sandbox verification result

    # Execution Metadata
    executed_nodes: list[str]  # History of executed nodes for tracking
    current_step: str  # Current step description
    error: Optional[str]  # Error message if any
    completed: bool  # Whether the workflow is completed

    # Session Info
    session_id: Optional[str]  # Session identifier
    sandbox_id: Optional[str]  # Sandbox identifier for file operations
    repo_path: Optional[str]  # Git repository path in sandbox (e.g., "project-name")
    timestamp: Optional[str]  # Timestamp
