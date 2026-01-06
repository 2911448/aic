"""
Token计数器模块
使用tiktoken统计LLM调用的token消耗
"""

from typing import Any, Dict, List, Optional
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult
import tiktoken
from app.core.logger_config import logger


class TokenCounterCallback(AsyncCallbackHandler):
    """异步Token计数回调处理器"""

    def __init__(self, model_name: str = "gpt-4"):
        """
        初始化Token计数器

        Args:
            model_name: 模型名称，用于获取正确的tokenizer
        """
        super().__init__()
        self.model_name = model_name
        self.total_tokens = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

        # 获取对应的encoding
        try:
            self.encoding = tiktoken.encoding_for_model(model_name)
        except KeyError:
            # 如果模型不存在，使用默认的cl100k_base（适用于gpt-4/gpt-3.5-turbo）
            logger.warning(
                f"模型 {model_name} 未找到对应的tokenizer，使用默认的cl100k_base"
            )
            self.encoding = tiktoken.get_encoding("cl100k_base")

    def count_tokens(self, text: str) -> int:
        """
        计算文本的token数量

        Args:
            text: 输入文本

        Returns:
            token数量
        """
        return len(self.encoding.encode(text))

    def count_messages_tokens(self, messages: List[BaseMessage]) -> int:
        """
        计算消息列表的token数量
        参考OpenAI的计算方式：https://platform.openai.com/docs/guides/chat/introduction

        Args:
            messages: 消息列表

        Returns:
            token数量
        """
        num_tokens = 0
        for message in messages:
            # 每条消息固定开销：4个token（role + content的格式化）
            num_tokens += 4
            # 消息内容
            if isinstance(message.content, str):
                num_tokens += self.count_tokens(message.content)
            elif isinstance(message.content, list):
                # 处理多模态消息
                for item in message.content:
                    if isinstance(item, dict) and "text" in item:
                        num_tokens += self.count_tokens(item["text"])
                    elif isinstance(item, str):
                        num_tokens += self.count_tokens(item)

        # 每次对话的额外开销
        num_tokens += 2

        return num_tokens

    async def on_llm_start(
        self, serialized: Dict[str, Any], prompts: List[str], **kwargs: Any
    ) -> None:
        """LLM开始调用时的回调"""
        # 计算prompt tokens
        prompt_tokens = sum(self.count_tokens(prompt) for prompt in prompts)
        self.prompt_tokens = prompt_tokens

        logger.info(
            f"LLM调用开始 - 模型: {self.model_name}, Prompt Tokens: {prompt_tokens}"
        )

    async def on_chat_model_start(
        self,
        serialized: Dict[str, Any],
        messages: List[List[BaseMessage]],
        **kwargs: Any,
    ) -> None:
        """Chat模型开始调用时的回调"""
        # 计算所有消息的tokens
        prompt_tokens = sum(
            self.count_messages_tokens(message_list) for message_list in messages
        )
        self.prompt_tokens = prompt_tokens

    async def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """LLM调用结束时的回调"""
        # 尝试从响应中获取token使用情况
        if hasattr(response, "llm_output") and response.llm_output:
            token_usage = response.llm_output.get("token_usage", {})
            if token_usage:
                self.prompt_tokens = token_usage.get("prompt_tokens", 0)
                self.completion_tokens = token_usage.get("completion_tokens", 0)
                self.total_tokens = token_usage.get("total_tokens", 0)
            else:
                # 如果没有token_usage信息，手动计算
                completion_tokens = 0
                for generations in response.generations:
                    for generation in generations:
                        if hasattr(generation, "text"):
                            completion_tokens += self.count_tokens(generation.text)
                        elif hasattr(generation, "message"):
                            content = generation.message.content
                            if isinstance(content, str):
                                completion_tokens += self.count_tokens(content)

                self.completion_tokens = completion_tokens
                self.total_tokens = self.prompt_tokens + self.completion_tokens
        else:
            # 手动计算completion tokens
            completion_tokens = 0
            for generations in response.generations:
                for generation in generations:
                    if hasattr(generation, "text"):
                        completion_tokens += self.count_tokens(generation.text)
                    elif hasattr(generation, "message"):
                        content = generation.message.content
                        if isinstance(content, str):
                            completion_tokens += self.count_tokens(content)

            self.completion_tokens = completion_tokens
            self.total_tokens = self.prompt_tokens + self.completion_tokens

        # 记录详细的token使用情况
        logger.info(
            f"LLM调用完成 - 模型: {self.model_name}, Prompt Tokens: {self.prompt_tokens}, Total Tokens: {self.total_tokens}",
        )

    async def on_llm_error(
        self, error: Exception, **kwargs: Any
    ) -> None:
        """LLM调用出错时的回调"""
        logger.error(
            f"LLM调用出错 - 模型: {self.model_name}, 错误: {error}"
        )

    def get_token_usage(self) -> Dict[str, int]:
        """
        获取当前的token使用情况

        Returns:
            包含token使用信息的字典
        """
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "model": self.model_name,
        }

    def reset(self) -> None:
        """重置计数器"""
        self.total_tokens = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0


class TokenCounter:
    """Token计数器管理类，用于追踪整个会话的token使用"""

    def __init__(self):
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_tokens = 0
        self.call_count = 0

    def add_usage(self, usage: Dict[str, int]) -> None:
        """
        添加一次调用的token使用情况

        Args:
            usage: token使用情况字典
        """
        self.total_prompt_tokens += usage.get("prompt_tokens", 0)
        self.total_completion_tokens += usage.get("completion_tokens", 0)
        self.total_tokens += usage.get("total_tokens", 0)
        self.call_count += 1

    def get_summary(self) -> Dict[str, Any]:
        """
        获取总体的token使用统计

        Returns:
            统计信息字典
        """
        return {
            "total_calls": self.call_count,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_tokens,
            "average_tokens_per_call": (
                self.total_tokens / self.call_count if self.call_count > 0 else 0
            ),
        }

    def reset(self) -> None:
        """重置统计"""
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_tokens = 0
        self.call_count = 0


# 全局token计数器实例
global_token_counter = TokenCounter()


__all__ = ["TokenCounterCallback", "TokenCounter", "global_token_counter"]

