"""
Trace 上下文管理 - 用于关联整个 workflow 的日志和指标
"""
import uuid
from contextvars import ContextVar
from typing import Optional

# 使用 contextvars 在异步上下文中传递 trace_id
_trace_id_var: ContextVar[Optional[str]] = ContextVar("trace_id", default=None)


def generate_trace_id() -> str:
    """生成新的 trace_id"""
    return str(uuid.uuid4())


def set_trace_id(trace_id: str):
    """设置当前上下文的 trace_id"""
    _trace_id_var.set(trace_id)


def get_trace_id() -> Optional[str]:
    """获取当前上下文的 trace_id"""
    return _trace_id_var.get()
