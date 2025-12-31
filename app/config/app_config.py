import os
from typing import Literal

from pydantic import BaseModel, Field
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


class SandboxDockerConfig(BaseModel):
    """沙箱 Docker 配置"""

    image: str = Field(default="my-ubuntu:20.04", description="默认 Docker 镜像")
    memory_limit: str = Field(default="512m", description="默认内存限制")
    cpu_limit: float = Field(default=1.0, description="默认 CPU 限制")
    timeout: int = Field(default=300, description="默认超时时间（秒）")
    workspace_path: str = Field(default="/workspace", description="容器内工作目录")
    network_mode: str | None = Field(default=None, description="网络模式")


class SandboxGitAuthConfig(BaseModel):
    """沙箱 Git 认证配置"""

    auth_type: Literal["ssh", "http", "auto"] = Field(
        default="auto", description="认证类型"
    )
    ssh_private_key_path: str | None = Field(
        default=None, description="SSH 私钥文件路径"
    )
    http_token: str | None = Field(
        default=None, description="HTTP Personal Access Token"
    )
    http_username: str | None = Field(default=None, description="HTTP 用户名")


class SandboxConfig(BaseModel):
    """沙箱环境配置"""

    enabled: bool = Field(default=True, description="是否启用沙箱功能")
    base_workspace_path: str = Field(
        default="/tmp/sandbox_workspaces", description="工作目录基础路径"
    )
    auto_cleanup: bool = Field(default=True, description="是否自动清理过期沙箱")
    max_age_seconds: int = Field(default=3600, description="沙箱最大存活时间（秒）")
    docker: SandboxDockerConfig = Field(
        default_factory=SandboxDockerConfig, description="Docker 配置"
    )
    git_auth: SandboxGitAuthConfig | None = Field(
        default=None, description="Git 认证配置"
    )


class AppConfig(BaseModel):
    app_name: str
    bailian: BailianConfig
    milvus: MilvusConfig
    log: LogConfig
    gpt_models: GPTModelsConfig
    sandbox: SandboxConfig = Field(
        default_factory=SandboxConfig, description="沙箱环境配置"
    )

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
