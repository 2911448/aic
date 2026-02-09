"""
Planner 决策输出 Schema

Planner 不再通过工具执行派发，而是产出结构化决策，由系统节点执行。
"""

from typing import Any
from pydantic import BaseModel, Field


class PlannerTask(BaseModel):
    """Planner 决策的单个任务"""
    task_id: str = Field(description="任务唯一标识")
    agent: str = Field(
        description="Agent 名称 (omni_explorer/code_agent/verification/mr_publisher)"
    )
    task: str = Field(description="任务描述")
    allowed_files: list[str] = Field(
        default_factory=list,
        description="允许修改的文件（仅 code_agent）"
    )


class PlannerDecision(BaseModel):
    """Planner 决策输出（支持分阶段执行）"""
    terminate: bool = Field(description="是否终止流程（MR 已提交/熔断/致命错误）")
    reason: str = Field(description="决策原因/推理过程")
    phases: list[list[PlannerTask]] = Field(
        default_factory=list,
        description="分阶段任务列表。每个阶段内的任务可并行执行，阶段间严格串行。"
    )
    
    def validate_decision(self) -> tuple[bool, str]:
        """
        校验决策有效性
        
        Returns:
            (is_valid, error_message)
        """
        # 如果不终止，必须有至少一个阶段且至少一个任务
        if not self.terminate:
            if len(self.phases) == 0:
                return False, "决策无效：未终止但没有指定任何阶段"
            
            # 检查是否有空阶段
            for idx, phase in enumerate(self.phases):
                if len(phase) == 0:
                    return False, f"决策无效：第 {idx + 1} 阶段为空"
        
        # 如果终止，不应该有任何阶段
        if self.terminate and len(self.phases) > 0:
            return False, "决策无效：已终止但仍指定了阶段"
        
        # 收集所有任务，检查 task_id 唯一性
        all_tasks = []
        for phase in self.phases:
            all_tasks.extend(phase)
        
        task_ids = [t.task_id for t in all_tasks]
        if len(task_ids) != len(set(task_ids)):
            return False, "决策无效：task_id 不唯一"
        
        # 检查 agent 名称合法性
        valid_agents = {"omni_explorer", "code_agent", "verification", "mr_publisher"}
        for task in all_tasks:
            if task.agent not in valid_agents:
                return False, f"决策无效：未知的 agent 类型 '{task.agent}'"
        
        return True, ""


__all__ = ["PlannerDecision", "PlannerTask"]
