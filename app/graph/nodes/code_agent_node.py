"""
Code Agent Node - 统一的代码生成与修复节点

职责：
- 生成初始补丁
- 修复验证错误
- 批量修改受影响的文件
- 支持单文件或多文件协同修改
"""

from typing import Any, Literal, Optional
from langchain.agents import create_agent
from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command

from app.core.logger_config import logger
from app.core.prompt_manager import prompt_manager
from app.core.trace_context import set_trace_id, set_agent_context, clear_agent_context
from app.decorators.tracking import track_node_metrics
from app.graph.state import IssueProcessState, NodeName, ProcessStage
from app.llms.llm_factory import get_llm_model
from app.sandbox.git_service import GitService
from app.sandbox.manager import get_sandbox_manager
from app.tools.registry import get_tools_for_agent
from app.schemas.agent_outputs import CodeAgentOutput


class CodeAgentNode:
    """
    代码生成与修复节点（统一的写代码节点）
    
    功能：
    - 生成初始补丁（从可编辑上下文）
    - 修复验证错误（从 verification 结果）
    - 批量修改受影响的文件（从 impact 报告）
    """
    
    def __init__(self):
        """初始化节点"""
        self.prompt_manager = prompt_manager
        # 获取 CodeAgent 可用的工具（读+写）
        self.tools = get_tools_for_agent("code_agent")
    
    @track_node_metrics("code_agent")
    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal["planner_orchestrator", "sandbox_teardown"]]:
        """
        执行代码生成/修复任务
        
        Args:
            state: 当前工作流状态
        
        Returns:
            Command 对象，成功返回 planner_orchestrator，失败返回 sandbox_teardown
        """
        # 从 state 恢复 trace_id 到上下文
        trace_id = state.get("runtime", {}).get("trace_id")
        if trace_id:
            set_trace_id(trace_id)
        
        update_dict = {}
        
        try:
            # 发送进度事件
            await adispatch_custom_event(
                ProcessStage.CODE_GENERATION.value,
                {
                    "status": NodeName.CODE_AGENT.value,
                    "progress": "正在生成代码补丁...",
                    "think_chain_item": {
                        "type": NodeName.CODE_AGENT.value,
                        "title": "代码生成",
                        "desc": "生成/修复代码补丁",
                        "urls": [],
                    },
                },
            )
            
            # 从 planning 中获取当前任务信息
            planning = state.get("planning", {})
            active_task_id = planning.get("active_task_id")
            execution_plan = planning.get("execution_plan", [])
            
            if not active_task_id:
                error_msg = "缺少 active_task_id，无法执行 CodeAgent"
                logger.error(error_msg)
                runtime = state.get("runtime", {})
                update_dict.update({
                    "runtime": {
                        **runtime,
                        "error": error_msg,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.CODE_AGENT.value,
                        ],
                        "current_step": NodeName.CODE_AGENT.value,
                    },
                })
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)
            
            # 查找当前任务
            current_task = None
            for task in execution_plan:
                if task.get("id") == active_task_id:
                    current_task = task
                    break
            
            if not current_task:
                error_msg = f"找不到任务 ID: {active_task_id}"
                logger.error(error_msg)
                runtime = state.get("runtime", {})
                update_dict.update({
                    "runtime": {
                        **runtime,
                        "error": error_msg,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.CODE_AGENT.value,
                        ],
                        "current_step": NodeName.CODE_AGENT.value,
                    },
                })
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)
            
            # 设置 agent 上下文（用于日志追踪）
            task_description = current_task.get("description", "")
            set_agent_context(
                agent_name="code_agent",
                task_id=active_task_id,
                task_description=task_description,
            )
            
            # 执行代码生成
            logger.info(f"开始执行 CodeAgent 任务: {task_description}")
            result = await self._generate_code(state, current_task)
            
            if not result or not result.patches:
                error_msg = "CodeAgent 未生成有效的补丁"
                logger.error(error_msg)
                runtime = state.get("runtime", {})
                update_dict.update({
                    "runtime": {
                        **runtime,
                        "error": error_msg,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.CODE_AGENT.value,
                        ],
                        "current_step": NodeName.CODE_AGENT.value,
                    },
                })
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)
            
            # 应用补丁到 sandbox
            sandbox = state.get("sandbox", {})
            sandbox_id = sandbox.get("sandbox_id")
            repo_path = sandbox.get("repo_path", ".")
            
            if not sandbox_id:
                error_msg = "缺少 sandbox_id，无法应用补丁"
                logger.error(error_msg)
                runtime = state.get("runtime", {})
                update_dict.update({
                    "runtime": {
                        **runtime,
                        "error": error_msg,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.CODE_AGENT.value,
                        ],
                        "current_step": NodeName.CODE_AGENT.value,
                    },
                })
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)
            
            # 应用所有补丁
            sandbox_manager = get_sandbox_manager()
            git_service = GitService(sandbox_manager, sandbox_id)
            
            for patch in result.patches:
                try:
                    logger.info(f"正在应用补丁 {patch.id} 到 sandbox {sandbox_id}")
                    await git_service.apply_patch(
                        patch_content=patch.unified_diff,
                        repo_path=repo_path
                    )
                    logger.info(f"补丁 {patch.id} 应用成功")
                    
                except Exception as e:
                    error_msg = f"补丁 {patch.id} 应用失败: {str(e)}"
                    logger.error(error_msg, exc_info=True)
                    
                    runtime = state.get("runtime", {})
                    update_dict.update({
                        "runtime": {
                            **runtime,
                            "error": error_msg,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.CODE_AGENT.value,
                            ],
                            "current_step": NodeName.CODE_AGENT.value,
                        },
                    })
                    return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)
            
            # 更新 state（将结果写入 patching 域和 planning.task_results）
            patching = state.get("patching", {})
            existing_patches = patching.get("patches", [])
            
            # 添加新生成的补丁（结构化）
            for patch in result.patches:
                existing_patches.append(patch.model_dump())
            
            runtime = state.get("runtime", {})
            task_results = planning.get("task_results", {})
            task_results[active_task_id] = {
                "patches": [p.model_dump() for p in result.patches],
                "reasoning": result.reasoning,
            }
            
            update_dict.update({
                "patching": {
                    **patching,
                    "patches": existing_patches,
                },
                "planning": {
                    **planning,
                    "task_results": task_results,
                },
                "runtime": {
                    **runtime,
                    "executed_nodes": [
                        *runtime.get("executed_nodes", []),
                        NodeName.CODE_AGENT.value,
                    ],
                    "current_step": NodeName.CODE_AGENT.value,
                },
            })
            
            logger.info(f"CodeAgent 完成: {result.patches[0].summary if result.patches else '无补丁'}")
            
            # 发送完成事件
            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": NodeName.CODE_AGENT.value,
                    "progress": "代码生成完成",
                    "think_chain_item": {
                        "type": NodeName.CODE_AGENT.value,
                        "title": "代码生成",
                        "desc": result.patches[0].summary if result.patches else "无补丁",
                        "urls": [],
                    },
                },
            )
            
            return Command(update=update_dict, goto=NodeName.PLANNER_ORCHESTRATOR.value)
        
        except Exception as e:
            logger.error(f"CodeAgent 执行失败: {e}", exc_info=True)
            runtime = state.get("runtime", {})
            update_dict.update({
                "runtime": {
                    **runtime,
                    "error": f"代码生成失败: {str(e)}",
                    "executed_nodes": [
                        *runtime.get("executed_nodes", []),
                        NodeName.CODE_AGENT.value,
                    ],
                    "current_step": NodeName.CODE_AGENT.value,
                },
            })
            
            return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)
        
        finally:
            # 清除 agent 上下文
            clear_agent_context()
    
    async def _generate_code(
        self,
        state: IssueProcessState,
        task: dict,
    ) -> Optional[CodeAgentOutput]:
        """
        生成代码（调用 LLM Agent）
        
        Args:
            state: 当前工作流状态
            task: 当前任务（from execution_plan）
        
        Returns:
            CodeAgentOutput 或 None
        """
        # 获取 sandbox 上下文
        sandbox = state.get("sandbox", {})
        sandbox_id = sandbox.get("sandbox_id", "")
        
        # 从任务中提取约束和上下文
        task_description = task.get("description", "")
        allowed_files = task.get("allowed_files", [])
        
        # 准备验证错误信息
        verification_errors = None
        verification = state.get("verification", {})
        final_verification = verification.get("final_verification")
        if final_verification:
            all_issues = final_verification.get("all_issues", [])
            if all_issues:
                # 只传递 error 级别的问题
                error_issues = [issue for issue in all_issues if issue.get("severity") == "error"]
                if error_issues:
                    verification_errors = error_issues
        
        # 构建 prompt
        prompt_text = self.prompt_manager.render(
            "code_agent",
            task_description=task_description,
            allowed_files=allowed_files,
            verification_errors=verification_errors,
            sandbox_id=sandbox_id,
        )
        
        # 调用 LLM Agent
        llm = await get_llm_model(model_name="gpt-5-2025-08-07")
        llm = llm.bind(parallel_tool_calls=True)
        
        agent = create_agent(
            model=llm,
            tools=self.tools,
            response_format=CodeAgentOutput
        )
        
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": prompt_text}]},
            config={"max_concurrency": 6},  # 允许并发执行多个工具
            parallel_tool_calls=True,  # 确保调用时也启用并行
        )
        
        output: CodeAgentOutput = result["structured_response"]
        
        if not output.patches:
            logger.error("Agent 未返回补丁列表")
            return None
        
        logger.info(
            f"[CodeAgent] 生成补丁完成, "
            f"补丁数: {len(output.patches)}, "
            f"推理: {output.reasoning}"
        )
        
        return output


