"""
Code Scope 相关的数据模型
定义代码定位、依赖分析、风险评估等数据结构
"""

from enum import Enum

from pydantic import BaseModel, Field


class ScopeStrategy(str, Enum):
    """代码定位策略枚举"""
    POINT_FIX = "POINT_FIX"  # 单点修复
    MULTI_FIX = "MULTI_FIX"  # 多点修复
    INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"  # 上下文不足
    RE_RETRIEVAL = "RE_RETRIEVAL"  # 需要重新检索


class CodeLocation(BaseModel):
    """代码位置定义（含代码锚点）"""
    file_path: str = Field(description="文件路径")
    symbol_name: str = Field(description="符号名称（函数名/类名）")
    lines: list[int] = Field(description="行号范围 [start_line, end_line]")
    anchor: str = Field(description="修改位置的原始代码行（用于唯一匹配）")
    change_reason: str = Field(description="修改原因")


class DependencyImpact(BaseModel):
    """依赖影响信息（用于 LLM 输出）"""
    caller_file: str = Field(description="调用方文件路径")
    caller_symbol: str = Field(description="调用函数名")
    in_top5: bool = Field(description="是否在 Top-5 检索结果中")
    risk_level: str = Field(description="风险级别: high | medium | low")
    requires_sync_change: bool = Field(
        default=False,
        description="是否需要同步修改"
    )


class ReverseDependency(BaseModel):
    """反向依赖信息（用于依赖分析）"""
    caller_file: str = Field(description="调用方文件路径")
    caller_symbol: str = Field(description="调用方函数名")
    callee_symbol: str = Field(description="被调用函数名")
    call_location: tuple[int, int] = Field(description="调用位置 (start_line, end_line)")
    in_top5: bool = Field(description="调用方是否在 Top-5 检索结果中")


class DependencyRelation(BaseModel):
    """依赖关系（用于依赖分析）"""
    from_symbol: str = Field(description="调用方符号")
    to_symbol: str = Field(description="被调用符号")
    from_file: str = Field(description="调用方文件")
    to_file: str = Field(description="被调用文件")
    relation_type: str = Field(description="关系类型（如：call, inherit）")
    impact_level: str = Field(description="影响级别（high, medium, low）")
    reverse_deps: list[ReverseDependency] = Field(
        default=[],
        description="该依赖的反向依赖列表"
    )


class ImpactScope(BaseModel):
    """影响范围评估（用于依赖分析）"""
    total_affected_files: int = Field(description="受影响的文件总数")
    total_affected_symbols: int = Field(description="受影响的符号总数")
    uncovered_dependencies: list[ReverseDependency] = Field(
        default=[],
        description="未覆盖的依赖（不在 Top-5 中）"
    )
    requires_additional_retrieval: bool = Field(
        description="是否需要额外检索"
    )


class RiskAssessment(BaseModel):
    """风险评估（简化版）"""
    side_effects: list[str] = Field(
        default=[],
        description="修改后可能导致的副作用列表"
    )
    confidence: float = Field(
        description="置信度（0.0-1.0）",
        ge=0.0,
        le=1.0
    )
    uncovered_risks: str = Field(
        default="",
        description="提示哪些潜在影响超出了当前上下文范围"
    )


class EnrichedSnippet(BaseModel):
    """补全后的代码片段"""
    file_path: str = Field(description="文件路径")
    content: str = Field(description="代码内容")
    start_line: int = Field(description="起始行号")
    end_line: int = Field(description="结束行号")
    language: str = Field(description="编程语言")
    is_skeleton: bool = Field(
        default=False,
        description="是否为骨架图模式"
    )
    has_imports: bool = Field(
        default=False,
        description="是否包含 import 语句"
    )
    has_class_context: bool = Field(
        default=False,
        description="是否包含类定义上下文"
    )


class CodeScopeResult(BaseModel):
    """代码定位结果"""
    
    # 策略枚举
    strategy: ScopeStrategy = Field(
        description="修复策略：POINT_FIX | MULTI_FIX | INSUFFICIENT_CONTEXT | RE_RETRIEVAL"
    )
    
    reasoning: str = Field(
        default="",
        description="选择该策略的原因，以及多个修改点之间的逻辑关联"
    )
    
    change_set: list[CodeLocation] = Field(
        default=[],
        description="需要修改的代码位置列表"
    )
    
    dependency_impact: list[DependencyImpact] = Field(
        default=[],
        description="依赖影响分析"
    )
    
    risk_assessment: RiskAssessment = Field(
        description="风险评估结果"
    )
    
    # 保留用于内部使用
    context_snippets: list[EnrichedSnippet] = Field(
        default=[],
        description="补全后的完整代码上下文"
    )
    
    skeleton_files: list[str] = Field(
        default=[],
        description="使用了骨架图模式的文件列表"
    )
    
    analysis_metadata: dict = Field(
        default={},
        description="分析元数据（如执行时间、AST 节点数等）"
    )

