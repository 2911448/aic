"""
Docker 客户端封装

提供 Docker 容器的创建、管理和命令执行能力
"""

import asyncio
import time
from typing import Any

import docker
from docker.errors import DockerException, ImageNotFound, NotFound, APIError
from docker.models.containers import Container
from loguru import logger

from app.sandbox.exceptions import (
    DockerConnectionError,
    DockerError,
    SandboxExecutionError,
    SandboxTimeoutError,
)
from app.sandbox.models import CommandResult, SandboxConfig


class DockerClient:
    """Docker 客户端封装类"""

    def __init__(self):
        self._client: docker.DockerClient | None = None

    @property
    def client(self) -> docker.DockerClient:
        """获取 Docker 客户端（懒加载）"""
        if self._client is None:
            try:
                self._client = docker.from_env()
                # 测试连接
                self._client.ping()
            except DockerException as e:
                logger.error(f"连接 Docker daemon 失败: {e}")
                raise DockerConnectionError(str(e)) from e
        return self._client

    def check_connection(self) -> bool:
        """检查 Docker 连接状态"""
        try:
            self.client.ping()
            return True
        except Exception as e:
            logger.error(f"Docker 连接检查失败: {e}")
            return False

    async def pull_image(self, image: str) -> bool:
        """
        拉取 Docker 镜像

        Args:
            image: 镜像名称（如 video-sandbox:0.1）

        Returns:
            是否成功
        """
        try:
            logger.info(f"拉取镜像: {image}")
            # Docker SDK 的 pull 是同步的，放到线程池执行
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self.client.images.pull, image)
            logger.info(f"镜像拉取成功: {image}")
            return True
        except ImageNotFound:
            logger.error(f"镜像不存在: {image}")
            return False
        except APIError as e:
            logger.error(f"拉取镜像失败: {e}")
            return False

    async def image_exists(self, image: str) -> bool:
        """检查镜像是否存在"""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self.client.images.get, image)
            return True
        except ImageNotFound:
            return False

    async def create_container(
        self,
        config: SandboxConfig,
        workspace_host_path: str,
        container_name: str | None = None,
        extra_volumes: dict[str, dict] | None = None,
    ) -> Container:
        """
        创建 Docker 容器

        Args:
            config: 沙箱配置
            workspace_host_path: 宿主机工作目录路径
            container_name: 容器名称（可选）
            extra_volumes: 额外的卷挂载

        Returns:
            Docker 容器对象
        """
        try:
            # 检查镜像是否存在，不存在则拉取
            if not await self.image_exists(config.docker_image):
                logger.info(f"镜像 {config.docker_image} 不存在，开始拉取...")
                await self.pull_image(config.docker_image)

            # 构建卷挂载配置
            volumes = {
                workspace_host_path: {
                    "bind": config.workspace_path,
                    "mode": "rw",
                }
            }
            if extra_volumes:
                volumes.update(extra_volumes)

            # 构建容器配置
            container_config: dict[str, Any] = {
                "image": config.docker_image,
                "command": "tail -f /dev/null",  # 保持容器运行
                "detach": True,
                "working_dir": config.workspace_path,
                "volumes": volumes,
                "environment": config.environment,
                "mem_limit": config.memory_limit,
                "nano_cpus": int(config.cpu_limit * 1e9),  # CPU 限制转换为纳秒
                "auto_remove": False,  # 不自动删除，由 manager 控制
            }

            if container_name:
                container_config["name"] = container_name

            if config.network_mode:
                container_config["network_mode"] = config.network_mode

            logger.info(
                f"创建容器: image: {config.docker_image}, workspace: {workspace_host_path}"
            )

            # 创建容器
            loop = asyncio.get_event_loop()
            container = await loop.run_in_executor(
                None,
                lambda: self.client.containers.create(**container_config),
            )

            logger.info(f"容器创建成功: {container.id[:12]}")
            return container

        except ImageNotFound as e:
            raise DockerError(f"镜像 {config.docker_image} 不存在", e) from e
        except APIError as e:
            raise DockerError(f"创建容器失败: {e}", e) from e

    async def start_container(self, container: Container) -> None:
        """启动容器"""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, container.start)
            logger.info(f"容器启动成功: {container.id[:12]}")
        except APIError as e:
            raise DockerError(f"启动容器失败: {e}", e) from e

    async def stop_container(self, container: Container, timeout: int = 10) -> None:
        """停止容器"""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: container.stop(timeout=timeout))
            logger.info(f"容器停止成功: {container.id[:12]}")
        except APIError as e:
            logger.warning(f"停止容器失败: {e}")

    async def remove_container(self, container: Container, force: bool = True) -> None:
        """删除容器"""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: container.remove(force=force))
            logger.info(f"容器删除成功: {container.id[:12]}")
        except NotFound:
            logger.debug(f"容器已不存在: {container.id[:12]}")
        except APIError as e:
            logger.warning(f"删除容器失败: {e}")

    async def get_container(self, container_id: str) -> Container | None:
        """根据 ID 获取容器"""
        try:
            loop = asyncio.get_event_loop()
            container = await loop.run_in_executor(
                None, self.client.containers.get, container_id
            )
            return container
        except NotFound:
            return None

    async def execute_command(
        self,
        container: Container,
        command: str | list[str],
        timeout: int = 60,
        workdir: str | None = None,
        environment: dict[str, str] | None = None,
        user: str | None = None,
    ) -> CommandResult:
        """
        在容器内执行命令

        Args:
            container: Docker 容器对象
            command: 要执行的命令
            timeout: 超时时间（秒）
            workdir: 工作目录
            environment: 额外的环境变量
            user: 执行用户

        Returns:
            命令执行结果
        """
        start_time = time.time()

        try:
            # 构建 exec 配置（注意：demux 只在 exec_start 中使用）
            exec_config: dict[str, Any] = {
                "cmd": command
                if isinstance(command, list)
                else ["/bin/sh", "-c", command],
                "stdout": True,
                "stderr": True,
            }

            if workdir:
                exec_config["workdir"] = workdir
            if environment:
                exec_config["environment"] = environment
            if user:
                exec_config["user"] = user

            loop = asyncio.get_event_loop()

            # 创建 exec 实例
            exec_instance = await loop.run_in_executor(
                None, lambda: self.client.api.exec_create(container.id, **exec_config)
            )

            # 执行命令（带超时）
            async def run_exec():
                return await loop.run_in_executor(
                    None,
                    lambda: self.client.api.exec_start(exec_instance["Id"], demux=True),
                )

            try:
                output = await asyncio.wait_for(run_exec(), timeout=timeout)
            except TimeoutError as e:
                raise SandboxTimeoutError(container.id[:12], timeout) from e

            # 获取退出码
            inspect_result = await loop.run_in_executor(
                None, lambda: self.client.api.exec_inspect(exec_instance["Id"])
            )
            exit_code = inspect_result.get("ExitCode", -1)

            # 解析输出
            stdout_bytes, stderr_bytes = output if output else (b"", b"")
            stdout = (
                stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            )
            stderr = (
                stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
            )

            duration_ms = int((time.time() - start_time) * 1000)

            return CommandResult(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
            )

        except SandboxTimeoutError:
            raise
        except APIError as e:
            raise SandboxExecutionError(
                f"执行命令失败: {e}",
                container.id[:12],
            ) from e

    async def copy_to_container(
        self,
        container: Container,
        src_path: str,
        dest_path: str,
    ) -> bool:
        """
        复制文件到容器

        Args:
            container: Docker 容器对象
            src_path: 源文件路径（宿主机）
            dest_path: 目标路径（容器内）

        Returns:
            是否成功
        """
        import tarfile
        import io
        import os

        try:
            # 创建 tar 归档
            tar_stream = io.BytesIO()
            with tarfile.open(fileobj=tar_stream, mode="w") as tar:
                tar.add(src_path, arcname=os.path.basename(src_path))
            tar_stream.seek(0)

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: container.put_archive(dest_path, tar_stream),
            )
            return True
        except Exception as e:
            logger.error(f"复制文件到容器失败: {e}")
            return False

    async def get_container_logs(
        self,
        container: Container,
        tail: int = 100,
    ) -> str:
        """获取容器日志"""
        try:
            loop = asyncio.get_event_loop()
            logs = await loop.run_in_executor(
                None,
                lambda: container.logs(tail=tail, stdout=True, stderr=True),
            )
            return logs.decode("utf-8", errors="replace")
        except Exception as e:
            logger.error(f"获取容器日志失败: {e}")
            return ""

    def close(self) -> None:
        """关闭 Docker 客户端连接"""
        if self._client:
            self._client.close()
            self._client = None


# 全局 Docker 客户端实例
_docker_client: DockerClient | None = None


def get_docker_client() -> DockerClient:
    """获取全局 Docker 客户端实例"""
    global _docker_client
    if _docker_client is None:
        _docker_client = DockerClient()
    return _docker_client
