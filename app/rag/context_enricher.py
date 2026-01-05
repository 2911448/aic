"""
上下文补全服务
从沙箱读取完整文件内容，补全缺失的上下文，生成骨架图
"""

from typing import Optional
from pydantic import BaseModel

from app.core.logger_config import logger
from app.rag.tree_sitter_service import ASTInfo, Symbol, tree_sitter_service
from app.schemas.code_scope import EnrichedSnippet


class FileContext(BaseModel):
    """文件上下文"""
    file_path: str
    content: str
    language: str
    total_lines: int
    imports: list[str] = []
    class_definitions: list[str] = []


class SkeletonContext(BaseModel):
    """骨架图上下文"""
    file_path: str
    skeleton_code: str
    original_lines: int
    skeleton_lines: int
    compression_ratio: float
    language: str


class ContextEnricher:
    """上下文补全服务"""

    def __init__(self):
        """初始化服务"""
        self.tree_sitter = tree_sitter_service
        self._file_cache: dict[str, str] = {}

    async def enrich_snippet(
        self,
        snippet: dict,
        sandbox_file_service = None
    ) -> EnrichedSnippet:
        """
        补全代码片段的上下文

        Args:
            snippet: 原始代码片段
            sandbox_file_service: 沙箱文件服务（用于读取完整文件）

        Returns:
            补全后的代码片段
        """
        file_path = snippet.get("file_path", "")
        content = snippet.get("content", "")
        start_line = snippet.get("start_line", 1)
        end_line = snippet.get("end_line", 1)
        language = snippet.get("language", "python")

        # 如果没有沙箱服务，直接返回原始内容
        if sandbox_file_service is None:
            return EnrichedSnippet(
                file_path=file_path,
                content=content,
                start_line=start_line,
                end_line=end_line,
                language=language,
            )

        try:
            # 读取完整文件
            full_content = await self._read_file(file_path, sandbox_file_service)
            
            # 判断是否需要使用骨架图
            if self.should_use_skeleton(file_path, full_content):
                skeleton = await self.get_skeleton_context(
                    file_path,
                    full_content,
                    language
                )
                return EnrichedSnippet(
                    file_path=file_path,
                    content=skeleton.skeleton_code,
                    start_line=1,
                    end_line=skeleton.skeleton_lines,
                    language=language,
                    is_skeleton=True,
                )

            # 提取 imports 和类定义
            has_imports = self._has_imports(full_content, language)
            has_class_context = self._has_class_context(full_content, language)

            # 如果原始片段缺少上下文，补全
            if not has_imports or not has_class_context:
                enriched_content = self._enrich_with_context(
                    full_content,
                    content,
                    start_line,
                    end_line,
                    language
                )
                return EnrichedSnippet(
                    file_path=file_path,
                    content=enriched_content,
                    start_line=start_line,
                    end_line=end_line,
                    language=language,
                    has_imports=True,
                    has_class_context=True,
                )

            # 否则返回原始内容
            return EnrichedSnippet(
                file_path=file_path,
                content=content,
                start_line=start_line,
                end_line=end_line,
                language=language,
                has_imports=has_imports,
                has_class_context=has_class_context,
            )

        except Exception as e:
            logger.error(f"补全代码片段失败 ({file_path}): {e}", exc_info=True)
            # 失败时返回原始内容
            return EnrichedSnippet(
                file_path=file_path,
                content=content,
                start_line=start_line,
                end_line=end_line,
                language=language,
            )

    async def get_full_file_context(
        self,
        file_path: str,
        sandbox_file_service = None
    ) -> Optional[FileContext]:
        """
        获取完整文件上下文

        Args:
            file_path: 文件路径
            sandbox_file_service: 沙箱文件服务

        Returns:
            文件上下文，失败返回 None
        """
        if sandbox_file_service is None:
            return None

        try:
            content = await self._read_file(file_path, sandbox_file_service)
            language = self._detect_language(file_path)
            
            # 解析 AST
            ast_info = self.tree_sitter.parse_code(content, language, file_path)
            
            imports = []
            class_defs = []
            
            if ast_info:
                imports = [imp.module for imp in ast_info.imports]
                class_defs = [
                    sym.name for sym in ast_info.symbols 
                    if sym.type == "class"
                ]

            return FileContext(
                file_path=file_path,
                content=content,
                language=language,
                total_lines=len(content.splitlines()),
                imports=imports,
                class_definitions=class_defs,
            )

        except Exception as e:
            logger.error(f"获取文件上下文失败 ({file_path}): {e}")
            return None

    async def get_skeleton_context(
        self,
        file_path: str,
        content: Optional[str] = None,
        language: Optional[str] = None,
        max_lines: int = 2000
    ) -> Optional[SkeletonContext]:
        """
        生成文件骨架图

        Args:
            file_path: 文件路径
            content: 文件内容（如果为 None，从缓存读取）
            language: 编程语言
            max_lines: 最大行数阈值

        Returns:
            骨架图上下文
        """
        if content is None:
            content = self._file_cache.get(file_path, "")
            if not content:
                logger.warning(f"无法获取文件内容: {file_path}")
                return None

        if language is None:
            language = self._detect_language(file_path)

        try:
            # 解析 AST
            ast_info = self.tree_sitter.parse_code(content, language, file_path)
            if not ast_info:
                logger.warning(f"AST 解析失败，无法生成骨架图: {file_path}")
                return None

            # 生成骨架代码
            skeleton_code = self._generate_skeleton(content, ast_info, language)
            
            original_lines = len(content.splitlines())
            skeleton_lines = len(skeleton_code.splitlines())
            compression_ratio = 1.0 - (skeleton_lines / original_lines) if original_lines > 0 else 0.0

            logger.info(
                f"生成骨架图: {file_path}, "
                f"原始 {original_lines} 行 -> 骨架 {skeleton_lines} 行 "
                f"(压缩率: {compression_ratio:.1%})"
            )

            return SkeletonContext(
                file_path=file_path,
                skeleton_code=skeleton_code,
                original_lines=original_lines,
                skeleton_lines=skeleton_lines,
                compression_ratio=compression_ratio,
                language=language,
            )

        except Exception as e:
            logger.error(f"生成骨架图失败 ({file_path}): {e}", exc_info=True)
            return None

    def should_use_skeleton(self, file_path: str, content: str) -> bool:
        """
        判断是否需要使用骨架图模式

        Args:
            file_path: 文件路径
            content: 文件内容

        Returns:
            是否使用骨架图
        """
        lines = content.splitlines()
        # 超过 2000 行使用骨架图
        return len(lines) > 2000

    def _generate_skeleton(
        self,
        content: str,
        ast_info: ASTInfo,
        language: str
    ) -> str:
        """
        生成骨架代码

        策略：
        - 保留所有 import 语句
        - 保留所有类定义和函数签名
        - 保留文档字符串
        - 删除函数体实现细节
        """
        lines = content.splitlines()
        skeleton_lines = []

        # 保留的行号集合
        keep_lines = set()

        # 1. 保留所有 import
        for imp in ast_info.imports:
            keep_lines.add(imp.start_line)

        # 2. 保留所有符号的定义行和文档字符串
        for symbol in ast_info.symbols:
            # 保留定义行
            keep_lines.add(symbol.start_line)
            
            # 保留签名行（通常是定义行）
            if symbol.signature:
                keep_lines.add(symbol.start_line)
            
            # 保留文档字符串（通常在定义后的几行）
            for i in range(symbol.start_line, min(symbol.start_line + 5, symbol.end_line + 1)):
                if i <= len(lines):
                    line = lines[i - 1].strip()
                    if line.startswith('"""') or line.startswith("'''") or line.startswith("#"):
                        keep_lines.add(i)

        # 3. 构建骨架代码
        sorted_lines = sorted(keep_lines)
        last_line = 0
        
        for line_num in sorted_lines:
            if line_num > len(lines):
                continue
            
            # 如果行号跳跃太大，添加省略标记
            if line_num - last_line > 1:
                skeleton_lines.append("    # ... (implementation details omitted)")
            
            skeleton_lines.append(lines[line_num - 1])
            last_line = line_num

        return "\n".join(skeleton_lines)

    def _has_imports(self, content: str, language: str) -> bool:
        """检查代码是否包含 import 语句"""
        if language == "python":
            return "import " in content or "from " in content
        elif language in ("javascript", "typescript"):
            return "import " in content or "require(" in content
        return False

    def _has_class_context(self, content: str, language: str) -> bool:
        """检查代码是否包含类定义"""
        if language == "python":
            return "class " in content
        elif language in ("javascript", "typescript"):
            return "class " in content
        return False

    def _enrich_with_context(
        self,
        full_content: str,
        snippet_content: str,
        start_line: int,
        end_line: int,
        language: str
    ) -> str:
        """
        用完整文件的上下文补全代码片段

        Args:
            full_content: 完整文件内容
            snippet_content: 代码片段内容
            start_line: 起始行号
            end_line: 结束行号
            language: 编程语言

        Returns:
            补全后的代码
        """
        lines = full_content.splitlines()
        enriched_lines = []

        # 1. 添加 imports（文件开头的 import 语句）
        for i, line in enumerate(lines[:50]):  # 只检查前 50 行
            if language == "python":
                if line.strip().startswith(("import ", "from ")):
                    enriched_lines.append(line)
            elif language in ("javascript", "typescript"):
                if line.strip().startswith("import ") or "require(" in line:
                    enriched_lines.append(line)

        if enriched_lines:
            enriched_lines.append("")  # 空行分隔

        # 2. 添加原始代码片段
        enriched_lines.append(snippet_content)

        return "\n".join(enriched_lines)

    async def _read_file(
        self,
        file_path: str,
        sandbox_file_service
    ) -> str:
        """
        从沙箱读取文件（带缓存）

        Args:
            file_path: 文件路径
            sandbox_file_service: 沙箱文件服务

        Returns:
            文件内容
        """
        # 检查缓存
        if file_path in self._file_cache:
            return self._file_cache[file_path]

        # 读取文件
        content = await sandbox_file_service.read_file(file_path)
        
        # 缓存
        self._file_cache[file_path] = content
        
        return content

    def _detect_language(self, file_path: str) -> str:
        """根据文件扩展名检测语言"""
        ext = file_path.split(".")[-1].lower()
        
        lang_map = {
            "py": "python",
            "js": "javascript",
            "ts": "typescript",
            "tsx": "typescript",
            "jsx": "javascript",
        }
        
        return lang_map.get(ext, "python")

    def clear_cache(self):
        """清空文件缓存"""
        self._file_cache.clear()
        logger.info("文件缓存已清空")


# 全局实例
context_enricher = ContextEnricher()

