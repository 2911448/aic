"""
Sandbox Bootstrap Node - Sandbox 生命周期前置节点
在 Issue 请求进入时创建 sandbox 并 clone 仓库
"""

from typing import Literal

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command

from app.config.app_config import app_config
from app.core.logger_config import logger
from app.graph.state import IssueProcessState
from app.graph.state.node_names import NodeName, ProcessStage
from app.sandbox.file_service import FileService
from app.sandbox.git_service import GitService
from app.sandbox.manager import get_sandbox_manager
from app.sandbox.models import GitAuthConfig, GitAuthType, SandboxConfig
from app.utils.gitignore_parser import (
    get_default_ignore_patterns,
    parse_gitignore_content,
)


class SandboxBootstrapNode:
    """Sandbox Bootstrap 节点 - 前置创建并 clone 仓库"""

    def __init__(self):
        """初始化节点"""
        self.sandbox_manager = get_sandbox_manager()

    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal["main_router", "sandbox_teardown", "__end__"]]:
        """
        创建 sandbox 并 clone 仓库
        
        Args:
            state: 当前工作流状态
            
        Returns:
            Command 对象，成功则 goto main_router，失败则 goto sandbox_teardown -> END
        """
        update_dict = {}
        
        try:
            # 发送进度事件
            await adispatch_custom_event(
                ProcessStage.SANDBOX_BOOTSTRAP.value,
                {
                    "status": NodeName.SANDBOX_BOOTSTRAP.value,
                    "progress": "正在创建 Sandbox 并克隆代码仓库...",
                    "think_chain_item": {
                        "type": NodeName.SANDBOX_BOOTSTRAP.value,
                        "title": "Sandbox 初始化",
                        "desc": "创建隔离环境并拉取代码",
                        "urls": [],
                    },
                },
            )

            # 1. 准备 Git 认证配置
            git_auth = None
            if app_config.sandbox.git_auth:
                git_auth = GitAuthConfig(
                    auth_type=GitAuthType(app_config.sandbox.git_auth.auth_type),
                    ssh_private_key_path=app_config.sandbox.git_auth.ssh_private_key_path,
                    http_token=app_config.sandbox.git_auth.http_token,
                    http_username=app_config.sandbox.git_auth.http_username,
                )

            # 2. 创建沙箱
            logger.info("SandboxBootstrap: 创建新沙箱")
            sb_config = SandboxConfig(git_auth=git_auth)
            sandbox = await self.sandbox_manager.create_sandbox(config=sb_config)
            
            sandbox_id = sandbox.id

            # 3. 获取项目信息并克隆代码
            project_info = state.get("project_info", {})
            repo_url = project_info.get("git_http_url") or project_info.get("http_url")
            default_branch = project_info.get("default_branch", "main")
            
            if not repo_url:
                error_msg = "未找到项目仓库地址，无法克隆代码"
                logger.error(error_msg)
                
                runtime = state.get("runtime", {})
                update_dict.update(
                    {
                        "sandbox": {
                            "sandbox_id": sandbox_id,  # 记录以便 teardown 清理
                        },
                        "runtime": {
                            **runtime,
                            "error": error_msg,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.SANDBOX_BOOTSTRAP.value,
                            ],
                            "current_step": NodeName.SANDBOX_BOOTSTRAP.value,
                        },
                    }
                )
                # 失败时需要进入 teardown 清理
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

            # 4. 克隆代码
            logger.info(f"开始克隆代码: {repo_url} (branch: {default_branch})")
            git_service = GitService(self.sandbox_manager, sandbox_id)
            
            try:
                clone_result = await git_service.clone(
                    repo_url=repo_url,
                    branch=default_branch,
                )
                repo_path = clone_result.repo_path
                logger.info(f"代码克隆成功: {repo_path}")
            except Exception as e:
                error_msg = f"代码克隆失败: {str(e)}"
                logger.error(error_msg, exc_info=True)
                
                runtime = state.get("runtime", {})
                update_dict.update(
                    {
                        "sandbox": {
                            "sandbox_id": sandbox_id,
                        },
                        "runtime": {
                            **runtime,
                            "error": error_msg,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.SANDBOX_BOOTSTRAP.value,
                            ],
                            "current_step": NodeName.SANDBOX_BOOTSTRAP.value,
                        },
                    }
                )
                # 失败时需要进入 teardown 清理
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

            # 5. 读取 .gitignore 并解析忽略规则
            ignore_patterns = await self._load_ignore_patterns(sandbox_id, repo_path)
            logger.info(f"成功加载忽略规则: {len(ignore_patterns)} 条")

            # 6. 成功：写入 sandbox 信息到 state
            runtime = state.get("runtime", {})
            update_dict.update(
                {
                    "sandbox": {
                        "sandbox_id": sandbox_id,
                        "repo_path": repo_path,  # 仓库根目录（容器内路径），仅用于日志和引用，所有命令已自动在此目录执行
                        "default_branch": default_branch,
                        "ignore_patterns": ignore_patterns,
                    },
                    "runtime": {
                        **runtime,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.SANDBOX_BOOTSTRAP.value,
                        ],
                        "current_step": NodeName.SANDBOX_BOOTSTRAP.value,
                    },
                }
            )

            # 发送完成事件
            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": NodeName.SANDBOX_BOOTSTRAP.value,
                    "progress": "Sandbox 初始化完成",
                    "think_chain_item": {
                        "type": NodeName.SANDBOX_BOOTSTRAP.value,
                        "title": "Sandbox 初始化",
                        "desc": f"Sandbox ID: {sandbox_id}, Repo: {repo_path}",
                        "urls": [],
                    },
                },
            )

            logger.info(f"SandboxBootstrap 完成: sandbox_id={sandbox_id}, repo_path={repo_path}")
            
            # 成功后进入主路由
            return Command(update=update_dict, goto=NodeName.MAIN_ROUTER.value)

        except Exception as e:
            error_msg = f"SandboxBootstrap 失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            
            # 检查是否有已创建的 sandbox_id
            sandbox_info = update_dict.get("sandbox", {})
            sandbox_id = sandbox_info.get("sandbox_id")
            
            runtime = state.get("runtime", {})
            update_dict.update(
                {
                    "runtime": {
                        **runtime,
                        "error": error_msg,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.SANDBOX_BOOTSTRAP.value,
                        ],
                        "current_step": NodeName.SANDBOX_BOOTSTRAP.value,
                    },
                }
            )
            
            # 如果有 sandbox_id，需要 teardown 清理
            if sandbox_id:
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)
            else:
                # 如果连 sandbox 都没创建成功，直接结束
                return Command(update=update_dict, goto=NodeName.END.value)
    
    async def _load_ignore_patterns(self, sandbox_id: str, repo_path: str) -> list[str]:
        """
        从 .gitignore 加载忽略规则
        
        Args:
            sandbox_id: Sandbox ID
            repo_path: 仓库根路径
        
        Returns:
            忽略规则列表
        """
        import os
        
        patterns = get_default_ignore_patterns()
        
        try:
            file_service = FileService(self.sandbox_manager, sandbox_id)
            gitignore_path = os.path.join(repo_path, ".gitignore")
            
            # 检查 .gitignore 是否存在
            if await file_service.exists(gitignore_path):
                logger.info(f"读取 .gitignore: {gitignore_path}")
                content = await file_service.read_file(gitignore_path)
                
                # 解析并合并规则
                gitignore_patterns = parse_gitignore_content(content)
                patterns.extend(gitignore_patterns)
                
                logger.info(f"从 .gitignore 读取到 {len(gitignore_patterns)} 条规则")
        
        except Exception as e:
            logger.warning(f"读取 .gitignore 失败，使用默认规则: {e}")
        
        return patterns
