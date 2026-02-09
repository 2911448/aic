"""
Symbolic Search Tool - 符号搜索工具

使用 ripgrep (rg) 在 sandbox 代码库中定位符号定义和引用（定位置）。
"""

import re
from typing import Literal

from langchain_core.tools import tool

from app.core.logger_config import logger
from app.sandbox.manager import get_sandbox_manager


async def symbolic_search_core(
    sandbox_id: str,
    symbol_name: str,
    search_type: Literal["definition", "reference", "all"] = "all",
    file_pattern: str | None = None,
) -> dict:
    """
    符号搜索核心函数
    
    Args:
        sandbox_id: Sandbox ID
        symbol_name: 符号名称（类名、函数名等）
        search_type: 搜索类型（definition: 定义, reference: 引用, all: 全部）
        file_pattern: 文件模式（如 "*.py"，可选）
    
    Returns:
        搜索结果字典，包含：
        - definitions: 定义列表 [{"file": str, "line": int, "content": str}]
        - references: 引用列表 [{"file": str, "line": int, "content": str}]
    """
    try:
        sandbox_manager = get_sandbox_manager()
        
        results = {
            "definitions": [],
            "references": [],
        }
        
        # 构建 rg 命令
        # 定义模式：def xxx, class xxx, function xxx 等
        if search_type in ["definition", "all"]:
            def_patterns = [
                f"def {symbol_name}",  # Python function
                f"class {symbol_name}",  # Python class
                f"function {symbol_name}",  # JavaScript function
                f"const {symbol_name}",  # JavaScript const
                f"let {symbol_name}",  # JavaScript let
            ]
            
            for pattern in def_patterns:
                cmd = f"rg -n '{pattern}'"
                if file_pattern:
                    cmd += f" -g '{file_pattern}'"
                
                result = await sandbox_manager.execute_command(
                    sandbox_id=sandbox_id,
                    command=cmd,
                    timeout=10,
                )
                
                # 解析输出
                if result.exit_code == 0 and result.stdout:
                    for line in result.stdout.strip().split("\n"):
                        if not line:
                            continue
                        # 格式: file:line:content
                        match = re.match(r"^(.+?):(\d+):(.+)$", line)
                        if match:
                            results["definitions"].append({
                                "file": match.group(1),
                                "line": int(match.group(2)),
                                "content": match.group(3).strip(),
                            })
        
        # 引用模式：简单的符号名出现
        if search_type in ["reference", "all"]:
            cmd = f"rg -n '\\b{symbol_name}\\b'"
            if file_pattern:
                cmd += f" -g '{file_pattern}'"
            
            result = await sandbox_manager.execute_command(
                sandbox_id=sandbox_id,
                command=cmd,
                timeout=10,
            )
            
            if result.exit_code == 0 and result.stdout:
                for line in result.stdout.strip().split("\n"):
                    if not line:
                        continue
                    match = re.match(r"^(.+?):(\d+):(.+)$", line)
                    if match:
                        # 排除已经在 definitions 中的
                        ref = {
                            "file": match.group(1),
                            "line": int(match.group(2)),
                            "content": match.group(3).strip(),
                        }
                        if ref not in results["definitions"]:
                            results["references"].append(ref)
        
        logger.info(
            f"[symbolic_search] symbol: {symbol_name}, "
            f"defs: {len(results['definitions'])}, refs: {len(results['references'])}"
        )
        
        return results
    
    except Exception as e:
        logger.error(f"[symbolic_search] 执行失败: {e}", exc_info=True)
        raise


@tool
async def symbolic_search(
    sandbox_id: str,
    symbol_name: str,
    search_type: Literal["definition", "reference", "all"] = "all",
    file_pattern: str | None = None,
) -> dict:
    """
    符号搜索：使用 ripgrep 在代码库中定位符号定义和引用（定位置）
    
    Args:
        sandbox_id: Sandbox ID
        symbol_name: 符号名称（类名、函数名、变量名等）
        search_type: 搜索类型
            - "definition": 只搜索定义（def/class/function 等）
            - "reference": 只搜索引用（符号出现的位置）
            - "all": 搜索定义和引用（默认）
        file_pattern: 文件模式（如 "*.py"，可选）
    
    Returns:
        搜索结果字典：
        - definitions: 定义列表 [{"file": str, "line": int, "content": str}]
        - references: 引用列表 [{"file": str, "line": int, "content": str}]
    """
    return await symbolic_search_core(sandbox_id, symbol_name, search_type, file_pattern)


# 导出
__all__ = ["symbolic_search", "symbolic_search_core"]
