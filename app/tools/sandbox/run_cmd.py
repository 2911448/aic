"""
Run Command Tool - 在 Sandbox 中执行命令

这是一个可被 LangChain Agent 调用的工具。
用于运行 linter、测试、import smoke 等命令。
"""

from langchain_core.tools import tool

from app.core.logger_config import logger
from app.sandbox.manager import get_sandbox_manager


async def run_command_in_sandbox_core(
    sandbox_id: str,
    command: str,
    timeout: int = 30
) -> dict:
    """
    在 Sandbox 中执行命令
    
    Args:
        sandbox_id: Sandbox ID
        command: 要执行的命令（可以在命令中使用任意所需命令，如 cd 切换目录、ruff 检查代码等）
        timeout: 超时时间（秒），默认 30 秒
    
    Returns:
        执行结果字典，包含:
        - exit_code: 退出码
        - stdout: 标准输出
        - stderr: 标准错误
        - success: 是否成功（exit_code == 0）
    
    Raises:
        Exception: 命令执行失败或超时
    """
    try:
        sandbox_manager = get_sandbox_manager()
        
        # 执行命令
        result = await sandbox_manager.execute_command(
            sandbox_id=sandbox_id,
            command=command,
            timeout=timeout
        )
        
        logger.info(
            f"[Tool] run_command: cmd='{command[:50]}...', "
            f"exit_code={result.exit_code}"
        )
        
        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "success": result.exit_code == 0,
        }
    except Exception as e:
        error_msg = f"执行命令失败 '{command}': {str(e)}"
        logger.error(error_msg)
        raise Exception(error_msg)


@tool
async def run_command_in_sandbox(
    sandbox_id: str,
    command: str,
    timeout: int = 30
) -> dict:
    """
    在 Sandbox 中执行命令 - LangChain Agent 工具版本
    
    Args:
        sandbox_id: Sandbox ID
        command: 要执行的命令（可以在命令中使用任意所需命令，如 cd 切换目录、ruff 检查代码等）
        timeout: 超时时间（秒），默认 30 秒
    
    Returns:
        执行结果字典
    
    Raises:
        Exception: 命令执行失败或超时
    """
    return await run_command_in_sandbox_core(sandbox_id, command, timeout)


# 导出工具
__all__ = ["run_command_in_sandbox", "run_command_in_sandbox_core"]

