"""
Queue Manager Node - 队列管理节点

管理涟漪递归的文件任务队列：
- 去重（避免重复处理同一文件）
- 每次从队列头取出 N=5 个任务
- 决定是否继续处理或进入最终验证
"""

from typing import Literal

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command

from app.core.logger_config import logger
from app.graph.state import IssueProcessState, NodeName, ProcessStage


class QueueManagerNode:
    """
    队列管理节点
    
    职责：
    - 检查 pending_file_tasks 是否为空
    - 检查是否达到最大迭代次数
    - 从队列头取出 N 个任务（默认5个）
    - 去重（基于 seen_files）
    - 决定下一步：batch_context_builder 或 final_verification
    """

    def __init__(self, batch_size: int = 5):
        """
        初始化队列管理器
        
        Args:
            batch_size: 每批处理的文件任务数量
        """
        self.batch_size = batch_size

    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal["batch_context_builder", "verification_flow", "sandbox_teardown"]]:
        """
        队列管理决策
        
        Args:
            state: 当前工作流状态
        
        Returns:
            Command对象，继续处理返回 batch_context_builder，
            队列清空返回 verification_flow，
            达到上限或出错返回 sandbox_teardown
        """
        update_dict = {}

        try:
            # 发送进度事件
            await adispatch_custom_event(
                ProcessStage.IMPACT_ANALYSIS.value,
                {
                    "status": NodeName.QUEUE_MANAGER.value,
                    "progress": "正在管理任务队列...",
                    "think_chain_item": {
                        "type": NodeName.QUEUE_MANAGER.value,
                        "title": "队列管理",
                        "desc": "决定下一批处理任务",
                        "urls": [],
                    },
                },
            )

            # 获取 ripple 信息
            ripple = state.get("ripple", {})
            pending_tasks = ripple.get("pending_file_tasks", [])
            seen_files = ripple.get("seen_files", [])
            iteration = ripple.get("iteration", 0)
            max_iterations = ripple.get("max_iterations", 10)

            runtime = state.get("runtime", {})

            # 检查是否达到最大迭代次数
            if iteration >= max_iterations:
                logger.warning(f"达到最大迭代次数 {max_iterations}，停止处理")
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.QUEUE_MANAGER.value,
                            ],
                            "current_step": NodeName.QUEUE_MANAGER.value,
                        },
                    }
                )
                
                await adispatch_custom_event(
                    ProcessStage.THINK_CHAIN.value,
                    {
                        "status": NodeName.QUEUE_MANAGER.value,
                        "progress": f"达到最大迭代次数 {max_iterations}",
                        "think_chain_item": {
                            "type": NodeName.QUEUE_MANAGER.value,
                            "title": "队列管理",
                            "desc": "达到迭代上限，进入最终验证",
                            "urls": [],
                        },
                    },
                )
                
                return Command(update=update_dict, goto=NodeName.VERIFICATION_FLOW.value)

            # 检查队列是否为空
            if not pending_tasks:
                logger.info("队列为空，进入最终验证")
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.QUEUE_MANAGER.value,
                            ],
                            "current_step": NodeName.QUEUE_MANAGER.value,
                        },
                    }
                )
                
                await adispatch_custom_event(
                    ProcessStage.THINK_CHAIN.value,
                    {
                        "status": NodeName.QUEUE_MANAGER.value,
                        "progress": "队列为空，进入最终验证",
                        "think_chain_item": {
                            "type": NodeName.QUEUE_MANAGER.value,
                            "title": "队列管理",
                            "desc": "所有任务处理完成",
                            "urls": [],
                        },
                    },
                )
                
                return Command(update=update_dict, goto=NodeName.VERIFICATION_FLOW.value)

            # 从队列头取出 N 个任务（去重）
            batch = []
            remaining = []
            
            for task in pending_tasks:
                file_path = task.get("file_path", "")
                
                # 去重：跳过已处理的文件
                if file_path in seen_files:
                    logger.debug(f"文件已处理，跳过: {file_path}")
                    continue
                
                if len(batch) < self.batch_size:
                    batch.append(task)
                    seen_files.append(file_path)
                else:
                    remaining.append(task)

            # 如果去重后没有可处理的任务，进入最终验证
            if not batch:
                logger.info("去重后队列为空，进入最终验证")
                update_dict.update(
                    {
                        "ripple": {
                            **ripple,
                            "pending_file_tasks": [],
                            "inflight_batch": [],
                        },
                        "runtime": {
                            **runtime,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.QUEUE_MANAGER.value,
                            ],
                            "current_step": NodeName.QUEUE_MANAGER.value,
                        },
                    }
                )
                
                return Command(update=update_dict, goto=NodeName.VERIFICATION_FLOW.value)

            # 更新状态
            logger.info(
                f"从队列取出 {len(batch)} 个任务 (iteration={iteration + 1}/{max_iterations}), "
                f"剩余 {len(remaining)} 个"
            )

            update_dict.update(
                {
                    "ripple": {
                        **ripple,
                        "pending_file_tasks": remaining,
                        "inflight_batch": batch,
                        "seen_files": seen_files,
                        "iteration": iteration + 1,
                    },
                    "runtime": {
                        **runtime,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.QUEUE_MANAGER.value,
                        ],
                        "current_step": NodeName.QUEUE_MANAGER.value,
                    },
                }
            )

            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": NodeName.QUEUE_MANAGER.value,
                    "progress": f"取出 {len(batch)} 个任务 (iteration {iteration + 1})",
                    "think_chain_item": {
                        "type": NodeName.QUEUE_MANAGER.value,
                        "title": "队列管理",
                        "desc": f"批次大小: {len(batch)}, 剩余: {len(remaining)}",
                        "urls": [],
                    },
                },
            )

            return Command(update=update_dict, goto=NodeName.BATCH_CONTEXT_BUILDER.value)

        except Exception as e:
            logger.error(f"队列管理异常: {e}", exc_info=True)
            runtime = state.get("runtime", {})
            update_dict.update(
                {
                    "runtime": {
                        **runtime,
                        "error": f"队列管理错误: {str(e)}",
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.QUEUE_MANAGER.value,
                        ],
                        "current_step": NodeName.QUEUE_MANAGER.value,
                    },
                }
            )
            
            return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)


# 导出
__all__ = ["QueueManagerNode"]

