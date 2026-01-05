"""
阿里云百炼 Rerank 重排序服务
"""

import httpx
from app.config.app_config import app_config
from app.core.logger_config import logger


class BailianRerankService:
    """阿里云百炼文本重排序服务"""

    def __init__(self):
        self.config = app_config.bailian
        self.api_key = self.config.api_key
        self.base_url = self.config.base_url
        self.model = self.config.rerank_model
        self.timeout = self.config.timeout
        self.max_retries = self.config.max_retries

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int | None = None,
        return_documents: bool = False,
    ) -> list[dict]:
        """
        对文档进行重排序

        Args:
            query: 查询文本
            documents: 待重排序的文档列表
            top_n: 返回前N个结果，None表示返回所有结果
            return_documents: 是否在结果中返回原始文档内容

        Returns:
            重排序结果列表，每个元素包含:
            - index: 原始文档索引
            - relevance_score: 相关性分数
            - document: 原始文档内容（如果return_documents=True）
        """
        if not documents:
            logger.warning("重排序文档列表为空")
            return []

        if not query:
            logger.warning("重排序查询为空")
            return []

        logger.info(
            f"准备重排序 {len(documents)} 个文档，query长度: {len(query)} 字符"
        )

        url = f"{self.base_url}/services/rerank/text-rerank/text-rerank"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "input": {
                "query": query,
                "documents": documents,
            },
            "parameters": {
                "top_n": top_n,
                "return_documents": True
            }
        }

        retry_count = 0
        last_error = None

        while retry_count < self.max_retries:
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(url, json=payload, headers=headers)
                    response.raise_for_status()

                    result = response.json()

                    # 阿里云百炼返回格式: {"output": {"results": [{"index": 0, "relevance_score": 0.95}]}}
                    if "output" in result and "results" in result["output"]:
                        rerank_results = result["output"]["results"]

                        logger.info(
                            f"重排序完成，返回 {len(rerank_results)} 个结果，"
                            f"分数范围: {rerank_results[0].get('relevance_score', 0):.4f} - "
                            f"{rerank_results[-1].get('relevance_score', 0):.4f}"
                        )
                        return rerank_results
                    else:
                        raise ValueError(f"重排序响应格式错误: {result}")

            except httpx.HTTPError as e:
                last_error = e
                retry_count += 1
                error_detail = ""
                if hasattr(e, "response") and e.response is not None:
                    try:
                        error_detail = f" - 响应内容: {e.response.text}"
                    except:
                        pass
                logger.warning(
                    f"重排序请求失败 (尝试 {retry_count}/{self.max_retries}): {e}{error_detail}"
                )
                if retry_count < self.max_retries:
                    await self._wait_before_retry(retry_count)
            except Exception as e:
                logger.error(f"重排序过程出错: {e}")
                raise

        raise Exception(
            f"重排序失败，已重试 {self.max_retries} 次。最后错误: {last_error}"
        )

    async def rerank_with_metadata(
        self,
        query: str,
        items: list[dict],
        content_key: str = "content",
        top_n: int | None = None,
    ) -> list[dict]:
        """
        对带有元数据的项目进行重排序

        Args:
            query: 查询文本
            items: 带有元数据的项目列表，每个项目必须包含content_key指定的字段
            content_key: 用于重排序的内容字段名
            top_n: 返回前N个结果

        Returns:
            重排序后的项目列表，包含原始元数据和relevance_score字段

        Raises:
            Exception: 重排序失败时抛出异常
        """
        if not items:
            return []

        # 提取文档内容
        documents = []
        for item in items:
            content = item.get(content_key, "")
            if not content:
                logger.warning(f"项目缺少 '{content_key}' 字段: {item}")
                content = str(item)  # 使用整个项目的字符串表示
            documents.append(content)

        # 执行重排序
        rerank_results = await self.rerank(
            query=query, documents=documents, top_n=top_n, return_documents=False
        )

        # 将重排序结果与原始元数据合并
        sorted_items = []
        for result in rerank_results:
            index = result["index"]
            relevance_score = result["relevance_score"]
            # 复制原始项目并添加相关性分数
            item_with_score = items[index].copy()
            item_with_score["relevance_score"] = relevance_score
            sorted_items.append(item_with_score)

        return sorted_items

    async def _wait_before_retry(self, retry_count: int):
        """重试前等待（指数退避）"""
        import asyncio

        wait_time = min(2**retry_count, 10)  # 最多等待10秒
        await asyncio.sleep(wait_time)


# 全局单例
rerank_service = BailianRerankService()

