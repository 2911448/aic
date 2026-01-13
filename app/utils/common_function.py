"""
公共函数
"""

import json
import os
from typing import TYPE_CHECKING

from app.core.logger_config import logger

if TYPE_CHECKING:
    from app.sandbox.file_service import FileService


def parse_json_response(text: str) -> dict:
    """
    解析JSON，优先尝试直接解析，失败后再提取markdown代码块

    Args:
        text: LLM响应文本，可能包含JSON wrapped in markdown

    Returns:
        解析后的JSON字典
    """
    if not text or not text.strip():
        logger.error("收到空的LLM响应")
        raise ValueError("LLM响应为空，无法解析JSON")
    
    text = text.strip()
    
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    if text.startswith("```json"):
        start = text.find("\n", 7) 
        if start != -1 and text.rstrip().endswith("```"):
            end = text.rstrip().rfind("```")
            if end > start:
                text = text[start+1:end].strip()
    elif text.startswith("```"):
        start = text.find("\n", 3) 
        if start != -1 and text.rstrip().endswith("```"):
            end = text.rstrip().rfind("```")
            if end > start:
                text = text[start+1:end].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"JSON解析失败: {e}\n响应文本: {text[:500]}")
        raise ValueError(f"无法解析LLM响应为JSON: {e}")


# ============================================================================
# Sandbox 代码读取工具
# ============================================================================

async def read_latest_code_from_sandbox(
    file_service: "FileService",
    retrieved_code: list[dict]
) -> list[dict]:
    """
    从 sandbox 读取应用 patch 后的最新代码
    
    该方法用于在代码修改后，从 sandbox 环境读取文件的最新状态，
    以便进行准确的依赖分析和影响扫描。
    
    Args:
        file_service: 文件服务实例（FileService）
        retrieved_code: 检索到的代码片段列表（包含文件路径信息）
    
    Returns:
        最新代码片段列表，每项包含 file_path, content, language, symbol_name
    """
    latest_snippets = []
    seen_files = set()

    for snippet in retrieved_code:
        file_path = snippet.get("file_path", "")
        if not file_path or file_path in seen_files:
            continue
        
        seen_files.add(file_path)
        
        try:
            # 读取 sandbox 中的最新文件内容
            latest_content = await file_service.read_file(file_path)
            
            latest_snippets.append({
                "file_path": file_path,
                "content": latest_content,
                "language": snippet.get("language", "python"),
                "symbol_name": snippet.get("symbol_name", ""),
            })
            
            logger.debug(f"成功读取 sandbox 文件: {file_path}")
            
        except Exception as e:
            logger.warning(f"读取 sandbox 文件 {file_path} 失败: {e}")
            # 降级：使用原始检索内容
            if snippet.get("content"):
                latest_snippets.append({
                    "file_path": file_path,
                    "content": snippet.get("content", ""),
                    "language": snippet.get("language", "python"),
                    "symbol_name": snippet.get("symbol_name", ""),
                })
                logger.debug(f"↓ 降级使用检索内容: {file_path}")
            continue

    return latest_snippets


def detect_language(file_path: str, default: str = "python") -> str:
    """
    从文件路径检测编程语言或文件类型
    
    Args:
        file_path: 文件路径（如 "app/main.py" 或 "src/index.ts" 或 "pyproject.toml"）
        default: 未识别时的默认语言，默认为 "python"
    
    Returns:
        语言标识符（python, javascript, typescript, yaml, markdown 等）
    """
    # 获取文件名（用于特殊文件判断）
    filename = os.path.basename(file_path).lower()
    
    # 特殊文件名优先匹配（精确匹配）
    special_files = {
        "requirements.txt": "python-requirements",
        "package.json": "node-dependencies",
        "package-lock.json": "node-lock",
        "cargo.toml": "rust-dependencies",
        "cargo.lock": "rust-lock",
        "go.mod": "go-dependencies",
        "go.sum": "go-lock",
        "pyproject.toml": "python-project",
        "poetry.lock": "python-lock",
        "pipfile": "python-dependencies",
        "pipfile.lock": "python-lock",
        "gemfile": "ruby-dependencies",
        "gemfile.lock": "ruby-lock",
        "composer.json": "php-dependencies",
        "composer.lock": "php-lock",
        "dockerfile": "dockerfile",
        ".dockerignore": "dockerignore",
        "readme.md": "readme",
        "readme.txt": "readme",
        "readme": "readme",
    }
    
    if filename in special_files:
        return special_files[filename]
    
    # 支持带点的扩展名检测
    if "." not in file_path:
        return default
    
    ext = "." + file_path.split(".")[-1].lower()
    
    language_map = {
        # 编程语言
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".jsx": "javascript",
        ".tsx": "typescript",
        ".java": "java",
        ".go": "go",
        ".rs": "rust",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".cxx": "cpp",
        ".c": "c",
        ".h": "c",
        ".hpp": "cpp",
        ".rb": "ruby",
        ".php": "php",
        ".swift": "swift",
        ".kt": "kotlin",
        ".kts": "kotlin",
        ".cs": "csharp",
        ".sh": "shell",
        ".bash": "shell",
        
        # 配置文件
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".json": "json",
        ".ini": "ini",
        ".conf": "config",
        ".cfg": "config",
        ".xml": "xml",
        
        # 文档文件
        ".md": "markdown",
        ".txt": "text",
        ".rst": "restructuredtext",
        ".adoc": "asciidoc",
        
        # 其他
        ".sql": "sql",
        ".graphql": "graphql",
        ".proto": "protobuf",
    }
    return language_map.get(ext, default)
