from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """健康检查响应"""

    status: str = Field(..., description="服务状态")
    message: str = Field(..., description="状态消息")


class ChatRequest(BaseModel):
    """聊天请求"""

    message: str = Field(..., description="用户消息", min_length=1, max_length=4096)


class ChatResponse(BaseModel):
    """聊天响应"""

    message: str = Field(..., description="AI 响应消息")

