from fastapi import APIRouter

from app.schemas.api import HealthResponse, ChatRequest, ChatResponse
from app.services.chat import chat_service

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """健康检查接口"""
    return HealthResponse(status="healthy", message="Service is running")


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """聊天接口 - 使用 LangGraph 工作流处理消息"""
    response = await chat_service.process(request.message)
    return ChatResponse(message=response)
