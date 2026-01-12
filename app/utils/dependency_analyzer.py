"""
依赖关系分析模块
分析代码片段之间的依赖关系，构建依赖图，识别影响范围
"""

from collections import defaultdict
from typing import Optional

from app.core.logger_config import logger
from app.utils.tree_sitter_service import ASTInfo, FunctionCall, tree_sitter_service
from app.schemas.code_scope import (
    CodeLocation,
    DependencyRelation,
    ImpactScope,
    ReverseDependency,
)


class DependencyGraph:
    """依赖图数据结构"""

    def __init__(self):
        """初始化依赖图"""
        # 前向依赖: symbol -> [依赖的 symbols]
        self.forward_deps: dict[str, set[str]] = defaultdict(set)
        
        # 反向依赖: symbol -> [调用它的 symbols]
        self.reverse_deps: dict[str, set[str]] = defaultdict(set)
        
        # 符号到文件的映射
        self.symbol_to_file: dict[str, str] = {}
        
        # 详细的调用信息
        self.call_details: dict[tuple[str, str], list[FunctionCall]] = defaultdict(list)

    def add_dependency(
        self,
        from_symbol: str,
        to_symbol: str,
        from_file: str = "",
        to_file: str = "",
        call: Optional[FunctionCall] = None
    ):
        """添加依赖关系"""
        self.forward_deps[from_symbol].add(to_symbol)
        self.reverse_deps[to_symbol].add(from_symbol)
        
        if from_file:
            self.symbol_to_file[from_symbol] = from_file
        if to_file:
            self.symbol_to_file[to_symbol] = to_file
        
        if call:
            self.call_details[(from_symbol, to_symbol)].append(call)

    def get_callers(self, symbol: str) -> set[str]:
        """获取调用某个符号的所有符号"""
        return self.reverse_deps.get(symbol, set())

    def get_callees(self, symbol: str) -> set[str]:
        """获取某个符号调用的所有符号"""
        return self.forward_deps.get(symbol, set())


