"""
run_agent_core - Agent 调度核心函数

设计原则：
- 调度权在 LLM，系统仅执行不决策
- 支持并行调度多个子任务
- 结果合并写回 state，按约定处理冲突
"""

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from app.core.logger_config import logger
from app.graph.planning.agent_registry import agent_registry


class ParallelTask(BaseModel):
    """并行子任务定义（支持异构 agent 并行）"""
    task_id: str = Field(description="子任务唯一标识")
    agent: str = Field(description="Agent 名称 (omni_explorer/code_agent/verification/mr_publisher)")
    task: str = Field(description="子任务描述")
    allowed_files: list[str] = Field(default_factory=list, description="允许修改的文件（仅 code_agent）")


async def _execute_agent(
    agent: str,
    task: str,
    state: dict[str, Any],
    task_id: str | None = None,
    **kwargs
) -> dict[str, Any]:
    """
    执行单个 agent
    
    Args:
        agent: Agent 名称
        task: 任务描述
        state: 当前 state（只读）
        task_id: 任务 ID（用于日志追踪）
        **kwargs: 额外参数（如 allowed_files）
    
    Returns:
        执行结果（包含 state 更新）
    """
    from app.core.trace_context import set_agent_context, clear_agent_context
    
    # 检查 agent 是否已注册且启用
    agent_card = agent_registry.get(agent)
    if not agent_card:
        raise ValueError(f"Agent '{agent}' 未注册")
    if not agent_card.enabled:
        raise ValueError(f"Agent '{agent}' 未启用")
    
    # 设置 agent 上下文（用于日志追踪）
    set_agent_context(
        agent_name=agent,
        task_id=task_id,
        task_description=task,
    )
    
    try:
        logger.info(f"[run_agent] 开始执行 agent 任务: {task}")
        
        # 根据 agent 类型分发到对应的执行器
        if agent == "omni_explorer":
            from app.graph.nodes.omni_explorer_node import execute_omni_explorer
            return await execute_omni_explorer(state, task)
        
        elif agent == "code_agent":
            from app.graph.nodes.code_agent_node import execute_code_agent
            allowed_files = kwargs.get("allowed_files", [])
            return await execute_code_agent(state, task, allowed_files)
        
        elif agent == "verification":
            from app.graph.nodes.verification_node import execute_verification
            return await execute_verification(state)
        
        elif agent == "mr_publisher":
            from app.graph.nodes.mr_publisher_agent_node import execute_mr_publisher
            return await execute_mr_publisher(state)
        
        else:
            raise ValueError(f"未知的 agent 类型: {agent}")
    
    finally:
        # 清除 agent 上下文
        clear_agent_context()


