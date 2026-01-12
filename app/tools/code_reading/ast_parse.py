"""
AST Parse Tool - 解析代码的抽象语法树

这是一个可被 LangChain Agent 调用的工具。
"""

from typing import Optional

from langchain_core.tools import tool

from app.core.logger_config import logger
from app.utils.tree_sitter_service import tree_sitter_service


def parse_code_ast_core(
    code: str,
    language: str,
    file_path: Optional[str] = None
) -> dict:
    """
    核心 AST 解析函数（可直接调用）
    
    Args:
        code: 代码内容
        language: 编程语言（python, javascript, typescript 等）
        file_path: 文件路径（用于日志，可选）
    
    Returns:
        AST 信息字典，包含:
        - symbols: 符号列表（函数、类、方法等）
        - imports: 导入语句列表
        - file_path: 文件路径
    
    Raises:
        Exception: 解析失败
    """
    try:
        ast_info = tree_sitter_service.parse_code(code, language, file_path or "unknown")
        
        if not ast_info:
            raise Exception(f"无法解析 AST: language={language}")
        
        # 转换为可序列化的字典
        result = {
            "file_path": ast_info.file_path,
            "symbols": [
                {
                    "name": sym.name,
                    "type": sym.type,
                    "start_line": sym.start_line,
                    "end_line": sym.end_line,
                    "signature": sym.signature,
                    "parent": sym.parent,
                }
                for sym in ast_info.symbols
            ],
            "imports": [
                {
                    "module": imp.module,
                    "names": imp.names,
                    "alias": imp.alias,
                }
                for imp in ast_info.imports
            ],
        }
        
        logger.info(
            f"[Tool] parse_ast: {file_path or 'unknown'}, "
            f"symbols={len(result['symbols'])}, imports={len(result['imports'])}"
        )
        
        return result
    except Exception as e:
        error_msg = f"解析 AST 失败: {str(e)}"
        logger.error(error_msg)
        raise Exception(error_msg)


@tool
def parse_code_ast(
    code: str,
    language: str,
    file_path: Optional[str] = None
) -> dict:
    """
    解析代码的 AST（抽象语法树）- LangChain Agent 工具版本
    
    Args:
        code: 代码内容
        language: 编程语言（python, javascript, typescript 等）
        file_path: 文件路径（用于日志，可选）
    
    Returns:
        AST 信息字典，包含符号列表、导入语句和文件路径
    
    Raises:
        Exception: 解析失败
    """
    return parse_code_ast_core(code, language, file_path)


# 导出工具
__all__ = ["parse_code_ast", "parse_code_ast_core"]

