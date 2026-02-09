"""
LangGraph State definitions for Issue processing workflow

State 说明：
- 从平铺字段改为分域结构，提升可维护性和清晰度
- 每个域（sandbox、analysis、patching等）独立管理相关字段
- 使用 TypedDict 嵌套结构，保持与 LangGraph 兼容性
"""

from typing import Any, Optional, TypedDict


# ============================================================================
# Domain-Specific State Groups
# ============================================================================


class SandboxInfo(TypedDict, total=False):
    """Sandbox 生命周期信息（SandboxBootstrap 产出）"""

    sandbox_id: Optional[str]  
    repo_path: Optional[str]  # Git repository path in sandbox
    default_branch: Optional[str]  
    ignore_patterns: list[str]  # 从 .gitignore 读取的忽略规则


class AnalysisInfo(TypedDict, total=False):
    """Issue 分析结果（PlannerAgent 产出 - Issue 理解部分 + OmniExplorer 产出）"""

    # OmniExplorer 产出（新三步工作流）
    omni_explorer: list[dict]  # OmniExplorer 探索结果列表，每个元素包含 task_id 和探索结果 




class PatchingInfo(TypedDict, total=False):
    """补丁生成与管理（CodeAgent 产出）- 结构化版本"""

    patches: list[dict]  # 补丁产物列表（PatchArtifact 的 dict 表示）


class VerificationInfo(TypedDict, total=False):
    """验证结果（VerificationFlow 产出）"""

    final_verification: Optional[dict]  # 最终验证结果：mypy + ruff 全量检查（包含 passed, error_count, warnings, errors 等）


class DeliveryInfo(TypedDict, total=False):
    """MR 提交结果（MRPublisher 产出）- 包含评审报告"""

    mr_url: Optional[str]  # Merge Request URL
    mr_iid: Optional[int]  # Merge Request IID
    branch_name: Optional[str]  # 创建的分支名称
    review_artifact: Optional[dict]  # 结构化评审产物（ReviewArtifact 的 dict 表示）


class PlanningInfo(TypedDict, total=False):
    """计划与调度信息（Planner 决策 → TaskRunner 执行）"""

    retry_count: int  # 重试计数器（用于熔断验证/修复循环）
    orchestration_history: list[dict]  # 编排历史记录（包含 Planner 决策 + TaskRunner 执行结果）
    next_phases: list[list[dict]]  # Planner 决策的全局阶段列表（TaskRunner 只执行第 1 个阶段，执行后回到 Planner 动态更新）
    last_decision: Optional[dict]  # 上一次 Planner 决策（用于可观测性）
    idle_count: int  # 无进展计数（用于空转熔断）
    round: int  # 当前执行轮次（TaskRunner 每次执行 Phase 1 后 +1）
    last_round_summary: str  # 最近一轮执行的汇总摘要（单agent时为该agent的reasoning；多agent时为多行汇总，每行≤300字）


class RuntimeInfo(TypedDict, total=False):
    """执行元数据与流程控制"""

    executed_nodes: list[str]  # History of executed nodes for tracking
    current_step: str  # Current step description
    error: Optional[str]  # Error message if any
    completed: bool  # Whether the workflow is completed
    trace_id: Optional[str]  # 可选：分布式追踪 ID


class MergeInfo(TypedDict, total=False):
    """Merge Request 处理信息（MergeDiffCollector / IndexUpdate 产出）"""

    mr_iid: Optional[int]  # Merge Request IID (项目内 ID)
    mr_id: Optional[int]  # Merge Request global ID
    target_branch: Optional[str]  # 目标分支
    source_branch: Optional[str]  # 源分支
    merge_commit_sha: Optional[str]  # 合并后的 commit SHA
    changed_files: list[dict]  # 变更文件列表 [{"status": "added/modified/deleted/renamed", "path": str, "old_path": Optional[str]}]
    indexed_files: list[str]  # 已成功索引的文件路径列表
    failed_files: list[dict]  # 索引失败的文件 [{"path": str, "error": str}]


# ============================================================================
# Main IssueProcessState (Top-Level State)
# ============================================================================


class IssueProcessState(TypedDict, total=False):
    """
    Issue Processing Workflow State (Domain-Grouped Structure)

    分域结构说明：
    - sandbox: SandboxBootstrap 填充，其他节点只读
    - analysis: PlannerAgent 产出 - Issue 理解
    - planning: PlannerAgent + Scheduler 产出 - 任务编排（execution_plan, task_status, retry_count）
    - patching: CodeAgent 产出（结构化补丁产物列表 - PatchArtifact[]）
    - verification: VerificationFlow 产出（mypy/ruff 验证结果）
    - delivery: MRPublisher 产出（MR URL, branch_name, review_artifact）
    - merge: Merge Request 处理信息（用于 merge workflow）
    - runtime: 流程控制元数据（executed_nodes, error, trace_id）
    - execution_history: 执行轨迹记录（字符串列表，格式：[Round N] Agent: xxx | Task: "..." | Result: xxx）
    """

    # Original Input
    issue_data: dict  # GitLab Issue raw data from webhook
    project_info: dict  # Project information

    # Domain-Specific Groups
    sandbox: SandboxInfo  # Sandbox 生命周期信息
    planning: PlanningInfo  # Planner 与 Scheduler 信息
    analysis: AnalysisInfo  # Issue 分析结果
    patching: PatchingInfo  # 补丁生成与管理（结构化）
    verification: VerificationInfo  # 验证结果
    delivery: DeliveryInfo  # MR 提交结果（含评审报告）
    merge: MergeInfo  # Merge Request 处理信息
    runtime: RuntimeInfo  # 执行元数据与流程控制
    execution_history: list[str]  # 执行轨迹记录（每轮 agent 执行的摘要）


# ============================================================================
# Helper Functions for State Access
# ============================================================================


def init_state_defaults(state: IssueProcessState) -> IssueProcessState:
    """
    初始化 state 默认值（避免 KeyError）

    使用示例：在 workflow 入口调用
    """
    if "sandbox" not in state:
        state["sandbox"] = {}
    if "planning" not in state:
        state["planning"] = {
            "retry_count": 0,
            "orchestration_history": [],
            "next_phases": [],
            "last_decision": None,
            "idle_count": 0,
            "round": 0,
            "last_round_summary": "",
        }
    if "analysis" not in state:
        state["analysis"] = {
            "omni_explorer": [],
        }
    if "patching" not in state:
        state["patching"] = {
            "patches": [],
        }
    if "verification" not in state:
        state["verification"] = {}
    if "delivery" not in state:
        state["delivery"] = {
            "review_artifact": None,
        }
    if "merge" not in state:
        state["merge"] = {
            "changed_files": [],
            "indexed_files": [],
            "failed_files": [],
        }
    if "runtime" not in state:
        state["runtime"] = {
            "executed_nodes": [],
            "current_step": "",
            "error": None,
            "completed": False,
        }
    if "execution_history" not in state:
        state["execution_history"] = []

    return state

