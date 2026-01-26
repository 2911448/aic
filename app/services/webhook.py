"""
GitLab Webhook 服务
处理来自 GitLab 的 webhook 事件
"""

import hmac
import time
from typing import Optional

from app.core.logger_config import logger

from app.config.app_config import app_config
from app.schemas.webhook import GitLabWebhookPayload, WebhookResponse
from app.sandbox.manager import get_sandbox_manager
from app.core.trace_context import generate_trace_id, set_trace_id
from app.core.metrics import metrics_collector


class WebhookService:
    """GitLab Webhook 服务"""

    def __init__(self):
        self.secret = app_config.gitlab.webhook_secret

    def verify_signature(
        self,
        payload_body: bytes,
        signature_header: Optional[str] = None,
    ) -> bool:
        """
        验证 GitLab webhook 签名

        GitLab 使用 X-Gitlab-Token 头部进行验证
        如果配置了 Secret Token，GitLab 会在请求头中发送该 token

        Args:
            payload_body: 请求体原始字节
            signature_header: X-Gitlab-Token 头部值

        Returns:
            验证是否通过
        """
        if not self.secret:
            logger.warning("未配置 webhook secret，跳过签名验证")
            return True

        if not signature_header:
            logger.error("缺少 X-Gitlab-Token 头部")
            return False

        # GitLab 的 Secret Token 是简单的字符串比较
        is_valid = hmac.compare_digest(signature_header, self.secret)

        if not is_valid:
            logger.error("Webhook 签名验证失败")

        return is_valid

    async def process_webhook(
        self,
        payload: GitLabWebhookPayload,
    ) -> WebhookResponse:
        """
        处理 webhook 事件

        Args:
            payload: GitLab webhook 载荷

        Returns:
            处理响应
        """
        # 处理不同类型的事件
        if payload.object_kind == "issue":
            return await self._process_issue_event(payload)
        elif payload.object_kind == "note":
            return await self._process_note_event(payload)
        elif payload.object_kind == "merge_request":
            return await self._process_merge_request_event(payload)
        else:
            logger.warning(f"暂不支持的事件类型: {payload.object_kind}")
            return WebhookResponse(
                status="success",
                message=f"收到事件 {payload.object_kind}，暂不处理",
                event_type=payload.object_kind,
            )

    async def _process_issue_event(
        self,
        payload: GitLabWebhookPayload,
    ) -> WebhookResponse:
        """
        处理 Issue 事件

        Args:
            payload: GitLab webhook 载荷

        Returns:
            处理响应
        """
        issue = payload.object_attributes
        project = payload.project
        issue_state = issue.get("state")
        issue_iid = issue.get("iid")

        logger.info(
            f"处理 Issue 事件: "
            f"#{issue_iid} - {issue.get('title')} "
            f"(状态: {issue_state})"
        )

        # 记录 Issue 详情
        if issue.get("description"):
            logger.info(f"Issue 描述: {issue.get('description')}")

        logger.info(f"项目: {project.path_with_namespace}")

        # 过滤：只处理 opened 状态的 Issue
        if issue_state != "opened":
            logger.info(f"Issue 状态为 {issue_state}，跳过处理")
            return WebhookResponse(
                status="success",
                message=f"Issue #{issue_iid} 状态为 {issue_state}，无需处理",
                event_type="issue",
                issue_iid=issue_iid,
                issue_title=issue.get("title"),
                project_path=project.path_with_namespace,
            )

        # 生成并设置 trace_id
        trace_id = generate_trace_id()
        set_trace_id(trace_id)
        
        logger.info(
            f"启动 AI Agent 工作流处理 Issue #{issue_iid}",
            extra={"trace_id": trace_id, "project_id": project.id}
        )

        workflow_start = time.time()
        result = {}  # 初始化 result，以便在 finally 中访问
        try:
            from app.graph.workflows.issue_workflow import create_issue_workflow
            from app.graph.state import IssueProcessState, init_state_defaults

            # 构造初始状态（注入 trace_id）
            initial_state: IssueProcessState = {
                "issue_data": issue,
                "project_info": project.model_dump(),
                # 初始化所有分域（通过 helper 函数）
                "sandbox": {},
                "analysis": {"search_queries": []},
                "retrieval": {"retrieved_code": []},
                "targeting": {
                    "target_queue": [],
                    "current_expansion_depth": 0,
                    "max_expansion_depth": 3,
                },
                "context": {},
                "patching": {
                    "patch_candidates": [],
                    "generated_patches": {},
                    "patch_retry_count": 0,
                    "retry_history": [],
                },
                "verification": {"verification_results_by_candidate": {}},
                "impact": {},
                "review": {},
                "delivery": {},
                "runtime": {
                    "trace_id": trace_id,  # 注入 trace_id
                    "executed_nodes": [],
                    "current_step": "init",
                    "error": None,
                    "completed": False,
                },
            }

            # 初始化默认值
            initial_state = init_state_defaults(initial_state)

            # 创建并执行工作流
            workflow = create_issue_workflow()
            result = await workflow.ainvoke(
                initial_state,
                config={
                    "recursion_limit": 100,  # 增加递归限制以支持更复杂的流程
                    "configurable": {
                        "thread_id": trace_id  # 关联 Opik thread_id
                    },
                    "metadata": {
                        "issue_iid": issue_iid,
                        "project_path": project.path_with_namespace,
                        "issue_title": issue.get("title", ""),
                    }
                }
            )

            # 检查执行结果（从分域结构）
            runtime = result.get("runtime", {})
            executed_nodes = runtime.get("executed_nodes", [])
            error = runtime.get("error")
            
            # 记录 workflow 总结指标
            workflow_duration = (time.time() - workflow_start) * 1000
            metrics_collector.log_workflow_summary(
                issue_iid=issue_iid,
                project_path=project.path_with_namespace,
                total_duration_ms=workflow_duration,
                executed_nodes=executed_nodes,
                success=error is None,
                error=error,
            )

            logger.info(
                f"Workflow完成 | "
                f"执行路径: {' → '.join(executed_nodes)} | "
                f"错误: {error or 'None'} | "
                f"耗时: {workflow_duration:.2f}ms"
            )

            if error:
                return WebhookResponse(
                    status="error",
                    message=f"Issue #{issue_iid} 处理失败: {error}",
                    event_type="issue",
                    issue_iid=issue_iid,
                    issue_title=issue.get("title"),
                    project_path=project.path_with_namespace,
                )

            return WebhookResponse(
                status="success",
                message=f"Issue #{issue_iid} 处理完成，执行路径: {' → '.join(executed_nodes)}",
                event_type="issue",
                issue_iid=issue_iid,
                issue_title=issue.get("title"),
                project_path=project.path_with_namespace,
            )

        except Exception as e:
            logger.error(f"Workflow执行失败: {e}", exc_info=True)
            return WebhookResponse(
                status="error",
                message=f"Issue #{issue_iid} 处理失败: {str(e)}",
                event_type="issue",
                issue_iid=issue_iid,
                issue_title=issue.get("title"),
                project_path=project.path_with_namespace,
            )
        finally:
            # Sandbox 清理由 SandboxTeardown 节点统一管理
            # 这里只做兜底检查：如果 workflow 未正常完成且 sandbox 未被销毁
            sandbox_info = result.get("sandbox", {})
            sandbox_id = sandbox_info.get("sandbox_id")
            teardown_status = sandbox_info.get("teardown_status")
            
            # 只有当 workflow 未走到 teardown 时才兜底清理
            if sandbox_id and not teardown_status:
                try:
                    logger.warning(f"Workflow 未正常完成 teardown，兜底销毁沙箱: {sandbox_id}")
                    sandbox_manager = get_sandbox_manager()
                    await sandbox_manager.destroy_sandbox(sandbox_id)
                except Exception as cleanup_error:
                    logger.error(f"兜底销毁沙箱 {sandbox_id} 失败: {cleanup_error}")

    async def _process_note_event(
        self,
        payload: GitLabWebhookPayload,
    ) -> WebhookResponse:
        """
        处理 Note (评论) 事件

        Args:
            payload: GitLab webhook 载荷

        Returns:
            处理响应
        """
        note = payload.object_attributes
        project = payload.project
        user = payload.user

        logger.info(f"处理 Note 类型: {note.get('noteable_type')}")
        logger.info(f"评论内容: {note.get('note', '')}")
        logger.info(f"项目: {project.path_with_namespace}")

        # 如果是 Issue 的评论，记录 Issue 信息
        if note.get("noteable_type") == "Issue" and payload.issue:
            issue = payload.issue
            logger.info(f"关联 Issue: #{issue.get('iid')} - {issue.get('title')}")

        # TODO: 后续可以通过评论触发特定操作
        # 例如：评论 "/fix" 触发自动修复

        return WebhookResponse(
            status="success",
            message=f"Note 事件已接收，暂不处理",
            event_type="note",
            project_path=project.path_with_namespace,
        )

    async def _process_merge_request_event(
        self,
        payload: GitLabWebhookPayload,
    ) -> WebhookResponse:
        """
        处理 Merge Request 事件

        Args:
            payload: GitLab webhook 载荷

        Returns:
            处理响应
        """
        mr = payload.object_attributes
        project = payload.project
        mr_state = mr.get("state")
        mr_iid = mr.get("iid")

        logger.info(
            f"处理 Merge Request 事件: "
            f"!{mr_iid} - {mr.get('title')} "
            f"(状态: {mr_state})"
        )

        logger.info(f"项目: {project.path_with_namespace}")

        # 过滤：只处理 merged 状态的 MR
        if mr_state != "merged":
            logger.info(f"MR 状态为 {mr_state}，跳过处理")
            return WebhookResponse(
                status="success",
                message=f"MR !{mr_iid} 状态为 {mr_state}，无需处理",
                event_type="merge_request",
                project_path=project.path_with_namespace,
            )

        # 生成并设置 trace_id
        trace_id = generate_trace_id()
        set_trace_id(trace_id)

        logger.info(
            f"启动 Merge Workflow 处理 MR !{mr_iid}",
            extra={"trace_id": trace_id, "project_id": project.id},
        )

        workflow_start = time.time()
        result = {}  # 初始化 result，以便在 finally 中访问
        try:
            from app.graph.workflows.merge_workflow import create_merge_workflow
            from app.graph.state import init_state_defaults

            # 构造初始状态（注入 trace_id 和 MR 信息）
            initial_state = {
                "project_info": project.model_dump(),
                # 初始化所有分域
                "sandbox": {},
                "merge": {
                    "mr_iid": mr_iid,
                    "mr_id": mr.get("id"),
                    "target_branch": mr.get("target_branch"),
                    "source_branch": mr.get("source_branch"),
                    "merge_commit_sha": mr.get("merge_commit_sha"),
                    "changed_files": [],
                    "indexed_files": [],
                    "failed_files": [],
                },
                "runtime": {
                    "trace_id": trace_id,  # 注入 trace_id
                    "executed_nodes": [],
                    "current_step": "init",
                    "error": None,
                    "completed": False,
                },
            }

            # 初始化默认值
            initial_state = init_state_defaults(initial_state)

            # 创建并执行工作流
            workflow = create_merge_workflow()
            result = await workflow.ainvoke(
                initial_state,
                config={
                    "recursion_limit": 50,
                    "configurable": {"thread_id": trace_id},  # 关联 Opik thread_id
                    "metadata": {
                        "mr_iid": mr_iid,
                        "project_path": project.path_with_namespace,
                        "mr_title": mr.get("title", ""),
                    },
                },
            )

            # 检查执行结果
            runtime = result.get("runtime", {})
            executed_nodes = runtime.get("executed_nodes", [])
            error = runtime.get("error")

            # 记录 workflow 总结指标
            workflow_duration = (time.time() - workflow_start) * 1000
            metrics_collector.log_workflow_summary(
                issue_iid=mr_iid,  # 复用 issue_iid 字段记录 mr_iid
                project_path=project.path_with_namespace,
                total_duration_ms=workflow_duration,
                executed_nodes=executed_nodes,
                success=error is None,
                error=error,
            )

            logger.info(
                f"Merge Workflow 完成 | "
                f"执行路径: {' → '.join(executed_nodes)} | "
                f"错误: {error or 'None'} | "
                f"耗时: {workflow_duration:.2f}ms"
            )

            # 获取索引结果
            merge_info = result.get("merge", {})
            indexed_files = merge_info.get("indexed_files", [])
            failed_files = merge_info.get("failed_files", [])

            if error:
                return WebhookResponse(
                    status="error",
                    message=f"MR !{mr_iid} 处理失败: {error}",
                    event_type="merge_request",
                    project_path=project.path_with_namespace,
                )

            return WebhookResponse(
                status="success",
                message=f"MR !{mr_iid} 处理完成，索引更新: 成功={len(indexed_files)}, 失败={len(failed_files)}",
                event_type="merge_request",
                project_path=project.path_with_namespace,
            )

        except Exception as e:
            logger.error(f"Merge Workflow 执行失败: {e}", exc_info=True)
            return WebhookResponse(
                status="error",
                message=f"MR !{mr_iid} 处理失败: {str(e)}",
                event_type="merge_request",
                project_path=project.path_with_namespace,
            )
        finally:
            # Sandbox 清理由 SandboxTeardown 节点统一管理
            # 这里只做兜底检查：如果 workflow 未正常完成且 sandbox 未被销毁
            sandbox_info = result.get("sandbox", {})
            sandbox_id = sandbox_info.get("sandbox_id")
            teardown_status = sandbox_info.get("teardown_status")

            # 只有当 workflow 未走到 teardown 时才兜底清理
            if sandbox_id and not teardown_status:
                try:
                    logger.warning(
                        f"Merge Workflow 未正常完成 teardown，兜底销毁沙箱: {sandbox_id}"
                    )
                    sandbox_manager = get_sandbox_manager()
                    await sandbox_manager.destroy_sandbox(sandbox_id)
                except Exception as cleanup_error:
                    logger.error(f"兜底销毁沙箱 {sandbox_id} 失败: {cleanup_error}")


# 全局服务实例
webhook_service = WebhookService()
