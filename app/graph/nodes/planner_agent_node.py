"""
Planner Orchestrator Node - 闭环决策中心

职责：
- 根据当前 state 缺口做出结构化决策
- 不直接执行 agent，而是输出 next_tasks 交给 TaskRunner
- 形成"状态反馈的动态拓扑循环"
"""

import hashlib
import json
import time
from typing import Any, Literal

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command
from langchain.agents import create_agent

from app.core.logger_config import logger
from app.core.prompt_manager import prompt_manager
from app.core.trace_context import set_trace_id
from app.decorators.tracking import track_node_metrics
from app.graph.planning.agent_registry import agent_registry
from app.graph.state import IssueProcessState, ProcessStage
from app.graph.state.node_names import NodeName
from app.llms.llm_factory import get_llm_model
from app.schemas.planner_decision import PlannerDecision
from app.tools.registry import get_tools_for_agent


class PlannerAgentNode:
    """
    闭环决策中心（Closed-Loop Planner）
    
    功能：
    - 根据 state 缺口做出结构化决策
    - 输出 next_tasks 交给 TaskRunner 执行
    - 通过 graph 多次进入形成闭环
    """
    
    def __init__(self):
        """初始化节点"""
        self.prompt_manager = prompt_manager
        self.agent_registry = agent_registry
        self.max_retries = 3  # 验证/修复循环的最大重试次数
        self.max_idle_steps = 3  # 无进展步数上限（防空转）
        self.tools = get_tools_for_agent("planner")
    
    @track_node_metrics("planner_orchestrator")
    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal["task_runner", "sandbox_teardown"]]:
        """
        执行单轮决策（检查终止条件 → 读取 state → 做出结构化决策 → 返回）
        
        Args:
            state: 当前工作流状态
        
        Returns:
            Command 对象，继续则 goto task_runner，完成/失败则 goto sandbox_teardown
        """
        # 从 state 恢复 trace_id 到上下文
        trace_id = state.get("runtime", {}).get("trace_id")
        if trace_id:
            set_trace_id(trace_id)
        
        update_dict = {}
        
        try:
            # 检查终止条件
            should_terminate, termination_reason = self._check_termination(state)
            if should_terminate:
                logger.info(f"Planner 终止: {termination_reason}")
                
                # 发送完成事件
                await adispatch_custom_event(
                    ProcessStage.THINK_CHAIN.value,
                    {
                        "status": NodeName.PLANNER_ORCHESTRATOR.value,
                        "progress": f"任务编排完成: {termination_reason}",
                        "think_chain_item": {
                            "type": NodeName.PLANNER_ORCHESTRATOR.value,
                            "title": "任务编排",
                            "desc": termination_reason,
                            "urls": [],
                        },
                    },
                )
                
                runtime = state.get("runtime", {})
                update_dict.update({
                    "runtime": {
                        **runtime,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.PLANNER_ORCHESTRATOR.value,
                        ],
                        "current_step": termination_reason,
                        "completed": "MR" in termination_reason,
                    },
                })
                
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)
            
            # 发送进度事件
            await adispatch_custom_event(
                ProcessStage.PLANNING.value,
                {
                    "status": NodeName.PLANNER_ORCHESTRATOR.value,
                    "progress": "执行单轮决策...",
                    "think_chain_item": {
                        "type": NodeName.PLANNER_ORCHESTRATOR.value,
                        "title": "Planner 决策",
                        "desc": "根据 state 缺口做出结构化决策",
                        "urls": [],
                    },
                },
            )
            
            # 执行单轮决策（产出 PlannerDecision）
            decision = await self._make_decision(state)
            
            # 校验决策有效性
            is_valid, error_msg = decision.validate_decision()
            if not is_valid:
                logger.error(f"Planner 决策无效: {error_msg}")
                
                # 增加 idle_count
                planning = state.get("planning", {})
                idle_count = planning.get("idle_count", 0) + 1
                
                # 如果 idle 次数过多，熔断终止
                if idle_count >= self.max_idle_steps:
                    runtime = state.get("runtime", {})
                    update_dict.update({
                        "runtime": {
                            **runtime,
                            "error": f"Planner 空转熔断: 连续 {idle_count} 轮无有效决策",
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.PLANNER_ORCHESTRATOR.value,
                            ],
                            "current_step": NodeName.PLANNER_ORCHESTRATOR.value,
                        },
                    })
                    return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)
                
                # 记录无效决策并重试
                runtime = state.get("runtime", {})
                update_dict.update({
                    "planning": {
                        **planning,
                        "idle_count": idle_count,
                        "last_decision": {
                            "timestamp": time.time(),
                            "decision": decision.model_dump(),
                            "valid": False,
                            "error": error_msg,
                        },
                    },
                    "runtime": {
                        **runtime,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.PLANNER_ORCHESTRATOR.value,
                        ],
                        "current_step": f"无效决策（重试 {idle_count}/{self.max_idle_steps}）",
                    },
                })
                
                logger.warning(f"Planner 无效决策，重新进入决策（{idle_count}/{self.max_idle_steps}）")
                return Command(update=update_dict, goto=NodeName.PLANNER_ORCHESTRATOR.value)
            
            # 决策有效，检查是否需要终止
            if decision.terminate:
                logger.info(f"Planner 决策终止: {decision.reason}")
                runtime = state.get("runtime", {})
                update_dict.update({
                    "runtime": {
                        **runtime,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.PLANNER_ORCHESTRATOR.value,
                        ],
                        "current_step": decision.reason,
                        "completed": True,
                    },
                })
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)
            
            # 检测进展（state 指纹）
            current_fingerprint = self._compute_state_fingerprint(state)
            planning = state.get("planning", {})
            last_decision = planning.get("last_decision")
            last_fingerprint = last_decision.get("fingerprint") if last_decision else None
            
            # 如果 state 没有变化，增加 idle_count
            if last_fingerprint and current_fingerprint == last_fingerprint:
                idle_count = planning.get("idle_count", 0) + 1
                logger.warning(f"State 指纹未变化，idle_count={idle_count}/{self.max_idle_steps}")
                
                if idle_count >= self.max_idle_steps:
                    runtime = state.get("runtime", {})
                    update_dict.update({
                        "runtime": {
                            **runtime,
                            "error": f"Planner 空转熔断: 连续 {idle_count} 轮 state 无进展",
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.PLANNER_ORCHESTRATOR.value,
                            ],
                            "current_step": NodeName.PLANNER_ORCHESTRATOR.value,
                        },
                    })
                    return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)
            else:
                # state 有进展，重置 idle_count
                idle_count = 0
            
            # 写入 planning.next_phases 和决策记录
            runtime = state.get("runtime", {})
            
            # 将 phases 转换为可序列化格式
            serialized_phases = []
            for phase in decision.phases:
                serialized_phases.append([task.model_dump() for task in phase])
            
            # 统计总任务数
            total_tasks = sum(len(phase) for phase in decision.phases)
            
            update_dict.update({
                "planning": {
                    **planning,
                    "next_phases": serialized_phases,  # 全局计划（系统只执行第 1 个 phase）
                    "last_decision": {
                        "timestamp": time.time(),
                        "decision": decision.model_dump(),
                        "valid": True,
                        "fingerprint": current_fingerprint,
                    },
                    "idle_count": idle_count,
                },
                "runtime": {
                    **runtime,
                    "executed_nodes": [
                        *runtime.get("executed_nodes", []),
                        NodeName.PLANNER_ORCHESTRATOR.value,
                    ],
                    "current_step": "Planner 决策完成（已更新全局计划）",
                },
            })
            
            logger.info(
                f"[Planner] 全局计划已更新: {len(decision.phases)} 个阶段, "
                f"共 {total_tasks} 个任务 (Phase 1: {len(decision.phases[0]) if decision.phases else 0} 个任务), "
                f"推理: {decision.reason}"
            )
            
            return Command(update=update_dict, goto=NodeName.TASK_RUNNER.value)
        
        except Exception as e:
            logger.opt(exception=True).error(f"Planner 执行失败: {e}")
            runtime = state.get("runtime", {})
            update_dict.update({
                "runtime": {
                    **runtime,
                    "error": f"Planner 失败: {str(e)}",
                    "executed_nodes": [
                        *runtime.get("executed_nodes", []),
                        NodeName.PLANNER_ORCHESTRATOR.value,
                    ],
                    "current_step": NodeName.PLANNER_ORCHESTRATOR.value,
                },
            })
            
            return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)
    
    def _check_termination(self, state: IssueProcessState) -> tuple[bool, str]:
        """
        检查是否满足终止条件
        
        Returns:
            (should_terminate, reason)
        """
        # 1. 成功完成：MR 已提交
        delivery = state.get("delivery", {})
        if delivery.get("mr_url"):
            return True, "MR 已提交，任务完成"
        
        # 2. 熔断：重试次数过多
        planning = state.get("planning", {})
        retry_count = planning.get("retry_count", 0)
        if retry_count >= self.max_retries:
            return True, f"重试次数达到上限 ({retry_count}/{self.max_retries})，熔断终止"
        
        # 3. 熔断：空转次数过多
        idle_count = planning.get("idle_count", 0)
        if idle_count >= self.max_idle_steps:
            return True, f"空转次数达到上限 ({idle_count}/{self.max_idle_steps})，熔断终止"
        
        # 4. 致命错误：runtime.error 存在且无法恢复
        runtime = state.get("runtime", {})
        error = runtime.get("error")
        if error and "无法恢复" in error:
            return True, f"致命错误: {error}"
        
        return False, ""
    
    def _compute_state_fingerprint(self, state: IssueProcessState) -> str:
        """
        计算 state 指纹（用于检测进展）
        
        Returns:
            SHA256 哈希值
        """
        # 提取关键域的关键字段
        analysis = state.get("analysis", {})
        patching = state.get("patching", {})
        verification = state.get("verification", {})
        delivery = state.get("delivery", {})
        
        # OmniExplorer 结果
        omni_explorer = analysis.get("omni_explorer", [])
        # 统计所有探索结果
        explorer_task_count = len(omni_explorer) if isinstance(omni_explorer, list) else 0
        total_references = 0
        has_targets = False
        
        if isinstance(omni_explorer, list):
            for item in omni_explorer:
                if isinstance(item, dict):
                    task_result = item.get("result", {})
                    if task_result.get("target"):
                        has_targets = True
                    total_references += len(task_result.get("references", []))
        
        fingerprint_data = {
            "omni_explorer_task_count": explorer_task_count,
            "omni_explorer_has_targets": has_targets,
            "omni_explorer_total_references": total_references,
            "patches_count": len(patching.get("patches", [])),
            "verification_passed": verification.get("final_verification", {}).get("passed"),
            "has_review_artifact": bool(delivery.get("review_artifact")),
            "mr_url": delivery.get("mr_url"),
        }
        
        # 计算哈希
        fingerprint_str = json.dumps(fingerprint_data, sort_keys=True)
        return hashlib.sha256(fingerprint_str.encode()).hexdigest()
    
    def _build_execution_history_text(self, state: IssueProcessState) -> str:
        """
        构建执行轨迹文本（注入到 prompt）
        
        从 state.execution_history 中抽取最近 3 轮（按 Round 分组）
        
        Returns:
            执行轨迹文本
        """
        execution_history = state.get("execution_history", [])
        
        if not execution_history:
            return "（无执行历史）"
        
        # 按 Round 分组
        rounds_dict = {}
        for entry in execution_history:
            # 解析 Round 编号
            if entry.startswith("[Round "):
                try:
                    round_no = int(entry.split("]")[0].split("Round ")[1])
                    if round_no not in rounds_dict:
                        rounds_dict[round_no] = []
                    rounds_dict[round_no].append(entry)
                except (IndexError, ValueError):
                    continue
        
        # 取最近 3 轮
        sorted_rounds = sorted(rounds_dict.keys(), reverse=True)[:3]
        sorted_rounds = sorted(sorted_rounds)  # 按时间顺序输出
        
        lines = []
        for round_no in sorted_rounds:
            lines.extend(rounds_dict[round_no])
        
        return "\n".join(lines) if lines else "（无执行历史）"
    
    def _build_global_plan_text(self, state: IssueProcessState) -> str:
        """
        构建当前全局计划的文本表示（注入到 prompt）
        
        从 state.planning.next_phases 中取出当前全局计划，渲染为简洁文本
        
        Returns:
            全局计划文本
        """
        planning = state.get("planning", {})
        next_phases = planning.get("next_phases", [])
        
        if not next_phases:
            return "（无当前计划）"
        
        lines = []
        lines.append(f"当前全局计划（共 {len(next_phases)} 个阶段）：")
        lines.append("")
        
        for phase_idx, phase in enumerate(next_phases, start=1):
            task_count = len(phase)
            agents = [t.get("agent", "?") for t in phase]
            lines.append(f"Phase {phase_idx}: {task_count} 个任务 ({', '.join(agents)})")
            for task in phase:
                task_id = task.get("task_id", "?")
                agent = task.get("agent", "?")
                task_desc = task.get("task", "")
                # 截断过长的任务描述
                if len(task_desc) > 100:
                    task_desc = task_desc[:97] + "..."
                lines.append(f"  - [{task_id}] {agent}: {task_desc}")
            lines.append("")
        
        return "\n".join(lines)
    
    async def _make_decision(
        self,
        state: IssueProcessState,
    ) -> PlannerDecision:
        """
        做出结构化决策
        
        Args:
            state: 当前工作流状态
        
        Returns:
            PlannerDecision
        """
        issue_data = state.get("issue_data", {})
        project_info = state.get("project_info", {})
        sandbox = state.get("sandbox", {})
        
        issue_title = issue_data.get("title", "")
        issue_description = issue_data.get("description", "")
        labels = issue_data.get("labels", [])
        project_name = project_info.get("name", "")
        # 在 sandbox 中，工作目录已经是仓库根目录
        # 所有文件路径都是相对于根目录的，不需要任何前缀
        project_path = "."  # 表示当前目录（仓库根目录）
        sandbox_id = sandbox.get("sandbox_id", "")
        
        if labels and isinstance(labels[0], dict):
            labels = [label.get("title", "") for label in labels]
        
        # 动态注入 Agent 能力清单
        agent_capabilities = self.agent_registry.to_capability_list()
        
        # 获取上一轮汇总摘要
        planning = state.get("planning", {})
        last_round_summary = planning.get("last_round_summary", "（无上一轮执行记录）")
        
        # 构建 execution_history
        execution_history = self._build_execution_history_text(state)
        
        # 构建当前全局计划的文本表示
        current_global_plan = self._build_global_plan_text(state)
        
        # 构建 System Prompt
        system_prompt = self.prompt_manager.render(
            "planner_agent",
            issue_title=issue_title,
            issue_description=issue_description or "无描述",
            labels=labels,
            project_name=project_name,
            project_path=project_path,
            sandbox_id=sandbox_id,
            agent_capabilities=agent_capabilities,
            state_summary=last_round_summary,  # 直接传入上一轮汇总摘要
            execution_history=execution_history,
            current_global_plan=current_global_plan,  # 注入当前全局计划
            max_retries=self.max_retries,
            max_idle_steps=self.max_idle_steps,
        )
        
        llm = await get_llm_model(model_name="gpt-5-2025-08-07", temperature=0.1)
        
        # 创建 agent
        agent = create_agent(
            model=llm,
            tools=self.tools,  
            system_prompt=system_prompt,
            response_format=PlannerDecision,  # 要求结构化输出
        )
        
        # 调用 Agent
        result = await agent.ainvoke({
            "messages": [{"role": "user", "content": f"处理任务: {issue_title}"}]
        })
        
        decision: PlannerDecision = result["structured_response"]
        
        # 计算总任务数
        total_tasks = sum(len(phase) for phase in decision.phases)
        
        logger.info(
            f"[Planner] 决策完成: terminate={decision.terminate}, "
            f"phases={len(decision.phases)}, total_tasks={total_tasks}, "
            f"reason={decision.reason}"
        )
        
        return decision


__all__ = ["PlannerAgentNode"]
