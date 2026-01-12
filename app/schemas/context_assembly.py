"""
Context Assembly 相关的数据模型
定义迭代式上下文构建所需的数据结构
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TargetStatus(str, Enum):
    """目标符号处理状态"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class TargetContext(BaseModel):
    """目标符号上下文"""
    symbol_name: str = Field(description="函数/类名")
    file_path: str = Field(description="文件路径")
    symbol_type: str = Field(description="符号类型: function/class/method")
    start_line: int = Field(description="起始行号")
    end_line: int = Field(description="结束行号")
    status: TargetStatus = Field(
        default=TargetStatus.PENDING,
        description="处理状态"
    )
    reason: str = Field(
        default="",
        description="选择该符号的原因"
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="选择置信度"
    )


class DependencySignature(BaseModel):
    """依赖签名信息（仅签名，不含完整实现）"""
    symbol_name: str = Field(description="符号名称")
    file_path: str = Field(description="所在文件路径")
    signature: str = Field(description="函数/类签名")
    docstring: Optional[str] = Field(
        default=None,
        description="文档字符串"
    )
    symbol_type: str = Field(description="符号类型: function/class/method")


class EditableContextSlice(BaseModel):
    """
    可编辑上下文切片
    
    为 Patch 生成节点提供唯一且安全的修改上下文。
    明确区分可编辑区域和只读参考区域。
    """
    # 修改目标
    target: TargetContext = Field(description="当前修改目标")
    
    # 目标完整代码（可编辑区域）
    full_code: str = Field(description="目标符号的完整代码")
    
    # 依赖签名（只读参考）
    dependency_signatures: list[DependencySignature] = Field(
        default=[],
        description="前向依赖的签名列表（仅签名+Docstring）"
    )
    
    # 导入语句
    imports: list[str] = Field(
        default=[],
        description="相关的导入语句"
    )
    
    # Schema 定义（如 Pydantic 模型）
    schema_definitions: list[str] = Field(
        default=[],
        description="相关的 Schema/类型定义"
    )
    
    # 可编辑边界
    editable_start_line: int = Field(description="可编辑区域起始行")
    editable_end_line: int = Field(description="可编辑区域结束行")
    
    # 文件完整内容（用于生成 diff）
    file_content: str = Field(
        default="",
        description="目标文件的完整内容"
    )


class AffectedCaller(BaseModel):
    """受影响的调用方"""
    file_path: str = Field(description="调用方文件路径")
    symbol_name: str = Field(description="调用方符号名")
    call_line: int = Field(description="调用位置行号")
    requires_change: bool = Field(
        default=False,
        description="是否需要修改调用方"
    )
    change_reason: str = Field(
        default="",
        description="需要修改的原因"
    )


class ImpactReport(BaseModel):
    """影响分析报告"""
    # 受影响的调用方
    affected_callers: list[AffectedCaller] = Field(
        default=[],
        description="受影响的调用方列表"
    )
    
    # 是否需要扩散
    requires_expansion: bool = Field(
        default=False,
        description="是否需要扩散修改到其他符号"
    )
    
    # 下一批待处理目标
    next_targets: list[TargetContext] = Field(
        default=[],
        description="下一批需要处理的目标符号"
    )
    
    # 风险级别
    risk_level: str = Field(
        default="low",
        description="风险级别: high/medium/low"
    )
    
    # 分析推理
    reasoning: str = Field(
        default="",
        description="影响分析的推理过程"
    )
    
    # 测试建议
    test_suggestions: list[str] = Field(
        default=[],
        description="建议的测试用例或验证点"
    )


class EntrySelectionResult(BaseModel):
    """切入点选择结果"""
    selected_target: TargetContext = Field(
        description="选中的切入点"
    )
    alternatives: list[TargetContext] = Field(
        default=[],
        description="备选切入点"
    )
    selection_reasoning: str = Field(
        default="",
        description="选择该切入点的推理过程"
    )


class PatchResult(BaseModel):
    """补丁生成结果"""
    file_path: str = Field(description="目标文件路径")
    original_code: str = Field(description="原始代码")
    modified_code: str = Field(description="修改后的代码")
    unified_diff: str = Field(description="Unified diff 格式补丁")
    change_summary: str = Field(
        default="",
        description="修改摘要"
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="修改置信度"
    )

