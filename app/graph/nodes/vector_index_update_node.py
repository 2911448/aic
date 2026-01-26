"""
Vector Index Update Node - 根据 MR 变更文件更新向量库

对变更文件进行增量索引更新：deleted/added/modified/renamed
"""

import asyncio
import os
from typing import Literal

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command

from app.core.logger_config import logger
from app.core.milvus import milvus_service
from app.graph.state import IssueProcessState
from app.graph.state.node_names import NodeName, ProcessStage
from app.rag.indexer import code_indexer
from app.sandbox.manager import get_sandbox_manager
from app.utils.gitignore_parser import should_ignore_path


class VectorIndexUpdateNode:
    """Vector Index Update 节点 - 根据变更文件更新向量库"""

    def __init__(self, max_concurrent_files: int = 5):
        """
        初始化节点

        Args:
            max_concurrent_files: 最大并发索引文件数
        """
        self.max_concurrent_files = max_concurrent_files
        self.sandbox_manager = get_sandbox_manager()

    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal["sandbox_teardown", "__end__"]]:
        """
        根据变更文件更新向量库

        Args:
            state: 当前工作流状态

        Returns:
            Command 对象，完成后 goto sandbox_teardown -> END
        """
        update_dict = {}

        try:
            # 发送进度事件
            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": NodeName.VECTOR_INDEX_UPDATE.value,
                    "progress": "正在更新向量库索引...",
                    "think_chain_item": {
                        "type": NodeName.VECTOR_INDEX_UPDATE.value,
                        "title": "更新向量库",
                        "desc": "根据变更文件增量更新向量库索引",
                        "urls": [],
                    },
                },
            )

            # 从 state 获取必要信息
            project_info = state.get("project_info", {})
            sandbox_info = state.get("sandbox", {})
            merge_info = state.get("merge", {})
            runtime = state.get("runtime", {})

            sandbox_id = sandbox_info.get("sandbox_id")
            repo_path = sandbox_info.get("repo_path")  # 容器内路径
            ignore_patterns = sandbox_info.get("ignore_patterns", [])
            changed_files = merge_info.get("changed_files", [])

            # 获取项目名称（用于 Milvus 定位）
            project_name = (
                project_info.get("name")
                or project_info.get("path_with_namespace")
                or project_info.get("path")
            )

            if not sandbox_id or not repo_path or not project_name:
                error_msg = f"缺少必要字段: sandbox_id={sandbox_id}, repo_path={repo_path}, project_name={project_name}"
                logger.error(error_msg)

                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "error": error_msg,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.VECTOR_INDEX_UPDATE.value,
                            ],
                            "current_step": NodeName.VECTOR_INDEX_UPDATE.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

            if not changed_files:
                logger.info("没有变更文件需要索引")
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.VECTOR_INDEX_UPDATE.value,
                            ],
                            "current_step": NodeName.VECTOR_INDEX_UPDATE.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

            # 获取宿主机路径（用于读取文件）
            sandbox = await self.sandbox_manager.get_sandbox(sandbox_id)
            container_workspace = sandbox.config.workspace_path
            host_workspace = sandbox.workspace_path

            # 将容器内的 repo_path 转换为宿主机路径
            if repo_path.startswith(container_workspace):
                relative_path = repo_path[len(container_workspace) :].lstrip("/")
                host_repo_path = os.path.join(host_workspace, relative_path)
            else:
                host_repo_path = repo_path

            logger.info(
                f"开始索引更新: {len(changed_files)} 个变更文件, project={project_name}"
            )

            # 使用 Semaphore 限制并发
            semaphore = asyncio.Semaphore(self.max_concurrent_files)

            # 统计结果
            indexed_files = []
            failed_files = []

            # 处理每个变更文件
            tasks = [
                self._process_file_change(
                    file_change=file_change,
                    host_repo_path=host_repo_path,
                    project_name=project_name,
                    ignore_patterns=ignore_patterns,
                    semaphore=semaphore,
                    indexed_files=indexed_files,
                    failed_files=failed_files,
                )
                for file_change in changed_files
            ]

            await asyncio.gather(*tasks, return_exceptions=True)

            logger.info(
                f"索引更新完成: 成功={len(indexed_files)}, 失败={len(failed_files)}"
            )

            # 更新 state
            merge_info_update = {
                **merge_info,
                "indexed_files": indexed_files,
                "failed_files": failed_files,
            }

            update_dict.update(
                {
                    "merge": merge_info_update,
                    "runtime": {
                        **runtime,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.VECTOR_INDEX_UPDATE.value,
                        ],
                        "current_step": NodeName.VECTOR_INDEX_UPDATE.value,
                    },
                }
            )

            # 发送完成事件
            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": NodeName.VECTOR_INDEX_UPDATE.value,
                    "progress": f"向量库更新完成: 成功={len(indexed_files)}, 失败={len(failed_files)}",
                    "think_chain_item": {
                        "type": NodeName.VECTOR_INDEX_UPDATE.value,
                        "title": "更新向量库",
                        "desc": f"索引更新完成: {len(indexed_files)}/{len(changed_files)} 个文件成功",
                        "urls": [],
                    },
                },
            )

            logger.info(
                f"VectorIndexUpdate 完成: 成功={len(indexed_files)}, 失败={len(failed_files)}"
            )

            # 完成后进入 teardown
            return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

        except Exception as e:
            error_msg = f"VectorIndexUpdate 失败: {str(e)}"
            logger.error(error_msg, exc_info=True)

            runtime = state.get("runtime", {})
            update_dict.update(
                {
                    "runtime": {
                        **runtime,
                        "error": error_msg,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.VECTOR_INDEX_UPDATE.value,
                        ],
                        "current_step": NodeName.VECTOR_INDEX_UPDATE.value,
                    },
                }
            )

            # 失败时需要 teardown 清理
            return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

    async def _process_file_change(
        self,
        file_change: dict,
        host_repo_path: str,
        project_name: str,
        ignore_patterns: list[str],
        semaphore: asyncio.Semaphore,
        indexed_files: list[str],
        failed_files: list[dict],
    ) -> None:
        """
        处理单个文件变更（带并发控制）

        Args:
            file_change: 文件变更信息 {"status": str, "path": str, "old_path": Optional[str]}
            host_repo_path: 宿主机仓库根路径
            project_name: 项目名称
            ignore_patterns: ignore 规则列表
            semaphore: 并发控制 Semaphore
            indexed_files: 成功索引的文件列表（会被修改）
            failed_files: 失败文件列表（会被修改）
        """
        async with semaphore:
            try:
                status = file_change.get("status")
                file_path = file_change.get("path")
                old_path = file_change.get("old_path")

                logger.debug(f"处理文件变更: status={status}, path={file_path}")

                # 检查是否应该 ignore
                if should_ignore_path(file_path, ignore_patterns, host_repo_path):
                    logger.debug(f"文件被 ignore 规则跳过: {file_path}")
                    return

                if status == "deleted":
                    # 删除向量库中的记录
                    await self._handle_deleted_file(file_path, project_name)
                    indexed_files.append(file_path)

                elif status in ["added", "modified"]:
                    # 重新索引文件
                    abs_file_path = os.path.join(host_repo_path, file_path)
                    await self._handle_added_or_modified_file(
                        abs_file_path, file_path, project_name, host_repo_path
                    )
                    indexed_files.append(file_path)

                elif status == "renamed":
                    # 先删除旧路径，再索引新路径
                    if old_path:
                        # 检查 old_path 是否被 ignore（如果被 ignore，跳过删除）
                        if not should_ignore_path(
                            old_path, ignore_patterns, host_repo_path
                        ):
                            await self._handle_deleted_file(old_path, project_name)

                    # 索引新路径（已经检查过 new_path 的 ignore 规则）
                    abs_file_path = os.path.join(host_repo_path, file_path)
                    await self._handle_added_or_modified_file(
                        abs_file_path, file_path, project_name, host_repo_path
                    )
                    indexed_files.append(file_path)

                else:
                    logger.warning(f"未知的文件状态: {status}, 文件: {file_path}")

            except Exception as e:
                logger.error(f"处理文件变更失败: {file_path}, 错误: {e}", exc_info=True)
                failed_files.append({"path": file_path, "error": str(e)})

    async def _handle_deleted_file(self, file_path: str, project_name: str) -> None:
        """
        处理删除文件：从 Milvus 删除对应记录

        Args:
            file_path: 文件相对路径
            project_name: 项目名称
        """
        logger.info(f"删除向量库记录: {file_path}")
        deleted_count = milvus_service.delete_by_file_path(file_path, project_name)
        logger.info(f"删除了 {deleted_count} 条记录: {file_path}")

    async def _handle_added_or_modified_file(
        self,
        abs_file_path: str,
        rel_file_path: str,
        project_name: str,
        project_root: str,
    ) -> None:
        """
        处理新增/修改文件：重新索引整个文件（保证行号一致性）

        Args:
            abs_file_path: 文件绝对路径（宿主机）
            rel_file_path: 文件相对路径
            project_name: 项目名称
            project_root: 项目根路径（宿主机）
        """
        logger.info(f"重新索引文件: {rel_file_path}")

        # 检查文件是否存在
        if not os.path.exists(abs_file_path):
            logger.warning(f"文件不存在，跳过索引: {abs_file_path}")
            return

        # 检查是否为文件（而非目录）
        if not os.path.isfile(abs_file_path):
            logger.warning(f"不是文件，跳过索引: {abs_file_path}")
            return

        # 调用 code_indexer.index_file（会自动 upsert：删旧插新）
        snippet_count = await code_indexer.index_file(
            file_path=abs_file_path,
            project_name=project_name,
            project_root=project_root,
        )

        logger.info(f"成功索引文件 {rel_file_path}, 插入/更新 {snippet_count} 个片段")
