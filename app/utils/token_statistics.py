"""
Token统计工具
用于在workflow中追踪和展示token使用情况
"""

from typing import Dict, Any
from app.core.logger_config import logger


class WorkflowTokenTracker:
    """工作流Token追踪器"""

    def __init__(self):
        """初始化追踪器"""
        self.node_token_usage = {}  # 每个节点的token使用情况
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_tokens = 0
        self.llm_call_count = 0

    def record_node_usage(
        self, node_name: str, prompt_tokens: int, completion_tokens: int
    ) -> None:
        """
        记录节点的token使用情况

        Args:
            node_name: 节点名称
            prompt_tokens: 输入token数
            completion_tokens: 输出token数
        """
        total = prompt_tokens + completion_tokens

        if node_name not in self.node_token_usage:
            self.node_token_usage[node_name] = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "call_count": 0,
            }

        self.node_token_usage[node_name]["prompt_tokens"] += prompt_tokens
        self.node_token_usage[node_name]["completion_tokens"] += completion_tokens
        self.node_token_usage[node_name]["total_tokens"] += total
        self.node_token_usage[node_name]["call_count"] += 1

        self.total_prompt_tokens += prompt_tokens
        self.total_completion_tokens += completion_tokens
        self.total_tokens += total
        self.llm_call_count += 1

        logger.info(
            f"节点 {node_name} LLM调用 - "
            f"Prompt: {prompt_tokens}, Completion: {completion_tokens}, "
            f"Total: {total}",
            extra={
                "node_name": node_name,
                "token_usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total,
                },
            },
        )

    def get_summary(self) -> Dict[str, Any]:
        """
        获取汇总统计信息

        Returns:
            统计信息字典
        """
        return {
            "total_llm_calls": self.llm_call_count,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_tokens,
            "average_tokens_per_call": (
                round(self.total_tokens / self.llm_call_count, 2)
                if self.llm_call_count > 0
                else 0
            ),
            "nodes_breakdown": self.node_token_usage,
        }

    def log_summary(self) -> None:
        """记录统计摘要到日志"""
        summary = self.get_summary()

        logger.info(
            f"工作流Token使用统计 - "
            f"总调用: {summary['total_llm_calls']}, "
            f"总Token: {summary['total_tokens']} "
            f"(Prompt: {summary['total_prompt_tokens']}, "
            f"Completion: {summary['total_completion_tokens']})",
            extra={"workflow_token_summary": summary},
        )

        # 记录各节点详细使用情况
        for node_name, usage in self.node_token_usage.items():
            logger.info(
                f"  - {node_name}: {usage['total_tokens']} tokens "
                f"({usage['call_count']} 次调用)",
                extra={"node_token_detail": {node_name: usage}},
            )

    def reset(self) -> None:
        """重置统计"""
        self.node_token_usage = {}
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_tokens = 0
        self.llm_call_count = 0


__all__ = ["WorkflowTokenTracker"]

