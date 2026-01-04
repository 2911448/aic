"""
沙箱环境数据模型定义
"""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class SandboxStatus(str, Enum):
    """沙箱状态枚举"""

    PENDING = "pending"  # 等待创建
    CREATING = "creating"  # 创建中
    RUNNING = "running"  # 运行中
    STOPPED = "stopped"  # 已停止
    FAILED = "failed"  # 创建失败
    DESTROYED = "destroyed"  # 已销毁


class GitAuthType(str, Enum):
    """Git 认证类型"""

    SSH = "ssh"  # SSH Key 认证
    HTTP = "http"  # HTTP Token 认证
    AUTO = "auto"  # 自动检测


class GitAuthConfig(BaseModel):
    """Git 认证配置"""

    auth_type: GitAuthType = GitAuthType.AUTO
    ssh_private_key: str | None = Field(default=None, description="SSH 私钥内容")
    ssh_private_key_path: str | None = Field(
        default=None, description="SSH 私钥文件路径"
    )
    http_token: str | None = Field(
        default=None, description="HTTP Personal Access Token"
    )
    http_username: str | None = Field(default=None, description="HTTP 用户名（可选）")

    def get_effective_auth_type(self, repo_url: str) -> GitAuthType:
        """根据仓库 URL 和配置确定实际使用的认证类型"""
        if self.auth_type != GitAuthType.AUTO:
            return self.auth_type

        # 自动检测：根据 URL 格式判断
        if repo_url.startswith("git@") or repo_url.startswith("ssh://"):
            return GitAuthType.SSH
        return GitAuthType.HTTP


class SandboxConfig(BaseModel):
    """沙箱配置"""

    docker_image: str = Field(
        default="video-sandbox:0.1", description="Docker 基础镜像"
    )
    memory_limit: str = Field(default="512m", description="内存限制")
    cpu_limit: float = Field(default=1.0, description="CPU 核心数限制")
    timeout: int = Field(default=300, description="超时时间（秒）")
    workspace_path: str = Field(default="/workspace", description="容器内工作目录")
    network_mode: str | None = Field(default=None, description="网络模式")
    environment: dict[str, str] = Field(default_factory=dict, description="环境变量")
    git_auth: GitAuthConfig | None = Field(default=None, description="Git 认证配置")
    auto_cleanup: bool = Field(default=True, description="是否自动清理容器")


class Sandbox(BaseModel):
    """沙箱实例"""

    id: str = Field(description="沙箱唯一标识")
    container_id: str | None = Field(default=None, description="Docker 容器 ID")
    status: SandboxStatus = Field(default=SandboxStatus.PENDING, description="沙箱状态")
    config: SandboxConfig = Field(description="沙箱配置")
    workspace_path: str = Field(description="宿主机工作目录路径")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    started_at: datetime | None = Field(default=None, description="启动时间")
    stopped_at: datetime | None = Field(default=None, description="停止时间")
    error_message: str | None = Field(default=None, description="错误信息")

    # Git 仓库信息
    repo_url: str | None = Field(default=None, description="Git 仓库地址")
    current_branch: str | None = Field(default=None, description="当前分支")


class CommandResult(BaseModel):
    """命令执行结果"""

    exit_code: int = Field(description="退出码")
    stdout: str = Field(default="", description="标准输出")
    stderr: str = Field(default="", description="标准错误")
    duration_ms: int = Field(default=0, description="执行耗时（毫秒）")

    @property
    def success(self) -> bool:
        """命令是否执行成功"""
        return self.exit_code == 0


class CloneResult(BaseModel):
    """Git Clone 结果"""

    success: bool = Field(description="是否成功")
    repo_path: str = Field(description="仓库本地路径")
    branch: str = Field(description="克隆的分支")
    commit_hash: str | None = Field(default=None, description="当前 commit hash")
    message: str = Field(default="", description="结果信息")


class GitFileStatus(str, Enum):
    """Git 文件状态"""

    MODIFIED = "modified"
    ADDED = "added"
    DELETED = "deleted"
    RENAMED = "renamed"
    UNTRACKED = "untracked"


class GitFileChange(BaseModel):
    """Git 文件变更信息"""

    path: str = Field(description="文件路径")
    status: GitFileStatus = Field(description="文件状态")
    old_path: str | None = Field(default=None, description="原路径（重命名时）")


class GitStatus(BaseModel):
    """Git 仓库状态"""

    branch: str = Field(description="当前分支")
    commit_hash: str = Field(description="当前 commit hash")
    is_clean: bool = Field(description="工作区是否干净")
    staged_files: list[GitFileChange] = Field(
        default_factory=list, description="暂存区文件"
    )
    unstaged_files: list[GitFileChange] = Field(
        default_factory=list, description="未暂存文件"
    )
    untracked_files: list[str] = Field(default_factory=list, description="未跟踪文件")
    ahead: int = Field(default=0, description="领先远程分支的提交数")
    behind: int = Field(default=0, description="落后远程分支的提交数")


class FileInfo(BaseModel):
    """文件信息"""

    path: str = Field(description="文件路径")
    name: str = Field(description="文件名")
    is_dir: bool = Field(description="是否为目录")
    size: int = Field(default=0, description="文件大小（字节）")
    modified_at: datetime | None = Field(default=None, description="修改时间")


class PatchResult(BaseModel):
    """Patch 应用结果"""

    success: bool = Field(description="是否成功")
    applied_files: list[str] = Field(default_factory=list, description="成功应用的文件")
    failed_files: list[str] = Field(default_factory=list, description="应用失败的文件")
    message: str = Field(default="", description="结果信息")
