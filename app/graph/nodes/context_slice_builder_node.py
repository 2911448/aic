"""
Context Slice Builder Node - 上下文切片构建节点
仅负责构建可编辑上下文切片（Editable Context Slice）
"""

from typing import Literal, Optional

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command

from app.core.logger_config import logger
from app.graph.state import IssueProcessState, NodeName, ProcessStage
from app.utils.dependency_analyzer import DependencyAnalyzer, DependencyGraph
from app.utils.tree_sitter_service import ASTInfo, Symbol, tree_sitter_service
from app.sandbox.manager import get_sandbox_manager
from app.sandbox.exceptions import SandboxNotFoundError
from app.sandbox.file_service import FileService
from app.schemas.context_assembly import (
    DependencySignature,
    EditableContextSlice,
    TargetContext,
)
from app.utils.common_function import detect_language


class ContextSliceBuilderNode:
    """
    上下文切片构建节点
    
    职责：
    - 从 sandbox 读取目标文件
    - 使用 AST 提取目标符号完整代码
    - 分析依赖关系并生成依赖签名
    - 构建 EditableContextSlice 并写入 state.context
    
    不涉及：
    - Sandbox 创建/销毁（由 SandboxBootstrap/Teardown 管理）
    - 路由决策（由 MainRouter 管理）
    """

    def __init__(self):
        """初始化节点"""
        self.tree_sitter = tree_sitter_service
        self.dependency_analyzer = DependencyAnalyzer()
        self.sandbox_manager = get_sandbox_manager()

    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal["main_router", "sandbox_teardown"]]:
        """
        构建可编辑上下文切片

        Args:
            state: 当前工作流状态

        Returns:
            Command对象，成功返回 main_router，失败返回 sandbox_teardown
        """
        update_dict = {}

        try:
            # 发送进度事件
            await adispatch_custom_event(
                ProcessStage.CONTEXT_BUILDING.value,
                {
                    "status": NodeName.CONTEXT_SLICE_BUILDER.value,
                    "progress": "正在构建可编辑上下文切片...",
                    "think_chain_item": {
                        "type": NodeName.CONTEXT_SLICE_BUILDER.value,
                        "title": "上下文切片构建",
                        "desc": "加载目标代码，分析依赖，构建编辑上下文",
                        "urls": [],
                    },
                },
            )

            # 1. 获取当前目标
            targeting = state.get("targeting", {})
            current_target_dict = targeting.get("current_target")
            
            if not current_target_dict:
                error_msg = "没有当前目标符号，无法构建上下文"
                logger.error(error_msg)
                runtime = state.get("runtime", {})
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "error": error_msg,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.CONTEXT_SLICE_BUILDER.value,
                            ],
                            "current_step": NodeName.CONTEXT_SLICE_BUILDER.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

            current_target = TargetContext(**current_target_dict)

            # 2. 获取 Sandbox 信息
            sandbox = state.get("sandbox", {})
            sandbox_id = sandbox.get("sandbox_id")
            
            if not sandbox_id:
                error_msg = "缺少 sandbox_id，Sandbox 应由 SandboxBootstrap 节点预先创建"
                logger.error(error_msg)
                runtime = state.get("runtime", {})
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "error": error_msg,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.CONTEXT_SLICE_BUILDER.value,
                            ],
                            "current_step": NodeName.CONTEXT_SLICE_BUILDER.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)
            
            # 3. 获取 sandbox 实例
            try:
                sandbox_instance = await self.sandbox_manager.get_sandbox(sandbox_id)
                logger.debug(f"使用已有沙箱: {sandbox_id}")
            except SandboxNotFoundError:
                error_msg = f"Sandbox {sandbox_id} 不存在"
                logger.error(error_msg)
                runtime = state.get("runtime", {})
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "error": error_msg,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.CONTEXT_SLICE_BUILDER.value,
                            ],
                            "current_step": NodeName.CONTEXT_SLICE_BUILDER.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

            # 4. 构建上下文切片
            context_slice = await self._build_context_slice(state, current_target, sandbox_instance)

            if context_slice is None:
                error_msg = "无法构建上下文切片"
                logger.error(error_msg)
                runtime = state.get("runtime", {})
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "error": error_msg,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.CONTEXT_SLICE_BUILDER.value,
                            ],
                            "current_step": NodeName.CONTEXT_SLICE_BUILDER.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

            # 5. 更新状态
            runtime = state.get("runtime", {})
            update_dict.update(
                {
                    "context": {
                        "editable_context": context_slice.model_dump(),
                    },
                    "runtime": {
                        **runtime,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.CONTEXT_SLICE_BUILDER.value,
                        ],
                        "current_step": NodeName.CONTEXT_SLICE_BUILDER.value,
                    },
                }
            )

            logger.info(
                f"上下文切片构建完成: {current_target.symbol_name}, "
                f"依赖: {len(context_slice.dependency_signatures)} 个"
            )

            # 发送完成事件
            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": NodeName.CONTEXT_SLICE_BUILDER.value,
                    "progress": "上下文切片构建完成",
                    "think_chain_item": {
                        "type": NodeName.CONTEXT_SLICE_BUILDER.value,
                        "title": "上下文切片构建",
                        "desc": f"目标: {current_target.symbol_name}, "
                               f"依赖: {len(context_slice.dependency_signatures)} 个",
                        "urls": [],
                    },
                },
            )

            return Command(update=update_dict, goto=NodeName.MAIN_ROUTER.value)

        except Exception as e:
            logger.error(f"上下文切片构建失败: {e}", exc_info=True)
            runtime = state.get("runtime", {})
            update_dict.update(
                {
                    "runtime": {
                        **runtime,
                        "error": f"上下文切片构建失败: {str(e)}",
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.CONTEXT_SLICE_BUILDER.value,
                        ],
                        "current_step": NodeName.CONTEXT_SLICE_BUILDER.value,
                    },
                }
            )

            return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

    async def _build_context_slice(
        self,
        state: IssueProcessState,
        target: TargetContext,
        sandbox,
    ) -> Optional[EditableContextSlice]:
        """
        构建可编辑上下文切片
        节点职责：编排 tools 调用，组装 EditableContextSlice

        Args:
            state: 当前工作流状态
            target: 目标符号
            sandbox: 沙箱实例

        Returns:
            可编辑上下文切片
        """
        # 获取检索结果
        retrieval = state.get("retrieval", {})
        retrieved_code = retrieval.get("retrieved_code", [])

        # 1. 从目标中获取文件路径和语言
        file_path = target.file_path
        language = detect_language(file_path)
        
        # 2. 先从检索结果中获取 target_snippet
        target_snippet = self._find_target_snippet(target, retrieved_code)
        
        # 3. 从 Sandbox 读取完整文件内容
        content = None
        try:
            # 直接使用 FileService 读取文件
            file_service = FileService(self.sandbox_manager, sandbox.id)
            content = await file_service.read_file(file_path)
            logger.info(f"从沙箱读取完整文件: {file_path}")
            
            # 更新 target_snippet 的内容为完整文件
            if target_snippet:
                target_snippet["content"] = content
            else:
                target_snippet = {
                    "content": content,
                    "file_path": file_path,
                    "symbol_name": target.symbol_name,
                }
        except Exception as e:
            logger.warning(f"从沙箱读取文件失败 {file_path}: {e}, 使用检索片段")
            if target_snippet:
                content = target_snippet.get("content", "")
                logger.info(f"使用检索片段作为回退")
            else:
                logger.error(f"无法获取目标符号的代码: {target.symbol_name}")
                return None

        # 4. 解析 AST
        try:
            # 直接使用 tree_sitter_service 解析
            ast_info = self.tree_sitter.parse_code(content, language, file_path)
            if not ast_info:
                raise Exception(f"无法解析 AST: language={language}")
        except Exception as e:
            logger.warning(f"无法解析 AST: {file_path}, {e}")
            return self._create_fallback_slice(target, target_snippet)

        # 5. 提取目标符号的完整代码
        target_symbol = self._find_symbol_in_ast(target.symbol_name, ast_info)
        if target_symbol:
            full_code = self._extract_symbol_code(content, target_symbol)
            editable_start = target_symbol.start_line
            editable_end = target_symbol.end_line
        else:
            full_code = content
            editable_start = target.start_line
            editable_end = target.end_line

        # 6. 分析依赖关系
        try:
            # 直接使用 dependency_analyzer 分析
            # 构建 AST 信息字典
            ast_infos = {file_path: ast_info}
            dependency_graph = self.dependency_analyzer.analyze_dependencies(
                snippets=[target_snippet],
                ast_infos=ast_infos
            )
        except Exception as e:
            logger.warning(f"依赖分析失败: {e}, 使用空依赖图")
            dependency_graph = DependencyGraph()

        # 7. 获取前向依赖的签名
        dependency_signatures = await self._get_dependency_signatures(
            target.symbol_name,
            dependency_graph,
            retrieved_code,
            language
        )

        # 8. 提取导入语句
        imports = self._extract_imports(ast_info)

        # 9. 提取相关 Schema 定义
        schema_definitions = await self._extract_schemas(
            retrieved_code,
            dependency_signatures,
            language
        )

        # 10. 构建上下文切片
        context_slice = EditableContextSlice(
            target=target,
            full_code=full_code,
            dependency_signatures=dependency_signatures,
            imports=imports,
            schema_definitions=schema_definitions,
            editable_start_line=editable_start,
            editable_end_line=editable_end,
            file_content=content
        )

        return context_slice

    # ========================================================================
    # Helper Methods (TODO: 考虑下沉到 tools 层)
    # ========================================================================

    def _find_target_snippet(
        self,
        target: TargetContext,
        snippets: list[dict]
    ) -> Optional[dict]:
        """查找包含目标符号的代码片段"""
        # 优先按文件路径和符号名匹配
        for snippet in snippets:
            if (snippet.get("file_path") == target.file_path and
                snippet.get("symbol_name") == target.symbol_name):
                return snippet

        # 降级：按文件路径匹配
        for snippet in snippets:
            if snippet.get("file_path") == target.file_path:
                return snippet

        # 最终降级：返回第一个
        return snippets[0] if snippets else None

    def _find_symbol_in_ast(
        self,
        symbol_name: str,
        ast_info: ASTInfo
    ) -> Optional[Symbol]:
        """在 AST 中查找符号"""
        for symbol in ast_info.symbols:
            if symbol.name == symbol_name:
                return symbol
            # 检查带父类的名称
            if symbol.parent and f"{symbol.parent}.{symbol.name}" == symbol_name:
                return symbol
        return None

    def _extract_symbol_code(
        self,
        content: str,
        symbol: Symbol
    ) -> str:
        """提取符号的完整代码"""
        lines = content.splitlines()
        start_idx = symbol.start_line - 1
        end_idx = symbol.end_line
        return "\n".join(lines[start_idx:end_idx])

    async def _get_dependency_signatures(
        self,
        target_symbol: str,
        dependency_graph: DependencyGraph,
        snippets: list[dict],
        language: str
    ) -> list[DependencySignature]:
        """
        获取依赖的签名（仅签名+Docstring，不含完整实现）

        Args:
            target_symbol: 目标符号名
            dependency_graph: 依赖图
            snippets: 代码片段列表
            language: 编程语言

        Returns:
            依赖签名列表
        """
        signatures = []
        callees = dependency_graph.get_callees(target_symbol)

        # 构建符号到片段的映射
        symbol_to_snippet = {}
        for snippet in snippets:
            content = snippet.get("content", "")
            file_path = snippet.get("file_path", "")
            ast_info = self.tree_sitter.parse_code(content, language, file_path)
            if ast_info:
                for sym in ast_info.symbols:
                    symbol_to_snippet[sym.name] = (snippet, sym, ast_info)
                    if sym.parent:
                        full_name = f"{sym.parent}.{sym.name}"
                        symbol_to_snippet[full_name] = (snippet, sym, ast_info)

        for callee in callees:
            if callee in symbol_to_snippet:
                snippet, sym, ast_info = symbol_to_snippet[callee]
                
                # 提取 Docstring
                docstring = self._extract_docstring(
                    snippet.get("content", ""),
                    sym,
                    language
                )

                signatures.append(DependencySignature(
                    symbol_name=callee,
                    file_path=snippet.get("file_path", ""),
                    signature=sym.signature or f"def {sym.name}(...)",
                    docstring=docstring,
                    symbol_type=sym.type,
                ))

        logger.info(f"提取到 {len(signatures)} 个依赖签名")
        return signatures

    def _extract_docstring(
        self,
        content: str,
        symbol: Symbol,
        language: str
    ) -> Optional[str]:
        """提取符号的 Docstring"""
        if language != "python":
            return None

        lines = content.splitlines()
        if symbol.start_line >= len(lines):
            return None

        # 查找定义后的 docstring
        for i in range(symbol.start_line, min(symbol.start_line + 5, len(lines))):
            line = lines[i].strip()
            if line.startswith('"""') or line.startswith("'''"):
                # 单行 docstring
                if line.count('"""') >= 2 or line.count("'''") >= 2:
                    return line.strip('"""').strip("'''").strip()
                # 多行 docstring
                quote = '"""' if '"""' in line else "'''"
                docstring_lines = [line.replace(quote, "")]
                for j in range(i + 1, min(i + 20, len(lines))):
                    end_line = lines[j]
                    if quote in end_line:
                        docstring_lines.append(end_line.replace(quote, ""))
                        break
                    docstring_lines.append(end_line)
                return "\n".join(docstring_lines).strip()

        return None

    def _extract_imports(self, ast_info: ASTInfo) -> list[str]:
        """提取导入语句"""
        imports = []
        for imp in ast_info.imports:
            if imp.names:
                imports.append(f"from {imp.module} import {', '.join(imp.names)}")
            else:
                imports.append(f"import {imp.module}")
        return imports

    async def _extract_schemas(
        self,
        snippets: list[dict],
        dependency_signatures: list[DependencySignature],
        language: str
    ) -> list[str]:
        """
        提取相关的 Schema/类型定义

        主要关注 Pydantic 模型等输出结构
        """
        schemas = []
        
        # 收集依赖中的类型引用
        type_names = set()
        for sig in dependency_signatures:
            # 简单启发式：查找类似 -> SomeType 或 : SomeType 的模式
            signature = sig.signature
            if "->" in signature:
                return_part = signature.split("->")[1].strip()
                # 提取类型名（去除 Optional, list 等包装）
                type_name = return_part.split("[")[0].strip()
                if type_name and type_name[0].isupper():
                    type_names.add(type_name)

        # 在代码片段中查找这些类型定义
        for snippet in snippets:
            content = snippet.get("content", "")
            file_path = snippet.get("file_path", "")
            
            ast_info = self.tree_sitter.parse_code(content, language, file_path)
            if ast_info:
                for sym in ast_info.symbols:
                    if sym.type == "class" and sym.name in type_names:
                        # 提取类定义
                        class_code = self._extract_symbol_code(content, sym)
                        # 只保留签名和字段定义（前 20 行）
                        lines = class_code.splitlines()[:20]
                        schemas.append("\n".join(lines))

        return schemas

    def _create_fallback_slice(
        self,
        target: TargetContext,
        snippet: dict = None
    ) -> EditableContextSlice:
        """创建降级的上下文切片"""
        content = snippet.get("content", "") if snippet else ""
        
        return EditableContextSlice(
            target=target,
            full_code=content,
            dependency_signatures=[],
            imports=[],
            schema_definitions=[],
            editable_start_line=target.start_line,
            editable_end_line=target.end_line,
            file_content=content
        )


