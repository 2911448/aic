"""
Opik tracing helper for LangGraph workflows.

Centralizes the duplicated "enable Opik if configured" logic.
"""

from __future__ import annotations

import os
from typing import Any

from app.config.app_config import app_config
from app.core.logger_config import logger


def track_langgraph_workflow_with_opik(
    workflow: Any,
    *,
    workflow_label: str,
    tags: list[str],
    metadata: dict[str, Any] | None = None,
) -> Any:
    """
    Conditionally wrap a compiled LangGraph workflow with Opik tracing.

    - If Opik is not configured / disabled, returns the workflow unchanged.
    - If Opik import/init fails, logs a warning and returns the workflow unchanged.
    """

    opik_config = app_config.opik
    opik_disabled = os.getenv("OPIK_TRACK_DISABLE", "").lower() in ("true", "1", "yes")

    if not opik_config or not opik_config.enabled or opik_disabled:
        if opik_disabled:
            logger.info(
                f"Opik tracing 已通过 OPIK_TRACK_DISABLE 环境变量禁用 ({workflow_label})"
            )
        else:
            logger.info(f"Opik tracing 未配置或已在配置中禁用 ({workflow_label})")
        return workflow

    try:
        from opik.integrations.langchain import OpikTracer, track_langgraph

        # Configure Opik env vars (if not already set).
        if not os.getenv("OPIK_API_KEY"):
            os.environ["OPIK_API_KEY"] = opik_config.api_key
        if not os.getenv("OPIK_WORKSPACE"):
            os.environ["OPIK_WORKSPACE"] = opik_config.workspace
        if not os.getenv("OPIK_PROJECT_NAME"):
            os.environ["OPIK_PROJECT_NAME"] = opik_config.project_name

        opik_tracer = OpikTracer(
            project_name=opik_config.project_name,
            tags=tags,
            metadata=metadata or {},
        )
        tracked = track_langgraph(workflow, opik_tracer)
        logger.info(f"Opik tracing 已启用 ({workflow_label})")
        return tracked
    except Exception as e:
        logger.warning(
            f"Opik tracing 初始化失败（将继续运行但不记录 traces）({workflow_label}): {e}"
        )
        return workflow

