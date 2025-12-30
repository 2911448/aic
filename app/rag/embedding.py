"""
阿里云百炼 Embedding 向量化服务
"""

import httpx
from app.config.app_config import app_config
from app.core.logger_config import logger


class BailianEmbeddingService:
    """阿里云百炼文本向量化服务"""

    def __init__(self):
        self.config = app_config.bailian
        self.api_key = self.config.api_key
        self.base_url = self.config.base_url
        self.model = self.config.embedding_model
        self.timeout = self.config.timeout
        self.max_retries = self.config.max_retries

    async def embed_text(self, text: str) -> list[float]:
        """
        单个文本向量化

        Args:
            text: 待向量化的文本

        Returns:
            1024维向量列表

        Raises:
            Exception: 向量化失败时抛出异常
        """
        try:
            vectors = await self.embed_texts([text])
            return vectors[0]
        except Exception as e:
            logger.error(f"文本向量化失败: {e}")
            raise

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        批量文本向量化

        Args:
            texts: 待向量化的文本列表

        Returns:
            向量列表，每个向量为1024维

        Raises:
            Exception: 向量化失败时抛出异常
        """
        if not texts:
            return []

        # 阿里云 DashScope API 批量限制：每次最多10个文本
        # 如果超过限制，分批处理
        BATCH_SIZE = 10
        if len(texts) > BATCH_SIZE:
            logger.info(f"文本数量 {len(texts)} 超过批量限制 {BATCH_SIZE}，将分批处理")
            all_embeddings = []
            for i in range(0, len(texts), BATCH_SIZE):
                batch = texts[i : i + BATCH_SIZE]
                batch_embeddings = await self.embed_texts(batch)
                all_embeddings.extend(batch_embeddings)
            return all_embeddings

        # 记录请求详情
        text_lengths = [len(t) for t in texts]
        logger.info(
            f"准备向量化 {len(texts)} 个文本，长度范围: {min(text_lengths)}-{max(text_lengths)} 字符"
        )

        url = f"{self.base_url}/services/embeddings/text-embedding/text-embedding"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {"model": self.model, "input": {"texts": texts}}

        retry_count = 0
        last_error = None

        while retry_count < self.max_retries:
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(url, json=payload, headers=headers)
                    response.raise_for_status()

                    result = response.json()

                    # 阿里云百炼返回格式: {"output": {"embeddings": [{"embedding": [...], "text_index": 0}]}}
                    if "output" in result and "embeddings" in result["output"]:
                        embeddings = result["output"]["embeddings"]
                        # 按text_index排序
                        embeddings.sort(key=lambda x: x.get("text_index", 0))
                        vectors = [item["embedding"] for item in embeddings]

                        logger.info(
                            f"成功向量化 {len(texts)} 个文本，向量维度: {len(vectors[0]) if vectors else 0}"
                        )
                        return vectors
                    else:
                        raise ValueError(f"向量化响应格式错误: {result}")

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
                    f"向量化请求失败 (尝试 {retry_count}/{self.max_retries}): {e}{error_detail}"
                )
                if retry_count < self.max_retries:
                    await self._wait_before_retry(retry_count)
            except Exception as e:
                logger.error(f"向量化过程出错: {e}")
                raise

        raise Exception(
            f"向量化失败，已重试 {self.max_retries} 次。最后错误: {last_error}"
        )

    async def _wait_before_retry(self, retry_count: int):
        """重试前等待（指数退避）"""
        import asyncio

        wait_time = min(2**retry_count, 10)  # 最多等待10秒
        await asyncio.sleep(wait_time)


# 全局单例
embedding_service = BailianEmbeddingService()
