"""
OmniExplorer Node - 全面探索节点

三段式逻辑：
1. Semantic Search（定方向）：语义检索 Top-N 文件/代码块
2. Symbolic Search（定位置）：符号定位到具体类/函数
3. Structural Analysis（定影响）：构建涟漪图，找出所有调用方

输出契约：
- analysis.semantic_hits: 语义检索结果
- analysis.anchor_symbols: 锚定符号（含签名）
- analysis.ripple_graph: 调用涟漪图
- analysis.signature_contracts: 函数签名契约（供并行 CodeAgent 参考）
"""


from typing import Optional

from langchain.agents import create_agent
from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.core.logger_config import logger
from app.core.prompt_manager import prompt_manager
from app.core.trace_context import set_trace_id, set_agent_context, clear_agent_context
from app.decorators.tracking import track_node_metrics
from app.graph.state import IssueProcessState, ProcessStage
from app.graph.state.node_names import NodeName
from app.llms.llm_factory import get_llm_model
from app.tools.registry import get_tools_for_agent


class AnchorSymbol(BaseModel):
    """锚定符号"""
    file_path: str = Field(description="文件路径")
    symbol_name: str = Field(description="符号名称")
    start_line: int = Field(description="起始行")
    end_line: int = Field(description="结束行")
    signature: str = Field(description="函数/类签名")
    symbol_type: str = Field(description="符号类型：function/class/method")


class OmniExplorerOutput(BaseModel):
    """OmniExplorer 输出"""
    semantic_hits: list[dict] = Field(description="语义检索结果")
    anchor_symbols: list[AnchorSymbol] = Field(description="锚定符号列表")
    ripple_graph: dict = Field(description="调用涟漪图")
    signature_contracts: dict[str, str] = Field(
        description="函数签名契约：{symbol_name: signature}"
    )
    reasoning: str = Field(description="探索推理过程")


