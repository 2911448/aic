"""
GitLab Webhook 相关的数据模型
"""

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


def parse_gitlab_datetime(value: Any) -> datetime:
    """
    解析 GitLab 的日期时间格式
    GitLab 可能发送多种格式：
    - ISO 8601: 2025-12-31T03:49:03Z
    - GitLab 格式: 2025-12-31 03:49:03 UTC
    """
    if isinstance(value, datetime):
        return value

    if isinstance(value, str):
        # 尝试解析 GitLab 格式: "2025-12-31 03:49:03 UTC"
        if value.endswith(" UTC"):
            value = value.replace(" UTC", "+00:00").replace(" ", "T")
        # 尝试解析 ISO 8601 格式
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass

    raise ValueError(f"无法解析日期时间: {value}")


class GitLabUser(BaseModel):
    """GitLab 用户信息"""

    id: int
    name: str
    username: str
    avatar_url: Optional[str] = None
    email: Optional[str] = None


class GitLabProject(BaseModel):
    """GitLab 项目信息"""

    id: int
    name: str
    description: Optional[str] = None
    web_url: str
    avatar_url: Optional[str] = None
    git_ssh_url: str
    git_http_url: str
    namespace: str
    visibility_level: int
    path_with_namespace: str
    default_branch: str
    homepage: str
    url: str
    ssh_url: str
    http_url: str


class GitLabLabel(BaseModel):
    """GitLab 标签信息"""

    id: int
    title: str
    color: str
    project_id: int
    created_at: datetime
    updated_at: datetime
    template: bool = False
    description: Optional[str] = None
    type: str = "ProjectLabel"
    group_id: Optional[int] = None

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def parse_datetime(cls, v: Any) -> datetime:
        """解析日期时间字段"""
        return parse_gitlab_datetime(v)


class GitLabIssue(BaseModel):
    """GitLab Issue 信息"""

    id: int
    title: str
    assignee_ids: list[int] = []
    assignee_id: Optional[int] = None
    author_id: int
    project_id: int
    created_at: datetime
    updated_at: datetime
    position: int = 0
    branch_name: Optional[str] = None
    description: Optional[str] = None
    milestone_id: Optional[int] = None
    state: str  # opened, closed, etc.
    iid: int  # Issue 的项目内 ID
    labels: list[GitLabLabel] = []
    due_date: Optional[str] = None
    url: str

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def parse_datetime(cls, v: Any) -> datetime:
        """解析日期时间字段"""
        return parse_gitlab_datetime(v)


class GitLabIssueAttributes(BaseModel):
    """GitLab Issue 属性变化"""

    title: Optional[str] = None
    description: Optional[str] = None
    state: Optional[str] = None
    updated_at: Optional[datetime] = None
    labels: Optional[list[GitLabLabel]] = None

    @field_validator("updated_at", mode="before")
    @classmethod
    def parse_datetime(cls, v: Any) -> Optional[datetime]:
        """解析日期时间字段"""
        if v is None:
            return None
        return parse_gitlab_datetime(v)


class GitLabWebhookPayload(BaseModel):
    """GitLab Webhook 完整载荷"""

    object_kind: str  # issue, merge_request, note, etc.
    event_type: Optional[str] = None  # issue, note, etc.
    user: GitLabUser
    project: GitLabProject
    object_attributes: dict[str, Any]  # 使用 dict，因为不同事件类型结构不同
    assignees: Optional[list[GitLabUser]] = None
    labels: Optional[list[GitLabLabel]] = None
    changes: Optional[dict[str, Any]] = None
    repository: Optional[dict[str, Any]] = None
    issue: Optional[dict[str, Any]] = None  # Note Hook 中会包含关联的 Issue 信息


class WebhookResponse(BaseModel):
    """Webhook 响应"""

    status: Literal["success", "error", "pending"] = Field(..., description="处理状态")
    message: str = Field(..., description="响应消息")
    event_type: Optional[str] = Field(None, description="事件类型")
    issue_iid: Optional[int] = Field(None, description="Issue IID")
    issue_title: Optional[str] = Field(None, description="Issue 标题")
    project_path: Optional[str] = Field(None, description="项目路径")
