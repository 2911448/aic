"""
数据模型
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class SearchQuery(BaseModel):
    """RAG搜索查询"""

    query: str = Field(..., description="搜索查询文本")
    context: str = Field(..., description="搜索查询目标")
    weight: float = Field(
        default=1.0, ge=0.0, le=1.0, description="优先级权重 (0.0-1.0)"
    )


class IssueAnalysis(BaseModel):
    """任务分析结果"""

    valid: bool = Field(..., description="内容是否有效")
    reason: str = Field(..., description="内容有效或无效的原因说明")
    issue_type: Optional[Literal["bug", "feature"]] = Field(None, description="任务类型")
    branch_name_suggestion: Optional[str] = Field(
        None,
        description="建议的分支名（简短、语义化，如 fix/user-login, feat/export-excel）",
        max_length=30
    )
    search_queries: Optional[list[SearchQuery]] = Field(
        None, description="RAG搜索查询"
    )

