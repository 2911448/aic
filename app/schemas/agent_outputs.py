"""
Agent 输出结构化 Schema 定义

统一所有 Agent 的输出格式，确保类型安全与可预测性。
"""

from typing import Optional
from pydantic import BaseModel, Field


# ============================================================================
# Patch Artifact（补丁产物）
# ============================================================================

class PatchArtifact(BaseModel):
    """
    补丁产物的完整结构化表示
    
    用于统一 CodeAgent → Verification → Review → MRSubmit 的数据口径
    """
    
    id: str = Field(description="补丁唯一标识符（例如：task_id 或 uuid）")
    file_paths: list[str] = Field(
        description="本补丁涉及的文件路径列表（支持单文件或多文件）"
    )
    unified_diff: str = Field(
        description="unified diff 格式的补丁内容（可被 git apply 应用）"
    )
    summary: str = Field(
        description="变更摘要：简明扼要描述做了什么修改以及为什么（1-3段落）"
    )


# ============================================================================
# Review Artifact（评审产物）
# ============================================================================

class ReviewRisk(BaseModel):
    """单条风险项"""
    
    level: str = Field(description="风险级别：low / medium / high")
    category: str = Field(description="风险类别：例如 type_safety / performance / security")
    description: str = Field(description="风险描述")
    mitigation: Optional[str] = Field(default=None, description="缓解措施建议")


class ReviewChecklist(BaseModel):
    """评审检查项"""
    
    item: str = Field(description="检查项描述")
    file_path: Optional[str] = Field(default=None, description="相关文件路径")
    line_range: Optional[str] = Field(default=None, description="行号范围（例如：45-78）")


class ReviewArtifact(BaseModel):
    """
    代码评审产物的结构化表示
    
    由 ReviewerAgent 产出，MRSubmitter 统一渲染为 Markdown
    """
    
    summary: str = Field(
        description="评审摘要：1-2 句话概括核心内容"
    )
    technical_details: str = Field(
        description="技术细节：修改了哪些函数/类，为什么这样修改（2-3 段落）"
    )
    branch_name: str = Field(
        description="MR 分支名称：基于 issue 内容和修改类型生成的分支名"
    )
    risks: list[ReviewRisk] = Field(
        default_factory=list,
        description="风险评估列表"
    )
    test_plan: list[str] = Field(
        default_factory=list,
        description="测试建议：需要重点测试的场景和边界条件"
    )
    checklist: list[ReviewChecklist] = Field(
        default_factory=list,
        description="评审检查清单：Reviewer 需要重点关注的代码位置"
    )
    open_questions: list[str] = Field(
        default_factory=list,
        description="开放问题：需要人工确认或决策的事项"
    )
    overall_assessment: str = Field(
        description="总体评估：是否建议合并、是否需要额外审查等"
    )
    

# ============================================================================
# CodeAgent Output（已存在但需要升级）
# ============================================================================

class CodeAgentOutput(BaseModel):
    """
    CodeAgent 的结构化输出格式（升级版）
    
    直接产出 PatchArtifact，避免后续节点猜测解析
    """
    
    patches: list[PatchArtifact] = Field(
        description="生成的补丁列表（通常只有一个，但支持多文件协同修改）"
    )
    reasoning: str = Field(
        description="代码生成推理：为什么这样修改，考虑了哪些因素"
    )
