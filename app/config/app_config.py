import os
from typing import Dict, Any
from pydantic import BaseModel, Field
import yaml


class ClientConfig(BaseModel):
    """客户端配置基类 - 提供公共的超时和重试配置"""
    timeout: int = Field(default=30, description="超时时间（秒）")
    max_retries: int = Field(default=3, description="最大重试次数")


class BailianConfig(ClientConfig):
    """阿里云百炼配置"""
    api_key: str
    base_url: str
    embedding_model: str
    rerank_model: str


class MilvusConfig(ClientConfig):
    """Milvus向量数据库配置"""
    uri: str
    username: str
    password: str
    database: str
    collection_name: str
    vector_dimension: int


class GitLabConfig(ClientConfig):
    """GitLab配置"""
    webhook_secret: str
    verify_ssl: bool = True


class LogConfig(BaseModel):
    """日志配置"""
    path: str


class LLMModelConfig(ClientConfig):
    """LLM 模型配置"""
    api_key: str = Field(description="API密钥")
    base_url: str = Field(description="API基础URL")


class WorkflowConfig(BaseModel):
    """工作流配置"""
    max_refine_retry_count: int = Field(
        default=5, description="验证失败时的最大修复重试次数"
    )


class OpikConfig(BaseModel):
    """Opik tracing 配置"""
    api_key: str = Field(description="Opik API Key")
    workspace: str = Field(description="Opik workspace name")
    project_name: str = Field(default="aic", description="Opik project name")
    enabled: bool = Field(default=True, description="是否启用 Opik tracing")


class AppConfig(BaseModel):
    """应用配置 - 统一的配置入口"""
    
    # 应用级配置
    app_name: str
    host: str = Field(default="0.0.0.0", description="服务监听地址")
    port: int = Field(default=9000, description="服务端口")
    debug: bool = Field(default=False, description="调试模式")
    api_prefix: str = Field(default="/api/v1", description="API路径前缀")
    
    # 外部服务配置
    bailian: BailianConfig
    milvus: MilvusConfig
    gitlab: GitLabConfig
    log: LogConfig
    
    # LLM模型配置（强类型）
    llm_models: Dict[str, LLMModelConfig] = Field(default_factory=dict)
    
    # Sandbox 配置（运行时会转换为 app.sandbox.models.SandboxConfig）
    # 使用 Any 避免循环导入，实际类型在 load_config 中处理
    sandbox: Any = Field(default=None, description="沙箱环境配置")
    
    # 工作流配置
    workflow: WorkflowConfig = Field(default_factory=WorkflowConfig)
    
    # Opik tracing 配置（可选）
    opik: OpikConfig | None = Field(default=None, description="Opik tracing 配置")

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
        
        from app.sandbox.models import SandboxConfig, GitAuthConfig
        
        # 转换 sandbox 配置为运行时模型
        if "sandbox" in config_dict and config_dict["sandbox"]:
            sandbox_dict = config_dict["sandbox"]
            # 如果有 git_auth，也转换为 GitAuthConfig
            if "git_auth" in sandbox_dict and sandbox_dict["git_auth"]:
                sandbox_dict["git_auth"] = GitAuthConfig(**sandbox_dict["git_auth"])
            config_dict["sandbox"] = SandboxConfig(**sandbox_dict)
        
        return AppConfig(**config_dict)


# 全局配置实例（单例模式）
_app_config: AppConfig | None = None


def get_app_config() -> AppConfig:
    """获取全局配置实例（单例模式）"""
    global _app_config
    if _app_config is None:
        _app_config = AppConfig.load_config()
    return _app_config


# 向后兼容的别名
app_config = get_app_config()
