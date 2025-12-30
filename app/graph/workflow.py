from typing import TypedDict

from langgraph.graph import StateGraph, START, END


class ChatState(TypedDict):
    """聊天状态"""

    message: str
    response: str


def process_message(state: ChatState) -> ChatState:
    """处理消息节点 - 这里可以接入真实的 LLM"""
    message = state["message"]
    # TODO: 接入真实的 LLM API (如 OpenAI, Anthropic 等)
    # 目前返回一个简单的回显响应作为示例
    response = f"收到你的消息: {message}"
    return {"message": message, "response": response}


def create_chat_graph() -> StateGraph:
    """创建聊天工作流图"""
    # 构建状态图
    graph_builder = StateGraph(ChatState)

    # 添加节点
    graph_builder.add_node("process", process_message)

    # 添加边
    graph_builder.add_edge(START, "process")
    graph_builder.add_edge("process", END)

    # 编译图
    return graph_builder.compile()
