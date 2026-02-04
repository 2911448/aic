"""
Syntax Check Tool - Python 语法检查
这是一个可被 LangChain Agent 调用的工具。
"""

import ast

from langchain_core.tools import tool

from app.core.logger_config import logger


def check_python_syntax_core(
    code: str,
    file_path: str | None = None
) -> dict:
    """
    检查 Python 代码的语法正确性
    
    Args:
        code: 要检查的代码内容
        file_path: 文件路径（用于日志，可选）
    
    Returns:
        检查结果字典，包含:
        - passed: 是否通过检查（布尔值）
        - issues: 问题列表（如果有）
        - message: 消息（可选）
    
    Raises:
        Exception: 检查失败
    """
    try:
        # 只对 Python 文件进行语法检查
        if file_path and not file_path.endswith(".py"):
            return {
                "passed": True,
                "issues": [],
                "message": "非 Python 文件，跳过语法检查"
            }
        
        # 使用 ast.parse 检查语法
        ast.parse(code)
        
        logger.info(f"[Tool] check_python_syntax: passed, file={file_path or 'N/A'}")
        
        return {
            "passed": True,
            "issues": [],
            "message": "语法检查通过"
        }
        
    except SyntaxError as e:
        error_msg = f"语法错误: {e.msg} at line {e.lineno}"
        logger.warning(f"[Tool] check_python_syntax: failed, {error_msg}")
        
        return {
            "passed": False,
            "issues": [{
                "type": "syntax_error",
                "message": error_msg,
                "line": e.lineno,
                "offset": e.offset
            }],
            "message": error_msg
        }
    except Exception as e:
        error_msg = f"语法检查失败: {str(e)}"
        logger.error(error_msg)
        raise Exception(error_msg)


@tool
def check_python_syntax(
    code: str,
    file_path: str | None = None
) -> dict:
    """
    检查 Python 代码的语法正确性 - LangChain Agent 工具版本
    
    Args:
        code: 要检查的代码内容
        file_path: 文件路径（用于日志，可选）
    
    Returns:
        检查结果字典
    
    Raises:
        Exception: 检查失败
    """
    return check_python_syntax_core(code, file_path)


# 导出工具
__all__ = ["check_python_syntax", "check_python_syntax_core"]

