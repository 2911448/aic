"""
Tree-sitter 代码解析服务
支持多语言 AST 分析、符号提取、调用关系分析
"""

import hashlib
from pathlib import Path
from typing import Optional
import tree_sitter_python as tspython
import tree_sitter_javascript as tsjavascript
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Parser, Tree, Node

from pydantic import BaseModel, Field

from app.core.logger_config import logger


class Symbol(BaseModel):
    """代码符号（函数、类等）"""
    name: str = Field(description="符号名称")
    type: str = Field(description="符号类型：function, class, method, variable")
    start_line: int = Field(description="起始行号")
    end_line: int = Field(description="结束行号")
    start_byte: int = Field(description="起始字节位置")
    end_byte: int = Field(description="结束字节位置")
    parent: Optional[str] = Field(None, description="父符号名称（如类中的方法）")
    signature: Optional[str] = Field(None, description="函数/方法签名")


class Import(BaseModel):
    """导入语句"""
    module: str = Field(description="导入的模块名")
    names: list[str] = Field(default=[], description="导入的具体名称")
    alias: Optional[str] = Field(None, description="别名")
    start_line: int = Field(description="起始行号")
    is_relative: bool = Field(default=False, description="是否相对导入")


class FunctionCall(BaseModel):
    """函数调用"""
    caller: Optional[str] = Field(None, description="调用方（所在函数/类）")
    callee: str = Field(description="被调用的函数名")
    line: int = Field(description="调用所在行号")
    column: int = Field(description="调用所在列号")


class ASTInfo(BaseModel):
    """AST 分析结果"""
    symbols: list[Symbol] = Field(default=[], description="所有符号")
    imports: list[Import] = Field(default=[], description="所有导入")
    function_calls: list[FunctionCall] = Field(default=[], description="所有函数调用")
    language: str = Field(description="编程语言")
    file_path: str = Field(description="文件路径")


