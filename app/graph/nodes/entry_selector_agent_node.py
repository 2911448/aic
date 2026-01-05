"""
Entry Selector Agent Node - 切入点选择节点
从 RAG 召回的 Top-N 结果中选择最佳切入点
"""

import json
from typing import Literal

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command

from app.core.logger_config import logger
from app.core.prompt_manager import prompt_manager
from app.graph.state import IssueProcessState, NodeName, ProcessStage
from app.llms.llm_factory import get_gpt_model
from app.rag.tree_sitter_service import tree_sitter_service
from app.schemas.context_assembly import (
    EntrySelectionResult,
    TargetContext,
    TargetStatus,
)
from app.utils.common_function import parse_json_response


class EntrySelectorAgentNode:
    """切入点选择 Agent 节点"""

    def __init__(self):
        """初始化节点"""
        self.prompt_manager = prompt_manager
        self.tree_sitter = tree_sitter_service

    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal[NodeName.PLAN.value]]:
        """
        从 RAG 结果中选择最佳切入点

        Args:
            state: 当前工作流状态

        Returns:
            Command对象，返回 plan 节点
        """
        update_dict = {}

        try:
            # 发送进度事件
            await adispatch_custom_event(
                ProcessStage.ENTRY_SELECTION.value,
                {
                    "status": NodeName.ENTRY_SELECTOR.value,
                    "progress": "正在分析代码片段，选择最佳修改切入点...",
                    "think_chain_item": {
                        "type": NodeName.ENTRY_SELECTOR.value,
                        "title": "切入点选择",
                        "desc": "从检索结果中选择最佳修改入口",
                        "urls": [],
                    },
                },
            )

            # 执行切入点选择
            result = await self._select_entry_point(state)

            if result is None:
                logger.warning("无法选择切入点")
                update_dict.update(
                    {
                        "error": "无法从检索结果中选择有效的切入点",
                        "executed_nodes": [
                            *state.get("executed_nodes", []),
                            NodeName.ENTRY_SELECTOR.value,
                        ],
                        "current_step": NodeName.ENTRY_SELECTOR.value,
                    }
                )
                return Command(update=update_dict, goto=NodeName.END.value)

            # 更新状态
            update_dict.update(
                {
                    "current_target": result.selected_target.model_dump(),
                    "target_queue": [
                        t.model_dump() for t in result.alternatives
                    ],
                    "current_expansion_depth": 0,
                    "max_expansion_depth": state.get("max_expansion_depth", 3),
                    "executed_nodes": [
                        *state.get("executed_nodes", []),
                        NodeName.ENTRY_SELECTOR.value,
                    ],
                    "current_step": NodeName.ENTRY_SELECTOR.value,
                }
            )

            logger.info(
                f"切入点选择完成: {result.selected_target.symbol_name} "
                f"({result.selected_target.file_path})"
            )

            # 发送完成事件
            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": NodeName.ENTRY_SELECTOR.value,
                    "progress": "切入点选择完成",
                    "think_chain_item": {
                        "type": NodeName.ENTRY_SELECTOR.value,
                        "title": "切入点选择",
                        "desc": f"选中: {result.selected_target.symbol_name}",
                        "urls": [],
                    },
                },
            )

            return Command(update=update_dict, goto=NodeName.PLAN.value)

        except Exception as e:
            logger.error(f"切入点选择失败: {e}", exc_info=True)
            update_dict.update(
                {
                    "error": f"切入点选择失败: {str(e)}",
                    "executed_nodes": [
                        *state.get("executed_nodes", []),
                        NodeName.ENTRY_SELECTOR.value,
                    ],
                    "current_step": NodeName.ENTRY_SELECTOR.value,
                }
            )

            return Command(update=update_dict, goto=NodeName.END.value)

    async def _select_entry_point(
        self,
        state: IssueProcessState
    ) -> EntrySelectionResult | None:
        """
        选择最佳切入点

        Args:
            state: 当前工作流状态

        Returns:
            切入点选择结果
        """
        retrieved_code = state.get("retrieved_code", [])
        issue_data = state.get("issue_data", {})

        if not retrieved_code:
            logger.warning("没有检索到代码片段")
            return None

        # 解析代码片段，提取符号信息
        candidates = await self._extract_candidates(retrieved_code)

        if not candidates:
            logger.warning("无法从检索结果中提取有效符号")
            return None

        # 使用 LLM 选择最佳切入点
        result = await self._llm_select(issue_data, candidates, retrieved_code)

        return result

    async def _extract_candidates(
        self,
        snippets: list[dict]
    ) -> list[dict]:
        """
        从代码片段中提取候选符号

        Args:
            snippets: 代码片段列表

        Returns:
            候选符号列表
        """
        candidates = []

        for snippet in snippets:
            file_path = snippet.get("file_path", "")
            content = snippet.get("content", "")
            language = snippet.get("language", "python")
            symbol_name = snippet.get("symbol_name", "")

            # 使用 tree-sitter 解析 AST
            ast_info = self.tree_sitter.parse_code(content, language, file_path)

            if ast_info and ast_info.symbols:
                for symbol in ast_info.symbols:
                    candidates.append({
                        "symbol_name": symbol.name,
                        "file_path": file_path,
                        "symbol_type": symbol.type,
                        "start_line": symbol.start_line,
                        "end_line": symbol.end_line,
                        "signature": symbol.signature or "",
                        "parent": symbol.parent,
                        "snippet_content": content[:500],  # 截断
                    })
            elif symbol_name:
                # 如果 AST 解析失败，使用元数据
                candidates.append({
                    "symbol_name": symbol_name,
                    "file_path": file_path,
                    "symbol_type": "unknown",
                    "start_line": snippet.get("start_line", 1),
                    "end_line": snippet.get("end_line", 1),
                    "signature": "",
                    "parent": None,
                    "snippet_content": content[:500],
                })

        # 去重
        seen = set()
        unique_candidates = []
        for c in candidates:
            key = (c["file_path"], c["symbol_name"])
            if key not in seen:
                seen.add(key)
                unique_candidates.append(c)

        logger.info(f"提取到 {len(unique_candidates)} 个候选符号")
        return unique_candidates

    async def _llm_select(
        self,
        issue_data: dict,
        candidates: list[dict],
        snippets: list[dict]
    ) -> EntrySelectionResult | None:
        """
        使用 LLM 选择最佳切入点

        Args:
            issue_data: Issue 数据
            candidates: 候选符号列表
            snippets: 原始代码片段

        Returns:
            选择结果
        """
        try:
            issue_title = issue_data.get("title", "")
            issue_description = issue_data.get("description", "")
            labels = issue_data.get("labels", [])

            if labels and isinstance(labels[0], dict):
                labels = [label.get("title", "") for label in labels]

            # 格式化候选列表
            candidates_json = json.dumps(
                [
                    {
                        "index": i,
                        "symbol_name": c["symbol_name"],
                        "file_path": c["file_path"],
                        "symbol_type": c["symbol_type"],
                        "lines": [c["start_line"], c["end_line"]],
                        "signature": c.get("signature", ""),
                        "snippet_preview": c.get("snippet_content", "")[:200],
                    }
                    for i, c in enumerate(candidates[:10])  # 限制数量
                ],
                indent=2,
                ensure_ascii=False
            )

            # 渲染 Prompt
            prompt = self.prompt_manager.render(
                "entry_selection",
                issue_title=issue_title,
                issue_description=issue_description or "无描述",
                labels=labels,
                candidates=candidates_json,
            )

            # 调用 LLM
            llm = await get_gpt_model(temperature=0.1)
            response = await llm.ainvoke(prompt)

            # 解析响应
            result = parse_json_response(response.content)

            # 构建结果
            selected_index = result.get("selected_index", 0)
            if selected_index >= len(candidates):
                selected_index = 0

            selected = candidates[selected_index]

            selected_target = TargetContext(
                symbol_name=selected["symbol_name"],
                file_path=selected["file_path"],
                symbol_type=selected["symbol_type"],
                start_line=selected["start_line"],
                end_line=selected["end_line"],
                status=TargetStatus.IN_PROGRESS,
                reason=result.get("reasoning", ""),
                confidence=result.get("confidence", 0.8),
            )

            # 备选切入点
            alternatives = []
            for alt_index in result.get("alternative_indices", [])[:2]:
                if alt_index < len(candidates) and alt_index != selected_index:
                    alt = candidates[alt_index]
                    alternatives.append(TargetContext(
                        symbol_name=alt["symbol_name"],
                        file_path=alt["file_path"],
                        symbol_type=alt["symbol_type"],
                        start_line=alt["start_line"],
                        end_line=alt["end_line"],
                        status=TargetStatus.PENDING,
                        reason="备选切入点",
                        confidence=0.5,
                    ))

            return EntrySelectionResult(
                selected_target=selected_target,
                alternatives=alternatives,
                selection_reasoning=result.get("reasoning", ""),
            )

        except Exception as e:
            logger.error(f"LLM 选择切入点失败: {e}", exc_info=True)
            # 降级处理：选择第一个候选
            if candidates:
                first = candidates[0]
                return EntrySelectionResult(
                    selected_target=TargetContext(
                        symbol_name=first["symbol_name"],
                        file_path=first["file_path"],
                        symbol_type=first["symbol_type"],
                        start_line=first["start_line"],
                        end_line=first["end_line"],
                        status=TargetStatus.IN_PROGRESS,
                        reason="降级选择第一个候选",
                        confidence=0.5,
                    ),
                    alternatives=[],
                    selection_reasoning="LLM 调用失败，降级选择第一个候选",
                )
            return None

