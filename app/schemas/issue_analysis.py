"""
数据模型
"""

from typing import Literal

from pydantic import BaseModel, Field


class SearchQuery(BaseModel):
    """RAG搜索查询"""

    query: str = Field(..., description="搜索查询文本")
    context: str = Field(..., description="搜索查询目标")
    weight: float = Field(
        default=1.0, ge=0.0, le=1.0, description="优先级权重 (0.0-1.0)"
    )


class IssueAnalysis(BaseModel):
    """Issue分析结果 - 只包含核心信息"""

    issue_type: Literal["bug", "feature"] = Field(..., description="Issue类型")
    search_queries: list[SearchQuery] = Field(
        ..., min_length=3, max_length=7, description="RAG搜索查询"
    )


class PlanDecision(BaseModel):
    """Plan决策"""

    next_node: Literal[
        "issue_insight", "code_retriever", "code_scope", "patch_smith", "verify", "END"
    ] = Field(..., description="下一步执行节点")
    reason: str = Field(..., description="路由决策原因")
