"""
代码解析器 - 提取函数、类等代码符号
"""

import ast
import os
from pathlib import Path
import time

from app.schemas.code import CodeSnippet
from app.core.logger_config import logger
from app.utils.common_function import detect_language


# 延迟导入，避免循环依赖
def get_code_chunker():
    """延迟导入 CodeChunker"""
    from app.rag.chunking import code_chunker

    return code_chunker


class CodeParser:
    """代码解析器基类"""

    @staticmethod
    def detect_language(file_path: str) -> str | None:
        """
        根据文件扩展名检测编程语言
        
        Note: 此方法已迁移到 app.utils.common_function.detect_language
        保留此方法用于向后兼容
        """
        # 使用公共函数，返回 None 如果是默认值 "python" 以外的语言
        result = detect_language(file_path, default=None)
        return result if result != "python" else result

    @staticmethod
    def is_empty_or_comments_only(file_path: str, language: str) -> bool:
        """
        检查文件是否为空或只包含注释

        Args:
            file_path: 文件路径
            language: 编程语言

        Returns:
            True if 文件为空或只有注释，False otherwise
        """
        try:
            with open(file_path, encoding="utf-8") as f:
                content = f.read()

            # 检查是否为空文件
            if not content or not content.strip():
                return True

            # Python 特殊处理：使用 AST 检查
            if language == "python":
                try:
                    tree = ast.parse(content)
                    # 检查是否有任何实际的代码节点（函数、类、赋值等）
                    has_code = False
                    for node in ast.walk(tree):
                        # 排除 Module 和文档字符串
                        if isinstance(
                            node,
                            (
                                ast.FunctionDef,
                                ast.AsyncFunctionDef,
                                ast.ClassDef,
                                ast.Assign,
                                ast.Import,
                                ast.ImportFrom,
                                ast.For,
                                ast.While,
                                ast.If,
                                ast.With,
                                ast.Try,
                            ),
                        ):
                            has_code = True
                            break
                    return not has_code
                except SyntaxError:
                    # 如果语法错误，保守起见返回 False（让后续处理）
                    return False

            # 其他语言：检查去除注释和空白后是否还有内容
            lines = content.splitlines()
            for line in lines:
                stripped = line.strip()
                # 跳过空行
                if not stripped:
                    continue
                # 跳过常见注释
                if (
                    stripped.startswith("#")  # Python, Shell
                    or stripped.startswith(
                        "//"
                    )  # JavaScript, TypeScript, Java, C++, Rust, Go
                    or stripped.startswith("/*")  # 多行注释开始
                    or stripped.startswith("*")  # 多行注释中间
                    or stripped.startswith("*/")  # 多行注释结束
                    or stripped.startswith("<!--")
                ):  # HTML/XML
                    continue
                # 如果有非注释内容，返回 False
                return False

            # 所有行都是注释或空行
            return True

        except Exception as e:
            logger.debug(f"检查文件内容失败 {file_path}: {e}")
            # 出错时保守处理，返回 False
            return False

    @staticmethod
    def parse_file(
        file_path: str,
        project_name: str,
        project_root: str | None = None,
    ) -> list[CodeSnippet]:
        """
        解析代码文件，提取函数/类等符号

        Args:
            file_path: 代码文件绝对路径
            project_name: 项目名称
            project_root: 项目根目录（用于计算相对路径）

        Returns:
            代码片段列表
        """
        language = CodeParser.detect_language(file_path)
        if not language:
            logger.warning(f"不支持的文件类型: {file_path}")
            return []

        # 检查文件是否为空或只有注释
        if CodeParser.is_empty_or_comments_only(file_path, language):
            logger.info(f"跳过空文件或只有注释的文件: {file_path}")
            return []

        # 计算相对路径
        if project_root:
            try:
                relative_path = os.path.relpath(file_path, project_root)
            except ValueError:
                relative_path = file_path
        else:
            relative_path = file_path

        # 根据语言选择解析器
        if language == "python":
            return PythonCodeParser.parse(file_path, project_name, relative_path)
        else:
            # 其他语言暂时使用通用解析器
            return GenericCodeParser.parse(
                file_path, project_name, relative_path, language
            )


