"""
GitLab API 服务

提供 GitLab API 交互功能，包括创建 Merge Request 等
"""

import httpx
from loguru import logger
from pydantic import BaseModel, Field

from app.config.app_config import app_config
from app.decorators.retry import async_retry


class MergeRequestResult(BaseModel):
    """Merge Request 创建结果"""

    success: bool = Field(description="是否成功")
    mr_iid: int | None = Field(default=None, description="MR IID")
    mr_url: str | None = Field(default=None, description="MR URL")
    error: str | None = Field(default=None, description="错误信息")


class GitLabService:
    """GitLab API 服务类"""

    def __init__(
        self,
        gitlab_url: str,
        private_token: str,
        verify_ssl: bool = True,
    ):
        """
        初始化 GitLab 服务

        Args:
            gitlab_url: GitLab 实例 URL (e.g., https://gitlab.com)
            private_token: GitLab Personal Access Token
            verify_ssl: 是否验证 SSL 证书
        """
        self.gitlab_url = gitlab_url.rstrip("/")
        self.private_token = private_token
        self.verify_ssl = verify_ssl
        # 使用配置中的超时参数
        timeout = app_config.gitlab.timeout if hasattr(app_config.gitlab, 'timeout') else 30
        self._client = httpx.AsyncClient(
            headers={
                "PRIVATE-TOKEN": private_token,
                "Content-Type": "application/json",
            },
            verify=verify_ssl,
            timeout=float(timeout),
        )

    @async_retry(
        max_retries=app_config.gitlab.max_retries if hasattr(app_config.gitlab, 'max_retries') else 3,
        retriable_exceptions=(httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError)
    )
    async def create_merge_request(
        self,
        project_id: int,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str | None = None,
        labels: list[str] | None = None,
        assignee_ids: list[int] | None = None,
        remove_source_branch: bool = True,
    ) -> MergeRequestResult:
        """
        创建 Merge Request（自动重试网络错误和超时）

        Args:
            project_id: 项目 ID
            source_branch: 源分支
            target_branch: 目标分支
            title: MR 标题
            description: MR 描述
            labels: 标签列表
            assignee_ids: 指派人 ID 列表
            remove_source_branch: 合并后是否删除源分支

        Returns:
            MergeRequestResult
        """
        logger.info(
            f"创建 Merge Request: {source_branch} -> {target_branch} (项目 {project_id})"
        )

        url = f"{self.gitlab_url}/api/v4/projects/{project_id}/merge_requests"

        payload = {
            "source_branch": source_branch,
            "target_branch": target_branch,
            "title": title,
            "remove_source_branch": remove_source_branch,
        }

        if description:
            payload["description"] = description

        if labels:
            payload["labels"] = ",".join(labels)

        if assignee_ids:
            payload["assignee_ids"] = assignee_ids

        try:
            response = await self._client.post(url, json=payload)

            if response.status_code == 201:
                data = response.json()
                mr_iid = data.get("iid")
                mr_url = data.get("web_url")

                logger.info(f"Merge Request 创建成功: {mr_url}")

                return MergeRequestResult(
                    success=True,
                    mr_iid=mr_iid,
                    mr_url=mr_url,
                )
            else:
                error_msg = f"创建 MR 失败: HTTP {response.status_code}"
                try:
                    error_data = response.json()
                    error_msg += f", {error_data.get('message', '')}"
                except Exception:
                    error_msg += f", {response.text}"

                logger.error(error_msg)

                return MergeRequestResult(
                    success=False,
                    error=error_msg,
                )

        except httpx.HTTPStatusError as e:
            # 4xx 错误不重试，直接返回失败
            if 400 <= e.response.status_code < 500:
                error_msg = f"GitLab API 请求失败 (4xx): {str(e)}"
                logger.error(error_msg)
                return MergeRequestResult(
                    success=False,
                    error=error_msg,
                )
            # 5xx 会被重试
            raise
        except httpx.HTTPError as e:
            error_msg = f"GitLab API 请求失败: {str(e)}"
            logger.error(error_msg)
            return MergeRequestResult(
                success=False,
                error=error_msg,
            )

    @async_retry(
        max_retries=app_config.gitlab.max_retries if hasattr(app_config.gitlab, 'max_retries') else 3,
        retriable_exceptions=(httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError)
    )
    async def get_merge_request_changes(
        self,
        project_id: int,
        mr_iid: int,
    ) -> list[dict]:
        """
        获取 Merge Request 的变更文件列表（自动重试网络错误和超时）

        Args:
            project_id: 项目 ID
            mr_iid: MR IID (项目内唯一 ID)

        Returns:
            变更文件列表，每项包含：
            - status: "added" | "modified" | "deleted" | "renamed"
            - path: 文件路径（新路径）
            - old_path: 旧路径（仅 renamed 时有值）
        """
        logger.info(f"获取 MR 变更文件: 项目 {project_id}, MR !{mr_iid}")

        url = f"{self.gitlab_url}/api/v4/projects/{project_id}/merge_requests/{mr_iid}/changes"

        try:
            response = await self._client.get(url)

            if response.status_code == 200:
                data = response.json()
                changes = data.get("changes", [])

                # 转换为统一格式
                changed_files = []
                for change in changes:
                    new_path = change.get("new_path")
                    old_path = change.get("old_path")
                    new_file = change.get("new_file", False)
                    deleted_file = change.get("deleted_file", False)
                    renamed_file = change.get("renamed_file", False)

                    # 判断状态
                    if deleted_file:
                        status = "deleted"
                        file_path = old_path
                    elif renamed_file:
                        status = "renamed"
                        file_path = new_path
                    elif new_file:
                        status = "added"
                        file_path = new_path
                    else:
                        status = "modified"
                        file_path = new_path

                    file_change = {
                        "status": status,
                        "path": file_path,
                        "new_path": new_path,
                    }

                    if renamed_file and old_path != new_path:
                        file_change["old_path"] = old_path

                    changed_files.append(file_change)

                logger.info(f"成功获取 {len(changed_files)} 个变更文件")
                return changed_files

            else:
                error_msg = f"获取 MR 变更失败: HTTP {response.status_code}"
                try:
                    error_data = response.json()
                    error_msg += f", {error_data.get('message', '')}"
                except Exception:
                    error_msg += f", {response.text}"

                logger.error(error_msg)
                return []

        except httpx.HTTPStatusError as e:
            # 4xx 错误不重试
            if 400 <= e.response.status_code < 500:
                logger.error(f"GitLab API 请求失败 (4xx): {str(e)}")
                return []
            # 5xx 会被重试
            raise
        except httpx.HTTPError as e:
            logger.error(f"GitLab API 请求失败: {str(e)}")
            return []

    async def close(self):
        """关闭 HTTP 客户端"""
        await self._client.aclose()

