"""
Read File Tool - 从 Sandbox 读取文件内容

这是一个可被 LangChain Agent 调用的工具。
"""


from langchain_core.tools import tool

from app.core.logger_config import logger
from app.sandbox.file_service import FileService
from app.sandbox.manager import get_sandbox_manager


async def read_file_from_sandbox_core(
    sandbox_id: str,
    file_path: str,
    start_line: int | None = None,
    end_line: int | None = None
) -> str:
    """
    核心文件读取函数（可直接调用）
    
    Args:
        sandbox_id: Sandbox ID
        file_path: 文件相对路径，例如 "src/main.py"
        start_line: 读取起始行号（可选）
        end_line: 读取结束行号（包含该行，可选）
    
    Returns:
        文件内容字符串（如果指定了行范围，返回指定行的内容）
        如果文件不存在，返回提示消息
    """
    try:
        sandbox_manager = get_sandbox_manager()
        file_service = FileService(sandbox_manager, sandbox_id)
        
        content = await file_service.read_file(file_path)
        
        # 如果指定了行范围，提取指定行
        if start_line is not None or end_line is not None:
            lines = content.splitlines(keepends=True)
            total_lines = len(lines)
            
            # 处理行号（从 1 开始转换为从 0 开始的索引）
            start_idx = (start_line - 1) if start_line is not None else 0
            end_idx = end_line if end_line is not None else total_lines
            
            # 边界检查
            start_idx = max(0, min(start_idx, total_lines))
            end_idx = max(start_idx, min(end_idx, total_lines))
            
            # 提取行范围
            selected_lines = lines[start_idx:end_idx]
            content = "".join(selected_lines)
            
            logger.info(
                f"[Tool] read_file: {file_path}, "
                f"lines {start_idx + 1}-{end_idx}"
            )
        else:
            # 没有指定行范围，读取整个文件
            logger.info(
                f"[Tool] read_file: {file_path}"
            )
        
        return content
    except Exception as e:
        error_msg = str(e)
        # 检查是否是文件不存在错误
        if "不存在" in error_msg or "not found" in error_msg.lower():
            logger.warning(f"[Tool] read_file: 文件不存在 {file_path}")
            # 返回友好的消息而不是抛出异常，让 LLM 可以继续决策
            return (
                f"[FILE_NOT_FOUND] 文件 '{file_path}' 不存在。\n"
                f"提示：如果需要创建此文件，请使用 write_file 工具，传入完整的文件内容。"
            )
        else:
            # 其他错误（如权限问题）才抛出异常
            logger.error(f"读取文件失败 {file_path}: {error_msg}")
            raise Exception(f"读取文件失败 {file_path}: {error_msg}")


@tool
async def read_file_from_sandbox(
    sandbox_id: str,
    file_path: str,
    start_line: int | None = None,
    end_line: int | None = None
) -> str:
    """
    从 Sandbox 读取文件内容
    
    使用建议：
    - 对于小文件（< 300 行），直接读取完整文件（不指定 start_line 和 end_line）
    - 对于大文件（≥ 300 行），可以分段读取以节省 token，每次读取不超过 100 行
    - 避免重复读取：如果已读取完整文件，不要再分段读取同一文件
    - 如果文件不存在，工具会返回提示消息，你可以决定是否使用 write_file 创建
    
    Args:
        sandbox_id: Sandbox ID
        file_path: 文件相对路径，例如 "src/main.py"
        start_line: 读取起始行号（可选，不指定则从第1行开始）
        end_line: 读取结束行号（包含该行，可选，不指定则读到文件末尾）
    
    Returns:
        文件内容字符串，或文件不存在的提示消息
    """
    return await read_file_from_sandbox_core(sandbox_id, file_path, start_line, end_line)


# 导出工具
__all__ = ["read_file_from_sandbox", "read_file_from_sandbox_core"]

