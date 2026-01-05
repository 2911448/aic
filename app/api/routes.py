from fastapi import APIRouter, Request, Header, HTTPException
from typing import Optional
from app.core.logger_config import logger

from app.schemas.api import HealthResponse, ChatRequest, ChatResponse
from app.schemas.webhook import GitLabWebhookPayload, WebhookResponse
from app.services.webhook import webhook_service

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """健康检查接口"""
    return HealthResponse(status="healthy", message="Service is running")


@router.post("/webhook/gitlab", response_model=WebhookResponse)
async def gitlab_webhook(
    request: Request,
    x_gitlab_token: Optional[str] = Header(None, alias="X-Gitlab-Token"),
    x_gitlab_event: Optional[str] = Header(None, alias="X-Gitlab-Event"),
) -> WebhookResponse:
    """
    GitLab Webhook 接收端点

    接收来自 GitLab 的 webhook 事件，主要处理 Issue 创建/更新事件

    Headers:
        X-Gitlab-Token: GitLab Secret Token（用于验证）
        X-Gitlab-Event: 事件类型（Issue Hook, Merge Request Hook 等）

    Returns:
        WebhookResponse: 处理结果
    """
    logger.info(f"收到 GitLab webhook 请求, 事件类型: {x_gitlab_event}")

    try:
        # 获取原始请求体用于签名验证
        body = await request.body()

        # 验证签名
        if not webhook_service.verify_signature(body, x_gitlab_token):
            logger.error("Webhook 签名验证失败")
            raise HTTPException(status_code=401, detail="Invalid signature")

        # 解析 webhook 载荷
        payload_dict = await request.json()
        payload = GitLabWebhookPayload(**payload_dict)

        # 处理 webhook 事件
        response = await webhook_service.process_webhook(payload)

        logger.info(f"Webhook 处理完成: {response.message}")
        return response

    except ValueError as e:
        logger.error(f"解析 webhook 载荷失败: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid payload: {str(e)}")
    except Exception as e:
        logger.error(f"处理 webhook 失败: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
