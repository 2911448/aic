"""
Impact Analyzer Agent Node - 影响分析节点
分析补丁的影响范围，决定是否需要扩散修改
"""

import json
from typing import Literal, Optional

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command

from app.core.logger_config import logger
from app.core.prompt_manager import prompt_manager
from app.graph.state import IssueProcessState, NodeName, ProcessStage
from app.llms.llm_factory import get_gpt_model
from app.rag.dependency_analyzer import DependencyAnalyzer, DependencyGraph
from app.rag.tree_sitter_service import tree_sitter_service
from app.schemas.context_assembly import (
    AffectedCaller,
    EditableContextSlice,
    ImpactReport,
    TargetContext,
    TargetStatus,
)
from app.utils.common_function import parse_json_response


class ImpactAnalyzerAgentNode:
    """影响分析 Agent 节点"""

    def __init__(self):
        """初始化节点"""
        self.prompt_manager = prompt_manager
        self.dependency_analyzer = DependencyAnalyzer()
        self.tree_sitter = tree_sitter_service

    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal[NodeName.PLAN.value]]:
        """
        分析补丁的影响范围

        Args:
            state: 当前工作流状态

        Returns:
            Command对象，返回 plan 节点
        """
        update_dict = {}

        try:
            # 发送进度事件
            await adispatch_custom_event(
                ProcessStage.IMPACT_ANALYSIS.value,
                {
                    "status": NodeName.IMPACT_ANALYZER.value,
                    "progress": "正在分析修改的影响范围...",
                    "think_chain_item": {
                        "type": NodeName.IMPACT_ANALYZER.value,
                        "title": "影响分析",
                        "desc": "分析调用关系，评估扩散需求",
                        "urls": [],
                    },
                },
            )

            # 获取当前上下文
            editable_context_dict = state.get("editable_context")
            current_patch = state.get("current_patch")

            if not editable_context_dict:
                logger.warning("没有可编辑上下文，跳过影响分析")
                update_dict.update(
                    {
                        "impact_report": ImpactReport(
                            requires_expansion=False,
                            risk_level="low",
                            reasoning="没有上下文信息",
                        ).model_dump(),
                        "executed_nodes": [
                            *state.get("executed_nodes", []),
                            NodeName.IMPACT_ANALYZER.value,
                        ],
                        "current_step": NodeName.IMPACT_ANALYZER.value,
                    }
                )
                return Command(update=update_dict, goto=NodeName.PLAN.value)

            editable_context = EditableContextSlice(**editable_context_dict)

            # 执行影响分析
            impact_report = await self._analyze_impact(state, editable_context)

            # 处理扩散逻辑
            current_depth = state.get("current_expansion_depth", 0)
            max_depth = state.get("max_expansion_depth", 3)

            # 如果需要扩散且未达到最大深度
            if impact_report.requires_expansion and current_depth < max_depth:
                # 将下一批目标加入队列
                target_queue = state.get("target_queue", [])
                for next_target in impact_report.next_targets:
                    target_queue.append(next_target.model_dump())

                # 更新当前目标为队列中的下一个
                if target_queue:
                    next_target_dict = target_queue.pop(0)
                    next_target_dict["status"] = TargetStatus.IN_PROGRESS.value
                    update_dict["current_target"] = next_target_dict
                    update_dict["target_queue"] = target_queue
                    update_dict["current_expansion_depth"] = current_depth + 1

                logger.info(
                    f"需要扩散: 深度 {current_depth + 1}/{max_depth}, "
                    f"队列中还有 {len(target_queue)} 个目标"
                )
            else:
                if current_depth >= max_depth:
                    logger.info(f"已达最大扩散深度 {max_depth}，停止扩散")
                    impact_report.reasoning += f" (已达最大扩散深度 {max_depth})"

            update_dict.update(
                {
                    "impact_report": impact_report.model_dump(),
                    "executed_nodes": [
                        *state.get("executed_nodes", []),
                        NodeName.IMPACT_ANALYZER.value,
                    ],
                    "current_step": NodeName.IMPACT_ANALYZER.value,
                }
            )

            # 发送完成事件
            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": NodeName.IMPACT_ANALYZER.value,
                    "progress": "影响分析完成",
                    "think_chain_item": {
                        "type": NodeName.IMPACT_ANALYZER.value,
                        "title": "影响分析",
                        "desc": f"风险: {impact_report.risk_level}, "
                               f"扩散: {'是' if impact_report.requires_expansion else '否'}",
                        "urls": [],
                    },
                },
            )

            return Command(update=update_dict, goto=NodeName.PLAN.value)

        except Exception as e:
            logger.error(f"影响分析失败: {e}", exc_info=True)
            update_dict.update(
                {
                    "error": f"影响分析失败: {str(e)}",
                    "executed_nodes": [
                        *state.get("executed_nodes", []),
                        NodeName.IMPACT_ANALYZER.value,
                    ],
                    "current_step": NodeName.IMPACT_ANALYZER.value,
                }
            )

            return Command(update=update_dict, goto=NodeName.END.value)

    async def _analyze_impact(
        self,
        state: IssueProcessState,
        context: EditableContextSlice
    ) -> ImpactReport:
        """
        分析补丁的影响

        Args:
            state: 当前工作流状态
            context: 可编辑上下文

        Returns:
            影响分析报告
        """
        retrieved_code = state.get("retrieved_code", [])
        current_patch = state.get("current_patch", "")
        target = context.target

        # 1. 分析依赖关系
        dependency_graph = await self._build_dependency_graph(retrieved_code)

        # 2. 查找反向依赖（谁调用了被修改的符号）
        callers = dependency_graph.get_callers(target.symbol_name)

        # 3. 分析每个调用方是否需要修改
        affected_callers = []
        for caller in callers:
            caller_file = dependency_graph.symbol_to_file.get(caller, "")
            
            # 检查调用方是否在检索结果中
            in_retrieved = any(
                s.get("file_path") == caller_file or
                s.get("symbol_name") == caller
                for s in retrieved_code
            )

            affected_callers.append(AffectedCaller(
                file_path=caller_file,
                symbol_name=caller,
                call_line=0,  # 简化处理
                requires_change=False,  # 稍后由 LLM 判断
                change_reason="",
            ))

        # 4. 使用 LLM 判断是否需要扩散
        llm_analysis = await self._llm_analyze_impact(
            state,
            context,
            affected_callers,
            current_patch
        )

        # 5. 构建下一批目标
        next_targets = []
        for caller in llm_analysis.get("callers_need_change", []):
            caller_name = caller.get("symbol_name", "")
            caller_file = caller.get("file_path", "")
            
            # 从检索结果中查找该符号
            for snippet in retrieved_code:
                if (snippet.get("symbol_name") == caller_name or
                    snippet.get("file_path") == caller_file):
                    next_targets.append(TargetContext(
                        symbol_name=caller_name,
                        file_path=caller_file,
                        symbol_type=snippet.get("symbol_type", "function"),
                        start_line=snippet.get("start_line", 1),
                        end_line=snippet.get("end_line", 1),
                        status=TargetStatus.PENDING,
                        reason=caller.get("reason", ""),
                        confidence=0.7,
                    ))
                    break

        # 6. 更新受影响调用方的状态
        for ac in affected_callers:
            for caller in llm_analysis.get("callers_need_change", []):
                if ac.symbol_name == caller.get("symbol_name"):
                    ac.requires_change = True
                    ac.change_reason = caller.get("reason", "")
                    break

        return ImpactReport(
            affected_callers=affected_callers,
            requires_expansion=len(next_targets) > 0,
            next_targets=next_targets,
            risk_level=llm_analysis.get("risk_level", "low"),
            reasoning=llm_analysis.get("reasoning", ""),
            test_suggestions=llm_analysis.get("test_suggestions", []),
        )

    async def _build_dependency_graph(
        self,
        snippets: list[dict]
    ) -> DependencyGraph:
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

    async def _llm_analyze_impact(
        self,
        state: IssueProcessState,
        context: EditableContextSlice,
        affected_callers: list[AffectedCaller],
        current_patch: str
    ) -> dict:
        """
        使用 LLM 分析影响

        Args:
            state: 当前工作流状态
            context: 可编辑上下文
            affected_callers: 受影响的调用方
            current_patch: 当前补丁

        Returns:
            LLM 分析结果
        """
        try:
            issue_data = state.get("issue_data", {})
            issue_title = issue_data.get("title", "")

            # 格式化调用方信息
            callers_json = json.dumps(
                [
                    {
                        "symbol_name": ac.symbol_name,
                        "file_path": ac.file_path,
                    }
                    for ac in affected_callers[:10]  # 限制数量
                ],
                indent=2,
                ensure_ascii=False
            )

            # 渲染 Prompt
            prompt = self.prompt_manager.render(
                "impact_analysis",
                issue_title=issue_title,
                modified_symbol=context.target.symbol_name,
                modified_file=context.target.file_path,
                original_code=context.full_code[:500],  # 截断
                current_patch=current_patch[:1000] if current_patch else "无补丁",
                affected_callers=callers_json,
            )

            # 调用 LLM
            llm = await get_gpt_model(temperature=0.1)
            response = await llm.ainvoke(prompt)

            # 解析响应
            result = parse_json_response(response.content)

            return {
                "callers_need_change": result.get("callers_need_change", []),
                "risk_level": result.get("risk_level", "low"),
                "reasoning": result.get("reasoning", ""),
                "test_suggestions": result.get("test_suggestions", []),
            }

        except Exception as e:
            logger.error(f"LLM 影响分析失败: {e}", exc_info=True)
            return {
                "callers_need_change": [],
                "risk_level": "medium",
                "reasoning": f"LLM 分析失败: {str(e)}",
                "test_suggestions": [],
            }

