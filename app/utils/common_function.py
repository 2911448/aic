"""
公共函数
"""

import json

from app.core.logger_config import logger


def parse_json_response(text: str) -> dict:
    """
    解析JSON

    Args:
        text: LLM响应文本，可能包含JSON wrapped in markdown

    Returns:
        解析后的JSON字典
    """
    if "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        if end != -1:
            text = text[start:end].strip()
    elif "```" in text:
        start = text.find("```") + 3
        end = text.find("```", start)
        if end != -1:
            text = text[start:end].strip()

    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"JSON解析失败: {e}\n响应文本: {text[:500]}")
        raise ValueError(f"无法解析LLM响应为JSON: {e}")

