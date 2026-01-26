"""
Merge Diff Collector Node - 获取 MR 变更文件列表

从 GitLab API 获取已合并 MR 的变更文件列表
"""

from typing import Literal

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command

from app.config.app_config import app_config
from app.core.logger_config import logger
from app.graph.state import IssueProcessState
from app.graph.state.node_names import NodeName, ProcessStage
from app.services.gitlab_service import GitLabService


class MergeDiffCollectorNode:
    """Merge Diff Collector 节点 - 获取 MR 变更文件列表"""

    def __init__(self):
        """初始化节点"""
        pass

    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal["vector_index_update", "sandbox_teardown", "__end__"]]:
        """
        获取 MR 变更文件列表

        Args:
            state: 当前工作流状态

        Returns:
            Command 对象，成功则 goto vector_index_update，失败则 goto sandbox_teardown -> END
        """
        update_dict = {}

        try:
            # 发送进度事件
            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": NodeName.MERGE_DIFF_COLLECTOR.value,
                    "progress": "正在获取 Merge Request 变更文件列表...",
                    "think_chain_item": {
                        "type": NodeName.MERGE_DIFF_COLLECTOR.value,
                        "title": "获取变更文件",
                        "desc": "从 GitLab API 获取 MR 变更文件列表",
                        "urls": [],
                    },
                },
            )

            # 从 state 获取必要信息
            project_info = state.get("project_info", {})
            merge_info = state.get("merge", {})

            project_id = project_info.get("id")
            mr_iid = merge_info.get("mr_iid")

            if not project_id or not mr_iid:
                error_msg = f"缺少必要字段: project_id={project_id}, mr_iid={mr_iid}"
                logger.error(error_msg)

                runtime = state.get("runtime", {})
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "error": error_msg,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.MERGE_DIFF_COLLECTOR.value,
                            ],
                            "current_step": NodeName.MERGE_DIFF_COLLECTOR.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

            # 从配置获取 GitLab 信息
            gitlab_url = self._extract_gitlab_url(project_info)
            gitlab_token = (
                app_config.sandbox.git_auth.http_token
                if app_config.sandbox.git_auth
                else None
            )

            if not gitlab_token:
                error_msg = "未配置 GitLab Token，无法获取 MR 变更"
                logger.error(error_msg)

                runtime = state.get("runtime", {})
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "error": error_msg,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.MERGE_DIFF_COLLECTOR.value,
                            ],
                            "current_step": NodeName.MERGE_DIFF_COLLECTOR.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

            # 创建 GitLab service
            gitlab_service = GitLabService(
                gitlab_url=gitlab_url,
                private_token=gitlab_token,
                verify_ssl=app_config.gitlab.verify_ssl,
            )

            # 调用 GitLab API 获取变更文件列表
            logger.info(f"获取 MR 变更文件: project_id={project_id}, mr_iid={mr_iid}")
            changed_files = await gitlab_service.get_merge_request_changes(
                project_id=project_id,
                mr_iid=mr_iid,
            )

            await gitlab_service.close()

            logger.info(f"成功获取 {len(changed_files)} 个变更文件")

            # 更新 state
            runtime = state.get("runtime", {})
            merge_info_update = {
                **merge_info,
                "changed_files": changed_files,
            }

            update_dict.update(
                {
                    "merge": merge_info_update,
                    "runtime": {
                        **runtime,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.MERGE_DIFF_COLLECTOR.value,
                        ],
                        "current_step": NodeName.MERGE_DIFF_COLLECTOR.value,
                    },
                }
            )

            # 发送完成事件
            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": NodeName.MERGE_DIFF_COLLECTOR.value,
                    "progress": f"成功获取 {len(changed_files)} 个变更文件",
                    "think_chain_item": {
                        "type": NodeName.MERGE_DIFF_COLLECTOR.value,
                        "title": "获取变更文件",
                        "desc": f"获取到 {len(changed_files)} 个变更文件",
                        "urls": [],
                    },
                },
            )

            logger.info(
                f"MergeDiffCollector 完成: 获取 {len(changed_files)} 个变更文件"
            )

            # 成功后进入索引更新节点
            return Command(update=update_dict, goto="vector_index_update")

        except Exception as e:
            error_msg = f"MergeDiffCollector 失败: {str(e)}"
            logger.error(error_msg, exc_info=True)

            runtime = state.get("runtime", {})
            update_dict.update(
                {
                    "runtime": {
                        **runtime,
                        "error": error_msg,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.MERGE_DIFF_COLLECTOR.value,
                        ],
                        "current_step": NodeName.MERGE_DIFF_COLLECTOR.value,
                    },
                }
            )

            # 失败时需要 teardown 清理
            return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

    def _extract_gitlab_url(self, project_info: dict) -> str:
        """
        从项目信息中提取 GitLab URL

        Args:
            project_info: 项目信息

        Returns:
            GitLab URL
        """
        web_url = project_info.get("web_url", "")
        if not web_url:
            return "https://gitlab.com"

        # 从 web_url 提取基础 URL
        # 例如：https://gitlab.com/group/project -> https://gitlab.com
        try:
            from urllib.parse import urlparse

            parsed = urlparse(web_url)
            return f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            return "https://gitlab.com"
