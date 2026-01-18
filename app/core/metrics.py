"""
指标收集工具 - 输出结构化日志
"""
from typing import Optional
from app.core.logger_config import logger
from app.core.trace_context import get_trace_id


class MetricsCollector:
    """指标收集器（输出结构化日志）"""
    
    @staticmethod
    def log_node_execution(
        node_name: str,
        duration_ms: float,
        success: bool,
        error: Optional[str] = None,
        **extra_fields
    ):
        """记录节点执行指标"""
        logger.info(
            f"Node {node_name} executed",
            extra={
                "type": "node_execution",
                "trace_id": get_trace_id(),
                "node_name": node_name,
                "duration_ms": round(duration_ms, 2),
                "success": success,
                "error": error,
                **extra_fields
            }
        )
    
    @staticmethod
    def log_llm_call(
        model_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        duration_ms: float,
        success: bool = True,
    ):
        """记录 LLM 调用指标"""
        logger.info(
            f"LLM call to {model_name}",
            extra={
                "type": "llm_call",
                "trace_id": get_trace_id(),
                "model_name": model_name,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "duration_ms": round(duration_ms, 2),
                "success": success,
            }
        )
    
    @staticmethod
    def log_workflow_summary(
        issue_iid: int,
        project_path: str,
        total_duration_ms: float,
        executed_nodes: list[str],
        success: bool,
        error: Optional[str] = None,
    ):
        """记录 workflow 总结指标"""
        logger.info(
            f"Workflow completed for Issue #{issue_iid}",
            extra={
                "type": "workflow_summary",
                "trace_id": get_trace_id(),
                "issue_iid": issue_iid,
                "project_path": project_path,
                "total_duration_ms": round(total_duration_ms, 2),
                "node_count": len(executed_nodes),
                "executed_nodes": executed_nodes,
                "success": success,
                "error": error,
            }
        )


# 全局单例
metrics_collector = MetricsCollector()
