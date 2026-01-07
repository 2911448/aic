"""
GitLab API 服务

提供 GitLab API 交互功能，包括创建 Merge Request 等
"""

import httpx
from loguru import logger
from pydantic import BaseModel, Field


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
        self._client = httpx.AsyncClient(
            headers={
                "PRIVATE-TOKEN": private_token,
                "Content-Type": "application/json",
            },
            verify=verify_ssl,
            timeout=30.0,
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
        创建 Merge Request

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

        except httpx.HTTPError as e:
            error_msg = f"GitLab API 请求失败: {str(e)}"
            logger.error(error_msg)
            return MergeRequestResult(
                success=False,
                error=error_msg,
            )

    async def get_project_info(self, project_id: int) -> dict | None:
        """
        获取项目信息

        Args:
            project_id: 项目 ID

        Returns:
            项目信息字典，失败返回 None
        """
        url = f"{self.gitlab_url}/api/v4/projects/{project_id}"

        try:
            response = await self._client.get(url)

            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"获取项目信息失败: HTTP {response.status_code}")
                return None

        except httpx.HTTPError as e:
            logger.error(f"GitLab API 请求失败: {str(e)}")
            return None

    async def close(self):
        """关闭 HTTP 客户端"""
        await self._client.aclose()

