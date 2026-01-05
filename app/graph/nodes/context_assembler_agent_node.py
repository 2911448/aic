"""
Context Assembler Agent Node - 上下文组装节点
构建可编辑上下文切片（Editable Context Slice）
"""

from typing import Literal, Optional

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command

from app.core.logger_config import logger
from app.graph.state import IssueProcessState, NodeName, ProcessStage
from app.rag.dependency_analyzer import DependencyAnalyzer, DependencyGraph
from app.rag.tree_sitter_service import ASTInfo, Symbol, tree_sitter_service
from app.sandbox.manager import get_sandbox_manager
from app.sandbox.exceptions import SandboxNotFoundError
from app.sandbox.models import GitAuthConfig, GitAuthType, SandboxConfig
from app.sandbox.git_service import GitService
from app.sandbox.file_service import FileService
from app.config.app_config import app_config
from app.schemas.context_assembly import (
    DependencySignature,
    EditableContextSlice,
    TargetContext,
    TargetStatus,
)


class ContextAssemblerAgentNode:
    """上下文组装 Agent 节点"""

    def __init__(self):
        """初始化节点"""
        self.tree_sitter = tree_sitter_service
        self.dependency_analyzer = DependencyAnalyzer()
        self.sandbox_manager = get_sandbox_manager()
        # 估算：每个字符约 0.25 个 token
        self.chars_per_token = 4

    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal[NodeName.PLAN.value]]:
        """
        构建可编辑上下文切片

        Args:
            state: 当前工作流状态

        Returns:
            Command对象，返回 plan 节点
        """
        update_dict = {}

        try:
            # 发送进度事件
            await adispatch_custom_event(
                ProcessStage.CONTEXT_ASSEMBLY.value,
                {
                    "status": NodeName.CONTEXT_ASSEMBLER.value,
                    "progress": "正在构建可编辑上下文...",
                    "think_chain_item": {
                        "type": NodeName.CONTEXT_ASSEMBLER.value,
                        "title": "上下文组装",
                        "desc": "加载目标代码，分析依赖，构建编辑上下文",
                        "urls": [],
                    },
                },
            )

            # 获取当前目标
            current_target_dict = state.get("current_target")
            if not current_target_dict:
                logger.error("没有当前目标符号")
                update_dict.update(
                    {
                        "error": "没有当前目标符号，无法构建上下文",
                        "executed_nodes": [
                            *state.get("executed_nodes", []),
                            NodeName.CONTEXT_ASSEMBLER.value,
                        ],
                        "current_step": NodeName.CONTEXT_ASSEMBLER.value,
                    }
                )
                return Command(update=update_dict, goto=NodeName.END.value)

            current_target = TargetContext(**current_target_dict)

            # 获取或创建 Sandbox
            sandbox_id = state.get("sandbox_id")
            sandbox = None
            
            if sandbox_id:
                try:
                    sandbox = await self.sandbox_manager.get_sandbox(sandbox_id)
                    logger.debug(f"复用现有沙箱: {sandbox_id}")
                except SandboxNotFoundError:
                    logger.warning(f"沙箱 {sandbox_id} 不存在，创建新沙箱")
                    sandbox = None
            
            # 如果没有有效的沙箱，创建一个
            if not sandbox:
                logger.info("ContextAssembler: 创建新沙箱用于文件操作")
                
                # 1. 准备 Git 认证配置 (从应用配置中加载)
                git_auth = None
                if app_config.sandbox.git_auth:
                    git_auth = GitAuthConfig(
                        auth_type=GitAuthType(app_config.sandbox.git_auth.auth_type),
                        ssh_private_key_path=app_config.sandbox.git_auth.ssh_private_key_path,
                        http_token=app_config.sandbox.git_auth.http_token,
                        http_username=app_config.sandbox.git_auth.http_username,
                    )

                # 2. 创建沙箱
                sb_config = SandboxConfig(git_auth=git_auth)
                sandbox = await self.sandbox_manager.create_sandbox(config=sb_config)
                update_dict["sandbox_id"] = sandbox.id
                logger.info(f"沙箱创建成功: {sandbox.id}")

                # 3. 拉取代码 (新增步骤)
                project_info = state.get("project_info", {})
                repo_url = project_info.get("git_http_url") or project_info.get("http_url")
                default_branch = project_info.get("default_branch", "main")
                
                if repo_url:
                    logger.info(f"正在克隆代码仓库: {repo_url}")
                    try:
                        # 创建 GitService 实例
                        git_service = GitService(self.sandbox_manager, sandbox.id)
                        clone_result = await git_service.clone(
                            repo_url=repo_url,
                            branch=default_branch,
                        )
                        # 保存仓库路径到 update_dict，供后续使用
                        update_dict["repo_path"] = clone_result.repo_path
                        repo_path = clone_result.repo_path
                        logger.info(f"代码克隆成功: {clone_result.repo_path}, commit={clone_result.commit_hash[:8] if clone_result.commit_hash else 'unknown'}")
                    except Exception as e:
                        logger.error(f"代码克隆失败: {e}")
                        update_dict.update(
                            {
                                "error": f"代码克隆失败: {str(e)}",
                                "executed_nodes": [
                                    *state.get("executed_nodes", []),
                                    NodeName.CONTEXT_ASSEMBLER.value,
                                ],
                                "current_step": NodeName.CONTEXT_ASSEMBLER.value,
                            }
                        )
                        return Command(update=update_dict, goto=NodeName.END.value)
                else:
                    logger.warning("未找到项目仓库地址，跳过代码克隆")
                    repo_path = None
            else:
                # 复用已有沙箱，从 state 获取 repo_path
                repo_path = state.get("repo_path")
                if not repo_path:
                    # 尝试从 sandbox 的 repo_url 推断
                    repo_url = getattr(sandbox, "repo_url", None)
                    if repo_url:
                        repo_name = repo_url.split("/")[-1].replace(".git", "")
                        repo_path = repo_name
                logger.debug(f"复用沙箱的仓库路径: {repo_path}")

            # 构建上下文切片
            context_slice = await self._assemble_context(state, current_target, sandbox, repo_path)

            if context_slice is None:
                logger.error("无法构建上下文切片")
                update_dict.update(
                    {
                        "error": "无法构建上下文切片",
                        "executed_nodes": [
                            *state.get("executed_nodes", []),
                            NodeName.CONTEXT_ASSEMBLER.value,
                        ],
                        "current_step": NodeName.CONTEXT_ASSEMBLER.value,
                    }
                )
                return Command(update=update_dict, goto=NodeName.END.value)

            # 更新状态
            update_dict.update(
                {
                    "editable_context": context_slice.model_dump(),
                    "executed_nodes": [
                        *state.get("executed_nodes", []),
                        NodeName.CONTEXT_ASSEMBLER.value,
                    ],
                    "current_step": NodeName.CONTEXT_ASSEMBLER.value,
                }
            )

            logger.info(
                f"上下文组装完成: {current_target.symbol_name}, "
                f"预估 {context_slice.estimated_tokens} tokens"
            )

            # 发送完成事件
            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": NodeName.CONTEXT_ASSEMBLER.value,
                    "progress": "上下文组装完成",
                    "think_chain_item": {
                        "type": NodeName.CONTEXT_ASSEMBLER.value,
                        "title": "上下文组装",
                        "desc": f"目标: {current_target.symbol_name}, "
                               f"依赖: {len(context_slice.dependency_signatures)} 个",
                        "urls": [],
                    },
                },
            )

            return Command(update=update_dict, goto=NodeName.PLAN.value)

        except Exception as e:
            logger.error(f"上下文组装失败: {e}", exc_info=True)
            update_dict.update(
                {
                    "error": f"上下文组装失败: {str(e)}",
                    "executed_nodes": [
                        *state.get("executed_nodes", []),
                        NodeName.CONTEXT_ASSEMBLER.value,
                    ],
                    "current_step": NodeName.CONTEXT_ASSEMBLER.value,
                }
            )

            return Command(update=update_dict, goto=NodeName.END.value)

    async def _assemble_context(
        self,
        state: IssueProcessState,
        target: TargetContext,
        sandbox,
        repo_path: str = None
    ) -> Optional[EditableContextSlice]:
        """
        组装可编辑上下文切片

        Args:
            state: 当前工作流状态
            target: 目标符号
            sandbox: 沙箱实例，用于读取文件
            repo_path: Git 仓库在沙箱中的路径

        Returns:
            可编辑上下文切片
        """
        retrieved_code = state.get("retrieved_code", [])

        # 1. 从目标中获取文件路径和语言
        file_path = target.file_path
        language = self._detect_language(file_path)
        
        # 2. 先从检索结果中获取 target_snippet
        target_snippet = self._find_target_snippet(target, retrieved_code)
        
        # 3. 尝试从 Sandbox 读取完整文件内容
        content = None
        try:
            # 创建 FileService 实例
            file_service = FileService(self.sandbox_manager, sandbox.id)
            # 拼接完整路径：repo_path/file_path
            full_path = f"{repo_path}/{file_path}" if repo_path else file_path
            content = await file_service.read_file(full_path)
            logger.info(f"从沙箱读取完整文件: {full_path}")
            
            # 更新 target_snippet 的内容为完整文件
            if target_snippet:
                target_snippet["content"] = content
            else:
                # 如果检索结果中没有，创建一个新的
                target_snippet = {
                    "content": content,
                    "file_path": file_path,
                    "symbol_name": target.symbol_name,
                }
        except Exception as e:
            logger.warning(f"从沙箱读取文件失败 {full_path}: {e}, 使用检索片段")
            # 回退：使用检索结果中的片段
            if target_snippet:
                content = target_snippet.get("content", "")
                logger.info(f"使用检索片段作为回退")
            else:
                logger.error(f"无法获取目标符号的代码: {target.symbol_name}")
                return None

        # 3. 解析 AST（基于完整文件内容）
        ast_info = self.tree_sitter.parse_code(content, language, file_path)
        if not ast_info:
            logger.warning(f"无法解析 AST: {file_path}")
            # 降级处理：使用原始内容
            return self._create_fallback_slice(target, target_snippet)

        # 4. 提取目标符号的完整代码
        target_symbol = self._find_symbol_in_ast(target.symbol_name, ast_info)
        if target_symbol:
            full_code = self._extract_symbol_code(content, target_symbol)
            editable_start = target_symbol.start_line
            editable_end = target_symbol.end_line
        else:
            full_code = content
            editable_start = target.start_line
            editable_end = target.end_line

        # 5. 分析依赖关系
        dependency_graph = self.dependency_analyzer.analyze_dependencies(
            [target_snippet],
            {file_path: ast_info}
        )

        # 6. 获取前向依赖的签名
        dependency_signatures = await self._get_dependency_signatures(
            target.symbol_name,
            dependency_graph,
            retrieved_code,
            language
        )

        # 7. 提取导入语句
        imports = self._extract_imports(ast_info)

        # 8. 提取相关 Schema 定义
        schema_definitions = await self._extract_schemas(
            retrieved_code,
            dependency_signatures,
            language
        )

        # 9. 计算 Token 估算
        total_chars = (
            len(full_code) +
            sum(len(sig.signature) + len(sig.docstring or "") for sig in dependency_signatures) +
            sum(len(imp) for imp in imports) +
            sum(len(s) for s in schema_definitions)
        )
        estimated_tokens = total_chars // self.chars_per_token

        # 10. 构建上下文切片
        context_slice = EditableContextSlice(
            target=target,
            full_code=full_code,
            dependency_signatures=dependency_signatures,
            imports=imports,
            schema_definitions=schema_definitions,
            editable_start_line=editable_start,
            editable_end_line=editable_end,
            file_content=content,
            estimated_tokens=estimated_tokens,
        )

        return context_slice

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
            file_content=content,
            estimated_tokens=len(content) // self.chars_per_token,
        )

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

