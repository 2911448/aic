"""
文件服务

在沙箱环境中进行文件操作，包括读写、列表、patch 应用等
"""

import base64
import os
from datetime import datetime
from typing import TYPE_CHECKING

from loguru import logger

from app.sandbox.exceptions import (
    FileNotFoundInSandboxError,
    FileOperationError,
    FilePermissionError,
    PatchApplyError,
)
from app.sandbox.models import (
    CommandResult,
    FileInfo,
    PatchResult,
)

if TYPE_CHECKING:
    from app.sandbox.manager import SandboxManager


class FileService:
    """
    文件服务类

    在沙箱环境中执行文件操作
    """

    def __init__(self, sandbox_manager: "SandboxManager", sandbox_id: str):
        """
        初始化文件服务

        Args:
            sandbox_manager: 沙箱管理器
            sandbox_id: 沙箱 ID
        """
        self._manager = sandbox_manager
        self._sandbox_id = sandbox_id

    async def _execute(
        self,
        command: str,
        timeout: int = 30,
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
        """
        result = await self._manager.execute_command(
            sandbox_id=self._sandbox_id,
            command=command,
            timeout=timeout,
        )

        if check and not result.success:
            raise FileOperationError(
                f"命令执行失败: {command}\n{result.stderr}",
                sandbox_id=self._sandbox_id,
            )

        return result

    async def read_file(
        self,
        path: str,
        encoding: str = "utf-8",
    ) -> str:
        """
        读取文件内容

        Args:
            path: 文件路径（相对于工作目录）
            encoding: 文件编码

        Returns:
            文件内容

        Raises:
            FileNotFoundInSandboxError: 文件不存在
            FileOperationError: 读取失败
        """
        # 检查文件是否存在
        check_result = await self._execute(
            f'test -f "{path}" && echo "exists"',
            check=False,
        )

        if not check_result.success or "exists" not in check_result.stdout:
            raise FileNotFoundInSandboxError(path, self._sandbox_id)

        # 读取文件内容
        result = await self._execute(f'cat "{path}"', check=False)

        if not result.success:
            if "Permission denied" in result.stderr:
                raise FilePermissionError(path, self._sandbox_id)
            raise FileOperationError(
                f"读取文件失败: {result.stderr}",
                path,
                self._sandbox_id,
            )

        return result.stdout

    async def read_file_binary(self, path: str) -> bytes:
        """
        读取二进制文件

        Args:
            path: 文件路径

        Returns:
            文件内容（bytes）
        """
        # 使用 base64 编码传输二进制内容
        result = await self._execute(f'base64 "{path}"', check=False)

        if not result.success:
            if "No such file" in result.stderr:
                raise FileNotFoundInSandboxError(path, self._sandbox_id)
            raise FileOperationError(
                f"读取文件失败: {result.stderr}",
                path,
                self._sandbox_id,
            )

        return base64.b64decode(result.stdout.strip())

    async def write_file(
        self,
        path: str,
        content: str,
        create_dirs: bool = True,
        mode: str | None = None,
    ) -> None:
        """
        写入文件内容

        Args:
            path: 文件路径
            content: 文件内容
            create_dirs: 是否自动创建目录
            mode: 文件权限（如 "644"）
        """
        logger.debug(f"沙箱 {self._sandbox_id}: 写入文件 {path}")

        # 创建目录
        if create_dirs:
            dir_path = os.path.dirname(path)
            if dir_path:
                await self._execute(f'mkdir -p "{dir_path}"', check=False)

        # 使用 base64 编码来安全传输内容
        encoded_content = base64.b64encode(content.encode("utf-8")).decode("ascii")

        # 写入文件
        result = await self._execute(
            f'echo "{encoded_content}" | base64 -d > "{path}"',
            check=False,
        )

        if not result.success:
            raise FileOperationError(
                f"写入文件失败: {result.stderr}",
                path,
                self._sandbox_id,
            )

        # 设置权限
        if mode:
            await self._execute(f'chmod {mode} "{path}"', check=False)

        logger.debug(f"沙箱 {self._sandbox_id}: 文件写入成功 {path}")

    async def write_file_binary(
        self,
        path: str,
        content: bytes,
        create_dirs: bool = True,
        mode: str | None = None,
    ) -> None:
        """
        写入二进制文件

        Args:
            path: 文件路径
            content: 文件内容（bytes）
            create_dirs: 是否自动创建目录
            mode: 文件权限
        """
        # 创建目录
        if create_dirs:
            dir_path = os.path.dirname(path)
            if dir_path:
                await self._execute(f'mkdir -p "{dir_path}"', check=False)

        # 使用 base64 编码传输
        encoded_content = base64.b64encode(content).decode("ascii")

        result = await self._execute(
            f'echo "{encoded_content}" | base64 -d > "{path}"',
            check=False,
        )

        if not result.success:
            raise FileOperationError(
                f"写入文件失败: {result.stderr}",
                path,
                self._sandbox_id,
            )

        if mode:
            await self._execute(f'chmod {mode} "{path}"', check=False)

    async def append_file(self, path: str, content: str) -> None:
        """
        追加内容到文件

        Args:
            path: 文件路径
            content: 要追加的内容
        """
        encoded_content = base64.b64encode(content.encode("utf-8")).decode("ascii")

        result = await self._execute(
            f'echo "{encoded_content}" | base64 -d >> "{path}"',
            check=False,
        )

        if not result.success:
            raise FileOperationError(
                f"追加文件失败: {result.stderr}",
                path,
                self._sandbox_id,
            )

    async def delete_file(self, path: str) -> None:
        """
        删除文件

        Args:
            path: 文件路径
        """
        result = await self._execute(f'rm -f "{path}"', check=False)

        if not result.success:
            raise FileOperationError(
                f"删除文件失败: {result.stderr}",
                path,
                self._sandbox_id,
            )

        logger.debug(f"沙箱 {self._sandbox_id}: 已删除文件 {path}")

    async def delete_directory(self, path: str, recursive: bool = True) -> None:
        """
        删除目录

        Args:
            path: 目录路径
            recursive: 是否递归删除
        """
        if recursive:
            result = await self._execute(f'rm -rf "{path}"', check=False)
        else:
            result = await self._execute(f'rmdir "{path}"', check=False)

        if not result.success:
            raise FileOperationError(
                f"删除目录失败: {result.stderr}",
                path,
                self._sandbox_id,
            )

    async def create_directory(self, path: str, mode: str | None = None) -> None:
        """
        创建目录

        Args:
            path: 目录路径
            mode: 目录权限
        """
        result = await self._execute(f'mkdir -p "{path}"', check=False)

        if not result.success:
            raise FileOperationError(
                f"创建目录失败: {result.stderr}",
                path,
                self._sandbox_id,
            )

        if mode:
            await self._execute(f'chmod {mode} "{path}"', check=False)

    async def exists(self, path: str) -> bool:
        """
        检查文件或目录是否存在

        Args:
            path: 路径

        Returns:
            是否存在
        """
        result = await self._execute(
            f'test -e "{path}" && echo "exists"',
            check=False,
        )
        return "exists" in result.stdout

    async def is_file(self, path: str) -> bool:
        """检查是否为文件"""
        result = await self._execute(
            f'test -f "{path}" && echo "file"',
            check=False,
        )
        return "file" in result.stdout

    async def is_directory(self, path: str) -> bool:
        """检查是否为目录"""
        result = await self._execute(
            f'test -d "{path}" && echo "dir"',
            check=False,
        )
        return "dir" in result.stdout

    async def list_files(
        self,
        path: str = ".",
        recursive: bool = False,
        pattern: str | None = None,
    ) -> list[FileInfo]:
        """
        列出目录内容

        Args:
            path: 目录路径
            recursive: 是否递归列出
            pattern: 文件名匹配模式

        Returns:
            文件信息列表
        """
        # 使用 find 命令获取详细信息
        if recursive:
            if pattern:
                cmd = f'find "{path}" -name "{pattern}" -printf "%p\\t%s\\t%T@\\t%y\\n"'
            else:
                cmd = f'find "{path}" -printf "%p\\t%s\\t%T@\\t%y\\n"'
        else:
            if pattern:
                cmd = f'find "{path}" -maxdepth 1 -name "{pattern}" -printf "%p\\t%s\\t%T@\\t%y\\n"'
            else:
                cmd = f'find "{path}" -maxdepth 1 -printf "%p\\t%s\\t%T@\\t%y\\n"'

        result = await self._execute(cmd, check=False)

        if not result.success:
            # 尝试备用命令（ls）
            if recursive:
                cmd = f'ls -laR "{path}"'
            else:
                cmd = f'ls -la "{path}"'
            result = await self._execute(cmd, check=False)

            if not result.success:
                raise FileOperationError(
                    f"列出目录失败: {result.stderr}",
                    path,
                    self._sandbox_id,
                )

            # 解析 ls 输出
            return self._parse_ls_output(result.stdout, path)

        # 解析 find 输出
        files: list[FileInfo] = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue

            parts = line.split("\t")
            if len(parts) >= 4:
                file_path = parts[0]
                size = int(parts[1]) if parts[1].isdigit() else 0
                try:
                    mtime = datetime.fromtimestamp(float(parts[2]))
                except (ValueError, OSError):
                    mtime = None
                is_dir = parts[3] == "d"

                files.append(
                    FileInfo(
                        path=file_path,
                        name=os.path.basename(file_path),
                        is_dir=is_dir,
                        size=size,
                        modified_at=mtime,
                    )
                )

        return files

    def _parse_ls_output(self, output: str, base_path: str) -> list[FileInfo]:
        """解析 ls -la 输出"""
        files: list[FileInfo] = []

        for line in output.strip().split("\n"):
            if not line or line.startswith("total") or line.startswith("d"):
                continue

            parts = line.split()
            if len(parts) >= 9:
                permissions = parts[0]
                size = int(parts[4]) if parts[4].isdigit() else 0
                name = " ".join(parts[8:])

                if name in (".", ".."):
                    continue

                is_dir = permissions.startswith("d")

                files.append(
                    FileInfo(
                        path=os.path.join(base_path, name),
                        name=name,
                        is_dir=is_dir,
                        size=size,
                        modified_at=None,
                    )
                )

        return files

    async def copy_file(
        self,
        src: str,
        dest: str,
        recursive: bool = False,
    ) -> None:
        """
        复制文件或目录

        Args:
            src: 源路径
            dest: 目标路径
            recursive: 是否递归复制（目录时需要）
        """
        if recursive:
            cmd = f'cp -r "{src}" "{dest}"'
        else:
            cmd = f'cp "{src}" "{dest}"'

        result = await self._execute(cmd, check=False)

        if not result.success:
            raise FileOperationError(
                f"复制失败: {result.stderr}",
                src,
                self._sandbox_id,
            )

    async def move_file(self, src: str, dest: str) -> None:
        """
        移动文件或目录

        Args:
            src: 源路径
            dest: 目标路径
        """
        result = await self._execute(f'mv "{src}" "{dest}"', check=False)

        if not result.success:
            raise FileOperationError(
                f"移动失败: {result.stderr}",
                src,
                self._sandbox_id,
            )

    async def apply_patch(
        self,
        patch: str,
        strip: int = 1,
        target_dir: str = ".",
        dry_run: bool = False,
    ) -> PatchResult:
        """
        应用 patch 文件

        Args:
            patch: patch 内容
            strip: 去除路径前缀的层数
            target_dir: 目标目录
            dry_run: 是否只检查不实际应用

        Returns:
            Patch 应用结果
        """
        logger.info(f"沙箱 {self._sandbox_id}: 应用 patch, target={target_dir}")

        # 将 patch 内容写入临时文件
        patch_file = "/tmp/patch.diff"
        await self.write_file(patch_file, patch)

        # 构建 patch 命令
        cmd_parts = [f"cd {target_dir} && patch -p{strip}"]

        if dry_run:
            cmd_parts.append("--dry-run")

        cmd_parts.append(f"< {patch_file}")

        command = " ".join(cmd_parts)

        result = await self._execute(command, timeout=60, check=False)

        # 解析结果
        applied_files: list[str] = []
        failed_files: list[str] = []

        for line in result.stdout.split("\n"):
            if "patching file" in line:
                file_name = line.replace("patching file", "").strip().strip("'\"")
                applied_files.append(file_name)
            elif "FAILED" in line:
                # 尝试提取文件名
                parts = line.split()
                for part in parts:
                    if "/" in part or "." in part:
                        failed_files.append(part)
                        break

        success = result.success and not failed_files

        if not success and not dry_run:
            raise PatchApplyError(
                f"Patch 应用失败: {result.stderr}",
                failed_files,
                self._sandbox_id,
            )

        # 清理临时文件
        await self._execute(f"rm -f {patch_file}", check=False)

        return PatchResult(
            success=success,
            applied_files=applied_files,
            failed_files=failed_files,
            message=result.stdout if success else result.stderr,
        )

    async def apply_diff(
        self,
        file_path: str,
        old_content: str,
        new_content: str,
    ) -> bool:
        """
        通过对比替换文件内容

        Args:
            file_path: 文件路径
            old_content: 原内容
            new_content: 新内容

        Returns:
            是否成功
        """
        # 读取当前文件内容
        try:
            current_content = await self.read_file(file_path)
        except FileNotFoundInSandboxError:
            # 文件不存在，如果 old_content 为空则创建
            if not old_content:
                await self.write_file(file_path, new_content)
                return True
            return False

        # 检查当前内容是否与预期的原内容匹配
        if current_content != old_content:
            logger.warning(
                f"沙箱 {self._sandbox_id}: 文件 {file_path} 内容已变更，无法应用差异"
            )
            return False

        # 写入新内容
        await self.write_file(file_path, new_content)
        return True

    async def search_in_files(
        self,
        pattern: str,
        path: str = ".",
        file_pattern: str | None = None,
        recursive: bool = True,
    ) -> list[tuple[str, int, str]]:
        """
        在文件中搜索内容

        Args:
            pattern: 搜索模式（正则表达式）
            path: 搜索路径
            file_pattern: 文件名模式
            recursive: 是否递归搜索

        Returns:
            匹配结果列表 [(文件路径, 行号, 匹配行内容), ...]
        """
        cmd_parts = ["grep", "-n"]

        if recursive:
            cmd_parts.append("-r")

        cmd_parts.extend([f'"{pattern}"', f'"{path}"'])

        if file_pattern:
            cmd_parts.extend(["--include", f'"{file_pattern}"'])

        command = " ".join(cmd_parts)

        result = await self._execute(command, timeout=60, check=False)

        matches: list[tuple[str, int, str]] = []

        for line in result.stdout.strip().split("\n"):
            if not line:
                continue

            # 格式: file:line:content
            parts = line.split(":", 2)
            if len(parts) >= 3:
                file_path = parts[0]
                try:
                    line_num = int(parts[1])
                except ValueError:
                    continue
                content = parts[2]
                matches.append((file_path, line_num, content))

        return matches

    async def get_file_info(self, path: str) -> FileInfo:
        """
        获取文件详细信息

        Args:
            path: 文件路径

        Returns:
            文件信息
        """
        # 使用 stat 命令获取文件信息
        result = await self._execute(
            f'stat -c "%s %Y" "{path}" && test -d "{path}" && echo "dir" || echo "file"',
            check=False,
        )

        if not result.success:
            raise FileNotFoundInSandboxError(path, self._sandbox_id)

        lines = result.stdout.strip().split("\n")
        if len(lines) >= 2:
            parts = lines[0].split()
            size = int(parts[0]) if parts[0].isdigit() else 0
            try:
                mtime = datetime.fromtimestamp(int(parts[1]))
            except (ValueError, OSError):
                mtime = None
            is_dir = lines[1].strip() == "dir"
        else:
            size = 0
            mtime = None
            is_dir = False

        return FileInfo(
            path=path,
            name=os.path.basename(path),
            is_dir=is_dir,
            size=size,
            modified_at=mtime,
        )