class PythonCodeParser:
    """Python 代码解析器"""

    @staticmethod
    def parse(
        file_path: str, project_name: str, relative_path: str
    ) -> list[CodeSnippet]:
        """解析Python文件"""
        snippets = []

        try:
            with open(file_path, encoding="utf-8") as f:
                source_code = f.read()

            # 解析AST
            tree = ast.parse(source_code)
            lines = source_code.splitlines()

            # 提取类和函数
            for node in ast.walk(tree):
                extracted_snippets = None

                if isinstance(node, ast.FunctionDef):
                    # 函数定义
                    extracted_snippets = PythonCodeParser._extract_function(
                        node, lines, file_path, project_name, relative_path
                    )
                elif isinstance(node, ast.AsyncFunctionDef):
                    # 异步函数定义
                    extracted_snippets = PythonCodeParser._extract_function(
                        node,
                        lines,
                        file_path,
                        project_name,
                        relative_path,
                        is_async=True,
                    )
                elif isinstance(node, ast.ClassDef):
                    # 类定义
                    extracted_snippets = PythonCodeParser._extract_class(
                        node, lines, file_path, project_name, relative_path
                    )

                # 现在返回的是列表，可能包含多个片段（如果代码被切分）
                if extracted_snippets:
                    snippets.extend(extracted_snippets)

            logger.info(f"从 {file_path} 提取了 {len(snippets)} 个代码片段")

        except Exception as e:
            logger.error(f"解析Python文件失败 {file_path}: {e}")

        return snippets

    @staticmethod
    def _extract_function(
        node: ast.FunctionDef,
        lines: list[str],
        file_path: str,
        project_name: str,
        relative_path: str,
        is_async: bool = False,
    ) -> list[CodeSnippet] | None:
        """提取函数定义（可能返回多个片段，如果函数过大）"""
        try:
            start_line = node.lineno
            end_line = node.end_lineno or start_line

            # 提取函数代码
            content = "\n".join(lines[start_line - 1 : end_line])

            # 提取Docstring作为摘要
            docstring = ast.get_docstring(node)
            summary = None

            if docstring:
                # 如果有Docstring，截取前500字符作为摘要
                summary = docstring[:500] + "..." if len(docstring) > 500 else docstring
            else:
                # 如果没有Docstring，生成简单摘要：函数名 + 参数列表
                args = [arg.arg for arg in node.args.args]
                async_prefix = "async " if is_async else ""
                summary = f"{async_prefix}def {node.name}({', '.join(args)})"

            # 如果代码过长，先进行智能切分
            if len(content) > 65535:
                logger.info(f"函数 {node.name} 过大({len(content)}字符)，进行智能切分")

                # 创建临时的基础snippet（使用截断的content以通过验证）
                base_snippet = CodeSnippet(
                    project_name=project_name,
                    file_path=relative_path,
                    symbol_name=node.name,
                    language="python",
                    start_line=start_line,
                    end_line=end_line,
                    content=content[:65000] + "\n# ...",  # 临时截断
                    summary=summary,
                    last_updated=int(time.time()),
                    use_count=0,
                )

                # 使用完整content进行切分
                chunker = get_code_chunker()
                return chunker.chunk_python_code(content, base_snippet)

            # 正常长度，直接创建snippet
            base_snippet = CodeSnippet(
                project_name=project_name,
                file_path=relative_path,
                symbol_name=node.name,
                language="python",
                start_line=start_line,
                end_line=end_line,
                content=content,
                summary=summary,
                last_updated=int(time.time()),
                use_count=0,
            )

            return [base_snippet]

        except Exception as e:
            logger.error(f"提取函数失败: {e}")
            return None

    @staticmethod
    def _extract_class(
        node: ast.ClassDef,
        lines: list[str],
        file_path: str,
        project_name: str,
        relative_path: str,
    ) -> list[CodeSnippet] | None:
        """提取类定义（可能返回多个片段，如果类过大）"""
        try:
            start_line = node.lineno
            end_line = node.end_lineno or start_line

            # 提取类代码
            content = "\n".join(lines[start_line - 1 : end_line])

            # 提取类的Docstring作为摘要
            docstring = ast.get_docstring(node)
            summary = None

            if docstring:
                # 如果有Docstring，截取前500字符作为摘要
                summary = docstring[:500] + "..." if len(docstring) > 500 else docstring
            else:
                # 如果没有Docstring，生成简单摘要：类名 + 基类
                bases = [
                    base.id if isinstance(base, ast.Name) else str(base)
                    for base in node.bases
                ]
                if bases:
                    summary = f"class {node.name}({', '.join(bases)})"
                else:
                    summary = f"class {node.name}"

            # 如果代码过长，先进行智能切分
            if len(content) > 65535:
                logger.info(f"类 {node.name} 过大({len(content)}字符)，进行智能切分")

                # 创建临时的基础snippet（使用截断的content以通过验证）
                base_snippet = CodeSnippet(
                    project_name=project_name,
                    file_path=relative_path,
                    symbol_name=node.name,
                    language="python",
                    start_line=start_line,
                    end_line=end_line,
                    content=content[:65000] + "\n# ...",  # 临时截断
                    summary=summary,
                    last_updated=int(time.time()),
                    use_count=0,
                )

                # 使用完整content进行切分
                chunker = get_code_chunker()
                return chunker.chunk_python_code(content, base_snippet)

            # 正常长度，直接创建snippet
            base_snippet = CodeSnippet(
                project_name=project_name,
                file_path=relative_path,
                symbol_name=node.name,
                language="python",
                start_line=start_line,
                end_line=end_line,
                content=content,
                summary=summary,
                last_updated=int(time.time()),
                use_count=0,
            )

            return [base_snippet]

        except Exception as e:
            logger.error(f"提取类失败: {e}")
            return None