class TreeSitterService:
    """Tree-sitter 代码解析服务"""

    def __init__(self):
        """初始化解析器"""
        self._parsers = {}
        try:
            # Python
            py_lang = Language(tspython.language())
            py_parser = Parser(py_lang)
            self._parsers["python"] = py_parser

            # JavaScript
            js_lang = Language(tsjavascript.language())
            js_parser = Parser(js_lang)
            self._parsers["javascript"] = js_parser

            # TypeScript
            ts_lang = Language(tstypescript.language_typescript())
            ts_parser = Parser(ts_lang)
            self._parsers["typescript"] = ts_parser
        except Exception as e:
            logger.error(f"初始化 Tree-sitter 解析器失败: {e}")
            self._parsers = {}

    def parse_code(
        self,
        code: str | bytes,
        language: str,
        file_path: str = "<unknown>"
    ) -> Optional[ASTInfo]:
        """
        解析代码并提取 AST 信息

        Args:
            code: 代码内容（字符串或字节）
            language: 编程语言
            file_path: 文件路径（用于日志）

        Returns:
            AST 分析结果，如果失败返回 None
        """
        if language not in self._parsers:
            logger.warning(f"不支持的语言: {language}")
            return None

        try:
            if isinstance(code, str):
                code_bytes = code.encode("utf-8")
            else:
                code_bytes = code

            # 解析
            parser = self._parsers[language]
            tree = parser.parse(code_bytes)
            root = tree.root_node

            # 提取信息
            symbols = self._extract_symbols(root, code_bytes, language)
            imports = self._extract_imports(root, code_bytes, language)
            function_calls = self._extract_function_calls(root, code_bytes, language)

            return ASTInfo(
                symbols=symbols,
                imports=imports,
                function_calls=function_calls,
                language=language,
                file_path=file_path,
            )

        except Exception as e:
            logger.error(f"解析代码失败 ({file_path}): {e}", exc_info=True)
            return None

    def _extract_symbols(
        self,
        root: "Node",
        code: bytes,
        language: str
    ) -> list[Symbol]:
        """提取符号（函数、类等）"""
        symbols = []

        if language == "python":
            symbols.extend(self._extract_python_symbols(root, code))
        elif language in ("javascript", "typescript"):
            symbols.extend(self._extract_js_symbols(root, code))

        return symbols

    def _extract_python_symbols(self, root: "Node", code: bytes) -> list[Symbol]:
        """提取 Python 符号"""
        symbols = []

        def traverse(node: "Node", parent_name: Optional[str] = None):
            # 类定义
            if node.type == "class_definition":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = code[name_node.start_byte:name_node.end_byte].decode("utf-8")
                    symbols.append(Symbol(
                        name=name,
                        type="class",
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_byte=node.start_byte,
                        end_byte=node.end_byte,
                        parent=parent_name,
                    ))
                    # 递归处理类内的方法
                    for child in node.children:
                        traverse(child, name)
                return

            # 函数定义
            if node.type == "function_definition":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = code[name_node.start_byte:name_node.end_byte].decode("utf-8")
                    # 提取函数签名
                    params_node = node.child_by_field_name("parameters")
                    if params_node:
                        params_text = code[params_node.start_byte:params_node.end_byte].decode("utf-8")
                        signature = f"def {name}{params_text}"
                    else:
                        signature = f"def {name}()"

                    symbol_type = "method" if parent_name else "function"
                    symbols.append(Symbol(
                        name=name,
                        type=symbol_type,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_byte=node.start_byte,
                        end_byte=node.end_byte,
                        parent=parent_name,
                        signature=signature,
                    ))
                return

            # 递归遍历子节点
            for child in node.children:
                traverse(child, parent_name)

        traverse(root)
        return symbols

    def _extract_js_symbols(self, root: "Node", code: bytes) -> list[Symbol]:
        """提取 JavaScript/TypeScript 符号"""
        symbols = []

        def traverse(node: "Node", parent_name: Optional[str] = None):
            # 类声明
            if node.type in ("class_declaration", "class"):
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = code[name_node.start_byte:name_node.end_byte].decode("utf-8")
                    symbols.append(Symbol(
                        name=name,
                        type="class",
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_byte=node.start_byte,
                        end_byte=node.end_byte,
                        parent=parent_name,
                    ))
                    # 递归处理类内的方法
                    for child in node.children:
                        traverse(child, name)
                return

            # 函数声明
            if node.type in ("function_declaration", "function", "arrow_function"):
                name_node = node.child_by_field_name("name")
                name = None
                if name_node:
                    name = code[name_node.start_byte:name_node.end_byte].decode("utf-8")
                
                if name:
                    # 提取参数
                    params_node = node.child_by_field_name("parameters")
                    if params_node:
                        params_text = code[params_node.start_byte:params_node.end_byte].decode("utf-8")
                        signature = f"function {name}{params_text}"
                    else:
                        signature = f"function {name}()"

                    symbol_type = "method" if parent_name else "function"
                    symbols.append(Symbol(
                        name=name,
                        type=symbol_type,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_byte=node.start_byte,
                        end_byte=node.end_byte,
                        parent=parent_name,
                        signature=signature,
                    ))
                return

            # 方法定义
            if node.type == "method_definition":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = code[name_node.start_byte:name_node.end_byte].decode("utf-8")
                    symbols.append(Symbol(
                        name=name,
                        type="method",
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        start_byte=node.start_byte,
                        end_byte=node.end_byte,
                        parent=parent_name,
                    ))
                return

            # 递归遍历子节点
            for child in node.children:
                traverse(child, parent_name)

        traverse(root)
        return symbols

    def _extract_imports(
        self,
        root: "Node",
        code: bytes,
        language: str
    ) -> list[Import]:
        """提取导入语句"""
        imports = []

        if language == "python":
            imports.extend(self._extract_python_imports(root, code))
        elif language in ("javascript", "typescript"):
            imports.extend(self._extract_js_imports(root, code))

        return imports

    def _extract_python_imports(self, root: "Node", code: bytes) -> list[Import]:
        """提取 Python 导入"""
        imports = []

        def traverse(node: "Node"):
            # import xxx
            if node.type == "import_statement":
                for child in node.children:
                    if child.type == "dotted_name":
                        module = code[child.start_byte:child.end_byte].decode("utf-8")
                        imports.append(Import(
                            module=module,
                            names=[],
                            start_line=node.start_point[0] + 1,
                        ))

            # from xxx import yyy
            elif node.type == "import_from_statement":
                module_node = node.child_by_field_name("module_name")
                module = ""
                is_relative = False
                
                if module_node:
                    module = code[module_node.start_byte:module_node.end_byte].decode("utf-8")
                else:
                    # 检查是否有相对导入的点
                    for child in node.children:
                        if child.type == "relative_import":
                            is_relative = True
                            break

                names = []
                for child in node.children:
                    if child.type == "dotted_name":
                        names.append(code[child.start_byte:child.end_byte].decode("utf-8"))
                    elif child.type == "aliased_import":
                        name_node = child.child_by_field_name("name")
                        if name_node:
                            names.append(code[name_node.start_byte:name_node.end_byte].decode("utf-8"))

                imports.append(Import(
                    module=module,
                    names=names,
                    start_line=node.start_point[0] + 1,
                    is_relative=is_relative,
                ))

            # 递归
            for child in node.children:
                traverse(child)

        traverse(root)
        return imports

    def _extract_js_imports(self, root: "Node", code: bytes) -> list[Import]:
        """提取 JavaScript/TypeScript 导入"""
        imports = []

        def traverse(node: "Node"):
            # import xxx from 'yyy'
            if node.type == "import_statement":
                source_node = node.child_by_field_name("source")
                module = ""
                if source_node:
                    module = code[source_node.start_byte:source_node.end_byte].decode("utf-8").strip("'\"")

                imports.append(Import(
                    module=module,
                    names=[],
                    start_line=node.start_point[0] + 1,
                ))

            # 递归
            for child in node.children:
                traverse(child)

        traverse(root)
        return imports

    def _extract_function_calls(
        self,
        root: "Node",
        code: bytes,
        language: str
    ) -> list[FunctionCall]:
        """提取函数调用"""
        calls = []

        if language == "python":
            calls.extend(self._extract_python_calls(root, code))
        elif language in ("javascript", "typescript"):
            calls.extend(self._extract_js_calls(root, code))

        return calls

    def _extract_python_calls(self, root: "Node", code: bytes) -> list[FunctionCall]:
        """提取 Python 函数调用"""
        calls = []
        current_function = None

        def traverse(node: "Node", func_name: Optional[str] = None):
            nonlocal current_function

            # 进入函数/方法定义
            if node.type in ("function_definition", "async_function_definition"):
                name_node = node.child_by_field_name("name")
                if name_node:
                    old_func = current_function
                    current_function = code[name_node.start_byte:name_node.end_byte].decode("utf-8")
                    for child in node.children:
                        traverse(child, current_function)
                    current_function = old_func
                return

            # 函数调用
            if node.type == "call":
                func_node = node.child_by_field_name("function")
                if func_node:
                    callee = code[func_node.start_byte:func_node.end_byte].decode("utf-8")
                    calls.append(FunctionCall(
                        caller=current_function,
                        callee=callee,
                        line=node.start_point[0] + 1,
                        column=node.start_point[1],
                    ))

            # 递归
            for child in node.children:
                traverse(child, func_name)

        traverse(root)
        return calls

    def _extract_js_calls(self, root: "Node", code: bytes) -> list[FunctionCall]:
        """提取 JavaScript/TypeScript 函数调用"""
        calls = []
        current_function = None

        def traverse(node: "Node"):
            nonlocal current_function

            # 进入函数定义
            if node.type in ("function_declaration", "function", "method_definition"):
                name_node = node.child_by_field_name("name")
                if name_node:
                    old_func = current_function
                    current_function = code[name_node.start_byte:name_node.end_byte].decode("utf-8")
                    for child in node.children:
                        traverse(child)
                    current_function = old_func
                return

            # 函数调用
            if node.type == "call_expression":
                func_node = node.child_by_field_name("function")
                if func_node:
                    callee = code[func_node.start_byte:func_node.end_byte].decode("utf-8")
                    calls.append(FunctionCall(
                        caller=current_function,
                        callee=callee,
                        line=node.start_point[0] + 1,
                        column=node.start_point[1],
                    ))

            # 递归
            for child in node.children:
                traverse(child)

        traverse(root)
        return calls

    def find_symbol_by_line(
        self,
        ast_info: ASTInfo,
        line: int
    ) -> Optional[Symbol]:
        """
        根据行号查找符号

        Args:
            ast_info: AST 分析结果
            line: 行号

        Returns:
            找到的符号，如果没有返回 None
        """
        for symbol in ast_info.symbols:
            if symbol.start_line <= line <= symbol.end_line:
                return symbol
        return None

    def generate_code_anchor(
        self,
        code: str,
        start_line: int,
        end_line: int,
        context_lines: int = 3
    ) -> tuple[str, str]:
        """
        生成代码锚点（代码指纹）

        Args:
            code: 完整代码
            start_line: 目标起始行（1-based）
            end_line: 目标结束行（1-based）
            context_lines: 前后上下文行数

        Returns:
            (anchor_code, anchor_hash) 元组
        """
        lines = code.splitlines()
        
        # 计算锚点范围（包含上下文）
        anchor_start = max(0, start_line - 1 - context_lines)
        anchor_end = min(len(lines), end_line + context_lines)
        
        # 提取锚点代码
        anchor_lines = lines[anchor_start:anchor_end]
        anchor_code = "\n".join(anchor_lines)
        
        # 计算哈希
        anchor_hash = hashlib.sha256(anchor_code.encode("utf-8")).hexdigest()[:16]
        
        return anchor_code, f"sha256:{anchor_hash}"


# 全局实例
tree_sitter_service = TreeSitterService()

