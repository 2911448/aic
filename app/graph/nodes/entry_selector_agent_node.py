"""
Entry Selector Agent Node - 切入点选择节点
使用 LangChain Agent + tools 实现智能切入点定位
Agent 可以主动查阅文件内容以确认选择
"""

from typing import Literal, Optional

from langchain.agents import create_agent
from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.core.logger_config import logger
from app.core.prompt_manager import prompt_manager
from app.graph.state import IssueProcessState, NodeName, ProcessStage
from app.llms.llm_factory import get_llm_model
from app.utils.tree_sitter_service import tree_sitter_service
from app.schemas.context_assembly import (
    EntrySelectionResult,
    TargetContext,
    TargetStatus,
)
from app.tools.registry import get_tools_for_agent
from app.tools.sandbox.read_file import read_file_from_sandbox_core
from app.utils.common_function import detect_language


class EntrySelectionOutput(BaseModel):
    """Entry Selector Agent 的结构化输出"""
    file_path: str = Field(description="目标文件路径")
    symbol_name: str = Field(description="目标符号名称 (如 ClassName.method_name 或 function_name)")
    symbol_type: str = Field(description="符号类型: function, method, class")
    start_line: int = Field(description="符号起始行号", ge=1)
    end_line: int = Field(description="符号结束行号", ge=1)
    reasoning: str = Field(description="选择该切入点的详细理由")
    confidence: float = Field(description="置信度 (0.0-1.0)", ge=0.0, le=1.0)