class GenericCodeParser:
    """通用代码解析器（用于非Python语言）"""

    @staticmethod
    def parse(
        file_path: str,
        project_name: str,
        relative_path: str,
        language: str,
    ) -> list[CodeSnippet]:
        """
        通用解析方法 - 将整个文件作为一个代码片段
        TODO: 后续可以集成tree-sitter等工具进行精确解析
        """
        snippets = []

        try:
            with open(file_path, encoding="utf-8") as f:
                content = f.read()

            # 如果文件过大，分块处理
            if len(content) > 65535:
                # 按行分块
                lines = content.splitlines()
                chunk_size = 1000  # 每1000行一个片段（更大的块）

                for i in range(0, len(lines), chunk_size):
                    chunk_lines = lines[i : i + chunk_size]
                    chunk_content = "\n".join(chunk_lines)

                    if len(chunk_content) > 65535:
                        chunk_content = chunk_content[:65000] + "\n// ... (truncated)"

                    # 生成摘要：文件名 + 行范围
                    chunk_num = i // chunk_size
                    total_chunks = (len(lines) + chunk_size - 1) // chunk_size
                    summary = f"{Path(file_path).stem} (lines {i + 1}-{min(i + chunk_size, len(lines))}, part {chunk_num + 1}/{total_chunks})"

                    snippet = CodeSnippet(
                        project_name=project_name,
                        file_path=relative_path,
                        symbol_name=f"{Path(file_path).stem}_chunk_{chunk_num}",
                        language=language,
                        start_line=i + 1,
                        end_line=min(i + chunk_size, len(lines)),
                        content=chunk_content,
                        summary=summary,
                        last_updated=int(time.time()),
                        use_count=0,
                    )
                    snippets.append(snippet)
            else:
                # 整个文件作为一个片段
                # 生成摘要：文件名 + 总行数
                total_lines = len(content.splitlines())
                summary = (
                    f"{Path(file_path).stem} ({language} file, {total_lines} lines)"
                )

                snippet = CodeSnippet(
                    project_name=project_name,
                    file_path=relative_path,
                    symbol_name=Path(file_path).stem,
                    language=language,
                    start_line=1,
                    end_line=total_lines,
                    content=content,
                    summary=summary,
                    last_updated=int(time.time()),
                    use_count=0,
                )
                snippets.append(snippet)

            logger.info(f"从 {file_path} 提取了 {len(snippets)} 个代码片段")

        except Exception as e:
            logger.error(f"解析文件失败 {file_path}: {e}")

        return snippets
