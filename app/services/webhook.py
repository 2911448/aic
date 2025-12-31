"""
GitLab Webhook 服务
处理来自 GitLab 的 webhook 事件
"""

import hmac
from typing import Optional

from app.core.logger_config import logger

from app.config.app_config import app_config
from app.schemas.webhook import GitLabWebhookPayload, WebhookResponse


class WebhookService:
    """GitLab Webhook 服务"""

    def __init__(self):
        self.secret = app_config.gitlab.webhook_secret

    def verify_signature(
        self,
        payload_body: bytes,
        signature_header: Optional[str] = None,
    ) -> bool:
        """
        验证 GitLab webhook 签名

        GitLab 使用 X-Gitlab-Token 头部进行验证
        如果配置了 Secret Token，GitLab 会在请求头中发送该 token

        Args:
            payload_body: 请求体原始字节
            signature_header: X-Gitlab-Token 头部值

        Returns:
            验证是否通过
        """
        if not self.secret:
            logger.warning("未配置 webhook secret，跳过签名验证")
            return True

        if not signature_header:
            logger.error("缺少 X-Gitlab-Token 头部")
            return False

        # GitLab 的 Secret Token 是简单的字符串比较
        is_valid = hmac.compare_digest(signature_header, self.secret)

        if not is_valid:
            logger.error("Webhook 签名验证失败")

        return is_valid

    async def process_webhook(
        self,
        payload: GitLabWebhookPayload,
    ) -> WebhookResponse:
        """
        处理 webhook 事件

        Args:
            payload: GitLab webhook 载荷

        Returns:
            处理响应
        """
        # 处理不同类型的事件
        if payload.object_kind == "issue":
            return await self._process_issue_event(payload)
        elif payload.object_kind == "note":
            return await self._process_note_event(payload)
        else:
            logger.warning(f"暂不支持的事件类型: {payload.object_kind}")
            return WebhookResponse(
                status="success",
                message=f"收到事件 {payload.object_kind}，暂不处理",
                event_type=payload.object_kind,
            )

    async def _process_issue_event(
        self,
        payload: GitLabWebhookPayload,
    ) -> WebhookResponse:
        """
        处理 Issue 事件

        Args:
            payload: GitLab webhook 载荷

        Returns:
            处理响应
        """
        issue = payload.object_attributes
        project = payload.project
        issue_state = issue.get("state")
        issue_iid = issue.get("iid")

        logger.info(
            f"处理 Issue 事件: "
            f"#{issue_iid} - {issue.get('title')} "
            f"(状态: {issue_state})"
        )

        # 记录 Issue 详情
        if issue.get("description"):
            logger.info(f"Issue 描述: {issue.get('description')}")

        logger.info(f"项目: {project.path_with_namespace}")

        # 过滤：只处理 opened 状态的 Issue
        if issue_state != "opened":
            logger.info(f"Issue 状态为 {issue_state}，跳过处理")
            return WebhookResponse(
                status="success",
                message=f"Issue #{issue_iid} 状态为 {issue_state}，无需处理",
                event_type="issue",
                issue_iid=issue_iid,
                issue_title=issue.get("title"),
                project_path=project.path_with_namespace,
            )

        # 检查是否是 Issue 关闭事件
        if payload.changes and "state" in payload.changes:
            state_change = payload.changes["state"]
            if state_change.get("current") == "closed":
                logger.info("Issue 已关闭，跳过处理")
                return WebhookResponse(
                    status="success",
                    message=f"Issue #{issue_iid} 已关闭，无需处理",
                    event_type="issue",
                    issue_iid=issue_iid,
                    issue_title=issue.get("title"),
                    project_path=project.path_with_namespace,
                )

        # TODO: 这里后续会添加 AI Agent 处理逻辑
        # 1. 解析 Issue 内容
        # 2. 通过 RAG 检索相关代码
        # 3. 定位问题代码
        # 4. 生成修复方案
        # 5. 创建 Merge Request

        logger.info(f"✅ Issue #{issue_iid} 待处理（状态: opened）")
        return WebhookResponse(
            status="success",
            message=f"Issue #{issue_iid} 事件已接收，等待 AI Agent 处理",
            event_type="issue",
            issue_iid=issue_iid,
            issue_title=issue.get("title"),
            project_path=project.path_with_namespace,
        )

    async def _process_note_event(
        self,
        payload: GitLabWebhookPayload,
    ) -> WebhookResponse:
        """
        处理 Note (评论) 事件

        Args:
            payload: GitLab webhook 载荷

        Returns:
            处理响应
        """
        note = payload.object_attributes
        project = payload.project
        user = payload.user

        logger.info(f"处理 Note 类型: {note.get('noteable_type')}")
        logger.info(f"评论内容: {note.get('note', '')}")
        logger.info(f"项目: {project.path_with_namespace}")

        # 如果是 Issue 的评论，记录 Issue 信息
        if note.get("noteable_type") == "Issue" and payload.issue:
            issue = payload.issue
            logger.info(
                f"关联 Issue: #{issue.get('iid')} - {issue.get('title')}"
            )

        # TODO: 后续可以通过评论触发特定操作
        # 例如：评论 "/fix" 触发自动修复

        return WebhookResponse(
            status="success",
            message=f"Note 事件已接收，暂不处理",
            event_type="note",
            project_path=project.path_with_namespace,
        )


# 全局服务实例
webhook_service = WebhookService()

