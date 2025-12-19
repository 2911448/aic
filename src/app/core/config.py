from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 应用配置
    app_name: str = "aic"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000

    # API 配置
    api_prefix: str = "/api/v1"

    # LLM 配置 (可选)
    openai_api_key: str | None = None


settings = Settings()

