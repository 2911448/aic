"""
OmniExplorer Node - 精准定位专家

三步工作流：
1. Semantic Search（语义检索）：在代码库中快速定位嫌疑函数
2. Tree-sitter Parse（结构解析）：精确获取目标函数的结构信息
3. Find References（查找引用）：用 ripgrep 找出所有调用者

输出契约：
- analysis.omni_explorer.queries: 改写的查询列表
- analysis.omni_explorer.suspects: 嫌疑函数列表（Top 3）
- analysis.omni_explorer.target: 目标函数详细信息
- analysis.omni_explorer.references: 调用者列表
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


class SuspectFunction(BaseModel):
    """嫌疑函数"""
    file_path: str = Field(description="文件路径")
    function_name: str = Field(description="函数名称")


class TargetFunction(BaseModel):
    """目标函数详细信息"""
    file_path: str = Field(description="文件路径")
    function_name: str = Field(description="函数名称")
    start_line: int = Field(description="起始行号")
    end_line: int = Field(description="结束行号")
    signature: str = Field(description="函数签名")
    indent: int = Field(description="缩进层级（空格数）")
    returns: str = Field(description="返回对象（轻量推断）")


class ReferenceLocation(BaseModel):
    """引用位置"""
    file_path: str = Field(description="文件路径")
    line: int = Field(description="行号")
    snippet: str = Field(description="代码片段")
    caller_symbol: Optional[str] = Field(None, description="调用者符号（可选）")


class OmniExplorerOutput(BaseModel):
    """OmniExplorer 输出"""
    queries: list[str] = Field(description="改写的查询列表（2-3个）")
    suspects: list[SuspectFunction] = Field(description="嫌疑函数列表（Top 3）")
    target: TargetFunction = Field(description="目标函数详细信息")
    references: list[ReferenceLocation] = Field(description="调用者列表")
    reasoning: str = Field(description="探索推理过程")


class OmniExplorerNode:
    """
    精准定位专家（OmniExplorer）
    
    三步工作流：Semantic Search → Tree-sitter Parse → Find References
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
        执行精准定位探索
        
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
                    "progress": "正在精准定位目标函数...",
                    "think_chain_item": {
                        "type": NodeName.OMNI_EXPLORER.value,
                        "title": "OmniExplorer",
                        "desc": "语义检索 → Tree-sitter → 查找引用",
                        "urls": [],
                    },
                },
            )
            
            # 从 planning 中获取当前任务信息（如果存在）
            planning = state.get("planning", {})
            active_task_id = planning.get("active_task_id")
            execution_plan = planning.get("execution_plan", [])
            
            # 查找当前任务的描述（如果由 Planner 调度）
            task_description = None
            if active_task_id:
                for task in execution_plan:
                    if task.get("id") == active_task_id:
                        task_description = task.get("description")
                        break
            
            # 设置 agent 上下文（用于日志追踪）
            set_agent_context(
                agent_name="omni_explorer",
                task_id=active_task_id,
                task_description=task_description,
            )
            
            logger.info(f"开始执行 OmniExplorer 任务{f': {task_description}' if task_description else ''}")
            
            # 执行探索（使用 LLM Agent）
            result = await self._explore(state, task_description=task_description)
            
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
            
            # 更新 state（添加到列表）
            runtime = state.get("runtime", {})
            analysis = state.get("analysis", {})
            
            # 确定 task_id（如果有则用，否则用时间戳作为唯一标识）
            import time
            task_id = active_task_id or f"omni_{int(time.time())}"
            
            # 获取现有的 omni_explorer 列表
            existing_omni_explorer = analysis.get("omni_explorer", [])
            if not isinstance(existing_omni_explorer, list):
                existing_omni_explorer = []
            
            # 添加本次结果到列表
            updated_omni_explorer = existing_omni_explorer + [{
                "task_id": task_id,
                "result": result.model_dump(),
            }]
            
            update_dict.update({
                "analysis": {
                    **analysis,
                    "omni_explorer": updated_omni_explorer,
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
                f"suspects={len(result.suspects)}, "
                f"target={result.target.function_name}, "
                f"references={len(result.references)}"
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
                        "desc": f"定位到 {result.target.function_name}，找到 {len(result.references)} 个调用者",
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
                    "error": f"精准定位失败: {str(e)}",
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
        task_description: str,
    ) -> Optional[OmniExplorerOutput]:
        """
        执行三步探索（使用 LLM Agent）
        
        Args:
            state: 当前工作流状态
            task_description: 任务描述
        
        Returns:
            OmniExplorerOutput 或 None
        """
        project_info = state.get("project_info", {})
        sandbox = state.get("sandbox", {})
        
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
            task_description=task_description,
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
            f"suspects={len(output.suspects)}, "
            f"target={output.target.function_name}, "
            f"references={len(output.references)}, "
            f"推理: {output.reasoning}"
        )
        
        return output


# 独立的执行函数
async def execute_omni_explorer(state: dict, task: str) -> dict:
    """
    执行 OmniExplorer（供 run_agent 工具调用）
    
    Args:
        state: 当前 state
        task: 任务描述（作为 task_description）
    
    Returns:
        state 更新字典（注意：omni_explorer 结果不在这里写入，而是在 run_agent 中按 task_id 归档）
    """
    node = OmniExplorerNode()
    # 使用 task 作为 task_description
    result = await node._explore(state, task)
    
    if not result:
        return {
            "__execution__": {
                "reasoning": "探索未返回结果",
                "result_hint": {
                    "suspects_count": 0,
                    "references_count": 0,
                },
            },
        }
    
    return {
        "analysis": {
            "omni_explorer": result.model_dump(),  # 这里返回结果，由 run_agent 按 task_id 归档
        },
        "__execution__": {
            "reasoning": result.reasoning,
            "result_hint": {
                "suspects_count": len(result.suspects),
                "target_function": result.target.function_name,
                "references_count": len(result.references),
            },
        },
    }


__all__ = ["OmniExplorerNode", "execute_omni_explorer", "OmniExplorerOutput"]
