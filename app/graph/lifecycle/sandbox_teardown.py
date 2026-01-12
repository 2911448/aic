"""
Sandbox Teardown Node - Sandbox 生命周期收尾节点
在流程结束或异常退出时统一销毁 sandbox
"""

from typing import Literal

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command

from app.core.logger_config import logger
from app.graph.state import IssueProcessState
from app.graph.state.node_names import NodeName, ProcessStage
from app.sandbox.exceptions import SandboxNotFoundError
from app.sandbox.manager import get_sandbox_manager


class SandboxTeardownNode:
    """Sandbox Teardown 节点 - 统一销毁 sandbox"""

    def __init__(self):
        """初始化节点"""
        self.sandbox_manager = get_sandbox_manager()

    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal["__end__"]]:
        """
        销毁 sandbox（容错）
        
        无论成功/失败/异常，都尝试销毁 sandbox
        即使销毁失败也不影响流程结束
        
        Args:
            state: 当前工作流状态
            
        Returns:
            Command 对象，始终 goto END
        """
        update_dict = {}
        
        try:
            # 发送进度事件
            await adispatch_custom_event(
                ProcessStage.SANDBOX_TEARDOWN.value,
                {
                    "status": NodeName.SANDBOX_TEARDOWN.value,
                    "progress": "正在清理 Sandbox...",
                    "think_chain_item": {
                        "type": NodeName.SANDBOX_TEARDOWN.value,
                        "title": "Sandbox 清理",
                        "desc": "销毁隔离环境",
                        "urls": [],
                    },
                },
            )

            # 获取 sandbox_id (从分域结构)
            sandbox = state.get("sandbox", {})
            sandbox_id = sandbox.get("sandbox_id")
            runtime = state.get("runtime", {})
            
            if not sandbox_id:
                logger.info("SandboxTeardown: 没有 sandbox_id，跳过清理")
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.SANDBOX_TEARDOWN.value,
                            ],
                            "current_step": NodeName.SANDBOX_TEARDOWN.value,
                            "completed": True,
                        },
                    }
                )
                
                await adispatch_custom_event(
                    ProcessStage.THINK_CHAIN.value,
                    {
                        "status": NodeName.SANDBOX_TEARDOWN.value,
                        "progress": "无需清理 Sandbox",
                        "think_chain_item": {
                            "type": NodeName.SANDBOX_TEARDOWN.value,
                            "title": "Sandbox 清理",
                            "desc": "无 Sandbox 需要清理",
                            "urls": [],
                        },
                    },
                )
                
                return Command(update=update_dict, goto=NodeName.END.value)

            # 尝试销毁 sandbox
            logger.info(f"SandboxTeardown: 开始销毁 Sandbox {sandbox_id}")
            
            try:
                await self.sandbox_manager.destroy_sandbox(sandbox_id)
                teardown_status = "success"
                teardown_message = f"Sandbox {sandbox_id} 已成功销毁"
                
            except SandboxNotFoundError:
                logger.warning(f"Sandbox {sandbox_id} 不存在，可能已被销毁")
                teardown_status = "already_gone"
                teardown_message = f"Sandbox {sandbox_id} 不存在"
                
            except Exception as e:
                logger.error(f"Sandbox 销毁失败: {e}", exc_info=True)
                teardown_status = "failed"
                teardown_message = f"Sandbox {sandbox_id} 销毁失败: {str(e)}"

            # 更新状态
            update_dict.update(
                {
                    "sandbox": {
                        **sandbox,
                        "teardown_status": teardown_status,
                        "teardown_message": teardown_message,
                    },
                    "runtime": {
                        **runtime,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.SANDBOX_TEARDOWN.value,
                        ],
                        "current_step": NodeName.SANDBOX_TEARDOWN.value,
                        "completed": True,
                    },
                }
            )

            # 发送完成事件
            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": NodeName.SANDBOX_TEARDOWN.value,
                    "progress": "Sandbox 清理完成",
                    "think_chain_item": {
                        "type": NodeName.SANDBOX_TEARDOWN.value,
                        "title": "Sandbox 清理",
                        "desc": teardown_message,
                        "urls": [],
                    },
                },
            )

            logger.info(f"SandboxTeardown 完成: {teardown_status}")
            
            # 无论销毁成功/失败，都进入 END
            return Command(update=update_dict, goto=NodeName.END.value)

        except Exception as e:
            # 即使 teardown 过程本身出错，也不应阻塞流程结束
            error_msg = f"SandboxTeardown 过程异常: {str(e)}"
            logger.error(error_msg, exc_info=True)
            
            runtime = state.get("runtime", {})
            sandbox = state.get("sandbox", {})
            update_dict.update(
                {
                    "sandbox": {
                        **sandbox,
                        "teardown_status": "exception",
                        "teardown_message": error_msg,
                    },
                    "runtime": {
                        **runtime,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.SANDBOX_TEARDOWN.value,
                        ],
                        "current_step": NodeName.SANDBOX_TEARDOWN.value,
                        "completed": True,
                    },
                }
            )
            
            # 仍然进入 END
            return Command(update=update_dict, goto=NodeName.END.value)
