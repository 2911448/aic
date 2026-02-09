"""
MR Publisher Agent Node - 评审+提交一体化节点

职责：
1. 调用 LLM 生成结构化评审报告（含分支名）
2. 创建 Git 分支并提交代码
3. 创建 GitLab Merge Request

输出：全部写入 state.delivery（含 review_artifact）
"""

from typing import Literal, Optional

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.config.app_config import app_config
from app.core.logger_config import logger
from app.core.prompt_manager import prompt_manager
from app.core.trace_context import set_trace_id
from app.decorators.tracking import track_node_metrics
from app.graph.state import IssueProcessState, NodeName, ProcessStage
from app.llms.llm_factory import get_llm_model
from app.sandbox.git_service import GitService
from app.sandbox.manager import get_sandbox_manager
from app.services.gitlab_service import GitLabService
from app.schemas.agent_outputs import ReviewArtifact
from app.utils.common_function import parse_json_response


class MRPublishResult(BaseModel):
    """MR 发布核心执行结果"""
    success: bool = Field(description="是否成功")
    error: Optional[str] = Field(default=None, description="错误信息（如果失败）")
    mr_url: Optional[str] = Field(default=None, description="MR URL")
    mr_iid: Optional[int] = Field(default=None, description="MR IID")
    branch_name: Optional[str] = Field(default=None, description="分支名")
    review_artifact: Optional[ReviewArtifact] = Field(default=None, description="评审报告")


