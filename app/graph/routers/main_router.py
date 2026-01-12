"""
MainRouter - 确定性路由器

基于 State completeness 的确定性路由，替代原 Plan 节点的 LLM 调度。
只在状态齐备性判断，不使用 LLM（除非在 fallback 场景）。
"""

from typing import Literal

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command

from app.config.app_config import app_config
from app.core.logger_config import logger
from app.graph.state import IssueProcessState, NodeName, ProcessStage


class MainRouterNode:
    """
    主路由器节点 - 确定性路由逻辑

    不使用 LLM，基于 state 字段完整性来判断下一步。
    """

    def __init__(self, max_patch_retries: int = 3):
        """
        初始化主路由器

        Args:
            max_patch_retries: 补丁重试最大次数
        """
        self.max_patch_retries = max_patch_retries

    async def __call__(
        self, state: IssueProcessState
    ) -> Command[
        Literal[
            # 分析与检索节点
            "issue_analyst",
            "code_retriever",
            "entry_selector",
            "context_slice_builder",
            # 补丁相关
            "patch_flow",
            # Ripple Loop
            "global_impact_scan",
            # 验证相关
            "verification_flow",
            "refine_agent",
            # 评审与提交
            "reviewer",
            "mr_submitter",
            # 生命周期
            "sandbox_teardown",
            "__end__",
        ]
    ]:
        """
        主路由决策逻辑

        Args:
            state: 当前状态

        Returns:
            Command 对象，指定下一个节点
        """
        update_dict = {}

        try:
            await adispatch_custom_event(
                ProcessStage.ROUTING.value,
                {
                    "status": NodeName.MAIN_ROUTER.value,
                    "progress": "确定性路由中...",
                    "think_chain_item": {
                        "type": NodeName.MAIN_ROUTER.value,
                        "title": "路由决策",
                        "desc": "基于状态完整性确定下一步",
                        "urls": [],
                    },
                },
            )

            # 获取各个域的信息
            runtime = state.get("runtime", {})
            sandbox = state.get("sandbox", {})
            analysis = state.get("analysis", {})
            retrieval = state.get("retrieval", {})
            targeting = state.get("targeting", {})
            context = state.get("context", {})
            patching = state.get("patching", {})
            verification = state.get("verification", {})
            review = state.get("review", {})
            delivery = state.get("delivery", {})

            # 1. 检查错误或完成状态
            if runtime.get("error") or runtime.get("completed"):
                reason = "检测到错误或已完成标记" if runtime.get("error") else "流程已标记完成"
                logger.info(f"MainRouter: {reason} → SandboxTeardown")
                update_dict["runtime"] = {
                    **runtime,
                    "current_step": "准备清理沙箱",
                }
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

            # 2. 检查 Sandbox 信息
            if not sandbox.get("sandbox_id"):
                error_msg = "Sandbox 信息缺失，流程异常"
                logger.error(f"MainRouter: {error_msg}")
                update_dict["runtime"] = {
                    **runtime,
                    "error": error_msg,
                    "current_step": "Sandbox 信息缺失",
                }
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

            # 3. 检查 Analysis（issue_type, search_queries）
            if not analysis.get("issue_type") or not analysis.get("search_queries"):
                logger.info("MainRouter: 缺少 Issue 分析结果 → IssueAnalyst")
                update_dict["runtime"] = {
                    **runtime,
                    "current_step": "Issue 分析",
                }
                return Command(update=update_dict, goto=NodeName.ISSUE_ANALYST.value)

            # 4. 检查 Retrieval（retrieved_code）
            retrieved_code = retrieval.get("retrieved_code", [])
            if not retrieved_code:
                logger.info("MainRouter: 缺少代码检索结果 → CodeRetriever")
                update_dict["runtime"] = {
                    **runtime,
                    "current_step": "代码检索",
                }
                return Command(update=update_dict, goto=NodeName.CODE_RETRIEVER.value)

            # 5. 检查 Targeting（current_target）
            current_target = targeting.get("current_target")

            if not current_target:
                logger.info("MainRouter: 缺少切入点目标 → EntrySelector")
                update_dict["runtime"] = {
                    **runtime,
                    "current_step": "选择切入点",
                }
                return Command(update=update_dict, goto=NodeName.ENTRY_SELECTOR.value)

            # 6. 检查 Context（editable_context）
            editable_context = context.get("editable_context")
            if not editable_context:
                logger.info("MainRouter: 缺少可编辑上下文切片 → ContextSliceBuilder")
                update_dict["runtime"] = {
                    **runtime,
                    "current_step": "构建上下文切片",
                }
                return Command(update=update_dict, goto=NodeName.CONTEXT_SLICE_BUILDER.value)

            # 7. 检查 Patching（current_patch）
            current_patch = patching.get("current_patch")
            if not current_patch:
                logger.info("MainRouter: 缺少补丁 → PatchFlow")
                update_dict["runtime"] = {
                    **runtime,
                    "current_step": "生成补丁",
                }
                return Command(update=update_dict, goto=NodeName.PATCH_FLOW.value)

            # 8. 检查 Verification（final_verification）
            # 优先检查是否已有验证结果，避免重复扫描
            final_verification = verification.get("final_verification")
            
            # 8.1 如果有验证结果，优先处理验证结果（重试或完成）
            if final_verification:
                # 跳到步骤 11 处理验证结果
                pass  # 继续执行下面的验证结果处理逻辑
            else:
                # 8.2 没有验证结果，检查是否需要全局扫描
                ripple = state.get("ripple", {})
                pending_file_tasks = ripple.get("pending_file_tasks", None)
                iteration = ripple.get("iteration", 0)
                
                # 如果队列未初始化(None)，说明还没进行全局扫描
                if pending_file_tasks is None:
                    # 还未进行全局扫描，进入 GlobalImpactScan
                    logger.info("MainRouter: 补丁已生成，进入全局影响扫描")
                    update_dict["runtime"] = {
                        **runtime,
                        "current_step": "全局影响扫描",
                    }
                    return Command(update=update_dict, goto=NodeName.GLOBAL_IMPACT_SCAN.value)
                
                # 8.3 队列已初始化但没有验证结果，需要触发验证
                logger.info("MainRouter: 队列已处理，等待验证结果 → QueueManager")
                update_dict["runtime"] = {
                    **runtime,
                    "current_step": "队列管理",
                }
                return Command(update=update_dict, goto=NodeName.QUEUE_MANAGER.value)

            # 11. 检查验证是否通过
            verification_passed = final_verification.get("passed", False)
            error_count = final_verification.get("error_count", 0)
            warning_count = final_verification.get("warning_count", 0)
            refine_retry_count = verification.get("refine_retry_count", 0)
            
            # 从配置中读取最大重试次数
            max_refine_retry = app_config.workflow.max_refine_retry_count
            
            if not verification_passed:
                # 有 error 需要修复
                if refine_retry_count < max_refine_retry:
                    logger.info(
                        f"MainRouter: 验证失败（{error_count} 个 errors），"
                        f"进入修复循环 ({refine_retry_count + 1}/{max_refine_retry}) → RefineAgent"
                    )
                    update_dict["runtime"] = {
                        **runtime,
                        "current_step": f"批量修复错误（第 {refine_retry_count + 1} 轮）",
                    }
                    return Command(update=update_dict, goto=NodeName.REFINE_AGENT.value)
                else:
                    error_msg = f"验证失败（{error_count} 个 errors）且已达到最大修复次数 ({max_refine_retry})"
                    logger.error(f"MainRouter: {error_msg}")
                    update_dict["runtime"] = {
                        **runtime,
                        "error": error_msg,
                        "current_step": "验证失败，达到熔断上限",
                    }
                    return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)
            
            # passed=True（没有 error，只有 warning 或全通过）
            logger.info(
                f"MainRouter: 验证通过（{error_count} errors, {warning_count} warnings）→ 继续流程"
            )

            # 12. 检查 Review（review_report）
            review_report = review.get("review_report")
            if not review_report:
                logger.info("MainRouter: 缺少评审报告 → Reviewer")
                update_dict["runtime"] = {
                    **runtime,
                    "current_step": "代码评审",
                }
                return Command(update=update_dict, goto=NodeName.REVIEWER.value)

            # 13. 检查 Delivery（mr_url）
            mr_url = delivery.get("mr_url")
            if not mr_url:
                logger.info("MainRouter: 缺少 MR 提交结果 → MRSubmitter")
                update_dict["runtime"] = {
                    **runtime,
                    "current_step": "提交 MR",
                }
                return Command(update=update_dict, goto=NodeName.MR_SUBMITTER.value)

            # 14. 全部完成 → SandboxTeardown → END
            logger.info("MainRouter: 所有步骤完成 → SandboxTeardown")
            update_dict["runtime"] = {
                **runtime,
                "completed": True,
                "current_step": "流程完成，准备清理",
            }
            return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

        except Exception as e:
            logger.error(f"MainRouter 执行异常: {e}", exc_info=True)
            update_dict["runtime"] = {
                **state.get("runtime", {}),
                "error": f"MainRouter 错误: {str(e)}",
                "current_step": "路由异常",
            }
            return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

