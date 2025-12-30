"""
代码智能切分器 - 基于LangChain的结构化切分
"""

import ast
from langchain_text_splitters import RecursiveCharacterTextSplitter, Language

from app.schemas.code import CodeSnippet
from app.core.logger_config import logger


class CodeChunker:
    """代码智能切分器"""

    # LangChain支持的语言映射
    LANGCHAIN_LANGUAGE_MAP = {
        "python": Language.PYTHON,
        "javascript": Language.JS,
        "typescript": Language.TS,
        "java": Language.JAVA,
        "go": Language.GO,
        "cpp": Language.CPP,
        "c": Language.CPP,  # C使用CPP的分隔符
        "rust": Language.RUST,
    }

    def __init__(self, chunk_size: int = 65000, chunk_overlap: int = 500):
        """
        初始化代码切分器

        Args:
            chunk_size: 每个代码块的最大字符数（默认65000，接近65535限制）
            chunk_overlap: 代码块之间的重叠字符数
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk_python_code(
        self,
        code: str,
        base_snippet: CodeSnippet,
    ) -> list[CodeSnippet]:
        """
        Python代码智能切分（保留完整的函数签名）

        Args:
            code: 完整代码
            base_snippet: 基础代码片段信息（包含元数据）

        Returns:
            切分后的代码片段列表
        """
        # 如果代码不需要切分，直接返回
        if len(code) <= self.chunk_size:
            return [base_snippet]

        logger.info(
            f"代码过长({len(code)}字符)，开始切分: {base_snippet.file_path}:{base_snippet.symbol_name}"
        )

        try:
            # 使用LangChain的Python分隔符
            splitter = RecursiveCharacterTextSplitter.from_language(
                language=Language.PYTHON,
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap,
            )

            # 切分代码
            chunks = splitter.split_text(code)
            snippets = []

            for i, chunk in enumerate(chunks):
                lines_in_chunk = chunk.count("\n") + 1
                estimated_start_line = base_snippet.start_line + sum(
                    c.count("\n") for c in chunks[:i]
                )
                estimated_end_line = estimated_start_line + lines_in_chunk - 1

                # 生成chunk摘要
                summary = self._generate_chunk_summary(
                    base_snippet.symbol_name, i + 1, len(chunks)
                )

                snippet = CodeSnippet(
                    project_name=base_snippet.project_name,
                    file_path=base_snippet.file_path,
                    symbol_name=f"{base_snippet.symbol_name}_chunk_{i + 1}",
                    language=base_snippet.language,
                    start_line=estimated_start_line,
                    end_line=estimated_end_line,
                    content=chunk,
                    summary=summary,
                    last_updated=base_snippet.last_updated,
                    use_count=base_snippet.use_count,
                )
                snippets.append(snippet)

            logger.info(f"代码切分完成，共{len(snippets)}个片段")
            return snippets

        except Exception as e:
            logger.error(f"Python代码切分失败: {e}")
            # 降级处理：返回截断的原始代码
            truncated_code = code[:65000] + "\n# ... (truncated)"
            base_snippet.content = truncated_code
            base_snippet.summary = f"{base_snippet.symbol_name} (truncated)"
            return [base_snippet]

    def chunk_generic_code(
        self,
        code: str,
        base_snippet: CodeSnippet,
    ) -> list[CodeSnippet]:
        """
        通用代码切分（使用LangChain）

        Args:
            code: 完整代码
            base_snippet: 基础代码片段信息

        Returns:
            切分后的代码片段列表
        """
        # 如果代码不需要切分，直接返回
        if len(code) <= self.chunk_size:
            return [base_snippet]

        logger.info(
            f"代码过长({len(code)}字符)，开始切分: {base_snippet.file_path}:{base_snippet.symbol_name}"
        )

        try:
            # 获取对应的LangChain语言类型
            langchain_lang = self.LANGCHAIN_LANGUAGE_MAP.get(base_snippet.language)

            if langchain_lang:
                # 使用语言特定的分隔符
                splitter = RecursiveCharacterTextSplitter.from_language(
                    language=langchain_lang,
                    chunk_size=self.chunk_size,
                    chunk_overlap=self.chunk_overlap,
                )
            else:
                # 使用通用分隔符
                splitter = RecursiveCharacterTextSplitter(
                    chunk_size=self.chunk_size,
                    chunk_overlap=self.chunk_overlap,
                    separators=["\n\n", "\n", " ", ""],
                )

            # 切分代码
            chunks = splitter.split_text(code)
            snippets = []

            for i, chunk in enumerate(chunks):
                lines_in_chunk = chunk.count("\n") + 1
                estimated_start_line = base_snippet.start_line + sum(
                    c.count("\n") for c in chunks[:i]
                )
                estimated_end_line = estimated_start_line + lines_in_chunk - 1

                summary = self._generate_chunk_summary(
                    base_snippet.symbol_name, i + 1, len(chunks)
                )

                snippet = CodeSnippet(
                    project_name=base_snippet.project_name,
                    file_path=base_snippet.file_path,
                    symbol_name=f"{base_snippet.symbol_name}_chunk_{i + 1}",
                    language=base_snippet.language,
                    start_line=estimated_start_line,
                    end_line=estimated_end_line,
                    content=chunk,
                    summary=summary,
                    last_updated=base_snippet.last_updated,
                    use_count=base_snippet.use_count,
                )
                snippets.append(snippet)

            logger.info(f"代码切分完成，共{len(snippets)}个片段")
            return snippets

        except Exception as e:
            logger.error(f"通用代码切分失败: {e}")
            # 降级处理
            truncated_code = code[:65000] + "\n# ... (truncated)"
            base_snippet.content = truncated_code
            base_snippet.summary = f"{base_snippet.symbol_name} (truncated)"
            return [base_snippet]

    def _generate_chunk_summary(
        self, symbol_name: str, chunk_index: int, total_chunks: int
    ) -> str:
        """
        生成代码块摘要

        Args:
            symbol_name: 符号名称
            chunk_index: 当前块索引（从1开始）
            total_chunks: 总块数

        Returns:
            摘要文本
        """
        if total_chunks == 1:
            return f"{symbol_name}"
        else:
            return f"{symbol_name} (part {chunk_index}/{total_chunks})"

    def extract_docstring(self, code: str, language: str) -> str | None:
        """
        提取代码的文档字符串

        Args:
            code: 代码文本
            language: 编程语言

        Returns:
            文档字符串，如果没有则返回None
        """
        if language == "python":
            return self._extract_python_docstring(code)
        # 其他语言可以扩展
        return None

    def _extract_python_docstring(self, code: str) -> str | None:
        """
        提取Python代码的Docstring

        Args:
            code: Python代码文本

        Returns:
            Docstring内容，如果没有则返回None
        """
        try:
            tree = ast.parse(code)
            docstring = ast.get_docstring(tree)
            if docstring:
                # 限制docstring长度
                if len(docstring) > 500:
                    docstring = docstring[:497] + "..."
                return docstring
            return None
        except Exception as e:
            logger.debug(f"提取Docstring失败: {e}")
            return None


# 全局单例
code_chunker = CodeChunker()
