"""
重试装饰器 - 支持指数退避的异步重试机制
"""
import asyncio
from functools import wraps
from typing import Callable, Type
from app.core.logger_config import logger


def async_retry(
    max_retries: int = 3,
    backoff_base: float = 2.0,
    max_backoff: float = 10.0,
    retriable_exceptions: tuple[Type[Exception], ...] = (Exception,),
):
    """异步重试装饰器
    
    Args:
        max_retries: 最大重试次数
        backoff_base: 退避基数（秒）
        max_backoff: 最大退避时间（秒）
        retriable_exceptions: 可重试的异常类型
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except retriable_exceptions as e:
                    last_exception = e
                    
                    if attempt < max_retries:
                        wait_time = min(backoff_base ** attempt, max_backoff)
                        logger.warning(
                            f"{func.__name__} 失败（尝试 {attempt + 1}/{max_retries + 1}），"
                            f"{wait_time:.1f}s 后重试: {e}"
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(
                            f"{func.__name__} 重试失败（已达最大次数 {max_retries}）"
                        )
            
            raise last_exception
        
        return wrapper
    return decorator
