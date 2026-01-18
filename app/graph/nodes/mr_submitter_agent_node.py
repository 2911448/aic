"""
MR Submitter Agent Node - Merge Request 提交节点
自动创建分支、应用补丁、推送代码并创建 Merge Request
"""

from typing import Literal

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command

from app.config.app_config import app_config
from app.core.logger_config import logger
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
    ) -> Command[Literal["sandbox_teardown"]]:
        """
        创建分支、应用补丁并提交 Merge Request

        Args:
            state: 当前工作流状态

        Returns:
            Command 对象，返回 sandbox_teardown 节点
        """
        update_dict = {}

        try:
            # 发送进度事件
            await adispatch_custom_event(
                ProcessStage.MR_SUBMISSION.value,
                {
                    "status": NodeName.MR_SUBMITTER.value,
                    "progress": "正在提交 Merge Request...",
                    "think_chain_item": {
                        "type": NodeName.MR_SUBMITTER.value,
                        "title": "提交 MR",
                        "desc": "创建分支并提交 Merge Request",
                        "urls": [],
                    },
                },
            )

            # 从分域结构获取必要信息
            sandbox = state.get("sandbox", {})
            sandbox_id = sandbox.get("sandbox_id")
            default_branch = sandbox.get("default_branch", "main")
            
            patching = state.get("patching", {})
            generated_patches = patching.get("generated_patches", {})
            
            review = state.get("review", {})
            review_report = review.get("review_report", "")
            
            analysis = state.get("analysis", {})
            branch_name_suggestion = analysis.get("branch_name_suggestion")
            issue_type = analysis.get("issue_type", "bug")
            
            issue_data = state.get("issue_data", {})
            issue_author_id = issue_data.get("author_id")  # 获取 Issue 作者 ID，用于分配 MR
            project_info = state.get("project_info", {})

            runtime = state.get("runtime", {})

            if not sandbox_id:
                error_msg = "缺少 sandbox_id，无法提交 MR"
                logger.error(error_msg)
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "error": error_msg,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.MR_SUBMITTER.value,
                            ],
                            "current_step": NodeName.MR_SUBMITTER.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

            if not generated_patches:
                error_msg = "没有生成的补丁，无法提交 MR"
                logger.error(error_msg)
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "error": error_msg,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.MR_SUBMITTER.value,
                            ],
                            "current_step": NodeName.MR_SUBMITTER.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

            # 初始化服务
            git_service = GitService(self.sandbox_manager, sandbox_id)

            # 1. 确定分支名称
            if branch_name_suggestion:
                branch_name = await self._get_unique_branch_name_from_suggestion(
                    git_service, branch_name_suggestion
                )
            else:
                # 回退到基于 issue_iid 的命名方式
                issue_iid = issue_data.get("iid", "unknown")
                branch_prefix = "fix" if issue_type == "bug" else "feat"
                logger.info(f"未找到分支名建议，使用默认命名: {branch_prefix}/{issue_iid}")
                branch_name = await self._get_unique_branch_name(
                    git_service, branch_prefix, issue_iid
                )

            logger.info(f"使用分支名: {branch_name}")

            # 2. Git 操作
            try:
                # 修复：PatchFlow/RefactoringAgentBatch 已经应用了所有补丁到工作区
                # 这里不需要重复 apply，直接基于当前工作区创建新分支并提交
                
                # 先检查工作区是否有变更
                git_status = await git_service.status()
                if git_status.is_clean:
                    logger.warning("工作区无变更，无法创建 MR")
                    error_msg = "工作区无变更，补丁可能未正确应用"
                    update_dict.update(
                        {
                            "runtime": {
                                **runtime,
                                "error": error_msg,
                                "executed_nodes": [
                                    *runtime.get("executed_nodes", []),
                                    NodeName.MR_SUBMITTER.value,
                                ],
                                "current_step": NodeName.MR_SUBMITTER.value,
                            },
                        }
                    )
                    return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

                # 提交当前工作区的变更到临时分支（避免干扰后续操作）
                logger.info("暂存当前工作区变更...")
                await git_service.add(all_files=True)
                temp_commit_msg = f"[临时] {self._generate_commit_message(issue_data)}"
                await git_service.commit(
                    message=temp_commit_msg
                )

                # fetch 最新远程分支
                await git_service.fetch()

                # 创建新分支（基于远程 default_branch）
                await git_service.checkout_branch(
                    branch=branch_name,
                    create=True,
                    start_point=f"origin/{default_branch}"
                )

                # cherry-pick 刚才的临时提交（将变更迁移到新分支）
                logger.info("将变更迁移到新分支...")
                cherry_pick_cmd = f"git cherry-pick HEAD@{{1}}"
                await git_service._execute(cherry_pick_cmd, timeout=30)

                # 修改提交信息为正式的
                commit_message = self._generate_commit_message(issue_data)
                # 转义提交信息中的引号（f-string 中不能直接使用反斜杠）
                escaped_message = commit_message.replace('"', '\\"')
                await git_service._execute(
                    f'git commit --amend -m "{escaped_message}"',
                    timeout=30
                )

                # 推送到远程
                await git_service.push(
                    branch=branch_name,
                    set_upstream=True
                )

            except Exception as e:
                error_msg = f"Git 操作失败: {str(e)}"
                logger.error(error_msg, exc_info=True)
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "error": error_msg,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.MR_SUBMITTER.value,
                            ],
                            "current_step": NodeName.MR_SUBMITTER.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

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
                            "runtime": {
                                **runtime,
                                "error": error_msg,
                                "executed_nodes": [
                                    *runtime.get("executed_nodes", []),
                                    NodeName.MR_SUBMITTER.value,
                                ],
                                "current_step": NodeName.MR_SUBMITTER.value,
                            },
                        }
                    )
                    return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

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
                    assignee_ids=[issue_author_id] if issue_author_id else None,
                    remove_source_branch=True,
                )

                await gitlab_service.close()

                if mr_result.success:
                    update_dict.update(
                        {
                            "delivery": {
                                "mr_url": mr_result.mr_url,
                                "mr_iid": mr_result.mr_iid,
                                "branch_name": branch_name,
                            },
                            "runtime": {
                                **runtime,
                                "executed_nodes": [
                                    *runtime.get("executed_nodes", []),
                                    NodeName.MR_SUBMITTER.value,
                                ],
                                "current_step": NodeName.MR_SUBMITTER.value,
                                "completed": True,
                            },
                        }
                    )

                    # 发送完成事件
                    await adispatch_custom_event(
                        ProcessStage.THINK_CHAIN.value,
                        {
                            "status": NodeName.MR_SUBMITTER.value,
                            "progress": "MR 创建成功",
                            "think_chain_item": {
                                "type": NodeName.MR_SUBMITTER.value,
                                "title": "提交 MR",
                                "desc": f"分支: {branch_name}",
                                "urls": [mr_result.mr_url] if mr_result.mr_url else [],
                            },
                        },
                    )

                    return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)
                else:
                    error_msg = f"创建 MR 失败: {mr_result.error}"
                    logger.error(error_msg)
                    update_dict.update(
                        {
                            "runtime": {
                                **runtime,
                                "error": error_msg,
                                "executed_nodes": [
                                    *runtime.get("executed_nodes", []),
                                    NodeName.MR_SUBMITTER.value,
                                ],
                                "current_step": NodeName.MR_SUBMITTER.value,
                            },
                        }
                    )
                    return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

            except Exception as e:
                error_msg = f"创建 MR 失败: {str(e)}"
                logger.error(error_msg, exc_info=True)
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "error": error_msg,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.MR_SUBMITTER.value,
                            ],
                            "current_step": NodeName.MR_SUBMITTER.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

        except Exception as e:
            error_msg = f"MR 提交失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            runtime = state.get("runtime", {})
            update_dict.update(
                {
                    "runtime": {
                        **runtime,
                        "error": error_msg,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.MR_SUBMITTER.value,
                        ],
                        "current_step": NodeName.MR_SUBMITTER.value,
                    },
                }
            )
            return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

    async def _get_unique_branch_name_from_suggestion(
        self,
        git_service: GitService,
        suggestion: str,
    ) -> str:
        """
        基于 LLM 建议获取唯一的分支名称

        Args:
            git_service: Git 服务
            suggestion: LLM 建议的分支名

        Returns:
            唯一的分支名称
        """
        remote_branches = await git_service.list_branches(
            remote=True
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
        prefix: str,
        issue_iid: str | int,
    ) -> str:
        """
        获取唯一的分支名称（回退方法，基于 issue_iid）

        Args:
            git_service: Git 服务
            prefix: 分支前缀 (fix/feat)
            issue_iid: Issue IID

        Returns:
            唯一的分支名称
        """
        base_branch_name = f"{prefix}/{issue_iid}"

        # 获取远程分支列表
        remote_branches = await git_service.list_branches(
            remote=True
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

