from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from app.config.app_config import app_config
from app.core.token_counter import TokenCounterCallback


async def get_llm_model(
    model_name: str,
    temperature: float = 0.1,
    enable_token_counter: bool = True,
    token_callback: TokenCounterCallback | None = None,
) -> BaseChatModel:
    """
    获取 GPT 模型实例
    config: GPTModelsConfig
    Args:
        temperature: 温度参数
        enable_token_counter: 是否启用token计数器
        token_callback: 外部传入的token计数回调，如果为None则自动创建

    Returns:
        LLM 实例
    """
    config = app_config.llm_models.get(model_name, {})

    # 创建token计数回调
    callbacks = []
    if enable_token_counter:
        if token_callback is None:
            token_callback = TokenCounterCallback(model_name=model_name)
        callbacks.append(token_callback)

    llm = ChatOpenAI(
        model=model_name,
        temperature=temperature,
        timeout=config.get("timeout", 30),
        api_key=SecretStr(config.get("api_key", "")),
        base_url=config.get("base_url", ""),
        callbacks=callbacks,
    )

    return llm
