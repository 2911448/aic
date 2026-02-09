"""
Semantic Search Tool - 语义检索工具

封装 Milvus + Embedding + Rerank 流程，用于 OmniExplorer 的 Semantic Search 阶段。
"""


from langchain_core.tools import tool

from app.core.logger_config import logger
from app.core.milvus import milvus_service
from app.rag.embedding import embedding_service
from app.rag.rerank import rerank_service


async def semantic_search_core(
    query: str,
    project_name: str,
    top_k: int = 20,
    final_top_n: int = 5,
) -> list[dict]:
    """
    语义检索核心函数
    
    Args:
        query: 搜索查询
        project_name: 项目名称
        top_k: 从 Milvus 召回的结果数
        final_top_n: 重排序后最终保留的结果数
    
    Returns:
        重排序后的代码片段列表
    """
    try:
        logger.info(f"[semantic_search] query: {query}, project: {project_name}")
        
        # 1. 向量化查询
        query_vector = await embedding_service.embed_text(query)
        
        # 2. Milvus 检索
        filter_expr = f'project_name == "{project_name}"'
        milvus_results = await milvus_service.search_similar_code(
            query_vector=query_vector,
            top_k=top_k,
            filter_expr=filter_expr,
        )
        
        if not milvus_results:
            logger.warning("[semantic_search] 未检索到任何结果")
            return []
        
        logger.info(f"[semantic_search] Milvus 召回 {len(milvus_results)} 个结果")
        
        # 2.5. 展平 Milvus 返回的数据结构（将 entity 字段合并到顶层）
        flattened_results = []
        for item in milvus_results:
            flattened_item = {
                "id": item.get("id"),
                "distance": item.get("distance"),
                **item.get("entity", {}),  # 将 entity 字段展平到顶层
            }
            flattened_results.append(flattened_item)
        
        # 3. 重排序
        reranked_results = await rerank_service.rerank_with_metadata(
            query=query,
            items=flattened_results,
            content_key="content",
            top_n=final_top_n,
        )
        
        logger.info(f"[semantic_search] 重排序后保留 {len(reranked_results)} 个结果")
        
        # 4. 过滤返回字段（只保留必要信息，不返回完整代码内容）
        filtered_results = []
        for item in reranked_results:
            filtered_item = {
                "rerank_score": item.get("rerank_score"),
                "symbol_name": item.get("symbol_name"),
                "summary": item.get("summary"),
                "file_path": item.get("file_path"),
                "start_line": item.get("start_line"),
                "end_line": item.get("end_line"),
                "symbol_type": item.get("language"),  # 语言类型
            }
            filtered_results.append(filtered_item)
        
        return filtered_results
    
    except Exception as e:
        logger.error(f"[semantic_search] 执行失败: {e}", exc_info=True)
        raise


@tool
async def semantic_search(
    query: str,
    project_name: str,
    top_k: int = 20,
    final_top_n: int = 5,
) -> list[dict]:
    """
    语义检索：使用向量数据库查找相关代码片段（定方向）
    
    Args:
        query: 搜索查询（自然语言描述或关键词）
        project_name: 项目名称
        top_k: 从 Milvus 召回的结果数
        final_top_n: 重排序后最终保留的结果数
    
    Returns:
        代码片段列表，每个包含：
        - rerank_score: 重排序分数（相关性得分）
        - symbol_name: 符号名称（函数/类名）
        - summary: 代码摘要（简要说明）
        - file_path: 文件路径
        - start_line: 起始行号
        - end_line: 结束行号
        - symbol_type: 符号类型（语言类型）
    """
    return await semantic_search_core(query, project_name, top_k, final_top_n)


# 导出
__all__ = ["semantic_search", "semantic_search_core"]