class DependencyAnalyzer:
    """依赖关系分析器"""

    def __init__(self):
        """初始化分析器"""
        self.tree_sitter = tree_sitter_service

    def analyze_dependencies(
        self,
        snippets: list[dict],
        ast_infos: Optional[dict[str, ASTInfo]] = None
    ) -> DependencyGraph:
        """
        分析代码片段之间的依赖关系

        Args:
            snippets: 代码片段列表（来自检索结果）
            ast_infos: 已解析的 AST 信息（可选）

        Returns:
            依赖图
        """
        graph = DependencyGraph()

        # 如果没有提供 AST 信息，先解析
        if ast_infos is None:
            ast_infos = {}
            for snippet in snippets:
                file_path = snippet.get("file_path", "")
                content = snippet.get("content", "")
                language = snippet.get("language", "python")
                
                if file_path not in ast_infos:
                    ast_info = self.tree_sitter.parse_code(content, language, file_path)
                    if ast_info:
                        ast_infos[file_path] = ast_info

        # 构建符号表
        symbol_to_file = {}
        for file_path, ast_info in ast_infos.items():
            for symbol in ast_info.symbols:
                full_name = f"{symbol.parent}.{symbol.name}" if symbol.parent else symbol.name
                symbol_to_file[full_name] = file_path
                symbol_to_file[symbol.name] = file_path

        # 分析函数调用关系
        for file_path, ast_info in ast_infos.items():
            for call in ast_info.function_calls:
                caller = call.caller or "<module>"
                callee = call.callee
                
                # 添加到依赖图
                graph.add_dependency(
                    from_symbol=caller,
                    to_symbol=callee,
                    from_file=file_path,
                    to_file=symbol_to_file.get(callee, ""),
                    call=call
                )

        logger.info(
            f"依赖分析完成: {len(graph.forward_deps)} 个符号, "
            f"{sum(len(v) for v in graph.forward_deps.values())} 条依赖关系"
        )

        return graph

    def find_reverse_dependencies(
        self,
        target_symbols: list[str],
        dependency_graph: DependencyGraph,
        current_snippets: list[dict]
    ) -> list[ReverseDependency]:
        """
        查找反向依赖（谁在调用目标符号）

        Args:
            target_symbols: 目标符号列表
            dependency_graph: 依赖图
            current_snippets: 当前已检索的代码片段

        Returns:
            反向依赖列表
        """
        reverse_deps = []
        
        # 构建当前片段的文件集合
        current_files = {snippet.get("file_path", "") for snippet in current_snippets}

        for symbol in target_symbols:
            callers = dependency_graph.get_callers(symbol)
            
            for caller in callers:
                caller_file = dependency_graph.symbol_to_file.get(caller, "")
                in_top5 = caller_file in current_files
                
                # 获取调用位置
                call_details = dependency_graph.call_details.get((caller, symbol), [])
                if call_details:
                    call = call_details[0]  # 取第一个调用位置
                    call_location = (call.line, call.line)
                else:
                    call_location = (0, 0)

                reverse_deps.append(ReverseDependency(
                    caller_file=caller_file,
                    caller_symbol=caller,
                    callee_symbol=symbol,
                    call_location=call_location,
                    in_top5=in_top5,
                ))

        logger.info(f"找到 {len(reverse_deps)} 个反向依赖")
        return reverse_deps

    def assess_impact_scope(
        self,
        change_set: list[CodeLocation],
        dependency_graph: DependencyGraph,
        current_snippets: list[dict]
    ) -> ImpactScope:
        """
        评估修改的影响范围

        Args:
            change_set: 修改集
            dependency_graph: 依赖图
            current_snippets: 当前已检索的代码片段

        Returns:
            影响范围评估
        """
        # 提取所有修改的符号
        modified_symbols = [loc.symbol_name for loc in change_set]
        
        # 查找反向依赖
        reverse_deps = self.find_reverse_dependencies(
            modified_symbols,
            dependency_graph,
            current_snippets
        )

        # 统计受影响的文件和符号
        affected_files = set()
        affected_symbols = set()
        uncovered_deps = []

        for dep in reverse_deps:
            affected_files.add(dep.caller_file)
            affected_symbols.add(dep.caller_symbol)
            
            # 如果不在 Top-5 中，标记为未覆盖
            if not dep.in_top5:
                uncovered_deps.append(dep)

        # 判断是否需要补充检索
        requires_additional_retrieval = len(uncovered_deps) > 0

        impact_scope = ImpactScope(
            total_affected_files=len(affected_files),
            total_affected_symbols=len(affected_symbols),
            uncovered_dependencies=uncovered_deps,
            requires_additional_retrieval=requires_additional_retrieval,
        )

        logger.info(
            f"影响范围评估: {impact_scope.total_affected_files} 个文件, "
            f"{impact_scope.total_affected_symbols} 个符号, "
            f"{len(uncovered_deps)} 个未覆盖依赖"
        )

        return impact_scope

    def build_dependency_relations(
        self,
        dependency_graph: DependencyGraph,
        target_symbols: Optional[list[str]] = None
    ) -> list[DependencyRelation]:
        """
        构建依赖关系列表

        Args:
            dependency_graph: 依赖图
            target_symbols: 目标符号列表（如果为 None，返回所有关系）

        Returns:
            依赖关系列表
        """
        relations = []

        # 如果没有指定目标符号，使用所有符号
        if target_symbols is None:
            target_symbols = list(dependency_graph.forward_deps.keys())

        for from_symbol in target_symbols:
            callees = dependency_graph.get_callees(from_symbol)
            
            for to_symbol in callees:
                # 获取反向依赖
                reverse_deps_list = []
                callers = dependency_graph.get_callers(to_symbol)
                for caller in callers:
                    caller_file = dependency_graph.symbol_to_file.get(caller, "")
                    call_details = dependency_graph.call_details.get((caller, to_symbol), [])
                    call_location = (call_details[0].line, call_details[0].line) if call_details else (0, 0)
                    
                    reverse_deps_list.append(ReverseDependency(
                        caller_file=caller_file,
                        caller_symbol=caller,
                        callee_symbol=to_symbol,
                        call_location=call_location,
                        in_top5=True,  # 这里简化处理，实际需要根据上下文判断
                    ))

                # 评估影响级别（简化版本）
                impact_level = self._assess_impact_level(from_symbol, to_symbol, dependency_graph)

                relations.append(DependencyRelation(
                    from_symbol=from_symbol,
                    to_symbol=to_symbol,
                    from_file=dependency_graph.symbol_to_file.get(from_symbol, ""),
                    to_file=dependency_graph.symbol_to_file.get(to_symbol, ""),
                    relation_type="call",  # 目前只支持函数调用
                    impact_level=impact_level,
                    reverse_deps=reverse_deps_list,
                ))

        logger.info(f"构建了 {len(relations)} 条依赖关系")
        return relations

    def _assess_impact_level(
        self,
        from_symbol: str,
        to_symbol: str,
        dependency_graph: DependencyGraph
    ) -> str:
        """
        评估依赖的影响级别

        Args:
            from_symbol: 源符号
            to_symbol: 目标符号
            dependency_graph: 依赖图

        Returns:
            影响级别: low, medium, high
        """
        # 简化的影响级别评估
        # 根据被调用符号的调用者数量判断
        callers_count = len(dependency_graph.get_callers(to_symbol))
        
        if callers_count >= 5:
            return "high"
        elif callers_count >= 2:
            return "medium"
        else:
            return "low"

    def find_call_chain(
        self,
        from_symbol: str,
        to_symbol: str,
        dependency_graph: DependencyGraph,
        max_depth: int = 5
    ) -> list[list[str]]:
        """
        查找从 from_symbol 到 to_symbol 的调用链

        Args:
            from_symbol: 起始符号
            to_symbol: 目标符号
            dependency_graph: 依赖图
            max_depth: 最大搜索深度

        Returns:
            调用链列表，每个调用链是一个符号列表
        """
        chains = []

        def dfs(current: str, target: str, path: list[str], depth: int):
            if depth > max_depth:
                return
            
            if current == target:
                chains.append(path + [current])
                return
            
            if current in path:  # 避免循环
                return
            
            callees = dependency_graph.get_callees(current)
            for callee in callees:
                dfs(callee, target, path + [current], depth + 1)

        dfs(from_symbol, to_symbol, [], 0)
        return chains

    def identify_change_set(
        self,
        target_snippets: list[dict],
        issue_description: str = ""
    ) -> list[str]:
        """
        识别需要修改的符号集合

        Args:
            target_snippets: 目标代码片段
            issue_description: Issue 描述（用于辅助判断）

        Returns:
            需要修改的符号名称列表
        """
        # 简化实现：返回所有片段中的主要符号
        symbols = []
        
        for snippet in target_snippets:
            symbol_name = snippet.get("symbol_name", "")
            if symbol_name:
                symbols.append(symbol_name)

        logger.info(f"识别出 {len(symbols)} 个需要修改的符号")
        return symbols


# 全局实例
dependency_analyzer = DependencyAnalyzer()

