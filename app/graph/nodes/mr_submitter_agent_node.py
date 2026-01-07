"""
MR Submitter Agent Node - Merge Request 提交节点
自动创建分支、应用补丁、推送代码并创建 Merge Request
"""

from typing import Literal

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command
from loguru import logger

from app.config.app_config import app_config
from app.graph.state import IssueProcessState, NodeName, ProcessStage
from app.sandbox.git_service import GitService
from app.sandbox.manager import get_sandbox_manager
from app.services.gitlab_service import GitLabService


class MRSubmitterAgentNode:
    """MR 提交 Agent 节点"""

    def __init__(self):
        """初始化节点"""
        self.sandbox_manager = get_sandbox_manager()

    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal[NodeName.END.value]]:
        """
        创建分支、应用补丁并提交 Merge Request

        Args:
            state: 当前工作流状态

        Returns:
            Command 对象，返回 END 节点
        """
        update_dict = {}

        try:
            # 发送进度事件
            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": "mr_submitter",
                    "progress": "正在提交 Merge Request...",
                    "think_chain_item": {
                        "type": "mr_submitter",
                        "title": "提交 MR",
                        "desc": "创建分支并提交 Merge Request",
                        "urls": [],
                    },
                },
            )

            # 获取必要信息
            sandbox_id = state.get("sandbox_id")
            repo_path = state.get("repo_path", ".")
            generated_patches = state.get("generated_patches", {})
            review_report = state.get("review_report", "")
            issue_data = state.get("issue_data", {})
            project_info = state.get("project_info", {})

            if not sandbox_id:
                error_msg = "缺少 sandbox_id，无法提交 MR"
                logger.error(error_msg)
                update_dict.update(
                    {
                        "error": error_msg,
                        "executed_nodes": [
                            *state.get("executed_nodes", []),
                            NodeName.MR_SUBMITTER.value,
                        ],
                        "current_step": NodeName.MR_SUBMITTER.value,
                    }
                )
                return Command(update=update_dict, goto=NodeName.END.value)

            if not generated_patches:
                error_msg = "没有生成的补丁，无法提交 MR"
                logger.error(error_msg)
                update_dict.update(
                    {
                        "error": error_msg,
                        "executed_nodes": [
                            *state.get("executed_nodes", []),
                            NodeName.MR_SUBMITTER.value,
                        ],
                        "current_step": NodeName.MR_SUBMITTER.value,
                    }
                )
                return Command(update=update_dict, goto=NodeName.END.value)

            # 初始化服务
            git_service = GitService(self.sandbox_manager, sandbox_id)

            # 1. 确定分支名称
            branch_name_suggestion = state.get("branch_name_suggestion")
            
            if branch_name_suggestion:
                branch_name = await self._get_unique_branch_name_from_suggestion(
                    git_service, repo_path, branch_name_suggestion
                )
            else:
                # 回退到基于 issue_iid 的命名方式
                issue_type = state.get("issue_type", "bug")
                issue_iid = issue_data.get("iid", "unknown")
                branch_prefix = "fix" if issue_type == "bug" else "feat"
                logger.info(f"未找到分支名建议，使用默认命名: {branch_prefix}/{issue_iid}")
                branch_name = await self._get_unique_branch_name(
                    git_service, repo_path, branch_prefix, issue_iid
                )

            logger.info(f"使用分支名: {branch_name}")

            # 2. Git 操作
            try:
                await git_service.fetch(repo_path=repo_path)

                default_branch = project_info.get("default_branch", "main")

                await git_service.checkout_branch(
                    branch=branch_name,
                    create=True,
                    start_point=f"origin/{default_branch}",
                    repo_path=repo_path,
                )

                for file_path, patch_content in generated_patches.items():
                    logger.info(f"应用补丁: {file_path}")
                    await git_service.apply_patch(
                        patch_content=patch_content,
                        repo_path=repo_path,
                    )

                # 提交变更
                commit_message = self._generate_commit_message(issue_data)
                await git_service.commit(
                    message=commit_message,
                    all_files=True,
                    repo_path=repo_path,
                )

                # 推送到远程
                await git_service.push(
                    branch=branch_name,
                    set_upstream=True,
                    repo_path=repo_path,
                )

            except Exception as e:
                error_msg = f"Git 操作失败: {str(e)}"
                logger.error(error_msg, exc_info=True)
                update_dict.update(
                    {
                        "error": error_msg,
                        "executed_nodes": [
                            *state.get("executed_nodes", []),
                            NodeName.MR_SUBMITTER.value,
                        ],
                        "current_step": NodeName.MR_SUBMITTER.value,
                    }
                )
                return Command(update=update_dict, goto=NodeName.END.value)

            # 3. 创建 Merge Request
            try:
                # 从配置获取 GitLab 信息
                gitlab_url = self._extract_gitlab_url(project_info)
                gitlab_token = app_config.sandbox.git_auth.http_token if app_config.sandbox.git_auth else None

                if not gitlab_token:
                    error_msg = "未配置 GitLab Token，无法创建 MR"
                    logger.error(error_msg)
                    update_dict.update(
                        {
                            "error": error_msg,
                            "executed_nodes": [
                                *state.get("executed_nodes", []),
                                NodeName.MR_SUBMITTER.value,
                            ],
                            "current_step": NodeName.MR_SUBMITTER.value,
                        }
                    )
                    return Command(update=update_dict, goto=NodeName.END.value)

                gitlab_service = GitLabService(
                    gitlab_url=gitlab_url,
                    private_token=gitlab_token,
                    verify_ssl=app_config.gitlab.verify_ssl,
                )

                # 创建 MR
                mr_title = self._generate_mr_title(issue_data)
                mr_description = self._generate_mr_description(
                    issue_data, review_report
                )

                project_id = project_info.get("id")
                default_branch = project_info.get("default_branch", "main")

                mr_result = await gitlab_service.create_merge_request(
                    project_id=project_id,
                    source_branch=branch_name,
                    target_branch=default_branch,
                    title=mr_title,
                    description=mr_description,
                    labels=["AI-Generated"],
                    remove_source_branch=True,
                )

                await gitlab_service.close()

                if mr_result.success:
                    update_dict.update(
                        {
                            "mr_url": mr_result.mr_url,
                            "mr_iid": mr_result.mr_iid,
                            "branch_name": branch_name,
                            "executed_nodes": [
                                *state.get("executed_nodes", []),
                                NodeName.MR_SUBMITTER.value,
                            ],
                            "current_step": NodeName.MR_SUBMITTER.value,
                            "completed": True,
                        }
                    )

                    # 发送完成事件
                    await adispatch_custom_event(
                        ProcessStage.THINK_CHAIN.value,
                        {
                            "status": "mr_submitter",
                            "progress": "MR 创建成功",
                            "think_chain_item": {
                                "type": "mr_submitter",
                                "title": "提交 MR",
                                "desc": f"分支: {branch_name}",
                                "urls": [mr_result.mr_url] if mr_result.mr_url else [],
                            },
                        },
                    )

                    return Command(update=update_dict, goto=NodeName.END.value)
                else:
                    error_msg = f"创建 MR 失败: {mr_result.error}"
                    logger.error(error_msg)
                    update_dict.update(
                        {
                            "error": error_msg,
                            "executed_nodes": [
                                *state.get("executed_nodes", []),
                                NodeName.MR_SUBMITTER.value,
                            ],
                            "current_step": NodeName.MR_SUBMITTER.value,
                        }
                    )
                    return Command(update=update_dict, goto=NodeName.END.value)

            except Exception as e:
                error_msg = f"创建 MR 失败: {str(e)}"
                logger.error(error_msg, exc_info=True)
                update_dict.update(
                    {
                        "error": error_msg,
                        "executed_nodes": [
                            *state.get("executed_nodes", []),
                            NodeName.MR_SUBMITTER.value,
                        ],
                        "current_step": NodeName.MR_SUBMITTER.value,
                    }
                )
                return Command(update=update_dict, goto=NodeName.END.value)

        except Exception as e:
            error_msg = f"MR 提交失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            update_dict.update(
                {
                    "error": error_msg,
                    "executed_nodes": [
                        *state.get("executed_nodes", []),
                        NodeName.MR_SUBMITTER.value,
                    ],
                    "current_step": NodeName.MR_SUBMITTER.value,
                }
            )
            return Command(update=update_dict, goto=NodeName.END.value)

    async def _get_unique_branch_name_from_suggestion(
        self,
        git_service: GitService,
        repo_path: str,
        suggestion: str,
    ) -> str:
        """
        基于 LLM 建议获取唯一的分支名称

        Args:
            git_service: Git 服务
            repo_path: 仓库路径
            suggestion: LLM 建议的分支名

        Returns:
            唯一的分支名称
        """
        remote_branches = await git_service.list_branches(
            remote=True, repo_path=repo_path
        )

        # 检查分支是否存在
        branch_name = suggestion
        counter = 1

        while any(branch.endswith(branch_name) for branch in remote_branches):
            branch_name = f"{suggestion}-{counter}"
            counter += 1

        return branch_name

    async def _get_unique_branch_name(
        self,
        git_service: GitService,
        repo_path: str,
        prefix: str,
        issue_iid: str | int,
    ) -> str:
        """
        获取唯一的分支名称（回退方法，基于 issue_iid）

        Args:
            git_service: Git 服务
            repo_path: 仓库路径
            prefix: 分支前缀 (fix/feat)
            issue_iid: Issue IID

        Returns:
            唯一的分支名称
        """
        base_branch_name = f"{prefix}/{issue_iid}"

        # 获取远程分支列表
        remote_branches = await git_service.list_branches(
            remote=True, repo_path=repo_path
        )

        # 检查分支是否存在
        branch_name = base_branch_name
        counter = 1

        while any(branch.endswith(branch_name) for branch in remote_branches):
            branch_name = f"{base_branch_name}-{counter}"
            counter += 1

        return branch_name

    def _generate_commit_message(self, issue_data: dict) -> str:
        """
        生成提交信息

        Args:
            issue_data: Issue 数据

        Returns:
            提交信息
        """
        issue_title = issue_data.get("title", "Fix issue")
        issue_iid = issue_data.get("iid", "")

        return f"{issue_title}\n\nCloses #{issue_iid}"

    def _generate_mr_title(self, issue_data: dict) -> str:
        """
        生成 MR 标题

        Args:
            issue_data: Issue 数据

        Returns:
            MR 标题
        """
        issue_title = issue_data.get("title", "Fix issue")
        issue_iid = issue_data.get("iid", "")

        return f"{issue_title} (#{issue_iid})"

    def _generate_mr_description(
        self, issue_data: dict, review_report: str
    ) -> str:
        """
        生成 MR 描述

        Args:
            issue_data: Issue 数据
            review_report: 评审报告

        Returns:
            MR 描述
        """
        issue_iid = issue_data.get("iid", "")
        issue_url = issue_data.get("url", "")

        description = f"## 关联 Issue\n\n"
        description += f"Closes #{issue_iid}\n\n"
        if issue_url:
            description += f"Issue 链接: {issue_url}\n\n"

        description += "## AI 生成的代码评审\n\n"
        description += review_report

        description += "\n\n---\n"
        description += "*此 Merge Request 由 AI 自动生成，请仔细审查代码变更。*"

        return description

    def _extract_gitlab_url(self, project_info: dict) -> str:
        """
        从项目信息中提取 GitLab URL

        Args:
            project_info: 项目信息

        Returns:
            GitLab URL
        """
        web_url = project_info.get("web_url", "")
        if web_url:
            # 从 web_url 提取基础 URL
            # 例如: https://gitlab.com/user/project -> https://gitlab.com
            parts = web_url.split("/")
            if len(parts) >= 3:
                return f"{parts[0]}//{parts[2]}"

        # 默认返回 gitlab.com
        return "https://gitlab.com"

