"""
Apply Patch Tool - 在 Sandbox 中应用补丁

这是一个可被 LangChain Agent 调用的工具。
"""

from typing import Optional

from langchain_core.tools import tool

from app.core.logger_config import logger
from app.sandbox.git_service import GitService
from app.sandbox.manager import get_sandbox_manager


@tool
async def apply_patch_in_sandbox(
    sandbox_id: str,
    patch_content: str
) -> str:
    """
    在 Sandbox 中应用 Git 补丁
    
    Args:
        sandbox_id: Sandbox ID
        patch_content: 补丁内容（unified diff 格式）
    
    Returns:
        应用结果消息
    
    Raises:
        Exception: 补丁应用失败
    """
    try:
        sandbox_manager = get_sandbox_manager()
        git_service = GitService(sandbox_manager, sandbox_id)
        
        # 应用补丁
        result = await git_service.apply_patch(patch_content)
        
        logger.info(f"[Tool] apply_patch: success={result.success}, message={result.message}")
        
        if not result.success:
            raise Exception(f"补丁应用失败: {result.message}")
        
        return result.message
    except Exception as e:
        error_msg = f"应用补丁失败: {str(e)}"
        logger.error(error_msg)
        raise Exception(error_msg)


# 导出工具
__all__ = ["apply_patch_in_sandbox"]

