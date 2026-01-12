"""
Git 服务

在沙箱环境中执行 Git 操作，支持 SSH Key 和 HTTP Token 认证
"""

from typing import TYPE_CHECKING

from loguru import logger

from app.sandbox.exceptions import (
    GitAuthError,
    GitBranchError,
    GitCloneError,
    GitCommitError,
    GitError,
    GitPushError,
)
from app.sandbox.models import (
    CloneResult,
    CommandResult,
    GitAuthConfig,
    GitAuthType,
    GitFileChange,
    GitFileStatus,
    GitStatus,
)

if TYPE_CHECKING:
    from app.sandbox.manager import SandboxManager


class GitService:
    """
    Git 服务类

    在沙箱环境中执行 Git 操作
    """

    def __init__(self, sandbox_manager: "SandboxManager", sandbox_id: str):
        """
        初始化 Git 服务

        Args:
            sandbox_manager: 沙箱管理器
            sandbox_id: 沙箱 ID
        """
        self._manager = sandbox_manager
        self._sandbox_id = sandbox_id

    async def _execute(
        self,
        command: str,
        timeout: int = 60,
        check: bool = True,
    ) -> CommandResult:
        """
        执行命令的辅助方法

        Args:
            command: 要执行的命令
            timeout: 超时时间
            check: 是否检查执行结果

        Returns:
            命令执行结果

        Raises:
            GitError: 命令执行失败且 check=True
        """
        result = await self._manager.execute_command(
            sandbox_id=self._sandbox_id,
            command=command,
            timeout=timeout,
        )

        if check and not result.success:
            raise GitError(
                f"命令执行失败: {command}\n{result.stderr}",
                self._sandbox_id,
                command,
            )

        return result
    
    async def _get_target_dir(self, repo_path: str = ".") -> str:
        """
        获取目标目录的绝对路径
        
        优先使用沙箱的 repo_working_dir（绝对路径），如果没有设置则使用传入的 repo_path
        
        Args:
            repo_path: 仓库路径（可能是相对路径）
            
        Returns:
            目标目录的路径（优先返回绝对路径）
        """
        sandbox = await self._manager.get_sandbox(self._sandbox_id)
        return sandbox.repo_working_dir or repo_path

    def _prepare_clone_url(
        self,
        repo_url: str,
        git_auth: GitAuthConfig | None,
    ) -> str:
        """
        准备克隆 URL（处理 HTTP Token 认证）

        Args:
            repo_url: 原始仓库 URL
            git_auth: Git 认证配置

        Returns:
            处理后的 URL
        """
        if not git_auth or not git_auth.http_token:
            return repo_url

        auth_type = git_auth.get_effective_auth_type(repo_url)
        if auth_type != GitAuthType.HTTP:
            return repo_url

        # 处理 HTTPS URL，注入 token
        # https://github.com/user/repo.git -> https://token@github.com/user/repo.git
        # https://github.com/user/repo.git -> https://username:token@github.com/user/repo.git
        if repo_url.startswith("https://"):
            if git_auth.http_username:
                auth_part = f"{git_auth.http_username}:{git_auth.http_token}"
            else:
                auth_part = git_auth.http_token
            return repo_url.replace("https://", f"https://{auth_part}@")

        return repo_url

    async def clone(
        self,
        repo_url: str,
        branch: str = "main",
        depth: int | None = None,
        target_dir: str | None = None,
        git_auth: GitAuthConfig | None = None,
    ) -> CloneResult:
        """
        克隆 Git 仓库

        Args:
            repo_url: 仓库地址
            branch: 分支名称
            depth: 克隆深度（可选，用于浅克隆）
            target_dir: 目标目录（可选，默认为仓库名）
            git_auth: Git 认证配置（可选，优先使用沙箱配置）

        Returns:
            克隆结果
        """
        logger.info(f"沙箱 {self._sandbox_id}: 克隆仓库 {repo_url}, 分支 {branch}")

        # 1. 提前确定仓库路径
        if target_dir:
            repo_path = target_dir
        else:
            # 从 URL 提取仓库名
            repo_name = repo_url.rstrip("/").split("/")[-1]
            if repo_name.endswith(".git"):
                repo_name = repo_name[:-4]
            repo_path = repo_name

        # 2. 检查目录是否存在，如果存在则清理
        # 避免 "destination path already exists" 错误
        check_result = await self._execute(
            f'test -d "{repo_path}" && echo "exists"',
            check=False,
        )

        if "exists" in check_result.stdout:
            logger.info(f"沙箱 {self._sandbox_id}: 目录 {repo_path} 已存在，正在清理...")
            await self._execute(f'rm -rf "{repo_path}"', check=False)

        # 获取沙箱的 git_auth 配置
        sandbox = await self._manager.get_sandbox(self._sandbox_id)
        effective_auth = git_auth or sandbox.config.git_auth

        # 准备 URL（处理 HTTP 认证）
        clone_url = self._prepare_clone_url(repo_url, effective_auth)

        # 构建 clone 命令
        cmd_parts = ["git", "clone"]

        if branch:
            cmd_parts.extend(["-b", branch])

        if depth:
            cmd_parts.extend(["--depth", str(depth)])

        cmd_parts.append(clone_url)

        if target_dir:
            cmd_parts.append(target_dir)

        command = " ".join(cmd_parts)

        try:
            result = await self._execute(command, timeout=300, check=False)

            if not result.success:
                # 检查是否是认证错误
                if (
                    "Authentication failed" in result.stderr
                    or "Permission denied" in result.stderr
                ):
                    raise GitAuthError(
                        "Git 认证失败，请检查 SSH Key 或 Token", self._sandbox_id
                    )
                raise GitCloneError(repo_url, result.stderr, self._sandbox_id)

            # 获取当前 commit hash
            commit_result = await self._execute(
                f"cd {repo_path} && git rev-parse HEAD",
                timeout=10,
                check=False,
            )
            commit_hash = (
                commit_result.stdout.strip() if commit_result.success else None
            )

            # 更新沙箱的仓库信息
            sandbox.repo_url = repo_url
            sandbox.current_branch = branch
            
            # 设置工作目录为仓库根目录
            # 获取仓库的绝对路径（容器内路径）
            pwd_result = await self._execute(
                f"cd {repo_path} && pwd",
                timeout=10,
                check=False,
            )
            if pwd_result.success:
                absolute_repo_path = pwd_result.stdout.strip()
                await self._manager.set_sandbox_working_dir(self._sandbox_id, absolute_repo_path)

            logger.info(
                f"沙箱 {self._sandbox_id}: 克隆成功, commit={commit_hash[:8] if commit_hash else 'unknown'}"
            )

            return CloneResult(
                success=True,
                repo_path=repo_path,
                branch=branch,
                commit_hash=commit_hash,
                message="克隆成功",
            )

        except (GitAuthError, GitCloneError):
            raise
        except Exception as e:
            raise GitCloneError(repo_url, str(e), self._sandbox_id) from e

    async def checkout_branch(
        self,
        branch: str,
        create: bool = False,
        start_point: str | None = None,
        repo_path: str = ".",
    ) -> None:
        """
        切换或创建分支

        Args:
            branch: 分支名称
            create: 是否创建新分支
            start_point: 新分支的起始点（仅 create=True 时有效）
            repo_path: 仓库路径
        """
        logger.info(f"沙箱 {self._sandbox_id}: 切换分支 {branch}, create={create}")
        
        target_dir = await self._get_target_dir(repo_path)
        cmd_parts = [f"git -C {target_dir} checkout"]

        if create:
            cmd_parts.append("-b")

        cmd_parts.append(branch)

        if create and start_point:
            cmd_parts.append(start_point)

        command = " ".join(cmd_parts)

        try:
            await self._execute(command, timeout=30)

            # 更新沙箱的当前分支
            sandbox = await self._manager.get_sandbox(self._sandbox_id)
            sandbox.current_branch = branch

            logger.info(f"沙箱 {self._sandbox_id}: 分支切换成功")

        except GitError as e:
            raise GitBranchError(branch, str(e), self._sandbox_id) from e

    async def pull(
        self,
        branch: str | None = None,
        remote: str = "origin",
        repo_path: str = ".",
        rebase: bool = False,
    ) -> CommandResult:
        """
        拉取远程更新

        Args:
            branch: 分支名称（可选，使用当前分支）
            remote: 远程名称
            repo_path: 仓库路径
            rebase: 是否使用 rebase 模式

        Returns:
            命令执行结果
        """
        target_dir = await self._get_target_dir(repo_path)
        cmd_parts = [f"git -C {target_dir} pull"]

        if rebase:
            cmd_parts.append("--rebase")

        cmd_parts.append(remote)

        if branch:
            cmd_parts.append(branch)

        command = " ".join(cmd_parts)

        logger.info(f"沙箱 {self._sandbox_id}: 拉取更新 {remote}/{branch or 'current'}")

        result = await self._execute(command, timeout=120)
        return result

    async def fetch(
        self,
        remote: str = "origin",
        branch: str | None = None,
        repo_path: str = ".",
        prune: bool = False,
    ) -> CommandResult:
        """
        获取远程更新（不合并）

        Args:
            remote: 远程名称
            branch: 分支名称（可选）
            repo_path: 仓库路径
            prune: 是否清理已删除的远程分支

        Returns:
            命令执行结果
        """
        target_dir = await self._get_target_dir(repo_path)
        cmd_parts = [f"git -C {target_dir} fetch"]

        if prune:
            cmd_parts.append("--prune")

        cmd_parts.append(remote)

        if branch:
            cmd_parts.append(branch)

        command = " ".join(cmd_parts)

        logger.info(f"沙箱 {self._sandbox_id}: fetch {remote}")

        result = await self._execute(command, timeout=120)
        return result

    async def add(
        self,
        files: list[str] | None = None,
        all_files: bool = False,
        repo_path: str = ".",
    ) -> CommandResult:
        """
        添加文件到暂存区

        Args:
            files: 要添加的文件列表
            all_files: 是否添加所有变更
            repo_path: 仓库路径

        Returns:
            命令执行结果
        """
        target_dir = await self._get_target_dir(repo_path)
        
        if all_files:
            command = f"git -C {target_dir} add -A"
        elif files:
            files_str = " ".join(f'"{f}"' for f in files)
            command = f"git -C {target_dir} add {files_str}"
        else:
            command = f"git -C {target_dir} add ."

        result = await self._execute(command, timeout=30)
        return result

    async def commit(
        self,
        message: str,
        files: list[str] | None = None,
        all_files: bool = False,
        allow_empty: bool = False,
        repo_path: str = ".",
    ) -> str:
        """
        提交变更

        Args:
            message: 提交信息
            files: 要提交的文件列表（可选，需要先 add）
            all_files: 是否自动 add 所有变更
            allow_empty: 是否允许空提交
            repo_path: 仓库路径

        Returns:
            提交的 commit hash
        """
        logger.info(f"沙箱 {self._sandbox_id}: 开始提交变更")
        
        target_dir = await self._get_target_dir(repo_path)

        # 如果指定了文件，先 add
        if files or all_files:
            await self.add(files=files, all_files=all_files, repo_path=repo_path)

        # 构建 commit 命令
        escaped_message = message.replace('"', '\\"')
        cmd_parts = [f'git -C {target_dir} commit -m "{escaped_message}"']

        if allow_empty:
            cmd_parts.append("--allow-empty")

        command = " ".join(cmd_parts)

        try:
            result = await self._execute(command, timeout=30, check=False)

            if not result.success:
                if (
                    "nothing to commit" in result.stdout
                    or "nothing to commit" in result.stderr
                ):
                    logger.info(f"沙箱 {self._sandbox_id}: 没有需要提交的变更")
                    # 返回当前 HEAD
                    head_result = await self._execute(
                        f"git -C {target_dir} rev-parse HEAD",
                        timeout=10,
                    )
                    return head_result.stdout.strip()
                raise GitCommitError(result.stderr, self._sandbox_id)

            # 获取提交的 hash
            hash_result = await self._execute(
                f"git -C {target_dir} rev-parse HEAD",
                timeout=10,
            )
            commit_hash = hash_result.stdout.strip()

            logger.info(f"沙箱 {self._sandbox_id}: 提交成功, hash={commit_hash[:8]}")
            return commit_hash

        except GitCommitError:
            raise
        except Exception as e:
            raise GitCommitError(str(e), self._sandbox_id) from e

    async def push(
        self,
        branch: str | None = None,
        remote: str = "origin",
        force: bool = False,
        set_upstream: bool = False,
        repo_path: str = ".",
    ) -> None:
        """
        推送到远程仓库

        Args:
            branch: 分支名称（可选，使用当前分支）
            remote: 远程名称
            force: 是否强制推送
            set_upstream: 是否设置上游分支
            repo_path: 仓库路径
        """
        target_dir = await self._get_target_dir(repo_path)
        
        # 获取当前分支
        if not branch:
            branch_result = await self._execute(
                f"git -C {target_dir} rev-parse --abbrev-ref HEAD",
                timeout=10,
            )
            branch = branch_result.stdout.strip()

        logger.info(f"沙箱 {self._sandbox_id}: 推送分支 {branch} 到 {remote}")

        cmd_parts = [f"git -C {target_dir} push"]

        if force:
            cmd_parts.append("--force")

        if set_upstream:
            cmd_parts.append("-u")

        cmd_parts.extend([remote, branch])

        command = " ".join(cmd_parts)

        try:
            result = await self._execute(command, timeout=120, check=False)

            if not result.success:
                stderr = result.stderr
                if "Authentication failed" in stderr or "Permission denied" in stderr:
                    raise GitAuthError("推送认证失败", self._sandbox_id)
                if "rejected" in stderr:
                    raise GitPushError(
                        branch, "推送被拒绝，可能需要先拉取更新", self._sandbox_id
                    )
                raise GitPushError(branch, stderr, self._sandbox_id)

            logger.info(f"沙箱 {self._sandbox_id}: 推送成功")

        except (GitAuthError, GitPushError):
            raise
        except Exception as e:
            raise GitPushError(branch, str(e), self._sandbox_id) from e

    async def status(self, repo_path: str = ".") -> GitStatus:
        """
        获取仓库状态

        Args:
            repo_path: 仓库路径

        Returns:
            Git 状态信息
        """
        target_dir = await self._get_target_dir(repo_path)
        
        # 获取当前分支
        branch_result = await self._execute(
            f"git -C {target_dir} rev-parse --abbrev-ref HEAD",
            timeout=10,
        )
        branch = branch_result.stdout.strip()

        # 获取当前 commit hash
        hash_result = await self._execute(
            f"git -C {target_dir} rev-parse HEAD",
            timeout=10,
        )
        commit_hash = hash_result.stdout.strip()

        # 获取状态信息
        status_result = await self._execute(
            f"git -C {target_dir} status --porcelain",
            timeout=30,
        )

        staged_files: list[GitFileChange] = []
        unstaged_files: list[GitFileChange] = []
        untracked_files: list[str] = []

        for line in status_result.stdout.strip().split("\n"):
            if not line:
                continue

            index_status = line[0]
            work_status = line[1]
            filepath = line[3:].strip()

            # 处理重命名
            old_path = None
            if " -> " in filepath:
                old_path, filepath = filepath.split(" -> ")

            # 暂存区状态
            if index_status != " " and index_status != "?":
                file_status = self._parse_status_char(index_status)
                staged_files.append(
                    GitFileChange(
                        path=filepath,
                        status=file_status,
                        old_path=old_path,
                    )
                )

            # 工作区状态
            if work_status != " ":
                if work_status == "?":
                    untracked_files.append(filepath)
                else:
                    file_status = self._parse_status_char(work_status)
                    unstaged_files.append(
                        GitFileChange(
                            path=filepath,
                            status=file_status,
                        )
                    )

        is_clean = not staged_files and not unstaged_files and not untracked_files

        # 获取与远程分支的差异
        ahead = 0
        behind = 0
        try:
            remote_result = await self._execute(
                f"git -C {target_dir} rev-list --left-right --count HEAD...@{{u}}",
                timeout=10,
                check=False,
            )
            if remote_result.success:
                parts = remote_result.stdout.strip().split()
                if len(parts) == 2:
                    ahead = int(parts[0])
                    behind = int(parts[1])
        except Exception:
            pass  # 可能没有设置上游分支

        return GitStatus(
            branch=branch,
            commit_hash=commit_hash,
            is_clean=is_clean,
            staged_files=staged_files,
            unstaged_files=unstaged_files,
            untracked_files=untracked_files,
            ahead=ahead,
            behind=behind,
        )

    def _parse_status_char(self, char: str) -> GitFileStatus:
        """解析 git status 的状态字符"""
        mapping = {
            "M": GitFileStatus.MODIFIED,
            "A": GitFileStatus.ADDED,
            "D": GitFileStatus.DELETED,
            "R": GitFileStatus.RENAMED,
            "?": GitFileStatus.UNTRACKED,
        }
        return mapping.get(char, GitFileStatus.MODIFIED)

    async def diff(
        self,
        base: str = "HEAD",
        target: str | None = None,
        files: list[str] | None = None,
        staged: bool = False,
        repo_path: str = ".",
    ) -> str:
        """
        获取差异

        Args:
            base: 基准（commit hash、分支名等）
            target: 目标（可选）
            files: 文件列表（可选）
            staged: 是否只显示暂存区的差异
            repo_path: 仓库路径

        Returns:
            diff 输出
        """
        target_dir = await self._get_target_dir(repo_path)
        cmd_parts = [f"git -C {target_dir} diff"]

        if staged:
            cmd_parts.append("--cached")

        if base:
            cmd_parts.append(base)

        if target:
            cmd_parts.append(target)

        if files:
            cmd_parts.append("--")
            cmd_parts.extend(files)

        command = " ".join(cmd_parts)

        result = await self._execute(command, timeout=60)
        return result.stdout

    async def log(
        self,
        count: int = 10,
        oneline: bool = True,
        repo_path: str = ".",
    ) -> str:
        """
        获取提交日志

        Args:
            count: 日志条数
            oneline: 是否单行显示
            repo_path: 仓库路径

        Returns:
            日志输出
        """
        target_dir = await self._get_target_dir(repo_path)
        cmd_parts = [f"git -C {target_dir} log -n {count}"]

        if oneline:
            cmd_parts.append("--oneline")

        command = " ".join(cmd_parts)

        result = await self._execute(command, timeout=30)
        return result.stdout

    async def list_branches(
        self,
        remote: bool = False,
        all_branches: bool = False,
        repo_path: str = ".",
    ) -> list[str]:
        """
        列出分支

        Args:
            remote: 是否列出远程分支
            all_branches: 是否列出所有分支
            repo_path: 仓库路径

        Returns:
            分支列表
        """
        target_dir = await self._get_target_dir(repo_path)
        cmd_parts = [f"git -C {target_dir} branch"]

        if all_branches:
            cmd_parts.append("-a")
        elif remote:
            cmd_parts.append("-r")

        command = " ".join(cmd_parts)

        result = await self._execute(command, timeout=30)

        branches = []
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if line.startswith("* "):
                line = line[2:]
            if line and not line.startswith("HEAD"):
                branches.append(line)

        return branches

    async def reset(
        self,
        mode: str = "mixed",
        target: str = "HEAD",
        repo_path: str = ".",
    ) -> None:
        """
        重置仓库状态

        Args:
            mode: 重置模式（soft, mixed, hard）
            target: 重置目标
            repo_path: 仓库路径
        """
        target_dir = await self._get_target_dir(repo_path)
        command = f"git -C {target_dir} reset --{mode} {target}"
        await self._execute(command, timeout=30)
        logger.info(f"沙箱 {self._sandbox_id}: 重置到 {target} (mode={mode})")

    async def stash(
        self,
        action: str = "push",
        message: str | None = None,
        repo_path: str = ".",
    ) -> CommandResult:
        """
        暂存/恢复工作区变更

        Args:
            action: 操作（push, pop, list, drop）
            message: stash 消息（仅 push 时有效）
            repo_path: 仓库路径

        Returns:
            命令执行结果
        """
        target_dir = await self._get_target_dir(repo_path)
        cmd_parts = [f"git -C {target_dir} stash {action}"]

        if action == "push" and message:
            cmd_parts.extend(["-m", f'"{message}"'])

        command = " ".join(cmd_parts)

        result = await self._execute(command, timeout=30)
        return result

    async def apply_patch(
        self,
        patch_content: str,
        repo_path: str = ".",
    ) -> None:
        """
        应用补丁

        Args:
            patch_content: unified diff 格式的补丁内容
            repo_path: 仓库路径

        Raises:
            GitError: 补丁应用失败
        """
        # 验证补丁内容
        if not patch_content or not patch_content.strip():
            raise GitError(
                "补丁内容为空",
                self._sandbox_id,
                "apply_patch",
            )
        
        # 验证补丁格式（应该以 --- 或 diff 开头）
        first_line = patch_content.strip().split('\n')[0]
        if not (first_line.startswith('---') or first_line.startswith('diff')):
            logger.warning(f"补丁格式可能不正确，首行: {first_line[:100]}")
        
        logger.info(
            f"沙箱 {self._sandbox_id}: 开始应用补丁 "
            f"(size={len(patch_content)} bytes, lines={patch_content.count(chr(10))})"
        )

        # 使用 FileService 安全地写入补丁文件
        from app.sandbox.file_service import FileService
        file_service = FileService(self._manager, self._sandbox_id)
        
        # 创建临时补丁文件
        patch_file = f"/tmp/patch_{self._sandbox_id}.diff"
        
        try:
            # 写入补丁内容
            await file_service.write_file(patch_file, patch_content, create_dirs=False)
            
            # 获取目标目录（优先使用绝对路径）
            target_dir = await self._get_target_dir(repo_path)
            
            # 使用 git -C 来指定工作目录，避免相对路径问题
            apply_cmd = f"git -C {target_dir} apply {patch_file}"
            result = await self._execute(apply_cmd, timeout=60, check=False)
            
            if not result.success:
                raise GitError(
                    f"补丁应用失败: {result.stderr}",
                    self._sandbox_id,
                    apply_cmd,
                )
            
            logger.info(f"沙箱 {self._sandbox_id}: 补丁应用成功")
            
        finally:
            # 清理临时文件
            try:
                await file_service.delete_file(patch_file)
            except Exception as e:
                logger.warning(f"清理临时补丁文件失败: {e}")
