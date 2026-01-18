"""
Issue Insight Agent Node - 任务语义理解
分析任务并生成RAG搜索查询
"""

from typing import Literal

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command

from app.core.logger_config import logger
from app.core.prompt_manager import prompt_manager
from app.graph.state import IssueProcessState, NodeName, ProcessStage
from app.llms.llm_factory import get_llm_model
from app.schemas.issue_analysis import IssueAnalysis
from app.utils.common_function import parse_json_response


class IssueInsightAgentNode:
    """任务语义理解Agent节点"""

    def __init__(self):
        """初始化节点，预加载资源"""
        self.prompt_manager = prompt_manager

    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal["main_router", "sandbox_teardown"]]:
        """
        执行Issue分析并生成RAG搜索查询

        Args:
            state: 当前工作流状态

        Returns:
            Command对象，指定下一步路由到 main_router 节点
        """
        update_dict = {}

        try:
            # 发送进度事件 - 开始分析
            await adispatch_custom_event(
                ProcessStage.ISSUE_ANALYSIS.value,
                {
                    "status": NodeName.ISSUE_ANALYST.value,
                    "progress": "正在分析任务并生成搜索查询...",
                    "think_chain_item": {
                        "type": NodeName.ISSUE_ANALYST.value,
                        "title": "任务语义理解",
                        "desc": "分析任务内容，生成RAG搜索查询",
                        "urls": [],
                    },
                },
            )

            # 执行分析逻辑
            analysis = await self._analyze_and_generate_queries(state)

            # 检查内容是否有效
            if not analysis.valid:
                logger.warning(f"任务内容无效: {analysis.reason}")
                
                runtime = state.get("runtime", {})
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "error": f"任务内容无效: {analysis.reason}",
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.ISSUE_ANALYST.value,
                            ],
                            "current_step": NodeName.ISSUE_ANALYST.value,
                        },
                    }
                )

                # 发送无效内容事件
                await adispatch_custom_event(
                    ProcessStage.THINK_CHAIN.value,
                    {
                        "status": NodeName.ISSUE_ANALYST.value,
                        "progress": "任务内容无效，终止处理",
                        "think_chain_item": {
                            "type": NodeName.ISSUE_ANALYST.value,
                            "title": "任务内容无效",
                            "desc": analysis.reason,
                            "urls": [],
                        },
                    },
                )

                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

            # 提取查询字符串列表
            search_queries = [q.query for q in analysis.search_queries]

            # 更新状态
            runtime = state.get("runtime", {})
            update_dict.update(
                {
                    "analysis": {
                        "issue_type": analysis.issue_type,
                        "branch_name_suggestion": analysis.branch_name_suggestion,
                        "search_queries": search_queries,
                    },
                    "runtime": {
                        **runtime,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.ISSUE_ANALYST.value,
                        ],
                        "current_step": NodeName.ISSUE_ANALYST.value,
                    },
                }
            )

            logger.info(
                f"任务分析完成: 类型={analysis.issue_type}, "
                f"分支名={analysis.branch_name_suggestion}, "
                f"生成{len(search_queries)}个搜索查询"
            )

            # 发送完成事件
            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": NodeName.ISSUE_ANALYST.value,
                    "progress": "任务分析完成",
                    "think_chain_item": {
                        "type": NodeName.ISSUE_ANALYST.value,
                        "title": "任务语义理解",
                        "desc": f"类型: {analysis.issue_type}, 生成{len(search_queries)}个搜索查询",
                        "urls": [],
                    },
                },
            )

            return Command(update=update_dict, goto=NodeName.MAIN_ROUTER.value)

        except Exception as e:
            logger.error(f"任务分析失败: {e}", exc_info=True)
            runtime = state.get("runtime", {})
            update_dict.update(
                {
                    "runtime": {
                        **runtime,
                        "error": f"任务分析失败: {str(e)}",
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.ISSUE_ANALYST.value,
                        ],
                        "current_step": NodeName.ISSUE_ANALYST.value,
                    },
                }
            )

            return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

    async def _analyze_and_generate_queries(
        self, state: IssueProcessState
    ) -> IssueAnalysis:
        """
        一次LLM调用完成分析和查询生成

        Returns:
            IssueAnalysis: 包含valid、issue_type和search_queries等字段
        """
        issue_data = state.get("issue_data", {})
        project_info = state.get("project_info", {})

        issue_title = issue_data.get("title", "")
        issue_description = issue_data.get("description", "")
        labels = issue_data.get("labels", [])
        project_name = project_info.get("name", "")
        project_path = project_info.get("path_with_namespace", "")

        if labels and isinstance(labels[0], dict):
            labels = [label.get("title", "") for label in labels]

        llm = await get_llm_model(model_name="gpt-5-2025-08-07")

        # 渲染统一prompt
        prompt = self.prompt_manager.render(
            "issue_queries_generation",
            issue_title=issue_title,
            issue_description=issue_description or "无描述",
            labels=labels,
            project_name=project_name,
            project_path=project_path,
        )

        response = await llm.ainvoke(prompt)
        response_data = parse_json_response(response.content)

        # 返回IssueAnalysis对象
        return IssueAnalysis(**response_data)
