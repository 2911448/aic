"""
Execution Summary Tool - 执行结果汇总工具

为每轮 TaskRunner 派发的 agent 结果生成简洁的中文汇总。
- agent_summaries: 每条≤150字（用于 execution_history）
- round_summary: 多行格式，每行≤300字（用于 last_round_summary）
"""

from typing import Any

from app.core.logger_config import logger
from app.core.prompt_manager import prompt_manager
from app.llms.llm_factory import get_llm_model
from app.utils.common_function import parse_json_response


async def summarize_execution_core(
    items: list[dict[str, Any]],
) -> dict:
    """
    汇总本轮执行结果（核心函数）
    
    Args:
        items: 本轮每个 agent 的执行条目列表，每项包含：
            - agent: str (agent 名称，如 omni_explorer)
            - task: str (任务描述)
            - task_id: str (任务 ID)
            - reasoning: str (agent 推理过程)
            - result_hint: dict (结果要点，如 anchor_count, patches_count 等)
    
    Returns:
        {
            "agent_summaries": list[str],  # 与输入 items 同序，每条 ≤150 字
            "round_summary": str,  # 多行格式，每行≤300字，点名各 agent 对应任务与结果
        }
    """
    if not items:
        logger.warning("[Summary] 输入为空，返回默认汇总")
        return {
            "agent_summaries": [],
            "round_summary": "本轮未执行任何 agent 任务。",
        }
    
    try:
        # 准备 prompt 参数
        items_with_hint_text = []
        for item in items:
            result_hint = item.get("result_hint", {})
            items_with_hint_text.append({
                "agent": item.get("agent", "unknown"),
                "task": item.get("task", "无描述"),
                "reasoning": item.get("reasoning", "无推理"),
                "result_hint_text": _format_result_hint(result_hint),
            })
        
        # 构建 prompt（使用 prompt_manager）
        prompt = prompt_manager.render(
            "execution_summary",
            items=items_with_hint_text,
        )
        
        # 调用 LLM
        llm = await get_llm_model(model_name="gpt-5-2025-08-07", temperature=0)
        response = await llm.ainvoke(prompt)
        
        # 解析 JSON
        result = parse_json_response(response.content)
        
        # 校验结果
        if "agent_summaries" not in result or "round_summary" not in result:
            raise ValueError("LLM 返回缺少必要字段")
        
        if len(result["agent_summaries"]) != len(items):
            logger.warning(
                f"agent_summaries 数量不匹配: 期望 {len(items)}, 实际 {len(result['agent_summaries'])}"
            )
            # 降级：补齐或截断
            while len(result["agent_summaries"]) < len(items):
                result["agent_summaries"].append("（汇总失败）")
        
        logger.info(
            f"[Summary] 汇总完成: {len(items)} 个 agent, "
            f"round_summary 长度: {len(result['round_summary'])}"
        )
        
        return result
    
    except Exception as e:
        logger.error(f"[Summary] 汇总失败: {e}", exc_info=True)
        
        # 降级：生成简单的摘要
        agent_summaries = []
        for item in items:
            agent = item.get("agent", "unknown")
            task = item.get("task", "")
            agent_summaries.append(f"{agent} 执行了任务: {task}")
        
        round_summary = f"本轮执行了 {len(items)} 个 agent 任务。"
        
        return {
            "agent_summaries": agent_summaries,
            "round_summary": round_summary,
        }


def _format_result_hint(result_hint: dict[str, Any]) -> str:
    """
    格式化结果要点为可读文本
    
    Args:
        result_hint: 结果要点字典
    
    Returns:
        格式化后的文本
    """
    if not result_hint:
        return "无"
    
    parts = []
    for key, value in result_hint.items():
        if isinstance(value, (int, float)):
            parts.append(f"{key}={value}")
        elif isinstance(value, str):
            parts.append(f"{key}={value}")
        elif isinstance(value, bool):
            parts.append(f"{key}={'是' if value else '否'}")
        elif isinstance(value, list):
            # 特殊处理列表类型（如 error_details）
            if key == "error_details" and value:
                # 错误详情列表，展开显示
                parts.append(f"{key}=[\n" + "\n".join(value) + "\n]")
            else:
                parts.append(f"{key}={len(value)}项")
        else:
            parts.append(f"{key}={str(value)}")
    
    return ", ".join(parts)


# 导出
__all__ = ["summarize_execution_core"]
