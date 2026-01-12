"""
Global Impact Scan Node - 全局影响扫描节点

在初始 patch 应用后，基于 sandbox 最新代码做全量扫描，
找出因接口变化导致的直接失效点（调用方/被调用方）。

核心职责：
1. 读取变更指纹：从 PatchFlow 产出的 Diff 中提取所有被修改的公共函数、类名或变量名
2. 跨文件引用查找：在整个 sandbox 代码库中全量扫描，查找所有引用了这些变更符号的位置
3. 任务原子化与入队：将发现的调用点按文件路径聚合，初始化任务队列供 QueueManager 调度
4. 流程状态标记：在 state 中记录扫描结果，触发 MainRouter 转向后续的 Batch 处理阶段

重要说明：
- 本节点针对第一个核心 Patch 进行全量扫描
- 扫描范围：整个 sandbox 代码库（不受 retrieval 范围限制）
- 识别层级：仅识别"第一层涟漪"（直接调用方）
- 后续影响：由 IncrementalImpactScanNode 处理二次影响
"""

import os
from typing import Literal

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command

from app.core.logger_config import logger
from app.graph.state import IssueProcessState, NodeName, ProcessStage
from app.utils.dependency_analyzer import DependencyAnalyzer
from app.utils.tree_sitter_service import tree_sitter_service
from app.sandbox.file_service import FileService
from app.sandbox.manager import get_sandbox_manager
from app.utils.diff_analyzer import diff_analyzer
from app.utils.sandbox_scanner import SandboxScanner


