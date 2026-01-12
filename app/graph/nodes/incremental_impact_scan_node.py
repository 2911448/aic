"""
Incremental Impact Scan Node - 增量影响扫描节点

在批量补丁应用后，检查这批修复是否引入新的依赖破坏（二次涟漪）。
发现新失效点后，按文件聚类并追加到队列尾部。
"""

from typing import Literal

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command

from app.core.logger_config import logger
from app.graph.state import IssueProcessState, NodeName, ProcessStage
from app.utils.dependency_analyzer import DependencyAnalyzer
from app.utils.tree_sitter_service import tree_sitter_service
from app.sandbox.file_service import FileService
from app.sandbox.manager import get_sandbox_manager
from app.utils.common_function import read_latest_code_from_sandbox


class IncrementalImpactScanNode:
    """
    增量影响扫描节点
    
    职责：
    - 基于本轮批量修复的文件，检查是否引入新的失效点
    - 按文件聚类新失效点
    - 追加到 pending_file_tasks 队列尾部
    - 去重（避免重复入队）
    """

    def __init__(self):
        """初始化节点"""
        self.dependency_analyzer = DependencyAnalyzer()
        self.tree_sitter = tree_sitter_service
        self.sandbox_manager = get_sandbox_manager()

    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal["queue_manager"]]:
        """
        执行增量影响扫描
        
        Args:
            state: 当前工作流状态
        
        Returns:
            Command对象，返回 queue_manager 继续循环
        """
        update_dict = {}

        try:
            # 发送进度事件
            await adispatch_custom_event(
                ProcessStage.IMPACT_ANALYSIS.value,
                {
                    "status": NodeName.INCREMENTAL_IMPACT_SCAN.value,
                    "progress": "正在执行增量影响扫描...",
                    "think_chain_item": {
                        "type": NodeName.INCREMENTAL_IMPACT_SCAN.value,
                        "title": "增量影响扫描",
                        "desc": "检查本轮修复是否引入新问题",
                        "urls": [],
                    },
                },
            )

            # 获取本轮修改的文件
            context = state.get("context", {})
            batch_contexts = context.get("batch_contexts", [])
            
            if not batch_contexts:
                logger.warning("batch_contexts 为空，跳过增量扫描")
                runtime = state.get("runtime", {})
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.INCREMENTAL_IMPACT_SCAN.value,
                            ],
                            "current_step": NodeName.INCREMENTAL_IMPACT_SCAN.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.QUEUE_MANAGER.value)

            # 执行增量扫描
            new_failed_points = await self._scan_incremental_impact(state)

            # 按文件聚类
            new_file_tasks = self._cluster_by_file(new_failed_points)

            # 去重并追加到队列
            # 去重规则调整：
            # 1. 跳过上一批次刚处理的文件（避免立即重复）
            # 2. 跳过已在队列中的文件（避免重复入队）
            # 3. 不再使用 seen_files 过滤（允许文件在后续批次重新入队）
            ripple = state.get("ripple", {})
            pending_tasks = ripple.get("pending_file_tasks", [])
            last_applied_files = ripple.get("last_applied_files", [])
            seen_files = ripple.get("seen_files", [])  # 保留用于追踪，不用于过滤
            
            added_count = 0
            for task in new_file_tasks:
                file_path = task.get("file_path", "")
                
                # 去重1：跳过上一批次刚处理的文件
                if file_path in last_applied_files:
                    logger.debug(f"文件在上一批次已处理，跳过: {file_path}")
                    continue
                
                # 去重2：检查是否已在队列中
                if any(t.get("file_path") == file_path for t in pending_tasks):
                    logger.debug(f"文件已在队列中，跳过: {file_path}")
                    continue
                
                # 追加到队列
                pending_tasks.append(task)
                seen_files.append(file_path)  # 仅用于历史追踪
                added_count += 1

            logger.info(
                f"增量扫描完成: 发现 {len(new_failed_points)} 个新失效点, "
                f"聚类为 {len(new_file_tasks)} 个文件任务, "
                f"新增 {added_count} 个到队列"
            )

            # 更新状态
            runtime = state.get("runtime", {})
            update_dict.update(
                {
                    "ripple": {
                        **ripple,
                        "pending_file_tasks": pending_tasks,
                    },
                    "runtime": {
                        **runtime,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.INCREMENTAL_IMPACT_SCAN.value,
                        ],
                        "current_step": NodeName.INCREMENTAL_IMPACT_SCAN.value,
                    },
                }
            )

            # 发送完成事件
            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": NodeName.INCREMENTAL_IMPACT_SCAN.value,
                    "progress": f"增量扫描完成 (新增 {added_count} 个任务)",
                    "think_chain_item": {
                        "type": NodeName.INCREMENTAL_IMPACT_SCAN.value,
                        "title": "增量影响扫描",
                        "desc": f"新失效点: {len(new_failed_points)}, 新增任务: {added_count}",
                        "urls": [],
                    },
                },
            )

            return Command(update=update_dict, goto=NodeName.QUEUE_MANAGER.value)

        except Exception as e:
            logger.error(f"增量影响扫描失败: {e}", exc_info=True)
            # 即使失败也继续流程（容错）
            runtime = state.get("runtime", {})
            update_dict.update(
                {
                    "runtime": {
                        **runtime,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.INCREMENTAL_IMPACT_SCAN.value,
                        ],
                        "current_step": NodeName.INCREMENTAL_IMPACT_SCAN.value,
                    },
                }
            )

            return Command(update=update_dict, goto=NodeName.QUEUE_MANAGER.value)

    async def _scan_incremental_impact(
        self,
        state: IssueProcessState
    ) -> list[dict]:
        """
        执行增量影响扫描
        
        核心逻辑：
        1. 从 state.ripple.last_signature_changes 获取上一批次的签名变更指纹
        2. 提取被修改的符号列表（只包含真正被修改的符号）
        3. 在整个检索范围内查找调用这些变更符号的其他文件
        4. 过滤规则：仅排除 last_applied_files（本批次修改的文件）
        
        Returns:
            新失效点列表
        """
        new_failed_points = []

        try:
            # 1. 获取上一批次应用的文件和签名变更
            ripple = state.get("ripple", {})
            last_applied_files = ripple.get("last_applied_files", [])
            last_signature_changes = ripple.get("last_signature_changes", {})
            
            if not last_applied_files:
                logger.warning("last_applied_files 为空，跳过增量扫描")
                return []
            
            logger.info(f"增量扫描：分析上一批次 {len(last_applied_files)} 个修改文件的影响")
            
            # 2. 从签名变更指纹中提取修改的符号
            if not last_signature_changes:
                logger.warning("未找到签名变更记录，跳过增量扫描")
                return []
            
            modified_symbols = []
            for file_path, changes in last_signature_changes.items():
                for change in changes:
                    symbol_name = change.get("symbol_name", "")
                    if symbol_name:
                        modified_symbols.append({
                            "symbol_name": symbol_name,
                            "file_path": file_path,
                            "type": change.get("symbol_type", "function"),
                        })
            
            logger.info(
                f"从签名变更指纹中提取 {len(modified_symbols)} 个符号 "
                f"(来自 {len(last_signature_changes)} 个文件)"
            )
            
            if not modified_symbols:
                logger.info("未提取到修改符号，跳过依赖分析")
                return []
            
            # 3. 从 sandbox 读取整个检索范围的最新代码（用于依赖分析）
            retrieval = state.get("retrieval", {})
            retrieved_code = retrieval.get("retrieved_code", [])
            
            if not retrieved_code:
                logger.warning("没有检索范围，无法进行依赖分析")
                return []
            
            # 读取检索范围内所有文件的最新代码
            sandbox = state.get("sandbox", {})
            sandbox_id = sandbox.get("sandbox_id")
            
            if not sandbox_id:
                logger.error("缺少 sandbox_id，无法执行增量扫描")
                return []
            
            file_service = FileService(self.sandbox_manager, sandbox_id)
            all_latest_snippets = await read_latest_code_from_sandbox(
                file_service,
                retrieved_code
            )
            
            if not all_latest_snippets:
                logger.warning("无法获取检索范围代码，跳过增量扫描")
                return []
            
            # 4. 构建依赖图（基于最新代码）
            dependency_graph = await self._build_dependency_graph(all_latest_snippets)
            
            # 5. 查找调用方（谁调用了被修改的符号）
            for modified_symbol_info in modified_symbols:
                symbol_name = modified_symbol_info["symbol_name"]
                source_file = modified_symbol_info["file_path"]
                
                # 查找调用方
                callers = dependency_graph.get_callers(symbol_name)
                
                for caller in callers:
                    caller_file = dependency_graph.symbol_to_file.get(caller, "")
                    
                    if not caller_file:
                        continue
                    
                    # 过滤规则：只排除本批次修改的文件
                    # 不排除 seen_files（允许文件在后续批次重新入队）
                    if caller_file in last_applied_files:
                        logger.debug(f"跳过本批次修改的文件: {caller_file}")
                        continue
                    
                    # 检查是否在检索范围中（只处理已检索的文件）
                    in_scope = any(
                        s.get("file_path") == caller_file
                        for s in all_latest_snippets
                    )
                    
                    if in_scope:
                        new_failed_points.append({
                            "file_path": caller_file,
                            "symbol_name": caller,
                            "reason": f"调用了本轮修改的符号 {symbol_name} (来自 {source_file})",
                            "priority": 2,  # 二次涟漪，优先级较低
                        })
                        logger.debug(
                            f"发现新失效点: {caller_file}::{caller} -> {symbol_name}"
                        )
            
            logger.info(f"增量扫描发现 {len(new_failed_points)} 个新失效点")
            return new_failed_points

        except Exception as e:
            logger.error(f"增量扫描异常: {e}", exc_info=True)
            return []

    async def _build_dependency_graph(self, snippets: list[dict]):
        """构建依赖图"""
        ast_infos = {}

        for snippet in snippets:
            file_path = snippet.get("file_path", "")
            content = snippet.get("content", "")
            language = snippet.get("language", "python")

            if file_path and file_path not in ast_infos:
                ast_info = self.tree_sitter.parse_code(content, language, file_path)
                if ast_info:
                    ast_infos[file_path] = ast_info

        return self.dependency_analyzer.analyze_dependencies(snippets, ast_infos)

    def _cluster_by_file(self, failed_points: list[dict]) -> list[dict]:
        """
        按文件聚类失效点
        
        Returns:
            文件任务列表
        """
        file_map = {}

        for point in failed_points:
            file_path = point.get("file_path", "")
            if not file_path:
                continue

            if file_path not in file_map:
                file_map[file_path] = {
                    "file_path": file_path,
                    "symbols": [],
                    "reasons": [],
                    "priority": point.get("priority", 2),
                }

            file_map[file_path]["symbols"].append(point.get("symbol_name", ""))
            file_map[file_path]["reasons"].append(point.get("reason", ""))

        # 转换为列表并按优先级排序
        file_tasks = list(file_map.values())
        file_tasks.sort(key=lambda x: x["priority"], reverse=True)

        return file_tasks


# 导出
__all__ = ["IncrementalImpactScanNode"]

