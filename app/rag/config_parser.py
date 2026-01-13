"""
配置文件解析器 - 处理非代码文件（配置、文档、依赖等）
"""

import os
import time
from pathlib import Path

from app.core.logger_config import logger
from app.schemas.code import CodeSnippet
from app.utils.common_function import detect_language


class ConfigFileParser:
    """非代码文件解析器"""

    # Symbol Name 策略映射
    SYMBOL_NAME_STRATEGIES = {
        # Python 相关
        "python-project": "Python项目配置与依赖管理",
        "python-requirements": "Python依赖包列表",
        "python-dependencies": "Python依赖配置",
        "python-lock": "Python依赖锁定文件",
        
        # Node.js 相关
        "node-dependencies": "Node.js项目配置与依赖",
        "node-lock": "Node.js依赖锁定文件",
        
        # Rust 相关
        "rust-dependencies": "Rust项目配置与依赖",
        "rust-lock": "Rust依赖锁定文件",
        
        # Go 相关
        "go-dependencies": "Go模块依赖配置",
        "go-lock": "Go依赖校验和文件",
        
        # Ruby 相关
        "ruby-dependencies": "Ruby项目依赖配置",
        "ruby-lock": "Ruby依赖锁定文件",
        
        # PHP 相关
        "php-dependencies": "PHP项目依赖配置",
        "php-lock": "PHP依赖锁定文件",
        
        # 配置文件
        "yaml": "YAML配置文件",
        "toml": "TOML配置文件",
        "json": "JSON配置文件",
        "ini": "INI配置文件",
        "config": "应用配置文件",
        "xml": "XML配置文件",
        
        # 文档文件
        "markdown": "Markdown文档",
        "text": "文本文档",
        "readme": "项目说明文档",
        "restructuredtext": "reStructuredText文档",
        "asciidoc": "AsciiDoc文档",
        
        # Docker 相关
        "dockerfile": "Docker容器构建配置",
        "dockerignore": "Docker忽略规则配置",
        
        # 其他
        "sql": "SQL脚本",
        "graphql": "GraphQL Schema定义",
        "protobuf": "Protocol Buffer定义",
    }

    @staticmethod
    def is_config_file(file_path: str) -> bool:
        """
        判断是否为配置/文档类文件

        Args:
            file_path: 文件路径

        Returns:
            是否为配置/文档文件
        """
        language = detect_language(file_path)
        
        # 检查是否为非代码文件类型
        non_code_types = [
            "yaml", "toml", "json", "ini", "config", "xml",
            "markdown", "text", "readme", "restructuredtext", "asciidoc",
            "python-requirements", "python-project", "python-dependencies", "python-lock",
            "node-dependencies", "node-lock",
            "rust-dependencies", "rust-lock",
            "go-dependencies", "go-lock",
            "ruby-dependencies", "ruby-lock",
            "php-dependencies", "php-lock",
            "dockerfile", "dockerignore",
            "sql", "graphql", "protobuf",
        ]
        
        return language in non_code_types

    @staticmethod
    def get_file_category(language: str) -> str:
        """
        获取文件分类（用于选择 LLM prompt 类型）

        Args:
            language: 文件语言类型

        Returns:
            文件分类：code/config/doc/dependency
        """
        dependency_types = [
            "python-requirements", "python-project", "python-dependencies", "python-lock",
            "node-dependencies", "node-lock",
            "rust-dependencies", "rust-lock",
            "go-dependencies", "go-lock",
            "ruby-dependencies", "ruby-lock",
            "php-dependencies", "php-lock",
        ]
        
        doc_types = [
            "markdown", "text", "readme", "restructuredtext", "asciidoc",
        ]
        
        config_types = [
            "yaml", "toml", "json", "ini", "config", "xml",
            "dockerfile", "dockerignore",
        ]
        
        if language in dependency_types:
            return "dependency"
        elif language in doc_types:
            return "doc"
        elif language in config_types:
            return "config"
        else:
            return "code"

    @staticmethod
    def generate_symbol_name(file_path: str, language: str) -> str:
        """
        根据文件类型生成有意义的 symbol_name

        Args:
            file_path: 文件路径
            language: 文件语言类型

        Returns:
            Symbol name
        """
        # 从策略映射中查找
        symbol_name = ConfigFileParser.SYMBOL_NAME_STRATEGIES.get(language)
        
        if symbol_name:
            return symbol_name
        
        # 降级处理：使用文件名
        filename = os.path.basename(file_path)
        return f"{filename}"

    @staticmethod
    async def parse(
        file_path: str,
        project_name: str,
        relative_path: str,
    ) -> list[CodeSnippet]:
        """
        解析配置/文档文件

        Args:
            file_path: 文件绝对路径
            project_name: 项目名称
            relative_path: 文件相对路径

        Returns:
            代码片段列表（通常只有1个）
        """
        snippets = []

        try:
            # 检测文件类型
            language = detect_language(file_path)
            
            # 读取文件内容
            with open(file_path, encoding="utf-8") as f:
                content = f.read()

            # 检查是否为空文件
            if not content.strip():
                logger.info(f"跳过空文件: {file_path}")
                return []

            # 生成 symbol_name
            symbol_name = ConfigFileParser.generate_symbol_name(file_path, language)

            # 获取文件行数
            total_lines = len(content.splitlines())

            # 如果文件过大，截断 content（但保留完整的用于 summary 生成）
            max_content_length = 65000
            truncated_content = content
            if len(content) > max_content_length:
                truncated_content = content[:max_content_length] + "\n... (内容过长，已截断)"
                logger.info(f"文件过大({len(content)}字符)，已截断: {file_path}")

            # 创建基础 snippet（summary 稍后填充）
            snippet = CodeSnippet(
                project_name=project_name,
                file_path=relative_path,
                symbol_name=symbol_name,
                language=language,
                start_line=1,
                end_line=total_lines,
                content=truncated_content,
                summary=None,  # 将由外部 LLM 生成
                last_updated=int(time.time()),
                use_count=0,
            )

            snippets.append(snippet)
            logger.info(f"解析配置/文档文件: {file_path} (类型: {language})")

        except Exception as e:
            logger.error(f"解析配置/文档文件失败 {file_path}: {e}")

        return snippets

    @staticmethod
    def should_skip_file(file_path: str) -> bool:
        """
        判断是否应该跳过该文件（如 .lock 文件）

        Args:
            file_path: 文件路径

        Returns:
            是否跳过
        """
        filename = os.path.basename(file_path).lower()
        
        # 跳过锁文件
        lock_files = [
            "uv.lock",
            "package-lock.json",
            "yarn.lock",
            "pnpm-lock.yaml",
            "poetry.lock",
            "pipfile.lock",
            "cargo.lock",
            "go.sum",
            "gemfile.lock",
            "composer.lock",
        ]
        
        if filename in lock_files:
            return True
        
        # 跳过 .lock 扩展名
        if filename.endswith(".lock"):
            return True
        
        return False


# 全局实例
config_file_parser = ConfigFileParser()