# 独立的执行函数（供 run_agent 工具调用）
async def execute_code_agent(
    state: dict,
    task: str,
    allowed_files: list[str],
) -> dict:
    """
    执行 CodeAgent（供 run_agent 工具调用）
    
    Args:
        state: 当前 state
        task: 任务描述
        allowed_files: 允许修改的文件
    
    Returns:
        state 更新字典
    """
    node = CodeAgentNode()
    
    # 构建临时任务
    temp_task = {
        "id": "temp_task",
        "description": task,
        "allowed_files": allowed_files,
    }
    
    # 执行代码生成
    result = await node._generate_code(state, temp_task)
    
    if not result or not result.patches:
        return {
            "__execution__": {
                "reasoning": "代码生成未返回有效补丁",
                "result_hint": {
                    "patches_count": 0,
                    "modified_files_count": 0,
                },
            },
        }
    
    # 应用补丁到 sandbox（如果有）
    sandbox = state.get("sandbox", {})
    sandbox_id = sandbox.get("sandbox_id")
    repo_path = sandbox.get("repo_path", ".")
    
    if sandbox_id:
        from app.sandbox.git_service import GitService
        from app.sandbox.manager import get_sandbox_manager
        
        sandbox_manager = get_sandbox_manager()
        git_service = GitService(sandbox_manager, sandbox_id)
        
        for patch in result.patches:
            try:
                await git_service.apply_patch(
                    patch_content=patch.unified_diff,
                    repo_path=repo_path
                )
            except Exception as e:
                logger.error(f"补丁应用失败: {e}")
    
    # 统计修改的文件
    all_files = set()
    for patch in result.patches:
        all_files.update(patch.file_paths)
    
    # 返回 state 更新
    return {
        "patching": {
            "patches": [p.model_dump() for p in result.patches],
        },
        "__execution__": {
            "reasoning": result.reasoning,
            "result_hint": {
                "patches_count": len(result.patches),
                "modified_files_count": len(all_files),
                "patch_summary": result.patches[0].summary if result.patches else "",  # 不截断
            },
        },
    }


# 导出
__all__ = ["CodeAgentNode", "CodeAgentOutput", "execute_code_agent"]
