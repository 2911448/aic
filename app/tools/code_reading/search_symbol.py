"""
Search Symbol Tool - 在 AST 中搜索符号

这是一个可被 LangChain Agent 调用的工具。
"""

from typing import Optional

from langchain_core.tools import tool

from app.core.logger_config import logger
from app.utils.tree_sitter_service import tree_sitter_service


@tool
def search_symbol_in_code(
    code: str,
    symbol_name: str,
    language: str,
    file_path: Optional[str] = None
) -> Optional[dict]:
    """
    在代码中搜索指定符号
    
    Args:
        code: 代码内容
        symbol_name: 符号名称（函数名、类名等）
        language: 编程语言
        file_path: 文件路径（用于日志，可选）
    
    Returns:
        符号信息字典，包含:
        - name: 符号名
        - type: 符号类型（function, class, method 等）
        - start_line: 起始行
        - end_line: 结束行
        - signature: 签名
        - code: 符号的完整代码
        
        如果未找到返回 None
    
    Raises:
        Exception: 解析失败
    """
    try:
        ast_info = tree_sitter_service.parse_code(code, language, file_path or "unknown")
        
        if not ast_info:
            raise Exception(f"无法解析 AST: language={language}")
        
        # 搜索符号
        found_symbol = None
        for symbol in ast_info.symbols:
            if symbol.name == symbol_name:
                found_symbol = symbol
                break
            # 检查带父类的名称
            if symbol.parent and f"{symbol.parent}.{symbol.name}" == symbol_name:
                found_symbol = symbol
                break
        
        if not found_symbol:
            logger.warning(f"[Tool] search_symbol: 未找到符号 '{symbol_name}'")
            return None
        
        # 提取符号代码
        lines = code.splitlines()
        start_idx = found_symbol.start_line - 1
        end_idx = found_symbol.end_line
        symbol_code = "\n".join(lines[start_idx:end_idx])
        
        result = {
            "name": found_symbol.name,
            "type": found_symbol.type,
            "start_line": found_symbol.start_line,
            "end_line": found_symbol.end_line,
            "signature": found_symbol.signature,
            "parent": found_symbol.parent,
            "code": symbol_code,
        }
        
        logger.info(
            f"[Tool] search_symbol: 找到符号 '{symbol_name}', "
            f"type={found_symbol.type}, lines={found_symbol.start_line}-{found_symbol.end_line}"
        )
        
        return result
    except Exception as e:
        error_msg = f"搜索符号失败: {str(e)}"
        logger.error(error_msg)
        raise Exception(error_msg)


# 导出工具
__all__ = ["search_symbol_in_code"]