class GlobalImpactScanNode:
    """
    全局影响扫描节点
    
    职责：
    - 从 Patch Diff 中提取签名变更指纹
    - 在整个 sandbox 代码库中全量扫描
    - 找出因接口变化导致的直接失效点（调用方）
    - 按文件聚类，产出待处理的文件任务列表
    """

    def __init__(self, max_scan_files: int = 500):
        """
        初始化节点
        
        Args:
            max_scan_files: 最大扫描文件数（防止超大代码库）
        """
        self.dependency_analyzer = DependencyAnalyzer()
        self.tree_sitter = tree_sitter_service
        self.sandbox_manager = get_sandbox_manager()
        self.max_scan_files = max_scan_files

    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal["queue_manager", "sandbox_teardown"]]:
        """
        执行全局影响扫描
        
        Args:
            state: 当前工作流状态
        
        Returns:
            Command对象，成功返回 queue_manager，失败返回 sandbox_teardown
        """
        update_dict = {}

        try:
            # 发送进度事件
            await adispatch_custom_event(
                ProcessStage.IMPACT_ANALYSIS.value,
                {
                    "status": NodeName.GLOBAL_IMPACT_SCAN.value,
                    "progress": "正在执行全局影响扫描：读取变更指纹 → 扫描最新代码 → 跨文件引用分析 → 任务队列初始化",
                    "think_chain_item": {
                        "type": NodeName.GLOBAL_IMPACT_SCAN.value,
                        "title": "全局影响扫描",
                        "desc": "基于 sandbox 最新代码进行静态分析，识别受影响的调用点",
                        "urls": [],
                    },
                },
            )

            # 获取 sandbox 信息
            sandbox = state.get("sandbox", {})
            sandbox_id = sandbox.get("sandbox_id")
            
            if not sandbox_id:
                error_msg = "缺少 sandbox_id，无法执行全局扫描"
                logger.error(error_msg)
                runtime = state.get("runtime", {})
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "error": error_msg,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.GLOBAL_IMPACT_SCAN.value,
                            ],
                            "current_step": NodeName.GLOBAL_IMPACT_SCAN.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

            # 执行全局扫描
            failed_points = await self._scan_global_impact(
                state,
                sandbox_id
            )

            # 按文件聚类
            file_tasks = self._cluster_by_file(failed_points)

            logger.info(
                f"全局影响扫描完成: 发现 {len(failed_points)} 个失效调用点，"
                f"聚类为 {len(file_tasks)} 个文件任务，已初始化 pending_file_tasks 队列"
            )

            # 生成影响分析报告（补充缺失的 impact_report）
            impact_report = {
                "affected_callers": [
                    {
                        "file_path": point.get("file_path", ""),
                        "symbol_name": point.get("symbol_name", ""),
                        "reason": point.get("reason", ""),
                        "priority": point.get("priority", 1),
                    }
                    for point in failed_points
                ],
                "total_affected": len(failed_points),
                "total_files": len(file_tasks),
                "risk_level": self._assess_risk_level(len(failed_points), len(file_tasks)),
                "scan_type": "global",
            }

            # 更新 ripple 队列和 impact 报告
            ripple = state.get("ripple", {})
            runtime = state.get("runtime", {})
            
            update_dict.update(
                {
                    "ripple": {
                        **ripple,
                        "pending_file_tasks": file_tasks,
                        "seen_files": [],  # 重置
                        "iteration": 0,
                    },
                    "impact": {
                        "impact_report": impact_report,
                    },
                    "runtime": {
                        **runtime,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.GLOBAL_IMPACT_SCAN.value,
                        ],
                        "current_step": NodeName.GLOBAL_IMPACT_SCAN.value,
                    },
                }
            )

            # 发送完成事件
            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": NodeName.GLOBAL_IMPACT_SCAN.value,
                    "progress": f"扫描完成，识别 {len(failed_points)} 个失效点，初始化 {len(file_tasks)} 个文件任务",
                    "think_chain_item": {
                        "type": NodeName.GLOBAL_IMPACT_SCAN.value,
                        "title": "全局影响扫描完成",
                        "desc": f"受影响调用点: {len(failed_points)} 个，待处理文件: {len(file_tasks)} 个，风险级别: {impact_report['risk_level']}",
                        "urls": [],
                    },
                },
            )

            return Command(update=update_dict, goto=NodeName.QUEUE_MANAGER.value)

        except Exception as e:
            logger.error(f"全局影响扫描失败: {e}", exc_info=True)
            runtime = state.get("runtime", {})
            update_dict.update(
                {
                    "runtime": {
                        **runtime,
                        "error": f"全局影响扫描失败: {str(e)}",
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.GLOBAL_IMPACT_SCAN.value,
                        ],
                        "current_step": NodeName.GLOBAL_IMPACT_SCAN.value,
                    },
                }
            )

            return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

    async def _scan_global_impact(
        self,
        state: IssueProcessState,
        sandbox_id: str
    ) -> list[dict]:
        """
        执行全局影响扫描
        
        核心步骤：
        1. 读取变更指纹：从 Patch Diff 中提取所有签名变更
        2. 全量扫描 sandbox：递归扫描整个代码库
        3. 跨文件引用查找：针对每个签名变更，查找所有调用方
        4. 生成失效点列表：标记受影响的符号和文件
        
        Returns:
            失效点列表，每项包含 file_path, symbol_name, reason 等
        """
        failed_points = []

        try:
            # 步骤 1: 从 Patch Diff 中提取签名变更指纹
            patching = state.get("patching", {})
            current_patch_dict = patching.get("selected_patch", {})
            
            if not current_patch_dict:
                logger.warning("没有找到当前补丁，跳过全局扫描")
                return []
            
            unified_diff = current_patch_dict.get("unified_diff", "")
            file_path = current_patch_dict.get("file_path", "")
            original_code = current_patch_dict.get("original_code", "")
            modified_code = current_patch_dict.get("modified_code", "")
            
            if not unified_diff:
                return []
            
            # 推断语言
            from app.utils.common_function import detect_language
            language = detect_language(file_path)
            
            # 提取签名变更
            signature_changes = diff_analyzer.extract_signature_changes(
                unified_diff=unified_diff,
                file_path=file_path,
                old_content=original_code,
                new_content=modified_code,
                language=language
            )
            
            if not signature_changes:
                logger.info("未检测到签名变更，跳过全局扫描")
                return []
            
            # 仅关注公共接口的变更和删除
            relevant_changes = [
                change for change in signature_changes
                if change.is_public and change.change_type in ["modified", "removed"]
            ]
            
            if not relevant_changes:
                logger.info("没有会导致现有代码失效的签名变更（仅添加或私有接口变更）")
                return []
            
            logger.info(
                f"检测到 {len(relevant_changes)} 个需要扫描的签名变更: "
                f"{[c.symbol_name for c in relevant_changes]}"
            )

            # 步骤 2: 全量扫描 sandbox 代码库
            file_service = FileService(self.sandbox_manager, sandbox_id)
            
            # 从 state 获取忽略规则
            sandbox = state.get("sandbox", {})
            ignore_patterns = sandbox.get("ignore_patterns", [])
            
            scanner = SandboxScanner(file_service, ignore_patterns=ignore_patterns)
            
            # 只扫描与 patch 相同语言的文件
            extensions = self._get_extensions_for_language(language)
            
            all_snippets = await scanner.scan_all_code_files(
                repo_path=".",
                max_files=self.max_scan_files,
                extensions=extensions
            )

            if not all_snippets:
                logger.warning("无法扫描到任何代码文件，跳过全局扫描")
                return []

            logger.info(f"全量扫描获取 {len(all_snippets)} 个文件")

            # 步骤 3: 构建依赖图
            dependency_graph = await self._build_dependency_graph(all_snippets)

            # 步骤 4: 针对每个签名变更，查找调用方
            for change in relevant_changes:
                symbol_name = change.symbol_name
                
                # 查找调用方
                callers = dependency_graph.get_callers(symbol_name)
                
                # 将 set 转换为 list 以便切片展示
                callers_list = list(callers)
                logger.info(
                    f"符号 '{symbol_name}' 被 {len(callers_list)} 个地方调用: {callers_list[:10]}"
                )

                # 生成失效点
                for caller in callers:
                    caller_file = dependency_graph.symbol_to_file.get(caller, "")
                    
                    if not caller_file:
                        continue
                    
                    # 排除修改文件本身（避免自身引用）
                    if caller_file == file_path:
                        continue
                    
                    # 构建失效原因
                    if change.change_type == "modified":
                        reason = f"调用了被修改的符号 {symbol_name} (签名变更: {change.old_signature} → {change.new_signature})"
                    elif change.change_type == "removed":
                        reason = f"调用了被删除的符号 {symbol_name}"
                    else:
                        reason = f"调用了被修改的符号 {symbol_name}"
                    
                    failed_points.append({
                        "file_path": caller_file,
                        "symbol_name": caller,
                        "reason": reason,
                        "priority": 1,  # 直接调用方，优先级高
                        "modified_symbol": symbol_name,
                        "change_type": change.change_type,
                    })

            logger.info(f"全局扫描识别出 {len(failed_points)} 个受影响的调用点")
            return failed_points

        except Exception as e:
            logger.error(f"全局扫描异常: {e}", exc_info=True)
            return []
    
    def _get_extensions_for_language(self, language: str) -> set[str]:
        """根据语言返回文件扩展名"""
        ext_map = {
            "python": {".py"},
            "javascript": {".js", ".jsx"},
            "typescript": {".ts", ".tsx"},
            "java": {".java"},
            "go": {".go"},
            "rust": {".rs"},
            "c": {".c", ".h"},
            "cpp": {".cpp", ".hpp", ".cc", ".cxx"},
        }
        return ext_map.get(language, {".py"})  # 默认 Python

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
        任务原子化与入队：按文件聚类失效点
        
        将发现的调用点按文件路径聚合，避免同一个文件在后续流程中被反复读写，
        并初始化任务队列供 QueueManager 调度。
        
        Args:
            failed_points: 失效点列表
        
        Returns:
            文件任务列表，每项包含 file_path, symbols, reasons, priority
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
                    "priority": point.get("priority", 1),
                }

            # 聚合同一文件内的多个失效点
            file_map[file_path]["symbols"].append(point.get("symbol_name", ""))
            file_map[file_path]["reasons"].append(point.get("reason", ""))

        # 转换为列表并按优先级排序（高优先级的文件任务先处理）
        file_tasks = list(file_map.values())
        file_tasks.sort(key=lambda x: x["priority"], reverse=True)

        logger.debug(f"文件聚类完成: {len(failed_points)} 个失效点 → {len(file_tasks)} 个文件任务")
        return file_tasks

    def _assess_risk_level(self, affected_count: int, file_count: int) -> str:
        """
        评估风险级别
        
        Args:
            affected_count: 失效点数量
            file_count: 受影响文件数量
        
        Returns:
            风险级别：low/medium/high
        """
        if affected_count == 0:
            return "low"
        elif affected_count <= 3 and file_count <= 2:
            return "low"
        elif affected_count <= 10 and file_count <= 5:
            return "medium"
        else:
            return "high"


# 导出
__all__ = ["GlobalImpactScanNode"]

