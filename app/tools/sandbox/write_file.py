"""
Write File Tool - 向 Sandbox 写入文件内容

这是一个可被 LangChain Agent 调用的工具。
"""


from langchain_core.tools import tool

from app.core.logger_config import logger
from app.sandbox.file_service import FileService
from app.sandbox.manager import get_sandbox_manager


async def write_file_to_sandbox_core(
    sandbox_id: str,
    file_path: str,
    content: str,
    create_dirs: bool = True,
) -> str:
    """
    核心文件写入函数（可直接调用）
    
    Args:
        sandbox_id: Sandbox ID
        file_path: 文件相对路径，例如 "src/main.py"
        content: 要写入的文件内容
        create_dirs: 是否自动创建目录（默认 True）
    
    Returns:
        成功消息
    
    Raises:
        Exception: 文件写入失败
    """
    try:
        sandbox_manager = get_sandbox_manager()
        file_service = FileService(sandbox_manager, sandbox_id)
        
        # 写入文件
        await file_service.write_file(
            path=file_path,
            content=content,
            create_dirs=create_dirs,
        )
        
        # 获取文件行数用于日志
        line_count = len(content.splitlines())
        
        logger.info(
            f"[Tool] write_file: {file_path}, "
            f"{line_count} lines, {len(content)} bytes"
        )
        
        return f"成功写入文件 {file_path}"
    except Exception as e:
        error_msg = f"写入文件失败 {file_path}: {str(e)}"
        logger.error(error_msg)
        raise Exception(error_msg)


@tool
async def write_file_to_sandbox(
    sandbox_id: str,
    file_path: str,
    content: str,
    create_dirs: bool = True,
) -> str:
    """
    向 Sandbox 写入文件内容
    
    使用说明：
    - 用于创建新文件或覆盖现有文件
    - 自动创建必要的目录结构
    - 适合写入代码文件、配置文件等文本内容
    
    Args:
        sandbox_id: Sandbox ID
        file_path: 文件相对路径，例如 "src/main.py"
        content: 要写入的文件内容
        create_dirs: 是否自动创建目录（默认 True）
    
    Returns:
        成功消息
    
    Raises:
        Exception: 文件写入失败
    """
    return await write_file_to_sandbox_core(sandbox_id, file_path, content, create_dirs)


# 导出工具
__all__ = ["write_file_to_sandbox", "write_file_to_sandbox_core"]
