import os
from pydantic import BaseModel
import yaml


class BailianConfig(BaseModel):
    api_key: str
    base_url: str
    embedding_model: str
    rerank_model: str
    timeout: int = 30
    max_retries: int = 3


class MilvusConfig(BaseModel):
    uri: str
    username: str
    password: str
    database: str
    collection_name: str
    vector_dimension: int
    timeout: int = 30
    max_retries: int = 3


class LogConfig(BaseModel):
    path: str


class GPTModelsConfig(BaseModel):
    model: str
    base_url: str
    api_key: str
    timeout: int = 30
    max_retries: int = 3


class AppConfig(BaseModel):
    app_name: str
    bailian: BailianConfig
    milvus: MilvusConfig
    log: LogConfig
    gpt_models: GPTModelsConfig

    @classmethod
    def load_config(cls):
        """
        根据环境变量ENV加载不同的配置文件
        支持的环境: test(开发/测试环境), prod(生产环境), local(本地环境)
        """
        env = os.getenv("ENV", "local")  # 默认使用local环境
        config_file = os.path.join(os.path.dirname(__file__), f"config_{env}.yaml")

        if not os.path.exists(config_file):
            raise FileNotFoundError(
                f"配置文件 {config_file} 不存在。请确保配置文件存在，或设置环境变量 ENV=local"
            )

        with open(config_file, encoding="utf-8") as f:
            content = f.read()

        config_dict = yaml.safe_load(content)
        return AppConfig(**config_dict)


app_config: AppConfig = AppConfig.load_config()
