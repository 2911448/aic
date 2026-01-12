"""
沙箱管理器

负责沙箱的生命周期管理，包括创建、启动、停止和销毁
"""

import os
import shutil
import tempfile
import uuid
from datetime import datetime

from docker.models.containers import Container
from loguru import logger

from app.sandbox.docker_client import DockerClient, get_docker_client
from app.sandbox.exceptions import (
    SandboxCreationError,
    SandboxExecutionError,
    SandboxNotFoundError,
)
from app.sandbox.models import (
    CommandResult,
    Sandbox,
    SandboxConfig,
    SandboxStatus,
    GitAuthConfig,
)


class SandboxManager:
    """
    沙箱管理器

    管理 Docker 容器的生命周期，提供统一的沙箱操作接口
    """

    def __init__(
        self,
        docker_client: DockerClient | None = None,
        base_workspace_path: str | None = None,
    ):
        """
        初始化沙箱管理器

        Args:
            docker_client: Docker 客户端（可选，默认使用全局客户端）
            base_workspace_path: 工作目录基础路径（可选，默认使用临时目录）
        """
        self._docker_client = docker_client or get_docker_client()
        self._base_workspace_path = base_workspace_path or tempfile.gettempdir()
        self._sandboxes: dict[str, Sandbox] = {}
        self._containers: dict[str, Container] = {}

    @property
    def docker_client(self) -> DockerClient:
        """获取 Docker 客户端"""
        return self._docker_client

    async def create_sandbox(
        self,
        config: SandboxConfig | None = None,
        sandbox_id: str | None = None,
    ) -> Sandbox:
        """
        创建新的沙箱环境

        Args:
            config: 沙箱配置（可选，使用默认配置）
            sandbox_id: 指定沙箱 ID（可选，自动生成）

        Returns:
            创建的沙箱实例
        """
        config = config or SandboxConfig()
        sandbox_id = sandbox_id or str(uuid.uuid4())[:8]

        logger.info(f"开始创建沙箱: {sandbox_id}")

        # 创建宿主机工作目录
        workspace_path = os.path.join(
            self._base_workspace_path, "sandbox_workspaces", sandbox_id
        )
        os.makedirs(workspace_path, exist_ok=True)

        # 创建沙箱实例
        sandbox = Sandbox(
            id=sandbox_id,
            status=SandboxStatus.CREATING,
            config=config,
            workspace_path=workspace_path,
            created_at=datetime.now(),
        )
        self._sandboxes[sandbox_id] = sandbox

        try:
            # 准备 Git 认证相关的卷挂载和环境变量
            extra_volumes, extra_env = await self._prepare_git_auth(
                config.git_auth, workspace_path
            )

            # 合并环境变量
            merged_env = {**config.environment, **extra_env}
            config_with_env = config.model_copy(update={"environment": merged_env})

            # 创建 Docker 容器
            container = await self._docker_client.create_container(
                config=config_with_env,
                workspace_host_path=workspace_path,
                container_name=f"sandbox-{sandbox_id}",
                extra_volumes=extra_volumes,
            )

            sandbox.container_id = container.id
            self._containers[sandbox_id] = container

            # 启动容器
            await self._docker_client.start_container(container)
            sandbox.status = SandboxStatus.RUNNING
            sandbox.started_at = datetime.now()

            # 初始化容器环境（安装 git 等必要工具）
            await self._init_container_environment(sandbox)

            logger.info(f"沙箱创建成功: {sandbox_id}")
            return sandbox

        except Exception as e:
            sandbox.status = SandboxStatus.FAILED
            sandbox.error_message = str(e)
            logger.error(f"沙箱创建失败: {sandbox_id}, 错误: {e}")

            # 清理失败的资源
            await self._cleanup_failed_sandbox(sandbox_id)
            raise SandboxCreationError(str(e), sandbox_id) from e

    async def _prepare_git_auth(
        self,
        git_auth: GitAuthConfig | None,
        workspace_path: str,
    ) -> tuple[dict[str, dict], dict[str, str]]:
        """
        准备 Git 认证配置

        Returns:
            (extra_volumes, extra_env) 元组
        """
        extra_volumes: dict[str, dict] = {}
        extra_env: dict[str, str] = {}

        if not git_auth:
            return extra_volumes, extra_env

        # SSH 认证配置
        if git_auth.ssh_private_key or git_auth.ssh_private_key_path:
            ssh_dir = os.path.join(workspace_path, ".ssh")
            os.makedirs(ssh_dir, mode=0o700, exist_ok=True)

            # 写入 SSH 私钥
            key_path = os.path.join(ssh_dir, "id_rsa")
            if git_auth.ssh_private_key:
                with open(key_path, "w") as f:
                    f.write(git_auth.ssh_private_key)
                os.chmod(key_path, 0o600)
            elif git_auth.ssh_private_key_path and os.path.exists(
                git_auth.ssh_private_key_path
            ):
                shutil.copy2(git_auth.ssh_private_key_path, key_path)
                os.chmod(key_path, 0o600)

            # 创建 SSH 配置
            ssh_config_path = os.path.join(ssh_dir, "config")
            with open(ssh_config_path, "w") as f:
                f.write("Host *\n")
                f.write("  StrictHostKeyChecking no\n")
                f.write("  UserKnownHostsFile /dev/null\n")
            os.chmod(ssh_config_path, 0o600)

            extra_volumes[ssh_dir] = {"bind": "/root/.ssh", "mode": "ro"}

        # HTTP Token 认证配置
        if git_auth.http_token:
            extra_env["GIT_TOKEN"] = git_auth.http_token
            if git_auth.http_username:
                extra_env["GIT_USERNAME"] = git_auth.http_username

        return extra_volumes, extra_env

    async def _init_container_environment(self, sandbox: Sandbox) -> None:
        """初始化容器环境"""
        container = self._containers.get(sandbox.id)
        if not container:
            return

        # 检查并安装 git（如果镜像中没有）
        check_git = await self._docker_client.execute_command(
            container, "which git", timeout=30
        )

        if not check_git.success:
            logger.info(f"沙箱 {sandbox.id}: 安装 git...")
            # 根据不同的基础镜像使用不同的包管理器
            install_cmds = [
                "apt-get update && apt-get install -y git",  # Debian/Ubuntu
                "apk add --no-cache git",  # Alpine
                "yum install -y git",  # CentOS/RHEL
            ]

            for cmd in install_cmds:
                result = await self._docker_client.execute_command(
                    container, cmd, timeout=120
                )
                if result.success:
                    logger.info(f"沙箱 {sandbox.id}: git 安装成功")
                    break
            else:
                logger.warning(f"沙箱 {sandbox.id}: git 安装失败，可能需要手动安装")

        # 配置 git 用户信息
        await self._docker_client.execute_command(
            container,
            'git config --global user.email "sandbox@aic.local" && git config --global user.name "AIC Sandbox"',
            timeout=10,
        )

    async def _cleanup_failed_sandbox(self, sandbox_id: str) -> None:
        """清理创建失败的沙箱资源"""
        try:
            container = self._containers.pop(sandbox_id, None)
            if container:
                await self._docker_client.remove_container(container, force=True)

            sandbox = self._sandboxes.get(sandbox_id)
            if (
                sandbox
                and sandbox.workspace_path
                and os.path.exists(sandbox.workspace_path)
            ):
                shutil.rmtree(sandbox.workspace_path, ignore_errors=True)
        except Exception as e:
            logger.warning(f"清理失败的沙箱资源时出错: {e}")

    async def get_sandbox(self, sandbox_id: str) -> Sandbox:
        """
        获取沙箱实例

        Args:
            sandbox_id: 沙箱 ID

        Returns:
            沙箱实例

        Raises:
            SandboxNotFoundError: 沙箱不存在
        """
        sandbox = self._sandboxes.get(sandbox_id)
        if not sandbox:
            raise SandboxNotFoundError(sandbox_id)
        return sandbox

    async def get_container(self, sandbox_id: str) -> Container:
        """
        获取沙箱对应的 Docker 容器

        Args:
            sandbox_id: 沙箱 ID

        Returns:
            Docker 容器对象

        Raises:
            SandboxNotFoundError: 沙箱不存在
        """
        container = self._containers.get(sandbox_id)
        if not container:
            raise SandboxNotFoundError(sandbox_id)
        return container

    async def set_sandbox_working_dir(
        self,
        sandbox_id: str,
        working_dir: str
    ) -> None:
        """
        设置 sandbox 的工作目录
        
        Args:
            sandbox_id: 沙箱 ID
            working_dir: 工作目录路径（容器内路径）
        """
        sandbox = await self.get_sandbox(sandbox_id)
        sandbox.repo_working_dir = working_dir
        logger.info(f"Sandbox {sandbox_id} 工作目录设置为: {working_dir}")

    async def execute_command(
        self,
        sandbox_id: str,
        command: str,
        timeout: int | None = None,
        workdir: str | None = None,
        environment: dict[str, str] | None = None,
    ) -> CommandResult:
        """
        在沙箱中执行命令

        Args:
            sandbox_id: 沙箱 ID
            command: 要执行的命令
            timeout: 超时时间（秒），默认使用沙箱配置
            workdir: 工作目录
            environment: 额外的环境变量

        Returns:
            命令执行结果
        """
        sandbox = await self.get_sandbox(sandbox_id)
        container = await self.get_container(sandbox_id)

        if sandbox.status != SandboxStatus.RUNNING:
            raise SandboxExecutionError(
                f"沙箱 {sandbox_id} 状态为 {sandbox.status}，无法执行命令",
                sandbox_id,
            )

        effective_timeout = timeout or sandbox.config.timeout
        effective_workdir = workdir or sandbox.repo_working_dir or sandbox.config.workspace_path

        result = await self._docker_client.execute_command(
            container=container,
            command=command,
            timeout=effective_timeout,
            workdir=effective_workdir,
            environment=environment,
        )

        return result

    async def stop_sandbox(self, sandbox_id: str) -> None:
        """
        停止沙箱

        Args:
            sandbox_id: 沙箱 ID
        """
        sandbox = await self.get_sandbox(sandbox_id)
        container = self._containers.get(sandbox_id)

        if container:
            await self._docker_client.stop_container(container)

        sandbox.status = SandboxStatus.STOPPED
        sandbox.stopped_at = datetime.now()
        logger.info(f"沙箱已停止: {sandbox_id}")

    async def destroy_sandbox(
        self, sandbox_id: str, cleanup_workspace: bool = True
    ) -> None:
        """
        销毁沙箱

        Args:
            sandbox_id: 沙箱 ID
            cleanup_workspace: 是否清理工作目录
        """
        sandbox = self._sandboxes.get(sandbox_id)
        container = self._containers.pop(sandbox_id, None)

        # 停止并删除容器
        if container:
            await self._docker_client.stop_container(container)
            await self._docker_client.remove_container(container)

        # 清理工作目录
        if cleanup_workspace and sandbox and sandbox.workspace_path:
            if os.path.exists(sandbox.workspace_path):
                shutil.rmtree(sandbox.workspace_path, ignore_errors=True)
                logger.debug(f"已清理工作目录: {sandbox.workspace_path}")

        # 更新状态
        if sandbox:
            sandbox.status = SandboxStatus.DESTROYED
            sandbox.stopped_at = datetime.now()

        # 从管理器中移除
        self._sandboxes.pop(sandbox_id, None)
        logger.info(f"沙箱已销毁: {sandbox_id}")

    async def list_sandboxes(
        self,
        status: SandboxStatus | None = None,
    ) -> list[Sandbox]:
        """
        列出所有沙箱

        Args:
            status: 按状态过滤（可选）

        Returns:
            沙箱列表
        """
        sandboxes = list(self._sandboxes.values())
        if status:
            sandboxes = [s for s in sandboxes if s.status == status]
        return sandboxes

    async def cleanup_expired_sandboxes(self, max_age_seconds: int = 3600) -> int:
        """
        清理过期的沙箱

        Args:
            max_age_seconds: 最大存活时间（秒）

        Returns:
            清理的沙箱数量
        """
        now = datetime.now()
        expired_ids = []

        for sandbox_id, sandbox in self._sandboxes.items():
            age = (now - sandbox.created_at).total_seconds()
            if age > max_age_seconds:
                expired_ids.append(sandbox_id)

        for sandbox_id in expired_ids:
            try:
                await self.destroy_sandbox(sandbox_id)
            except Exception as e:
                logger.warning(f"清理过期沙箱 {sandbox_id} 失败: {e}")

        if expired_ids:
            logger.info(f"已清理 {len(expired_ids)} 个过期沙箱")

        return len(expired_ids)

    async def close(self) -> None:
        """关闭管理器，清理所有资源"""
        logger.info("关闭沙箱管理器，清理所有沙箱...")

        # 销毁所有沙箱
        sandbox_ids = list(self._sandboxes.keys())
        for sandbox_id in sandbox_ids:
            try:
                await self.destroy_sandbox(sandbox_id)
            except Exception as e:
                logger.warning(f"关闭时销毁沙箱 {sandbox_id} 失败: {e}")

        logger.info("沙箱管理器已关闭")


# 全局沙箱管理器实例
_sandbox_manager: SandboxManager | None = None


def get_sandbox_manager() -> SandboxManager:
    """获取全局沙箱管理器实例"""
    global _sandbox_manager
    if _sandbox_manager is None:
        _sandbox_manager = SandboxManager()
    return _sandbox_manager