class EntrySelectorAgentNode:
    """切入点选择 Agent 节点 (Agentic Mode with Tools)"""

    def __init__(self):
        """初始化节点"""
        self.prompt_manager = prompt_manager
        self.tree_sitter = tree_sitter_service
        # 获取 entry_selector 专用工具集
        self.tools = get_tools_for_agent("entry_selector")

    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal["main_router", "sandbox_teardown"]]:
        """
        从 RAG 结果中选择最佳切入点

        Args:
            state: 当前工作流状态

        Returns:
            Command对象，返回 main_router 节点
        """
        update_dict = {}

        try:
            # 发送进度事件
            await adispatch_custom_event(
                ProcessStage.ENTRY_SELECTION.value,
                {
                    "status": NodeName.ENTRY_SELECTOR.value,
                    "progress": "正在智能分析代码并确认最佳修改切入点...",
                    "think_chain_item": {
                        "type": NodeName.ENTRY_SELECTOR.value,
                        "title": "切入点选择",
                        "desc": "Agent 主动查阅文件以定位问题根源",
                        "urls": [],
                    },
                },
            )

            # 执行切入点选择
            result = await self._select_entry_point(state)

            if result is None:
                error_msg = "无法从检索结果中选择有效的切入点"
                logger.warning(error_msg)
                runtime = state.get("runtime", {})
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "error": error_msg,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.ENTRY_SELECTOR.value,
                            ],
                            "current_step": NodeName.ENTRY_SELECTOR.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

            # 更新状态（使用分域结构）
            runtime = state.get("runtime", {})
            targeting = state.get("targeting", {})
            update_dict.update(
                {
                    "targeting": {
                        **targeting,
                        "current_target": result.selected_target.model_dump(),
                    },
                    "runtime": {
                        **runtime,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.ENTRY_SELECTOR.value,
                        ],
                        "current_step": NodeName.ENTRY_SELECTOR.value,
                    },
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

            return Command(update=update_dict, goto=NodeName.MAIN_ROUTER.value)

        except Exception as e:
            logger.error(f"切入点选择失败: {e}", exc_info=True)
            runtime = state.get("runtime", {})
            update_dict.update(
                {
                    "runtime": {
                        **runtime,
                        "error": f"切入点选择失败: {str(e)}",
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.ENTRY_SELECTOR.value,
                        ],
                        "current_step": NodeName.ENTRY_SELECTOR.value,
                    },
                }
            )

            return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

    async def _select_entry_point(
        self,
        state: IssueProcessState
    ) -> EntrySelectionResult | None:
        """
        使用 LangChain Agent 选择最佳切入点
        
        Agent 可以主动调用 read_file 等工具来查阅代码

        Args:
            state: 当前工作流状态

        Returns:
            切入点选择结果
        """
        # 1. 准备数据
        retrieval = state.get("retrieval", {})
        retrieved_code = retrieval.get("retrieved_code", [])
        issue_data = state.get("issue_data", {})
        sandbox = state.get("sandbox", {})
        
        sandbox_id = sandbox.get("sandbox_id", "")
        
        if not sandbox_id:
            logger.error("Sandbox ID 为空，无法提供工具访问能力")
            return None

        if not retrieved_code:
            logger.warning("没有检索到代码片段")
            return None

        # 2. 格式化候选摘要
        candidates_summary = self._format_candidates_summary(retrieved_code)
        
        issue_title = issue_data.get("title", "")
        issue_description = issue_data.get("description", "")
        labels = issue_data.get("labels", [])
        
        if labels and isinstance(labels[0], dict):
            labels = [label.get("title", "") for label in labels]

        # 3. 渲染 Prompt
        prompt_text = self.prompt_manager.render(
            "entry_selection",
            issue_title=issue_title,
            issue_description=issue_description or "无描述",
            labels=labels,
            candidates_summary=candidates_summary,
            candidate_count=min(len(retrieved_code), 10),
            sandbox_id=sandbox_id,
        )

        try:
            # 4. 构建 Agent 实例
            llm = await get_llm_model(model_name="gpt-5-2025-08-07", temperature=0.1)
            agent = create_agent(
                model=llm,
                tools=self.tools,
                response_format=EntrySelectionOutput
            )
            
            # 5. 调用 Agent
            logger.info(f"Entry Selector Agent 开始调查 (候选数: {len(retrieved_code)})")
            result = await agent.ainvoke({
                "messages": [{"role": "user", "content": prompt_text}]
            })
            
            # 6. 从 structured_response 获取结构化输出
            output: EntrySelectionOutput = result["structured_response"]
            
            logger.info(
                f"[Entry Selector Agent] 选择完成: {output.symbol_name} "
                f"in {output.file_path} (confidence: {output.confidence})"
            )

            # 7. 验证并构建 TargetContext
            target_context = await self._validate_and_build_target(
                output, sandbox_id
            )
            
            if target_context is None:
                logger.warning("Agent 输出的符号无法验证，尝试 Fallback")
                return self._fallback_selection(retrieved_code)

            # 8. 构建最终结果
            return EntrySelectionResult(
                selected_target=target_context,
                alternatives=[],  # Agent 模式下暂不提供 alternatives
                selection_reasoning=output.reasoning,
            )

        except Exception as e:
            logger.error(f"Entry Selector Agent 失败: {e}", exc_info=True)
            # Fallback: 选择第一个检索结果
            return self._fallback_selection(retrieved_code)

    def _format_candidates_summary(self, snippets: list[dict]) -> str:
        """格式化候选代码片段摘要供 Agent 参考"""
        lines = []
        for i, s in enumerate(snippets[:10]):  # 只显示前 10 个
            file_path = s.get("file_path", "unknown")
            symbol_name = s.get("symbol_name", "unknown")
            summary = s.get("summary", "")
            
            # 截取代码预览
            content_preview = s.get("content", "")[:150].replace("\n", " ")
            
            lines.append(f"**Snippet {i}**:")
            lines.append(f"  - File: `{file_path}`")
            lines.append(f"  - Symbol: `{symbol_name}`")
            if summary:
                lines.append(f"  - Summary: {summary}")
            lines.append(f"  - Preview: {content_preview}...")
            lines.append("")
        
        return "\n".join(lines)

    async def _validate_and_build_target(
        self,
        output: EntrySelectionOutput,
        sandbox_id: str
    ) -> Optional[TargetContext]:
        """
        验证 Agent 输出并构建 TargetContext
        
        通过读取完整文件并解析 AST 来验证符号的真实性和准确性

        Args:
            output: Agent 输出的选择结果
            sandbox_id: Sandbox ID

        Returns:
            TargetContext 或 None（如果验证失败）
        """
        try:
            # 读取完整文件内容
            content = await read_file_from_sandbox_core(
                sandbox_id=sandbox_id,
                file_path=output.file_path
            )
            
            # 推断语言
            language = detect_language(output.file_path)
            
            # 解析 AST 验证符号
            ast_info = self.tree_sitter.parse_code(content, language, output.file_path)
            
            if not ast_info or not ast_info.symbols:
                logger.warning(f"无法解析 AST: {output.file_path}")
                # 如果 AST 失败，使用 Agent 提供的行号（信任 Agent）
                return TargetContext(
                    symbol_name=output.symbol_name,
                    file_path=output.file_path,
                    symbol_type=output.symbol_type,
                    start_line=output.start_line,
                    end_line=output.end_line,
                    status=TargetStatus.IN_PROGRESS,
                    reason=output.reasoning,
                    confidence=output.confidence,
                )
            
            # 在 AST 中查找匹配的符号
            matched_symbol = None
            for symbol in ast_info.symbols:
                # 全名匹配 (Parent.Name)
                full_name = f"{symbol.parent}.{symbol.name}" if symbol.parent else symbol.name
                
                if full_name == output.symbol_name or symbol.name == output.symbol_name:
                    matched_symbol = symbol
                    # 完全匹配优先
                    if full_name == output.symbol_name:
                        break
            
            if matched_symbol:
                # 使用 AST 解析的精确行号
                logger.info(f"符号验证成功: {output.symbol_name} at lines {matched_symbol.start_line}-{matched_symbol.end_line}")
                return TargetContext(
                    symbol_name=output.symbol_name,
                    file_path=output.file_path,
                    symbol_type=matched_symbol.type,
                    start_line=matched_symbol.start_line,
                    end_line=matched_symbol.end_line,
                    status=TargetStatus.IN_PROGRESS,
                    reason=output.reasoning,
                    confidence=output.confidence,
                )
            else:
                # 符号未找到，但信任 Agent 的判断（可能是复杂符号名）
                logger.warning(
                    f"在 AST 中未找到符号 {output.symbol_name}，使用 Agent 提供的行号"
                )
                return TargetContext(
                    symbol_name=output.symbol_name,
                    file_path=output.file_path,
                    symbol_type=output.symbol_type,
                    start_line=output.start_line,
                    end_line=output.end_line,
                    status=TargetStatus.IN_PROGRESS,
                    reason=output.reasoning + " (符号未在 AST 中验证)",
                    confidence=max(0.3, output.confidence - 0.2),  # 降低置信度
                )

        except Exception as e:
            logger.error(f"验证符号失败: {e}", exc_info=True)
            return None

    def _fallback_selection(self, retrieved_code: list[dict]) -> EntrySelectionResult | None:
        """
        Fallback 逻辑：当 Agent 失败时，选择第一个检索结果

        Args:
            retrieved_code: 检索到的代码片段列表

        Returns:
            EntrySelectionResult 或 None
        """
        if not retrieved_code:
            return None
        
        first = retrieved_code[0]
        file_path = first.get("file_path", "")
        symbol_name = first.get("symbol_name", "")
        
        # 使用 snippet 自带的行号（可能不准确，但这是 fallback）
        start_line = first.get("start_line", 1)
        end_line = first.get("end_line", start_line + 50)
        
        logger.warning(f"使用 Fallback 选择: {symbol_name} in {file_path}")
        
        return EntrySelectionResult(
            selected_target=TargetContext(
                symbol_name=symbol_name,
                file_path=file_path,
                symbol_type="unknown",
                start_line=start_line,
                end_line=end_line,
                status=TargetStatus.IN_PROGRESS,
                reason="Agent 失败，降级为选择相关度最高的检索结果",
                confidence=0.3,
            ),
            alternatives=[],
            selection_reasoning="Fallback: Agent 失败或超时"
        )

