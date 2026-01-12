"""
GitIgnore Parser - 解析 .gitignore 文件

提供统一的 gitignore 解析和路径匹配功能。
"""

import os
import re
from typing import Optional

from app.core.logger_config import logger


# 默认忽略规则（作为后备）
DEFAULT_IGNORE_PATTERNS = [
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "dist",
    "build",
    ".tox",
    "htmlcov",
    ".coverage",
    "*.egg-info",
]


def parse_gitignore_content(content: str) -> list[str]:
    """
    解析 .gitignore 文件内容
    
    Args:
        content: .gitignore 文件内容
    
    Returns:
        忽略规则列表
    """
    patterns = []
    
    for line in content.splitlines():
        line = line.strip()
        
        # 跳过空行和注释
        if not line or line.startswith("#"):
            continue
        
        # 移除行尾注释
        if "#" in line:
            line = line.split("#")[0].strip()
        
        # 处理否定规则（!开头的）- 暂时跳过
        if line.startswith("!"):
            continue
        
        # 添加到规则集
        if line:
            patterns.append(line)
    
    return patterns


def should_ignore_path(file_path: str, patterns: list[str], repo_path: str = ".") -> bool:
    """
    判断文件路径是否应该被忽略
    
    Args:
        file_path: 文件路径（绝对或相对）
        patterns: 忽略规则列表
        repo_path: 仓库根路径
    
    Returns:
        是否应该忽略
    """
    # 计算相对路径
    if file_path.startswith(repo_path):
        rel_path = os.path.relpath(file_path, repo_path)
    else:
        rel_path = file_path
    
    # 规范化路径分隔符
    rel_path = rel_path.replace("\\", "/")
    
    for pattern in patterns:
        if match_gitignore_pattern(rel_path, pattern):
            return True
    
    return False


def match_gitignore_pattern(path: str, pattern: str) -> bool:
    """
    匹配 gitignore 规则
    
    支持的规则：
    - 简单字符串：匹配目录名或文件名
    - 以 / 结尾：匹配目录
    - * 通配符：匹配任意字符（除 /）
    - ** 通配符：匹配任意路径
    
    Args:
        path: 文件相对路径
        pattern: gitignore 规则
    
    Returns:
        是否匹配
    """
    # 规范化模式
    pattern = pattern.strip()
    
    # 处理目录规则（以 / 结尾）
    if pattern.endswith("/"):
        pattern = pattern[:-1]
        # 目录规则：检查路径是否包含该目录
        return f"/{pattern}/" in f"/{path}/" or path.startswith(f"{pattern}/")
    
    # 处理通配符规则
    if "*" in pattern:
        # 转换为正则表达式
        # ** 匹配任意路径
        regex_pattern = pattern.replace("**", "<<<DOUBLE_STAR>>>")
        regex_pattern = regex_pattern.replace("*", "[^/]*")
        regex_pattern = regex_pattern.replace("<<<DOUBLE_STAR>>>", ".*")
        
        # 如果模式包含 /，则匹配完整路径
        if "/" in pattern:
            regex_pattern = f"^{regex_pattern}$"
        else:
            # 否则匹配路径中的任意部分
            regex_pattern = f"(^|/){regex_pattern}($|/)"
        
        try:
            if re.search(regex_pattern, path):
                return True
        except re.error:
            # 正则表达式错误，回退到简单匹配
            logger.debug(f"正则表达式错误: {regex_pattern}")
    
    # 简单字符串匹配（目录名或文件名）
    path_parts = path.split("/")
    
    # 检查是否匹配路径中的某个部分
    if pattern in path_parts:
        return True
    
    # 检查是否匹配文件名
    if path.endswith(pattern):
        return True
    
    # 检查路径是否以模式开头（如 dist 匹配 dist/xxx）
    if pattern + "/" in path or path.startswith(pattern + "/"):
        return True
    
    return False


def get_default_ignore_patterns() -> list[str]:
    """
    获取默认忽略规则
    
    Returns:
        默认忽略规则列表
    """
    return list(DEFAULT_IGNORE_PATTERNS)

