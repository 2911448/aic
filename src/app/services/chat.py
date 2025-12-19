from src.app.graph.workflow import create_chat_graph


class ChatService:
    """聊天服务 - 封装 LangGraph 工作流"""

    def __init__(self) -> None:
        self.graph = create_chat_graph()

    async def process(self, message: str) -> str:
        """处理用户消息并返回响应"""
        result = await self.graph.ainvoke({"message": message})
        return result["response"]


chat_service = ChatService()

