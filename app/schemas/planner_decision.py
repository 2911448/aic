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
    contract_constraints: dict[str, Any] = Field(
        default_factory=dict,
        description="契约约束（仅 code_agent）"
    )


class PlannerDecision(BaseModel):
    """Planner 决策输出"""
    terminate: bool = Field(description="是否终止流程（MR 已提交/熔断/致命错误）")
    reason: str = Field(description="决策原因/推理过程")
    tasks: list[PlannerTask] = Field(
        default_factory=list,
        description="要执行的任务列表（支持单任务或并行任务）"
    )
    
    def validate_decision(self) -> tuple[bool, str]:
        """
        校验决策有效性
        
        Returns:
            (is_valid, error_message)
        """
        # 如果不终止，必须有至少一个任务
        if not self.terminate and len(self.tasks) == 0:
            return False, "决策无效：未终止但没有指定任何任务"
        
        # 如果终止，不应该有任务
        if self.terminate and len(self.tasks) > 0:
            return False, "决策无效：已终止但仍指定了任务"
        
        # 检查 task_id 唯一性
        task_ids = [t.task_id for t in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            return False, "决策无效：task_id 不唯一"
        
        # 检查 agent 名称合法性
        valid_agents = {"omni_explorer", "code_agent", "verification", "mr_publisher"}
        for task in self.tasks:
            if task.agent not in valid_agents:
                return False, f"决策无效：未知的 agent 类型 '{task.agent}'"
        
        return True, ""


__all__ = ["PlannerDecision", "PlannerTask"]