class MRPublisherAgentNode:
    """MR Publisher Agent 节点 - 评审+提交一体化"""

    def __init__(self):
        """初始化节点"""
        self.prompt_manager = prompt_manager
        self.sandbox_manager = get_sandbox_manager()

    async def _publish_core(self, state: dict) -> MRPublishResult:
        """
        核心发布逻辑：生成评审报告 → Git 操作 → 创建 MR
        
        Args:
            state: 当前状态（dict 格式，兼容 IssueProcessState 和 run_agent 调用）
        
        Returns:
            MRPublishResult 结构化结果
        """
        try:
            # Gate A: 强制检查 verification 必须通过
            verification = state.get("verification", {})
            final_verification = verification.get("final_verification")
            
            if not final_verification:
                return MRPublishResult(
                    success=False,
                    error="Verification not run; refusing to create MR. Please run verification first."
                )
            
            if not final_verification.get("passed", False):
                error_count = final_verification.get("error_count", 0)
                return MRPublishResult(
                    success=False,
                    error=f"Verification not passed ({error_count} errors); refusing to create MR. Please fix errors first."
                )
            
            logger.info("[MR Gate A] Verification passed, proceeding with MR creation")
            
            # 1. 前置检查：是否有补丁
            patching = state.get("patching", {})
            patches = patching.get("patches", [])
            
            if not patches:
                return MRPublishResult(
                    success=False,
                    error="没有补丁可提交"
                )
            
            # 2. 生成评审报告
            verification_result = final_verification
            
            review_artifact = await self._generate_review_artifact(
                state,
                patches,
                verification_result,
            )
            
            if not review_artifact:
                return MRPublishResult(
                    success=False,
                    error="评审报告生成失败"
                )
            
            logger.info(f"评审报告生成完成，建议分支名: {review_artifact.branch_name}")
            
            # 3. Git 操作前检查
            sandbox = state.get("sandbox", {})
            sandbox_id = sandbox.get("sandbox_id")
            default_branch = sandbox.get("default_branch", "main")
            issue_data = state.get("issue_data", {})
            project_info = state.get("project_info", {})
            
            if not sandbox_id:
                return MRPublishResult(
                    success=False,
                    error="缺少 sandbox_id，无法提交 MR"
                )
            
            # 4. 初始化 Git 服务
            git_service = GitService(self.sandbox_manager, sandbox_id)
            
            # 确定唯一分支名
            branch_name = await self._get_unique_branch_name_from_suggestion(
                git_service, review_artifact.branch_name
            )
            logger.info(f"使用分支名: {branch_name}")
            
            # 5. Git 操作
            try:
                # 检查工作区是否有变更
                git_status = await git_service.status()
                if git_status.is_clean:
                    return MRPublishResult(
                        success=False,
                        error="工作区无变更，补丁可能未正确应用"
                    )
                
                # fetch 最新远程分支
                await git_service.fetch()
                
                # 创建新分支（基于远程 default_branch）
                logger.info(f"创建新分支 {branch_name} (from origin/{default_branch})")
                await git_service.checkout_branch(
                    branch=branch_name,
                    create=True,
                    start_point=f"origin/{default_branch}"
                )
                
                # 在新分支上提交当前工作区变更
                await git_service.add(all_files=True)
                commit_message = self._generate_commit_message(issue_data)
                
                commit_hash = await git_service.commit(
                    message=commit_message,
                    allow_empty=False,
                )
                
                logger.info(f"成功提交到分支 {branch_name}, commit: {commit_hash}")
                
                # 推送到远程
                await git_service.push(
                    branch=branch_name,
                    set_upstream=True
                )
                
            except Exception as e:
                error_msg = f"Git 操作失败: {str(e)}"
                logger.error(error_msg, exc_info=True)
                return MRPublishResult(
                    success=False,
                    error=error_msg
                )
            
            # 6. 创建 Merge Request
            try:
                # 从配置获取 GitLab 信息
                gitlab_url = self._extract_gitlab_url(project_info)
                gitlab_token = app_config.sandbox.git_auth.http_token if app_config.sandbox.git_auth else None
                
                if not gitlab_token:
                    return MRPublishResult(
                        success=False,
                        error="未配置 GitLab Token，无法创建 MR"
                    )
                
                gitlab_service = GitLabService(
                    gitlab_url=gitlab_url,
                    private_token=gitlab_token,
                    verify_ssl=app_config.gitlab.verify_ssl,
                )
                
                # 生成 MR 标题和描述
                mr_title = self._generate_mr_title(issue_data)
                review_report = self._render_review_markdown(review_artifact)
                mr_description = self._generate_mr_description(
                    issue_data, review_report
                )
                
                project_id = project_info.get("id")
                issue_author_id = issue_data.get("author_id")
                
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
                    logger.info(f"MR 创建成功: {mr_result.mr_url}")
                    return MRPublishResult(
                        success=True,
                        mr_url=mr_result.mr_url,
                        mr_iid=mr_result.mr_iid,
                        branch_name=branch_name,
                        review_artifact=review_artifact,
                    )
                else:
                    return MRPublishResult(
                        success=False,
                        error=f"创建 MR 失败: {mr_result.error}"
                    )
                    
            except Exception as e:
                error_msg = f"创建 MR 失败: {str(e)}"
                logger.error(error_msg, exc_info=True)
                return MRPublishResult(
                    success=False,
                    error=error_msg
                )
                
        except Exception as e:
            error_msg = f"MR Publisher 核心执行失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return MRPublishResult(
                success=False,
                error=error_msg
            )

    @track_node_metrics("mr_publisher")
    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal["planner_orchestrator", "sandbox_teardown"]]:
        """
        生成评审报告并提交 Merge Request（LangGraph 节点入口）

        Args:
            state: 当前工作流状态

        Returns:
            Command 对象，成功返回 sandbox_teardown，失败返回 sandbox_teardown
        """
        # 从 state 恢复 trace_id 到上下文
        trace_id = state.get("runtime", {}).get("trace_id")
        if trace_id:
            set_trace_id(trace_id)

        runtime = state.get("runtime", {})
        update_dict = {}

        try:
            # 发送进度事件 - 开始执行
            await adispatch_custom_event(
                ProcessStage.MR_SUBMISSION.value,
                {
                    "status": NodeName.MR_PUBLISHER.value,
                    "progress": "正在生成评审报告...",
                    "think_chain_item": {
                        "type": NodeName.MR_PUBLISHER.value,
                        "title": "MR 发布",
                        "desc": "生成评审报告和分支名",
                        "urls": [],
                    },
                },
            )

            # 调用核心执行逻辑
            result = await self._publish_core(state)
            
            # 根据结果构建 update_dict 和返回 Command
            if result.success:
                update_dict.update(
                    {
                        "delivery": {
                            "mr_url": result.mr_url,
                            "mr_iid": result.mr_iid,
                            "branch_name": result.branch_name,
                            "review_artifact": result.review_artifact.model_dump() if result.review_artifact else None,
                        },
                        "runtime": {
                            **runtime,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.MR_PUBLISHER.value,
                            ],
                            "current_step": NodeName.MR_PUBLISHER.value,
                            "completed": True,
                        },
                    }
                )

                # 发送完成事件
                await adispatch_custom_event(
                    ProcessStage.THINK_CHAIN.value,
                    {
                        "status": NodeName.MR_PUBLISHER.value,
                        "progress": "MR 创建成功",
                        "think_chain_item": {
                            "type": NodeName.MR_PUBLISHER.value,
                            "title": "MR 发布",
                            "desc": f"分支: {result.branch_name}",
                            "urls": [result.mr_url] if result.mr_url else [],
                        },
                    },
                )
            else:
                # 失败：写入错误信息
                logger.error(f"MR Publisher 执行失败: {result.error}")
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "error": result.error,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.MR_PUBLISHER.value,
                            ],
                            "current_step": NodeName.MR_PUBLISHER.value,
                        },
                    }
                )

            return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

        except Exception as e:
            error_msg = f"MR Publisher 执行失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            update_dict.update(
                {
                    "runtime": {
                        **runtime,
                        "error": error_msg,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.MR_PUBLISHER.value,
                        ],
                        "current_step": NodeName.MR_PUBLISHER.value,
                    },
                }
            )

            return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

    async def _generate_review_artifact(
        self,
        state: IssueProcessState,
        patches: list[dict],
        verification_result: Optional[dict],
    ) -> ReviewArtifact | None:
        """
        生成结构化评审报告（JSON）

        Args:
            state: 当前状态
            patches: 补丁产物列表（PatchArtifact 的 dict 表示）
            verification_result: 验证结果

        Returns:
            ReviewArtifact 或 None
        """
        issue_data = state.get("issue_data", {})
        issue_title = issue_data.get("title", "")
        issue_description = issue_data.get("description", "")

        # 构建补丁信息列表（使用结构化 patches）
        patches_info = []
        for patch in patches:
            patches_info.append({
                "id": patch.get("id", ""),
                "file_paths": patch.get("file_paths", []),
                "summary": patch.get("summary", ""),
                "unified_diff": patch.get("unified_diff", ""),
            })

        # 变更摘要
        all_files = set()
        for patch in patches:
            all_files.update(patch.get("file_paths", []))
        changes_summary = f"修改了 {len(all_files)} 个文件，共 {len(patches)} 个补丁"

        # 验证结果摘要
        verification_summary = "未执行验证"
        if verification_result:
            passed = verification_result.get("passed", False)
            error_count = verification_result.get("error_count", 0)
            warning_count = verification_result.get("warning_count", 0)
            verification_summary = (
                f"验证状态: {'通过' if passed else '失败'}"
                + (f", 错误: {error_count}, 警告: {warning_count}" if error_count + warning_count > 0 else "")
            )

        # 渲染 Prompt
        prompt = self.prompt_manager.render(
            "change_review",
            issue_title=issue_title,
            issue_description=issue_description or "无描述",
            changes_summary=changes_summary,
            patches=patches_info,
            verification_results=verification_summary,
        )

        # 调用 LLM 生成结构化评审报告
        llm = await get_llm_model(model_name="gpt-5-2025-08-07")
        response = await llm.ainvoke(prompt)

        # 解析 JSON 响应并验证
        try:
            response_data = parse_json_response(response.content)
            return ReviewArtifact(**response_data)
        except Exception as e:
            logger.error(f"ReviewArtifact 解析失败: {e}")
            # 降级：返回基本的 ReviewArtifact
            issue_iid = issue_data.get("iid", "unknown")
            fallback_branch = f"fix/issue-{issue_iid}"

            return ReviewArtifact(
                summary=changes_summary,
                technical_details=f"补丁总数: {len(patches)}",
                branch_name=fallback_branch,
                risks=[],
                test_plan=[],
                checklist=[],
                open_questions=[],
                overall_assessment="评审报告生成失败，请人工审查"
            )

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

    def _render_review_markdown(self, review_artifact: ReviewArtifact) -> str:
        """
        将结构化 ReviewArtifact 渲染为 Markdown

        Args:
            review_artifact: 结构化评审报告

        Returns:
            Markdown 格式的评审报告
        """
        lines = []

        # 变更摘要
        lines.append("## 变更摘要\n")
        lines.append(review_artifact.summary)
        lines.append("\n")

        # 技术细节
        lines.append("## 技术细节\n")
        lines.append(review_artifact.technical_details)
        lines.append("\n")

        # 风险评估
        if review_artifact.risks:
            lines.append("## 风险评估\n")
            for risk in review_artifact.risks:
                level_emoji = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(risk.level, "⚪")
                lines.append(f"### {level_emoji} {risk.level.upper()} - {risk.category}\n")
                lines.append(f"{risk.description}\n")
                if risk.mitigation:
                    lines.append(f"**缓解措施**: {risk.mitigation}\n")
                lines.append("\n")

        # 测试建议
        if review_artifact.test_plan:
            lines.append("## 测试建议\n")
            for test in review_artifact.test_plan:
                lines.append(f"- {test}\n")
            lines.append("\n")

        # 评审检查清单
        if review_artifact.checklist:
            lines.append("## 评审检查清单\n")
            for item in review_artifact.checklist:
                lines.append(f"- [ ] {item.item}")
                if item.file_path:
                    lines.append(f" (`{item.file_path}`")
                    if item.line_range:
                        lines.append(f", lines {item.line_range}")
                    lines.append(")")
                lines.append("\n")
            lines.append("\n")

        # 开放问题
        if review_artifact.open_questions:
            lines.append("## 开放问题\n")
            for question in review_artifact.open_questions:
                lines.append(f"- {question}\n")
            lines.append("\n")

        # 总体评估
        lines.append("## 总体评估\n")
        lines.append(review_artifact.overall_assessment)
        lines.append("\n")

        return "".join(lines)

    def _generate_mr_description(
        self, issue_data: dict, review_report: str
    ) -> str:
        """
        生成 MR 描述

        Args:
            issue_data: Issue 数据
            review_report: 评审报告（Markdown 格式）

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


# 独立的执行函数（供 run_agent 工具调用）
async def execute_mr_publisher(state: dict) -> dict:
    """
    执行 MRPublisher（供 run_agent 工具调用）

    Args:
        state: 当前 state

    Returns:
        state 更新字典（包含 __execution__ 元数据）
    """
    try:
        logger.info("[execute_mr_publisher] 开始执行 MR 发布")

        node = MRPublisherAgentNode()
        
        # 调用核心执行逻辑
        result = await node._publish_core(state)
        
        # 根据结果构建返回字典
        if result.success:
            logger.info(f"[execute_mr_publisher] MR 创建成功: {result.mr_url}")
            return {
                "delivery": {
                    "mr_url": result.mr_url,
                    "mr_iid": result.mr_iid,
                    "branch_name": result.branch_name,
                    "review_artifact": result.review_artifact.model_dump() if result.review_artifact else None,
                },
                "__execution__": {
                    "reasoning": "成功生成评审报告并创建 MR",
                    "result_hint": {
                        "success": True,
                        "mr_url": result.mr_url or "",
                        "branch_name": result.branch_name or "",
                    },
                },
            }
        else:
            logger.error(f"[execute_mr_publisher] 执行失败: {result.error}")
            return {
                "runtime": {"error": result.error},
                "__execution__": {
                    "reasoning": result.error or "MR Publisher 执行失败",
                    "result_hint": {
                        "success": False,
                        "error": result.error or "",
                    },
                },
            }

    except Exception as e:
        logger.error(f"[execute_mr_publisher] 执行失败: {e}", exc_info=True)
        return {
            "runtime": {"error": f"MR Publisher 失败: {str(e)}"},
            "__execution__": {
                "reasoning": f"MR Publisher 失败: {str(e)}",
                "result_hint": {
                    "success": False,
                    "error": str(e),
                },
            },
        }


__all__ = ["MRPublisherAgentNode", "MRPublishResult", "execute_mr_publisher"]
