"""
Trace 上下文管理 - 用于关联整个 workflow 的日志和指标
"""
import uuid
from contextvars import ContextVar
from typing import Optional

# 使用 contextvars 在异步上下文中传递 trace_id
_trace_id_var: ContextVar[Optional[str]] = ContextVar("trace_id", default=None)

# Agent 上下文信息（用于追踪并行 agent 执行）
_agent_context_var: ContextVar[Optional[dict]] = ContextVar("agent_context", default=None)


def generate_trace_id() -> str:
    """生成新的 trace_id"""
    return str(uuid.uuid4())


def set_trace_id(trace_id: str):
    """设置当前上下文的 trace_id"""
    _trace_id_var.set(trace_id)


def get_trace_id() -> Optional[str]:
    """获取当前上下文的 trace_id"""
    return _trace_id_var.get()


def set_agent_context(
    agent_name: str,
    task_id: Optional[str] = None,
    task_description: Optional[str] = None,
):
    """
    设置当前 agent 执行上下文
    
    Args:
        agent_name: Agent 名称（如 omni_explorer, code_agent）
        task_id: 任务 ID（如果由 Planner 调度）
        task_description: 任务描述（简短摘要，用于日志识别）
    """
    context = {
        "agent": agent_name,
    }
    if task_id:
        context["task_id"] = task_id
    if task_description:
        context["task_desc"] = task_description
    
    _agent_context_var.set(context)


def get_agent_context() -> Optional[dict]:
    """获取当前 agent 执行上下文"""
    return _agent_context_var.get()


def clear_agent_context():
    """清除 agent 上下文"""
    _agent_context_var.set(None)
