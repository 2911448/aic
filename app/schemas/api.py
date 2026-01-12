"""
API 相关的数据模型
"""

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """健康检查响应"""

    status: str = Field(..., description="服务状态")
    message: str = Field(..., description="状态消息")

