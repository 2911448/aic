"""
State module for LangGraph workflow

提供统一的 state 导出，包括：
- IssueProcessState: 主状态定义（分域结构）
- Domain-specific TypedDicts: 各个领域的子状态
- NodeName & ProcessStage: 节点名和阶段枚举
"""

from app.graph.state.node_names import NodeName, ProcessStage
from app.graph.state.state import (
    AnalysisInfo,
    ContextInfo,
    DeliveryInfo,
    ImpactInfo,
    IssueProcessState,
    PatchingInfo,
    RetrievalInfo,
    ReviewInfo,
    RuntimeInfo,
    SandboxInfo,
    TargetingInfo,
    VerificationInfo,
    init_state_defaults,
)

__all__ = [
    # Main State
    "IssueProcessState",
    # Domain-Specific States
    "SandboxInfo",
    "AnalysisInfo",
    "RetrievalInfo",
    "TargetingInfo",
    "ContextInfo",
    "PatchingInfo",
    "VerificationInfo",
    "ImpactInfo",
    "ReviewInfo",
    "DeliveryInfo",
    "RuntimeInfo",
    # Helpers
    "init_state_defaults",
    # Enums
    "NodeName",
    "ProcessStage",
]
