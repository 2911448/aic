"""
公共函数
"""

import json

from app.core.logger_config import logger


def parse_json_response(text: str) -> dict:
    """
    解析JSON，优先尝试直接解析，失败后再提取markdown代码块

    Args:
        text: LLM响应文本，可能包含JSON wrapped in markdown

    Returns:
        解析后的JSON字典
    """
    if not text or not text.strip():
        logger.error("收到空的LLM响应")
        raise ValueError("LLM响应为空，无法解析JSON")
    
    text = text.strip()
    
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    if text.startswith("```json"):
        start = text.find("\n", 7) 
        if start != -1 and text.rstrip().endswith("```"):
            end = text.rstrip().rfind("```")
            if end > start:
                text = text[start+1:end].strip()
    elif text.startswith("```"):
        start = text.find("\n", 3) 
        if start != -1 and text.rstrip().endswith("```"):
            end = text.rstrip().rfind("```")
            if end > start:
                text = text[start+1:end].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"JSON解析失败: {e}\n响应文本: {text[:500]}")
        raise ValueError(f"无法解析LLM响应为JSON: {e}")
