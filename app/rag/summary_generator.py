"""
LLM Summary 生成服务 - 为代码片段、配置文件等生成智能摘要
"""

from typing import Literal

from langchain_core.prompts import ChatPromptTemplate

from app.core.logger_config import logger
from app.llms.llm_factory import get_llm_model


class SummaryGenerator:
    """LLM Summary 生成器"""

    # 代码文件 Summary Prompt
    CODE_SUMMARY_PROMPT = """分析以下代码片段，生成简洁的功能摘要（100字以内）。

代码语言：{language}
符号名称：{symbol_name}
代码内容：
{content}

要求：
1. 说明函数/类的核心功能
2. 列出关键参数和返回值（如有）
3. 简明扼要，便于检索
4. 不要包含代码示例，只需要文字描述

请直接输出摘要，不要包含其他内容："""

    # 配置文件 Summary Prompt
    CONFIG_SUMMARY_PROMPT = """分析以下配置文件，生成摘要（100字以内）。

文件类型：{file_type}
文件路径：{file_path}
内容：
{content}

要求：
1. 说明配置文件的用途
2. 列出关键配置项（3-5个）
3. 说明对项目的作用
4. 简明扼要，便于检索

请直接输出摘要，不要包含其他内容："""

    # 文档文件 Summary Prompt
    DOC_SUMMARY_PROMPT = """分析以下文档内容，生成摘要（100字以内）。

文档类型：{file_type}
文件路径：{file_path}
内容：
{content}

要求：
1. 提取文档的核心内容
2. 说明文档的用途和重要信息
3. 简明扼要，便于检索

请直接输出摘要，不要包含其他内容："""

    # 依赖文件 Summary Prompt
    DEPENDENCY_SUMMARY_PROMPT = """分析以下依赖配置文件，生成摘要（100字以内）。

文件类型：{file_type}
文件路径：{file_path}
内容（可能被截断）：
{content}

要求：
1. 列出关键依赖包（5-10个）
2. 说明项目使用的技术栈
3. 简明扼要，便于检索

请直接输出摘要，不要包含其他内容："""

    def __init__(self):
        """初始化 Summary 生成器"""
        pass

    async def generate_summary(
        self,
        content: str,
        file_type: Literal[
            "code", "config", "doc", "dependency"
        ] = "code",
        language: str = "unknown",
        symbol_name: str = "",
        file_path: str = "",
    ) -> str:
        """
        生成代码片段或文件的摘要

        Args:
            content: 文件内容
            file_type: 文件类型（code/config/doc/dependency）
            language: 编程语言或文件类型
            symbol_name: 符号名称（用于代码文件）
            file_path: 文件路径

        Returns:
            生成的摘要文本
        """
        try:
            # 限制内容长度（避免过长的输入）
            max_length = 10000  # 约 10K 字符
            truncated_content = content[:max_length]
            if len(content) > max_length:
                truncated_content += "\n... (内容过长，已截断)"

            # 根据文件类型选择 prompt
            if file_type == "code":
                prompt_template = self.CODE_SUMMARY_PROMPT
                prompt_vars = {
                    "language": language,
                    "symbol_name": symbol_name,
                    "content": truncated_content,
                }
            elif file_type == "config":
                prompt_template = self.CONFIG_SUMMARY_PROMPT
                prompt_vars = {
                    "file_type": language,
                    "file_path": file_path,
                    "content": truncated_content,
                }
            elif file_type == "doc":
                prompt_template = self.DOC_SUMMARY_PROMPT
                prompt_vars = {
                    "file_type": language,
                    "file_path": file_path,
                    "content": truncated_content,
                }
            elif file_type == "dependency":
                prompt_template = self.DEPENDENCY_SUMMARY_PROMPT
                prompt_vars = {
                    "file_type": language,
                    "file_path": file_path,
                    "content": truncated_content,
                }
            else:
                logger.warning(f"未知的文件类型: {file_type}，使用默认摘要")
                return f"{symbol_name or file_path} ({language})"

            # 创建 prompt
            prompt = ChatPromptTemplate.from_template(prompt_template)

            # 调用 LLM 生成摘要
            llm = await get_llm_model(model_name="gpt-4.1-2025-04-14")
            chain = prompt | llm

            result = await chain.ainvoke(prompt_vars)
            summary = result.content.strip()

            # 限制摘要长度（最多 500 字符）
            if len(summary) > 500:
                summary = summary[:497] + "..."

            logger.debug(f"生成摘要成功: {file_path or symbol_name}")
            return summary

        except Exception as e:
            logger.error(f"生成摘要失败: {e}", exc_info=True)
            # 降级处理：返回简单摘要
            if symbol_name:
                return f"{symbol_name} ({language})"
            elif file_path:
                return f"{file_path} ({language} file)"
            else:
                return f"{language} content"

    async def generate_batch_summaries(
        self,
        items: list[dict],
    ) -> list[str]:
        """
        批量生成摘要（提高效率）

        Args:
            items: 待生成摘要的项目列表，每项包含：
                - content: 文件内容
                - file_type: 文件类型
                - language: 编程语言
                - symbol_name: 符号名称（可选）
                - file_path: 文件路径（可选）

        Returns:
            摘要列表，与输入列表顺序对应
        """
        import asyncio

        # 限制并发数量（避免过多请求）
        semaphore = asyncio.Semaphore(5)

        async def _generate_with_limit(item: dict) -> str:
            async with semaphore:
                return await self.generate_summary(
                    content=item.get("content", ""),
                    file_type=item.get("file_type", "code"),
                    language=item.get("language", "unknown"),
                    symbol_name=item.get("symbol_name", ""),
                    file_path=item.get("file_path", ""),
                )

        # 并发生成所有摘要
        summaries = await asyncio.gather(
            *[_generate_with_limit(item) for item in items],
            return_exceptions=True,
        )

        # 处理异常情况
        results = []
        for i, summary in enumerate(summaries):
            if isinstance(summary, Exception):
                logger.error(f"批量生成摘要失败（第 {i} 项）: {summary}")
                # 降级处理
                item = items[i]
                results.append(
                    f"{item.get('symbol_name') or item.get('file_path', 'unknown')} ({item.get('language', 'unknown')})"
                )
            else:
                results.append(summary)

        return results


# 全局单例
summary_generator = SummaryGenerator()
