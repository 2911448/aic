"""
Diff Analyzer - 分析 unified diff 提取签名变更指纹

从 unified diff 中提取被修改的函数签名、类名、方法名等，
用于全局影响扫描。
"""

import re
from typing import Optional

from pydantic import BaseModel, Field

from app.core.logger_config import logger
from app.utils.tree_sitter_service import tree_sitter_service


class SignatureChange(BaseModel):
    """签名变更信息"""
    symbol_name: str = Field(description="符号名称（函数名、类名、方法名）")
    symbol_type: str = Field(description="符号类型：function/class/method")
    old_signature: Optional[str] = Field(None, description="旧签名")
    new_signature: Optional[str] = Field(None, description="新签名")
    file_path: str = Field(description="文件路径")
    change_type: str = Field(description="变更类型：modified/added/removed")
    is_public: bool = Field(default=True, description="是否为公共接口（非下划线开头）")


class DiffAnalyzer:
    """Diff 分析器"""
    
    def __init__(self):
        """初始化"""
        self.tree_sitter = tree_sitter_service
    
    def extract_signature_changes(
        self,
        unified_diff: str,
        file_path: str,
        old_content: str = "",
        new_content: str = "",
        language: str = "python"
    ) -> list[SignatureChange]:
        """
        从 unified diff 中提取签名变更指纹
        
        策略：
        1. 解析 diff 找出变更的行范围
        2. 解析新旧代码的 AST，提取符号定义
        3. 对比找出签名变更
        
        Args:
            unified_diff: unified diff 格式的补丁
            file_path: 文件路径
            old_content: 原始代码内容
            new_content: 修改后的代码内容
            language: 编程语言
        
        Returns:
            签名变更列表
        """
        changes = []
        
        try:
            # 如果没有提供代码内容，尝试从 diff 重构
            if not old_content or not new_content:
                logger.warning("未提供代码内容，仅从 diff 文本推断签名变更")
                return self._extract_from_diff_text(unified_diff, file_path, language)
            
            # 解析旧代码和新代码的符号
            old_ast = self.tree_sitter.parse_code(old_content, language, file_path)
            new_ast = self.tree_sitter.parse_code(new_content, language, file_path)
            
            if not old_ast or not new_ast:
                logger.warning("AST 解析失败，回退到文本模式")
                return self._extract_from_diff_text(unified_diff, file_path, language)
            
            # 构建符号映射
            old_symbols = {sym.name: sym for sym in old_ast.symbols}
            new_symbols = {sym.name: sym for sym in new_ast.symbols}
            
            # 检测变更
            all_symbol_names = set(old_symbols.keys()) | set(new_symbols.keys())
            
            for symbol_name in all_symbol_names:
                old_sym = old_symbols.get(symbol_name)
                new_sym = new_symbols.get(symbol_name)
                
                # 判断是否为公共接口
                is_public = not symbol_name.startswith("_")
                
                if old_sym and not new_sym:
                    # 符号被删除
                    changes.append(SignatureChange(
                        symbol_name=symbol_name,
                        symbol_type=old_sym.type,
                        old_signature=old_sym.signature or symbol_name,
                        new_signature=None,
                        file_path=file_path,
                        change_type="removed",
                        is_public=is_public,
                    ))
                elif not old_sym and new_sym:
                    # 符号被添加
                    changes.append(SignatureChange(
                        symbol_name=symbol_name,
                        symbol_type=new_sym.type,
                        old_signature=None,
                        new_signature=new_sym.signature or symbol_name,
                        file_path=file_path,
                        change_type="added",
                        is_public=is_public,
                    ))
                elif old_sym and new_sym:
                    # 符号可能被修改
                    old_sig = old_sym.signature or symbol_name
                    new_sig = new_sym.signature or symbol_name
                    
                    if old_sig != new_sig:
                        # 签名发生变化
                        changes.append(SignatureChange(
                            symbol_name=symbol_name,
                            symbol_type=new_sym.type,
                            old_signature=old_sig,
                            new_signature=new_sig,
                            file_path=file_path,
                            change_type="modified",
                            is_public=is_public,
                        ))
            
            logger.info(f"从 diff 提取到 {len(changes)} 个签名变更")
            return changes
        
        except Exception as e:
            logger.error(f"提取签名变更失败: {e}", exc_info=True)
            return []
    
    def _extract_from_diff_text(
        self,
        unified_diff: str,
        file_path: str,
        language: str
    ) -> list[SignatureChange]:
        """
        从 diff 文本直接推断签名变更（回退方案）
        
        使用正则表达式匹配函数/类定义的变更
        """
        changes = []
        
        try:
            if language == "python":
                # 匹配 Python 函数/类定义
                # +def func_name(...): 或 -def func_name(...):
                pattern = r'^([+-])\s*(def|class)\s+(\w+)\s*(\([^)]*\))?'
                
                for line in unified_diff.split('\n'):
                    match = re.match(pattern, line)
                    if match:
                        change_prefix = match.group(1)  # + 或 -
                        keyword = match.group(2)  # def 或 class
                        symbol_name = match.group(3)
                        params = match.group(4) or ""
                        
                        signature = f"{keyword} {symbol_name}{params}"
                        symbol_type = "function" if keyword == "def" else "class"
                        is_public = not symbol_name.startswith("_")
                        
                        if change_prefix == "+":
                            # 添加或修改
                            changes.append(SignatureChange(
                                symbol_name=symbol_name,
                                symbol_type=symbol_type,
                                old_signature=None,
                                new_signature=signature,
                                file_path=file_path,
                                change_type="added",
                                is_public=is_public,
                            ))
                        elif change_prefix == "-":
                            # 删除或修改
                            changes.append(SignatureChange(
                                symbol_name=symbol_name,
                                symbol_type=symbol_type,
                                old_signature=signature,
                                new_signature=None,
                                file_path=file_path,
                                change_type="removed",
                                is_public=is_public,
                            ))
            
            logger.info(f"从 diff 文本提取到 {len(changes)} 个签名变更（文本模式）")
            return changes
        
        except Exception as e:
            logger.error(f"从 diff 文本提取失败: {e}", exc_info=True)
            return []


# 单例
diff_analyzer = DiffAnalyzer()

