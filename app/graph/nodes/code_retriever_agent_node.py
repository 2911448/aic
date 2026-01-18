"""
Code Retriever Agent Node - 代码检索节点
使用RAG技术检索相关代码片段
"""

import asyncio
from typing import Literal

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command

from app.core.logger_config import logger
from app.core.milvus import milvus_service
from app.core.trace_context import set_trace_id
from app.decorators.tracking import track_node_metrics
from app.graph.state import IssueProcessState, NodeName, ProcessStage
from app.rag.embedding import embedding_service
from app.rag.rerank import rerank_service


class CodeRetrieverAgentNode:
    """代码检索Agent节点"""

    def __init__(self):
        """初始化节点"""
        self.top_k = 30  # 每个query从Milvus召回的结果数
        self.final_top_n = 5  # 重排序后最终保留的结果数

    @track_node_metrics("code_retriever")
    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal["main_router", "sandbox_teardown"]]:
        """
        执行代码检索

        Args:
            state: 当前工作流状态

        Returns:
            Command对象，返回 main_router 节点
        """
        # 从 state 恢复 trace_id 到上下文
        trace_id = state.get("runtime", {}).get("trace_id")
        if trace_id:
            set_trace_id(trace_id)
        
        update_dict = {}

        try:
            # 发送进度事件 - 开始检索
            await adispatch_custom_event(
                ProcessStage.CODE_SEARCH.value,
                {
                    "status": NodeName.CODE_RETRIEVER.value,
                    "progress": "正在从代码库检索相关代码片段...",
                    "think_chain_item": {
                        "type": NodeName.CODE_RETRIEVER.value,
                        "title": "代码检索",
                        "desc": "使用向量检索和重排序技术查找相关代码",
                        "urls": [],
                    },
                },
            )

            # 执行检索逻辑
            retrieved_code = await self._retrieve_and_rerank(state)

            # 更新状态
            runtime = state.get("runtime", {})
            update_dict.update(
                {
                    "retrieval": {
                        "retrieved_code": retrieved_code,
                        "retrieval_meta": {
                            "count": len(retrieved_code),
                            "top_k": self.top_k,
                            "final_top_n": self.final_top_n,
                        },
                    },
                    "runtime": {
                        **runtime,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.CODE_RETRIEVER.value,
                        ],
                        "current_step": NodeName.CODE_RETRIEVER.value,
                    },
                }
            )

            logger.info(
                f"代码检索完成，返回 {len(retrieved_code)} 个代码片段"
            )

            # 发送完成事件
            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": NodeName.CODE_RETRIEVER.value,
                    "progress": "代码检索完成",
                    "think_chain_item": {
                        "type": NodeName.CODE_RETRIEVER.value,
                        "title": "代码检索",
                        "desc": f"检索到 {len(retrieved_code)} 个相关代码片段",
                        "urls": [],
                    },
                },
            )

            return Command(update=update_dict, goto=NodeName.MAIN_ROUTER.value)

        except Exception as e:
            logger.error(f"代码检索失败: {e}", exc_info=True)
            runtime = state.get("runtime", {})
            update_dict.update(
                {
                    "runtime": {
                        **runtime,
                        "error": f"代码检索失败: {str(e)}",
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.CODE_RETRIEVER.value,
                        ],
                        "current_step": NodeName.CODE_RETRIEVER.value,
                    },
                }
            )

            return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

    async def _retrieve_and_rerank(
        self, state: IssueProcessState
    ) -> list[dict]:
        """
        执行完整的检索和重排序流程

        Args:
            state: 当前工作流状态

        Returns:
            重排序后的代码片段列表
        """
        # 1. 获取输入数据
        analysis = state.get("analysis", {})
        search_queries = analysis.get("search_queries", [])
        issue_data = state.get("issue_data", {})
        project_info = state.get("project_info", {})

        if not search_queries:
            logger.warning("没有搜索查询，跳过代码检索")
            return []

        # 提取项目信息
        project_name = project_info.get("name", "")
        if not project_name:
            logger.error("缺少项目名称，无法执行代码检索")
            raise ValueError("项目名称为空")

        # 构建Issue描述用于重排序
        issue_title = issue_data.get("title", "")
        issue_description = issue_data.get("description", "")
        issue_query = f"{issue_title}\n{issue_description}" if issue_description else issue_title

        logger.info(
            f"开始代码检索: 项目={project_name}, "
            f"查询数量={len(search_queries)}, "
            f"Issue查询长度={len(issue_query)}"
        )

        # 2. 向量化所有查询
        logger.info(f"正在向量化 {len(search_queries)} 个搜索查询...")
        query_vectors = await embedding_service.embed_texts(search_queries)
        logger.info(f"向量化完成，获得 {len(query_vectors)} 个查询向量")

        # 3. 并行检索Milvus
        logger.info(f"正在并行检索 Milvus（每个query top_k={self.top_k}）...")
        filter_expr = f'project_name == "{project_name}"'

        search_tasks = [
            self._search_milvus(vector, filter_expr)
            for vector in query_vectors
        ]
        search_results_list = await asyncio.gather(*search_tasks)

        # 4. 合并结果并去重
        logger.info("正在合并检索结果...")
        merged_results = self._merge_and_deduplicate(search_results_list)
        logger.info(f"合并后共 {len(merged_results)} 个唯一代码片段")

        if not merged_results:
            logger.warning("没有检索到任何代码片段")
            return []

        # 5. 重排序
        logger.info(f"正在使用重排序模型对 {len(merged_results)} 个结果进行重排序...")
        reranked_results = await rerank_service.rerank_with_metadata(
            query=issue_query,
            items=merged_results,
            content_key="content",
            top_n=self.final_top_n,
        )

        logger.info(
            f"重排序完成，保留前 {len(reranked_results)} 个结果"
        )

        return reranked_results

    async def _search_milvus(
        self, query_vector: list[float], filter_expr: str
    ) -> list[dict]:
        """
        执行单个向量检索

        Args:
            query_vector: 查询向量
            filter_expr: 过滤表达式

        Returns:
            检索结果列表
        """
        try:
            results = await milvus_service.search_similar_code(
                query_vector=query_vector,
                top_k=self.top_k,
                filter_expr=filter_expr,
            )
            return results
        except Exception as e:
            logger.error(f"Milvus检索失败: {e}")
            return []

    def _merge_and_deduplicate(
        self, results_list: list[list[dict]]
    ) -> list[dict]:
        """
        合并多个检索结果并去重

        Args:
            results_list: 多个检索结果列表

        Returns:
            去重后的结果列表
        """
        seen_ids = set()
        merged = []

        for results in results_list:
            for result in results:
                result_id = result.get("id")
                if result_id and result_id not in seen_ids:
                    seen_ids.add(result_id)
                    # 提取entity字段的数据并展平
                    entity = result.get("entity", {})
                    flat_result = {
                        "id": result_id,
                        "distance": result.get("distance", 0.0),
                        **entity,
                    }
                    merged.append(flat_result)

        return merged
