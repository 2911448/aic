
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from app.config.app_config import app_config


async def get_gpt_model(temperature: float = 0.1) -> BaseChatModel:
    """
    获取 GPT 模型实例
    config: GPTModelsConfig
    Args:
        temperature: 温度参数

    Returns:
        LLM 实例
    """
    config = app_config.gpt_models.model_dump()

    return ChatOpenAI(
        model=config["model"],
        temperature=temperature,
        timeout=config["timeout"],
        api_key=SecretStr(config["api_key"]),
        base_url=config["base_url"],
    )
