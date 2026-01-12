"""
Workflows module - LangGraph 工作流

包含：
- issue_workflow: Issue 处理工作流（Multi-Agent 架构）
"""

from app.graph.workflows.issue_workflow import (
    create_issue_workflow
)

__all__ = [
    "create_issue_workflow"
]