class OmniExplorerNode:
    """
    全面探索节点（替代 CodeRetriever + EntrySelector）
    
    三段式：Semantic → Symbolic → Structural
    """
    
    def __init__(self):
        """初始化节点"""
        self.prompt_manager = prompt_manager
        self.tools = get_tools_for_agent("omni_explorer")
    
    @track_node_metrics("omni_explorer")
    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command:
        """
        执行全面探索
        
        Args:
            state: 当前工作流状态
        
        Returns:
            Command 对象
        """
        # 从 state 恢复 trace_id 到上下文
        trace_id = state.get("runtime", {}).get("trace_id")
        if trace_id:
            set_trace_id(trace_id)
        
        update_dict = {}
        
        try:
            # 发送进度事件
            await adispatch_custom_event(
                ProcessStage.CODE_SEARCH.value,
                {
                    "status": NodeName.OMNI_EXPLORER.value,
                    "progress": "正在全面探索代码库...",
                    "think_chain_item": {
                        "type": NodeName.OMNI_EXPLORER.value,
                        "title": "OmniExplorer",
                        "desc": "Semantic→Symbolic→Structural 三段式探索",
                        "urls": [],
                    },
                },
            )
            
            # 从 planning 中获取当前任务信息（如果存在）
            planning = state.get("planning", {})
            active_task_id = planning.get("active_task_id")
            execution_plan = planning.get("execution_plan", [])
            
            # 查找当前任务的描述（如果由 Planner 调度）
            custom_query = None
            if active_task_id:
                for task in execution_plan:
                    if task.get("id") == active_task_id:
                        custom_query = task.get("description")
                        break
            
            # 设置 agent 上下文（用于日志追踪）
            set_agent_context(
                agent_name="omni_explorer",
                task_id=active_task_id,
                task_description=custom_query,
            )
            
            logger.info(f"开始执行 OmniExplorer 任务{f': {custom_query}' if custom_query else ''}")
            
            # 执行探索（使用 LLM Agent）
            result = await self._explore(state, custom_query=custom_query)
            
            if not result:
                error_msg = "OmniExplorer 未返回有效结果"
                logger.error(error_msg)
                runtime = state.get("runtime", {})
                update_dict.update({
                    "runtime": {
                        **runtime,
                        "error": error_msg,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.OMNI_EXPLORER.value,
                        ],
                        "current_step": NodeName.OMNI_EXPLORER.value,
                    },
                })
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)
            
            # 更新 state
            runtime = state.get("runtime", {})
            analysis = state.get("analysis", {})
            update_dict.update({
                "analysis": {
                    **analysis,
                    "semantic_hits": result.semantic_hits,
                    "anchor_symbols": [s.model_dump() for s in result.anchor_symbols],
                    "ripple_graph": result.ripple_graph,
                    "signature_contracts": result.signature_contracts,
                },
                "runtime": {
                    **runtime,
                    "executed_nodes": [
                        *runtime.get("executed_nodes", []),
                        NodeName.OMNI_EXPLORER.value,
                    ],
                    "current_step": NodeName.OMNI_EXPLORER.value,
                },
            })
            
            logger.info(
                f"OmniExplorer 完成: "
                f"semantic_hits={len(result.semantic_hits)}, "
                f"anchor_symbols={len(result.anchor_symbols)}, "
                f"ripple_edges={len(result.ripple_graph.get('edges', []))}"
            )
            
            # 发送完成事件
            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": NodeName.OMNI_EXPLORER.value,
                    "progress": "探索完成",
                    "think_chain_item": {
                        "type": NodeName.OMNI_EXPLORER.value,
                        "title": "OmniExplorer",
                        "desc": f"找到 {len(result.anchor_symbols)} 个锚点符号",
                        "urls": [],
                    },
                },
            )
            
            return Command(update=update_dict, goto=NodeName.PLANNER_ORCHESTRATOR.value)
        
        except Exception as e:
            logger.opt(exception=True).error(f"OmniExplorer 执行失败: {e}")
            runtime = state.get("runtime", {})
            update_dict.update({
                "runtime": {
                    **runtime,
                    "error": f"全面探索失败: {str(e)}",
                    "executed_nodes": [
                        *runtime.get("executed_nodes", []),
                        NodeName.OMNI_EXPLORER.value,
                    ],
                    "current_step": NodeName.OMNI_EXPLORER.value,
                },
            })
            
            return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)
        
        finally:
            # 清除 agent 上下文
            clear_agent_context()
    
    async def _explore(
        self,
        state: IssueProcessState,
        custom_query: Optional[str] = None,
    ) -> Optional[OmniExplorerOutput]:
        """
        执行三段式探索（使用 LLM Agent）
        
        Args:
            state: 当前工作流状态
            custom_query: 自定义查询（可选）
        
        Returns:
            OmniExplorerOutput 或 None
        """
        issue_data = state.get("issue_data", {})
        project_info = state.get("project_info", {})
        sandbox = state.get("sandbox", {})
        
        issue_title = issue_data.get("title", "")
        issue_description = issue_data.get("description", "")
        project_name = project_info.get("name", "")
        sandbox_id = sandbox.get("sandbox_id", "")
        
        if not project_name or not sandbox_id:
            logger.error("缺少必要信息：project_name 或 sandbox_id")
            return None
        
        # 构建 prompt
        prompt_text = self.prompt_manager.render(
            "omni_explorer",
            project_name=project_name,
            sandbox_id=sandbox_id,
            issue_title=issue_title,
            issue_description=issue_description,
            custom_query=custom_query,
        )
        
        # 调用 LLM Agent
        llm = await get_llm_model(model_name="gpt-5-2025-08-07")
        llm = llm.bind(parallel_tool_calls=True)
        
        agent = create_agent(
            model=llm,
            tools=self.tools,
            response_format=OmniExplorerOutput
        )
        
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": prompt_text}]},
            config={"max_concurrency": 6},  # 允许并发执行多个工具
            parallel_tool_calls=True,  # 确保调用时也启用并行
        )
        
        output: OmniExplorerOutput = result["structured_response"]
        
        logger.info(
            f"[OmniExplorer] 探索完成, "
            f"semantic_hits={len(output.semantic_hits)}, "
            f"anchor_symbols={len(output.anchor_symbols)}, "
            f"ripple_edges={len(output.ripple_graph.get('edges', []))}, "
            f"推理: {output.reasoning}"
        )
        
        return output


# 独立的执行函数
async def execute_omni_explorer(state: dict, task: str) -> dict:
    """
    执行 OmniExplorer（供 run_agent 工具调用）
    
    Args:
        state: 当前 state
        task: 任务描述（作为自定义查询）
    
    Returns:
        state 更新字典
    """
    node = OmniExplorerNode()
    # 使用 task 作为 custom_query
    result = await node._explore(state, custom_query=task)
    
    if not result:
        return {
            "__execution__": {
                "reasoning": "探索未返回结果",
                "result_hint": {
                    "semantic_hits_count": 0,
                    "anchor_symbols_count": 0,
                    "ripple_edges_count": 0,
                },
            },
        }
    
    return {
        "analysis": {
            "semantic_hits": result.semantic_hits,
            "anchor_symbols": [s.model_dump() for s in result.anchor_symbols],
            "ripple_graph": result.ripple_graph,
            "signature_contracts": result.signature_contracts,
        },
        "__execution__": {
            "reasoning": result.reasoning,
            "result_hint": {
                "semantic_hits_count": len(result.semantic_hits),
                "anchor_symbols_count": len(result.anchor_symbols),
                "ripple_edges_count": len(result.ripple_graph.get("edges", [])),
            },
        },
    }


__all__ = ["OmniExplorerNode", "execute_omni_explorer", "OmniExplorerOutput", "AnchorSymbol"]
