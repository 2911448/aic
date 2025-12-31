"""
沙箱环境自定义异常定义
"""


class SandboxError(Exception):
    """沙箱基础异常"""

    def __init__(self, message: str, sandbox_id: str | None = None):
        self.message = message
        self.sandbox_id = sandbox_id
        super().__init__(self.message)


class SandboxNotFoundError(SandboxError):
    """沙箱不存在异常"""

    def __init__(self, sandbox_id: str):
        super().__init__(f"沙箱 {sandbox_id} 不存在", sandbox_id)


class SandboxTimeoutError(SandboxError):
    """沙箱超时异常"""

    def __init__(self, sandbox_id: str, timeout: int):
        self.timeout = timeout
        super().__init__(f"沙箱 {sandbox_id} 操作超时（{timeout}秒）", sandbox_id)


class SandboxCreationError(SandboxError):
    """沙箱创建失败异常"""

    def __init__(self, message: str, sandbox_id: str | None = None):
        super().__init__(f"沙箱创建失败: {message}", sandbox_id)


class SandboxExecutionError(SandboxError):
    """沙箱命令执行异常"""

    def __init__(
        self,
        message: str,
        sandbox_id: str | None = None,
        exit_code: int | None = None,
        stderr: str | None = None,
    ):
        self.exit_code = exit_code
        self.stderr = stderr
        super().__init__(message, sandbox_id)


class DockerError(SandboxError):
    """Docker 相关异常"""

    def __init__(self, message: str, original_error: Exception | None = None):
        self.original_error = original_error
        super().__init__(f"Docker 错误: {message}")


class DockerConnectionError(DockerError):
    """Docker 连接异常"""

    def __init__(self, message: str = "无法连接到 Docker daemon"):
        super().__init__(message)


class GitError(SandboxError):
    """Git 操作基础异常"""

    def __init__(
        self,
        message: str,
        sandbox_id: str | None = None,
        command: str | None = None,
    ):
        self.command = command
        super().__init__(f"Git 错误: {message}", sandbox_id)


class GitAuthError(GitError):
    """Git 认证异常"""

    def __init__(self, message: str = "Git 认证失败", sandbox_id: str | None = None):
        super().__init__(message, sandbox_id)


class GitCloneError(GitError):
    """Git Clone 异常"""

    def __init__(
        self,
        repo_url: str,
        message: str = "克隆失败",
        sandbox_id: str | None = None,
    ):
        self.repo_url = repo_url
        super().__init__(f"克隆仓库 {repo_url} 失败: {message}", sandbox_id, f"git clone {repo_url}")


class GitPushError(GitError):
    """Git Push 异常"""

    def __init__(
        self,
        branch: str,
        message: str = "推送失败",
        sandbox_id: str | None = None,
    ):
        self.branch = branch
        super().__init__(f"推送分支 {branch} 失败: {message}", sandbox_id, f"git push origin {branch}")


class GitBranchError(GitError):
    """Git 分支操作异常"""

    def __init__(
        self,
        branch: str,
        message: str = "分支操作失败",
        sandbox_id: str | None = None,
    ):
        self.branch = branch
        super().__init__(f"分支 {branch} 操作失败: {message}", sandbox_id)


class GitCommitError(GitError):
    """Git Commit 异常"""

    def __init__(self, message: str = "提交失败", sandbox_id: str | None = None):
        super().__init__(message, sandbox_id, "git commit")


class FileOperationError(SandboxError):
    """文件操作异常"""

    def __init__(
        self,
        message: str,
        path: str | None = None,
        sandbox_id: str | None = None,
    ):
        self.path = path
        super().__init__(f"文件操作错误: {message}", sandbox_id)


class FileNotFoundInSandboxError(FileOperationError):
    """沙箱内文件不存在异常"""

    def __init__(self, path: str, sandbox_id: str | None = None):
        super().__init__(f"文件 {path} 不存在", path, sandbox_id)


class FilePermissionError(FileOperationError):
    """文件权限异常"""

    def __init__(self, path: str, sandbox_id: str | None = None):
        super().__init__(f"没有权限访问文件 {path}", path, sandbox_id)


class PatchApplyError(FileOperationError):
    """Patch 应用异常"""

    def __init__(
        self,
        message: str = "Patch 应用失败",
        failed_files: list[str] | None = None,
        sandbox_id: str | None = None,
    ):
        self.failed_files = failed_files or []
        super().__init__(message, None, sandbox_id)

