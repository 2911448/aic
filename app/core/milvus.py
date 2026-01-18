"""
Milvus 向量数据库管理服务
"""

import asyncio
from typing import Any
from pymilvus import (
    MilvusClient,
    DataType,
    FieldSchema,
    CollectionSchema,
)
from pymilvus.exceptions import MilvusException
from app.core.logger_config import logger

from app.config.app_config import app_config
from app.schemas.code import CodeSnippet
from app.decorators.retry import async_retry


class MilvusService:
    """Milvus 向量数据库服务"""

    def __init__(self):
        self.config = app_config.milvus
        self.client: MilvusClient | None = None
        self.collection_name = self.config.collection_name
        self.vector_dim = self.config.vector_dimension

    def __enter__(self):
        """上下文管理器：进入时连接"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器：退出时关闭连接"""
        self.close()
        return False

    def connect(self):
        """连接到 Milvus 数据库"""
        try:
            self.client = MilvusClient(
                uri=self.config.uri,
                user=self.config.username,
                password=self.config.password,
                db_name=self.config.database,
                timeout=self.config.timeout,
            )
            logger.info(f"成功连接到 Milvus 数据库: {self.config.uri}")
        except Exception as e:
            logger.error(f"连接 Milvus 失败: {e}")
            raise

    def create_collection(self, drop_existing: bool = False):
        """
        创建代码片段集合

        Args:
            drop_existing: 是否删除已存在的集合
        """
        if not self.client:
            self.connect()

        try:
            # 检查集合是否存在
            if self.client.has_collection(self.collection_name):
                if drop_existing:
                    logger.info(f"删除已存在的集合: {self.collection_name}")
                    self.client.drop_collection(self.collection_name)
                else:
                    logger.info(f"集合已存在: {self.collection_name}")
                    return

            # 定义字段 Schema
            fields = [
                FieldSchema(
                    name="id",
                    dtype=DataType.INT64,
                    is_primary=True,
                    auto_id=True,
                    description="主键ID",
                ),
                FieldSchema(
                    name="embedding",
                    dtype=DataType.FLOAT_VECTOR,
                    dim=self.vector_dim,
                    description="代码向量（1024维）",
                ),
                FieldSchema(
                    name="project_name",
                    dtype=DataType.VARCHAR,
                    max_length=50,
                    description="项目名称",
                ),
                FieldSchema(
                    name="file_path",
                    dtype=DataType.VARCHAR,
                    max_length=1024,
                    description="代码文件路径",
                ),
                FieldSchema(
                    name="symbol_name",
                    dtype=DataType.VARCHAR,
                    max_length=256,
                    description="类名/函数名",
                ),
                FieldSchema(
                    name="language",
                    dtype=DataType.VARCHAR,
                    max_length=32,
                    description="编程语言",
                ),
                FieldSchema(
                    name="start_line",
                    dtype=DataType.INT64,
                    description="代码起始行",
                ),
                FieldSchema(
                    name="end_line",
                    dtype=DataType.INT64,
                    description="代码结束行",
                ),
                FieldSchema(
                    name="content",
                    dtype=DataType.VARCHAR,
                    max_length=65535,
                    description="代码原文",
                ),
                FieldSchema(
                    name="summary",
                    dtype=DataType.VARCHAR,
                    max_length=1024,
                    description="函数摘要/Docstring",
                ),
                FieldSchema(
                    name="last_updated",
                    dtype=DataType.INT64,
                    description="时间戳",
                ),
                FieldSchema(
                    name="use_count",
                    dtype=DataType.INT64,
                    description="成功修复被采纳次数",
                ),
                FieldSchema(
                    name="summary_embedding",
                    dtype=DataType.FLOAT_VECTOR,
                    dim=self.vector_dim,
                    description="摘要向量（1024维）",
                ),
            ]

            # 创建集合 Schema
            schema = CollectionSchema(
                fields=fields,
                description="代码片段向量集合",
                enable_dynamic_field=False,
            )

            # 创建索引参数（COSINE 相似度）
            index_params = self.client.prepare_index_params()
            index_params.add_index(
                field_name="embedding",
                index_type="AUTOINDEX",
                metric_type="COSINE",
            )
            index_params.add_index(
                field_name="summary_embedding",
                index_type="AUTOINDEX",
                metric_type="COSINE",
            )

            # 创建集合
            self.client.create_collection(
                collection_name=self.collection_name,
                schema=schema,
                index_params=index_params,
            )

            logger.info(f"成功创建集合: {self.collection_name}")

        except Exception as e:
            logger.error(f"创建集合失败: {e}")
            raise

    def delete_by_file_path(self, file_path: str, project_name: str) -> int:
        """
        根据文件路径删除代码片段（用于重新索引前清理旧数据）

        Args:
            file_path: 文件相对路径
            project_name: 项目名称

        Returns:
            删除的记录数
        """
        if not self.client:
            self.connect()

        try:
            # 构建过滤条件
            filter_expr = (
                f'file_path == "{file_path}" && project_name == "{project_name}"'
            )

            # 查询符合条件的记录
            results = self.client.query(
                collection_name=self.collection_name,
                filter=filter_expr,
                output_fields=["id"],
            )

            if not results:
                logger.info(f"没有找到需要删除的记录: {file_path}")
                return 0

            # 提取 ID 列表
            ids = [str(r["id"]) for r in results]

            # 删除记录
            self.client.delete(
                collection_name=self.collection_name,
                ids=ids,
            )

            logger.info(f"删除了 {len(ids)} 条旧记录: {file_path}")
            return len(ids)

        except Exception as e:
            logger.error(f"删除代码片段失败: {e}")
            raise

    def upsert_snippets(self, snippets: list[CodeSnippet]) -> list[int]:
        """
        批量插入或更新代码片段（先删除旧数据，再插入新数据）

        Args:
            snippets: 代码片段列表

        Returns:
            插入的记录ID列表
        """
        if not self.client:
            self.connect()

        try:
            if not snippets:
                return []

            # 1. 删除旧数据（按文件路径分组删除）
            file_groups = {}
            for snippet in snippets:
                key = (snippet.file_path, snippet.project_name)
                if key not in file_groups:
                    file_groups[key] = []
                file_groups[key].append(snippet)

            total_deleted = 0
            for (file_path, project_name), _ in file_groups.items():
                deleted = self.delete_by_file_path(file_path, project_name)
                total_deleted += deleted

            if total_deleted > 0:
                logger.info(f"清理了 {total_deleted} 条旧记录，准备插入新数据")

            # 2. 插入新数据
            return self.insert_snippets(snippets)

        except Exception as e:
            logger.error(f"Upsert 操作失败: {e}")
            raise

    def insert_snippets(self, snippets: list[CodeSnippet]) -> list[int]:
        """
        批量插入代码片段（不检查重复，直接插入）

        Args:
            snippets: 代码片段列表

        Returns:
            插入的记录ID列表
        """
        if not self.client:
            self.connect()

        try:
            # 准备数据
            data = []
            for snippet in snippets:
                if not snippet.embedding:
                    raise ValueError(
                        f"代码片段缺少向量: {snippet.file_path}:{snippet.symbol_name}"
                    )
                if not snippet.summary_embedding:
                    raise ValueError(
                        f"代码片段缺少摘要向量: {snippet.file_path}:{snippet.symbol_name}"
                    )

                data.append(
                    {
                        "embedding": snippet.embedding,
                        "summary_embedding": snippet.summary_embedding,
                        "project_name": snippet.project_name,
                        "file_path": snippet.file_path,
                        "symbol_name": snippet.symbol_name,
                        "language": snippet.language,
                        "start_line": snippet.start_line,
                        "end_line": snippet.end_line,
                        "content": snippet.content,
                        "summary": snippet.summary or "",
                        "last_updated": snippet.last_updated,
                        "use_count": snippet.use_count,
                    }
                )

            # 批量插入
            result = self.client.insert(
                collection_name=self.collection_name,
                data=data,
            )

            logger.info(f"成功插入 {len(snippets)} 条代码片段")
            return result.get("ids", [])

        except Exception as e:
            logger.error(f"插入代码片段失败: {e}")
            raise

    @async_retry(
        max_retries=app_config.milvus.max_retries,
        retriable_exceptions=(MilvusException, TimeoutError, ConnectionError)
    )
    async def search_similar_code(
        self,
        query_vector: list[float],
        top_k: int = 10,
        filter_expr: str | None = None,
        output_fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        向量相似度搜索（自动重试连接错误）

        Args:
            query_vector: 查询向量
            top_k: 返回前K个最相似的结果
            filter_expr: 过滤表达式，例如: 'language == "python"'
            output_fields: 需要返回的字段列表

        Returns:
            搜索结果列表
        """
        if not self.client:
            self.connect()

        try:
            if output_fields is None:
                output_fields = [
                    "project_name",
                    "file_path",
                    "symbol_name",
                    "language",
                    "start_line",
                    "end_line",
                    "content",
                    "summary",
                    "use_count",
                ]

            # 在线程池中执行同步的 Milvus 搜索操作
            results = await asyncio.to_thread(
                self.client.search,
                collection_name=self.collection_name,
                data=[query_vector],
                limit=top_k,
                filter=filter_expr,
                output_fields=output_fields,
                anns_field="embedding",  # 指定使用代码向量字段
            )

            # 格式化结果
            formatted_results = []
            for hits in results:
                for hit in hits:
                    formatted_results.append(
                        {
                            "id": hit.get("id"),
                            "distance": hit.get("distance"),
                            "entity": hit.get("entity", {}),
                        }
                    )

            logger.info(f"搜索完成，返回 {len(formatted_results)} 条结果")
            return formatted_results

        except Exception as e:
            logger.error(f"向量搜索失败: {e}")
            raise

    @async_retry(
        max_retries=app_config.milvus.max_retries,
        retriable_exceptions=(MilvusException, TimeoutError, ConnectionError)
    )
    async def search_by_summary(
        self,
        query_vector: list[float],
        top_k: int = 10,
        filter_expr: str | None = None,
        output_fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        使用摘要向量进行相似度搜索（父子索引策略）（自动重试连接错误）

        Args:
            query_vector: 查询向量
            top_k: 返回前K个最相似的结果
            filter_expr: 过滤表达式，例如: 'language == "python"'
            output_fields: 需要返回的字段列表

        Returns:
            搜索结果列表，包含完整的content
        """
        if not self.client:
            self.connect()

        try:
            if output_fields is None:
                output_fields = [
                    "project_name",
                    "file_path",
                    "symbol_name",
                    "language",
                    "start_line",
                    "end_line",
                    "content",
                    "summary",
                    "use_count",
                ]

            # 在线程池中执行同步的 Milvus 搜索操作
            results = await asyncio.to_thread(
                self.client.search,
                collection_name=self.collection_name,
                data=[query_vector],
                anns_field="summary_embedding",  # 指定使用摘要向量字段
                limit=top_k,
                filter=filter_expr,
                output_fields=output_fields,
            )

            # 格式化结果
            formatted_results = []
            for hits in results:
                for hit in hits:
                    formatted_results.append(
                        {
                            "id": hit.get("id"),
                            "distance": hit.get("distance"),
                            "entity": hit.get("entity", {}),
                        }
                    )

            logger.info(f"摘要搜索完成，返回 {len(formatted_results)} 条结果")
            return formatted_results

        except Exception as e:
            logger.error(f"摘要向量搜索失败: {e}")
            raise

    def update_use_count(self, snippet_id: int, increment: int = 1):
        """
        更新代码片段的使用次数

        Args:
            snippet_id: 代码片段ID
            increment: 增量值
        """
        if not self.client:
            self.connect()

        try:
            # Milvus 不支持直接更新，需要先查询再删除再插入
            # 这里简化处理，实际应用中可能需要更复杂的逻辑
            logger.info(f"更新代码片段 {snippet_id} 的使用次数 +{increment}")
            # TODO: 实现完整的更新逻辑
        except Exception as e:
            logger.error(f"更新使用次数失败: {e}")
            raise

    def close(self):
        """关闭连接"""
        if self.client:
            self.client.close()
            logger.info("已关闭 Milvus 连接")


# 全局单例
milvus_service = MilvusService()
