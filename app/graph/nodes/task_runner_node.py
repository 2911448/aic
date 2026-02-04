"""
TaskRunner Node - 任务执行节点

职责：
- 从 planning.next_tasks 读取任务
- 调用 run_agent_core 执行（支持单任务或并行任务）
- 合并结果回写 state
- 记录执行历史到 orchestration_history
- 调用 summary 工具生成执行摘要并写入 execution_history
"""

from typing import Literal
import time

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command

from app.core.logger_config import logger
from app.core.trace_context import set_trace_id
from app.decorators.tracking import track_node_metrics
from app.graph.state import IssueProcessState, NodeName, ProcessStage
from app.tools.orchestration.run_agent import run_agent_core
from app.tools.analysis.execution_summary import summarize_execution_core


class TaskRunnerNode:
    """
    任务执行节点（系统派发，不依赖 LLM）
    
    功能：
    - 读取 planning.next_tasks
    - 执行单任务或并行任务
    - 合并结果并记录历史
    """
    
    def __init__(self):
        """初始化节点"""
        pass
    
    @track_node_metrics("task_runner")
    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal["planner_orchestrator", "sandbox_teardown"]]:
        """
        执行任务
        
        Args:
            state: 当前工作流状态
        
        Returns:
            Command 对象，执行完成返回 planner_orchestrator
        """
        # 从 state 恢复 trace_id 到上下文
        trace_id = state.get("runtime", {}).get("trace_id")
        if trace_id:
            set_trace_id(trace_id)
        
        update_dict = {}
        
        try:
            # 从 planning 读取待执行任务
            planning = state.get("planning", {})
            next_tasks = planning.get("next_tasks", [])
            
            if not next_tasks:
                error_msg = "TaskRunner: planning.next_tasks 为空，无任务可执行"
                logger.error(error_msg)
                runtime = state.get("runtime", {})
                update_dict.update({
                    "runtime": {
                        **runtime,
                        "error": error_msg,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.TASK_RUNNER.value,
                        ],
                        "current_step": NodeName.TASK_RUNNER.value,
                    },
                })
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)
            
            # 发送进度事件
            task_count = len(next_tasks)
            mode = "并行" if task_count > 1 else "单任务"
            await adispatch_custom_event(
                ProcessStage.PLANNING.value,
                {
                    "status": NodeName.TASK_RUNNER.value,
                    "progress": f"执行 {mode}（{task_count} 个任务）...",
                    "think_chain_item": {
                        "type": NodeName.TASK_RUNNER.value,
                        "title": "TaskRunner",
                        "desc": f"{mode}执行：{', '.join(t.get('agent', '?') for t in next_tasks)}",
                        "urls": [],
                    },
                },
            )
            
            # 记录开始时间
            start_time = time.time()
            
            # 执行任务（单任务 or 并行）
            logger.info(f"[TaskRunner] 开始执行 {task_count} 个任务（{mode}）")
            
            if task_count == 1:
                # 单任务执行
                task = next_tasks[0]
                result_update = await run_agent_core(
                    agent=task.get("agent"),
                    task=task.get("task"),
                    state=state,
                    allowed_files=task.get("allowed_files", []),
                    contract_constraints=task.get("contract_constraints", {}),
                )
            else:
                # 并行任务执行
                result_update = await run_agent_core(
                    agent=None,  # 并行模式不需要单个 agent
                    task=None,
                    state=state,
                    parallel_tasks=next_tasks,
                )
            
            # 记录结束时间
            elapsed_time = time.time() - start_time
            
            # 提取并移除 __execution_items__（不写入 state）
            execution_items = result_update.pop("__execution_items__", [])
            
            # 合并结果到 state（分域合并）
            for key, value in result_update.items():
                if key in state:
                    if isinstance(value, dict) and isinstance(state[key], dict):
                        # 字典类型：递归合并
                        current_value = state.get(key, {})
                        update_dict[key] = {**current_value, **value}
                    else:
                        # 非字典：直接覆盖
                        update_dict[key] = value
                else:
                    # 新字段：直接添加
                    update_dict[key] = value
            
            # 特殊处理：如果本次执行包含 verification，检查结果并更新 retry_count
            has_verification = any(t.get("agent") == "verification" for t in next_tasks)
            if has_verification:
                verification = state.get("verification", {})
                updated_verification = update_dict.get("verification", verification)
                final_verification = updated_verification.get("final_verification", {})
                
                if not final_verification.get("passed", True):
                    # 验证失败，增加 retry_count
                    current_retry = planning.get("retry_count", 0)
                    logger.warning(
                        f"Verification 失败，retry_count: {current_retry} -> {current_retry + 1}"
                    )
                    # 在 planning 更新中添加 retry_count
                    if "planning" not in update_dict:
                        update_dict["planning"] = {**planning}
                    update_dict["planning"]["retry_count"] = current_retry + 1
            
            # 调用 summary 工具生成摘要（如果有执行条目）
            agent_summaries = []
            round_summary = ""
            
            if execution_items:
                try:
                    summary_result = await summarize_execution_core(execution_items)
                    agent_summaries = summary_result.get("agent_summaries", [])
                    
                    # 根据 agent 数量决定 last_round_summary 来源
                    if len(execution_items) == 1:
                        # 单一 agent：直接使用 reasoning 作为 last_round_summary
                        round_summary = execution_items[0].get("reasoning", "") or "（无推理）"
                    else:
                        # 多个 agent：使用 LLM 生成的 round_summary
                        round_summary = summary_result.get("round_summary", "")
                except Exception as e:
                    logger.error(f"生成执行摘要失败: {e}", exc_info=True)
                    # 降级：使用简单摘要
                    agent_summaries = [
                        f"{item['agent']} 执行了任务" for item in execution_items
                    ]
                    if len(execution_items) == 1:
                        round_summary = execution_items[0].get("reasoning", "（无推理）")
                    else:
                        round_summary = f"本轮执行了 {len(execution_items)} 个 agent 任务"
            
            # 更新 round 编号
            current_round = planning.get("round", 0)
            round_no = current_round + 1
            
            # 构建 execution_history 条目
            existing_exec_history = state.get("execution_history", [])
            new_exec_history_entries = []
            
            for idx, item in enumerate(execution_items):
                agent = item.get("agent", "unknown")
                task = item.get("task", "")
                summary = agent_summaries[idx] if idx < len(agent_summaries) else "(无摘要)"
                
                entry = f'[Round {round_no}] Agent: {agent} | Task: "{task}" | Result: {summary}'
                new_exec_history_entries.append(entry)
            
            # 记录执行历史
            execution_record = {
                "timestamp": time.time(),
                "mode": mode,
                "task_count": task_count,
                "tasks": [
                    {
                        "task_id": t.get("task_id"),
                        "agent": t.get("agent"),
                        "task": t.get("task", ""),
                    }
                    for t in next_tasks
                ],
                "elapsed_time": elapsed_time,
                "status": "completed",
                "round": round_no,
                "agent_summaries": agent_summaries,
                "round_summary": round_summary,
            }
            
            existing_history = planning.get("orchestration_history", [])
            
            # 更新 planning
            runtime = state.get("runtime", {})
            update_dict.update({
                "planning": {
                    **planning,
                    "next_tasks": [],  # 清空已执行的任务
                    "orchestration_history": existing_history + [execution_record],
                    "round": round_no,
                    "last_round_summary": round_summary,
                },
                "execution_history": existing_exec_history + new_exec_history_entries,
                "runtime": {
                    **runtime,
                    "executed_nodes": [
                        *runtime.get("executed_nodes", []),
                        NodeName.TASK_RUNNER.value,
                    ],
                    "current_step": NodeName.TASK_RUNNER.value,
                },
            })
            
            logger.info(f"[TaskRunner] 执行完成，耗时 {elapsed_time:.2f}s")
            
            # 发送完成事件
            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": NodeName.TASK_RUNNER.value,
                    "progress": f"{mode}执行完成",
                    "think_chain_item": {
                        "type": NodeName.TASK_RUNNER.value,
                        "title": "TaskRunner",
                        "desc": f"完成 {task_count} 个任务（{elapsed_time:.2f}s）",
                        "urls": [],
                    },
                },
            )
            
            return Command(update=update_dict, goto=NodeName.PLANNER_ORCHESTRATOR.value)
        
        except Exception as e:
            logger.error(f"TaskRunner 执行失败: {e}", exc_info=True)
            
            # 记录失败历史
            planning = state.get("planning", {})
            existing_history = planning.get("orchestration_history", [])
            failure_record = {
                "timestamp": time.time(),
                "mode": "failure",
                "error": str(e),
                "status": "failed",
            }
            
            runtime = state.get("runtime", {})
            update_dict.update({
                "planning": {
                    **planning,
                    "orchestration_history": existing_history + [failure_record],
                },
                "runtime": {
                    **runtime,
                    "error": f"任务执行失败: {str(e)}",
                    "executed_nodes": [
                        *runtime.get("executed_nodes", []),
                        NodeName.TASK_RUNNER.value,
                    ],
                    "current_step": NodeName.TASK_RUNNER.value,
                },
            })
            
            return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)


__all__ = ["TaskRunnerNode"]
