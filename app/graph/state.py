"""
LangGraph State definitions for Issue processing workflow
"""

from collections.abc import Sequence
from enum import Enum
from typing import Annotated, Optional, TypedDict

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
    CODE_SCOPE = "code_scope"
    PATCH_SMITH = "patch_smith"
    VERIFY = "verify"
    END = "__end__"


class ProcessStage(str, Enum):
    """处理阶段枚举"""

    ISSUE_ANALYSIS = "issue_analysis"
    CODE_SEARCH = "code_search"
    CODE_LOCATION = "code_location"
    PATCH_GENERATION = "patch_generation"
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

    # Code Scope Results
    code_scope: Optional[dict]  # Located code regions from AST/CFG analysis

    # Patch Generation Results
    patch: Optional[str]  # Generated patch/fix
    patch_files: list[dict]  # List of files to be patched

    # Verification Results
    verification_result: Optional[dict]  # Sandbox verification result

    # Execution Metadata
    executed_nodes: list[str]  # History of executed nodes for tracking
    current_step: str  # Current step description
    error: Optional[str]  # Error message if any
    completed: bool  # Whether the workflow is completed

    # Session Info
    session_id: Optional[str]  # Session identifier
    timestamp: Optional[str]  # Timestamp
