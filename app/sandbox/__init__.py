"""
沙箱环境模块 - 提供基于 Docker 的代码隔离执行环境

支持功能:
- Docker 容器生命周期管理
- Git 操作（clone、branch、commit、push 等）
- 文件操作（读写、patch 应用）
- SSH Key 和 HTTP Token 双认证方式
"""

from app.sandbox.models import (
    Sandbox,
    SandboxConfig,
    SandboxStatus,
    GitAuthConfig,
    GitAuthType,
    CommandResult,
    CloneResult,
    GitStatus,
    FileInfo,
    PatchResult,
)
from app.sandbox.exceptions import (
    SandboxError,
    SandboxNotFoundError,
    SandboxTimeoutError,
    GitError,
    GitAuthError,
    GitCloneError,
    GitPushError,
    FileOperationError,
)
from app.sandbox.manager import SandboxManager
from app.sandbox.git_service import GitService
from app.sandbox.file_service import FileService

__all__ = [
    # Models
    "Sandbox",
    "SandboxConfig",
    "SandboxStatus",
    "GitAuthConfig",
    "GitAuthType",
    "CommandResult",
    "CloneResult",
    "GitStatus",
    "FileInfo",
    "PatchResult",
    # Exceptions
    "SandboxError",
    "SandboxNotFoundError",
    "SandboxTimeoutError",
    "GitError",
    "GitAuthError",
    "GitCloneError",
    "GitPushError",
    "FileOperationError",
    # Services
    "SandboxManager",
    "GitService",
    "FileService",
]
