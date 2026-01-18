"""
Sandbox Bootstrap Node - Sandbox 生命周期前置节点
在 Issue 请求进入时创建 sandbox 并 clone 仓库
"""

import os
from typing import Literal

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command

from app.config.app_config import app_config
from app.core.logger_config import logger
from app.graph.state import IssueProcessState
from app.graph.state.node_names import NodeName, ProcessStage
from app.rag.indexer import code_indexer
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

            # 1. 创建沙箱（直接使用配置，无需映射）
            logger.info("SandboxBootstrap: 创建新沙箱")
            sb_config = app_config.sandbox
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
            logger.info("开始克隆代码")
            git_service = GitService(self.sandbox_manager, sandbox_id)
            
            try:
                clone_result = await git_service.clone(
                    repo_url=repo_url,
                    branch=default_branch,
                )
                
                # 获取 sandbox 实例以获取绝对路径
                sandbox = await self.sandbox_manager.get_sandbox(sandbox_id)
                repo_path = sandbox.repo_working_dir or clone_result.repo_path
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

            # 6. 检查项目是否已在向量库中，如果不存在则进行首次索引
            await self._check_and_index_project(project_info, repo_path, sandbox_id)

            # 7. 成功：写入 sandbox 信息到 state
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
        
        except Exception as e:
            logger.warning(f"读取 .gitignore 失败，使用默认规则: {e}")
        
        return patterns
    
    async def _check_and_index_project(
        self,
        project_info: dict,
        repo_path: str,
        sandbox_id: str,
    ) -> None:
        """
        检查项目是否已在向量库中，如果不存在则进行首次完整索引
        
        Args:
            project_info: 项目信息
            repo_path: 仓库根路径（容器内路径）
            sandbox_id: Sandbox ID
        """
        try:
            # 获取项目名称
            project_name = (
                project_info.get("name")
                or project_info.get("path_with_namespace")
                or project_info.get("path")
            )
            
            if not project_name:
                logger.warning("无法获取项目名称，跳过向量化检查")
                return
            
            # 检查项目是否已在向量库中
            project_exists = await code_indexer.check_project_exists(project_name)
            
            if not project_exists:
                logger.info(f"项目 {project_name} 首次索引, 开始完整向量化")
                
                # 获取 sandbox 实例并转换路径：容器内路径 -> 宿主机路径
                sandbox = await self.sandbox_manager.get_sandbox(sandbox_id)
                container_workspace = sandbox.config.workspace_path
                host_workspace = sandbox.workspace_path
                
                # 将容器内的 repo_path 转换为宿主机路径
                if repo_path.startswith(container_workspace):
                    relative_path = repo_path[len(container_workspace):].lstrip("/")
                    host_repo_path = os.path.join(host_workspace, relative_path)
                else:
                    host_repo_path = repo_path
                
                try:
                    await adispatch_custom_event(
                        ProcessStage.SANDBOX_BOOTSTRAP.value,
                        {
                            "status": NodeName.SANDBOX_BOOTSTRAP.value,
                            "progress": f"正在对项目 {project_name} 进行首次向量化索引...",
                        },
                    )
                    
                    # 索引目录（包含 .gitignore 规则）
                    snippet_count = await code_indexer.index_directory(
                        directory=host_repo_path,
                        project_name=project_name,
                        file_extensions=None,  # 使用默认支持的所有类型
                        exclude_dirs=None,
                        use_gitignore=True,
                    )
                    
                    # 发送索引完成事件
                    await adispatch_custom_event(
                        ProcessStage.THINK_CHAIN.value,
                        {
                            "status": NodeName.SANDBOX_BOOTSTRAP.value,
                            "progress": f"项目索引完成，共 {snippet_count} 个片段",
                        },
                    )
                
                except Exception as e:
                    logger.error(f"项目 {project_name} 索引失败: {e}", exc_info=True)
                    # 索引失败不应阻断主流程，记录错误后继续
                    # 后续 RAG 检索时会发现没有数据，可以降级处理
                    pass
                
            else:
                logger.info(f"项目 {project_name} 已存在于向量库, 跳过首次索引")
        
        except Exception as e:
            logger.error(f"检查并索引项目失败: {e}", exc_info=True)
            # 失败时不应中断主流程
            pass