async def run_agent_core(
    agent: str,
    task: str,
    state: dict[str, Any],
    allowed_files: list[str] | None = None,
    parallel_tasks: list[dict] | None = None,
) -> dict[str, Any]:
    """
    run_agent 核心执行函数
    
    Args:
        agent: Agent 名称（单任务时使用，omni_explorer/code_agent/verification/mr_publisher）
        task: 任务描述（单任务时使用）
        state: 当前 state
        allowed_files: 允许修改的文件（单任务 + code_agent 时使用）
        parallel_tasks: 并行子任务列表（每个子任务可以是不同 agent）
    
    Returns:
        包含 state 更新的字典（包含 __execution_items__ 供 TaskRunner 汇总）
    """
    try:
        # 如果有并行任务，执行并行调度（支持异构 agent）
        if parallel_tasks:
            logger.info(f"[run_agent] 并行调度 {len(parallel_tasks)} 个子任务")
            tasks_coros = []
            for pt_dict in parallel_tasks:
                pt = ParallelTask(**pt_dict)
                tasks_coros.append(_execute_agent(
                    agent=pt.agent,  # 使用子任务自己的 agent
                    task=pt.task,
                    state=state,
                    task_id=pt.task_id,  # 传递 task_id 用于日志追踪
                    allowed_files=pt.allowed_files,
                ))
            
            # 并行执行
            results = await asyncio.gather(*tasks_coros, return_exceptions=True)
            
            # 合并结果（按 agent 类型分域合并）
            merged_update = {}
            all_patches = []
            omni_explorer_results = []  # 收集 omni_explorer 结果列表
            execution_items = []  # 收集执行条目
            
            for idx, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"并行任务 {idx} 失败: {result}")
                    continue
                
                # 提取并收集 __execution__ 元数据
                pt_dict = parallel_tasks[idx]
                execution_meta = result.pop("__execution__", None)
                if execution_meta:
                    execution_items.append({
                        "agent": pt_dict["agent"],
                        "task": pt_dict["task"],
                        "task_id": pt_dict["task_id"],
                        "reasoning": execution_meta.get("reasoning", ""),
                        "result_hint": execution_meta.get("result_hint", {}),
                    })
                
                # 提取并合并 patching（code_agent）
                patching_update = result.get("patching", {})
                patches = patching_update.get("patches", [])
                all_patches.extend(patches)
                
                # 提取并合并 analysis（omni_explorer）
                analysis_update = result.get("analysis", {})
                if "omni_explorer" in analysis_update:
                    task_id = pt_dict["task_id"]
                    omni_explorer_results.append({
                        "task_id": task_id,
                        "result": analysis_update["omni_explorer"],
                    })
                
                # 其他单值域（verification/delivery）直接覆盖
                for key in ["verification", "delivery"]:
                    if key in result:
                        merged_update[key] = result[key]
            
            # 合并写回
            if all_patches:
                existing_patches = state.get("patching", {}).get("patches", [])
                merged_update["patching"] = {
                    "patches": existing_patches + all_patches,
                }
            
            if omni_explorer_results:
                existing_analysis = state.get("analysis", {})
                # 合并到 omni_explorer 列表（每个专家的结果都保留）
                existing_omni_explorer = existing_analysis.get("omni_explorer", [])
                if not isinstance(existing_omni_explorer, list):
                    existing_omni_explorer = []
                
                merged_update["analysis"] = {
                    **existing_analysis,
                    "omni_explorer": existing_omni_explorer + omni_explorer_results,
                }
            
            # 附加执行条目（供 TaskRunner 汇总）
            merged_update["__execution_items__"] = execution_items
            
            return merged_update
        
        # 单任务执行
        else:
            task_id = "single_task"
            result = await _execute_agent(
                agent=agent,
                task=task,
                state=state,
                task_id=task_id,
                allowed_files=allowed_files or [],
            )
            
            # 提取并收集 __execution__ 元数据
            execution_meta = result.pop("__execution__", None)
            execution_items = []
            if execution_meta:
                execution_items.append({
                    "agent": agent,
                    "task": task,
                    "task_id": task_id,
                    "reasoning": execution_meta.get("reasoning", ""),
                    "result_hint": execution_meta.get("result_hint", {}),
                })
            
            # 对于 omni_explorer，也需要添加到列表中
            if agent == "omni_explorer" and "analysis" in result:
                analysis_update = result.get("analysis", {})
                if "omni_explorer" in analysis_update:
                    omni_result = analysis_update["omni_explorer"]
                    # 转换为列表格式
                    existing_analysis = state.get("analysis", {})
                    existing_omni_explorer = existing_analysis.get("omni_explorer", [])
                    if not isinstance(existing_omni_explorer, list):
                        existing_omni_explorer = []
                    
                    result["analysis"] = {
                        **analysis_update,
                        "omni_explorer": existing_omni_explorer + [{
                            "task_id": task_id,
                            "result": omni_result,
                        }],
                    }
            
            # 附加执行条目（供 TaskRunner 汇总）
            result["__execution_items__"] = execution_items
            
            return result
    
    except Exception as e:
        logger.error(f"[run_agent] 执行失败: {e}", exc_info=True)
        raise


# 导出
__all__ = ["run_agent_core"]
