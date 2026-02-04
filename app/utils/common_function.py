"""
公共函数
"""

import json
import os
from app.core.logger_config import logger


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
        logger.error(f"JSON解析失败: {e}")
        raise ValueError(f"无法解析LLM响应为JSON: {e}")


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
