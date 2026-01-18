"""
追踪装饰器 - 自动记录函数执行时间和指标
"""
import time
from functools import wraps
from app.core.metrics import metrics_collector


def track_node_metrics(node_name: str):
    """节点指标追踪装饰器
    
    自动记录节点执行时间、成功/失败状态，并输出到结构化日志
    
    Args:
        node_name: 节点名称，用于标识日志中的节点
    
    Example:
        @track_node_metrics("my_node")
        async def my_node_function(state):
            # 节点逻辑
            return result
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            success = True
            error = None
            
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                success = False
                error = str(e)
                raise
            finally:
                duration_ms = (time.time() - start_time) * 1000
                metrics_collector.log_node_execution(
                    node_name=node_name,
                    duration_ms=duration_ms,
                    success=success,
                    error=error,
                )
        
        return wrapper
    return decorator
