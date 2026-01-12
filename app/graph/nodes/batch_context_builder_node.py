"""
Batch Context Builder Node - 批量上下文构建节点

为当前批次（inflight_batch）的文件任务收集上下文：
- 从 sandbox 读取最新文件源码
- 提取相关符号定义（使用 AST）
- 分析依赖签名
"""

from typing import Literal

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command

from app.core.logger_config import logger
from app.graph.state import IssueProcessState, NodeName, ProcessStage
from app.utils.dependency_analyzer import DependencyAnalyzer
from app.utils.tree_sitter_service import tree_sitter_service
from app.sandbox.file_service import FileService
from app.sandbox.manager import get_sandbox_manager


class BatchContextBuilderNode:
    """
    批量上下文构建节点
    
    职责：
    - 为 inflight_batch 中的每个文件任务收集上下文
    - 从 sandbox 读取最新文件内容（已应用之前的 patch）
    - 使用 AST 提取相关符号定义
    - 分析依赖关系
    - 构建批量上下文并存入 state
    """

    def __init__(self):
        """初始化节点"""
        self.tree_sitter = tree_sitter_service
        self.dependency_analyzer = DependencyAnalyzer()
        self.sandbox_manager = get_sandbox_manager()

    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal["refactoring_agent_batch", "sandbox_teardown"]]:
        """
        构建批量上下文
        
        Args:
            state: 当前工作流状态
        
        Returns:
            Command对象，成功返回 refactoring_agent_batch，失败返回 sandbox_teardown
        """
        update_dict = {}

        try:
            # 发送进度事件
            await adispatch_custom_event(
                ProcessStage.CONTEXT_BUILDING.value,
                {
                    "status": NodeName.BATCH_CONTEXT_BUILDER.value,
                    "progress": "正在构建批量上下文...",
                    "think_chain_item": {
                        "type": NodeName.BATCH_CONTEXT_BUILDER.value,
                        "title": "批量上下文构建",
                        "desc": "收集文件源码和符号定义",
                        "urls": [],
                    },
                },
            )

            # 获取 sandbox 信息
            sandbox = state.get("sandbox", {})
            sandbox_id = sandbox.get("sandbox_id")
            
            if not sandbox_id:
                error_msg = "缺少 sandbox_id，无法构建上下文"
                logger.error(error_msg)
                runtime = state.get("runtime", {})
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "error": error_msg,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.BATCH_CONTEXT_BUILDER.value,
                            ],
                            "current_step": NodeName.BATCH_CONTEXT_BUILDER.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

            # 获取当前批次
            ripple = state.get("ripple", {})
            inflight_batch = ripple.get("inflight_batch", [])
            
            if not inflight_batch:
                error_msg = "inflight_batch 为空，无法构建上下文"
                logger.error(error_msg)
                runtime = state.get("runtime", {})
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "error": error_msg,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.BATCH_CONTEXT_BUILDER.value,
                            ],
                            "current_step": NodeName.BATCH_CONTEXT_BUILDER.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

            # 构建批量上下文
            batch_contexts = await self._build_batch_contexts(
                sandbox_id,
                inflight_batch,
                state
            )

            if not batch_contexts:
                error_msg = "无法构建批量上下文"
                logger.error(error_msg)
                runtime = state.get("runtime", {})
                update_dict.update(
                    {
                        "runtime": {
                            **runtime,
                            "error": error_msg,
                            "executed_nodes": [
                                *runtime.get("executed_nodes", []),
                                NodeName.BATCH_CONTEXT_BUILDER.value,
                            ],
                            "current_step": NodeName.BATCH_CONTEXT_BUILDER.value,
                        },
                    }
                )
                return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

            logger.info(f"批量上下文构建完成: {len(batch_contexts)} 个文件")

            # 更新状态（将批量上下文存入 context 域）
            runtime = state.get("runtime", {})
            context = state.get("context", {})
            
            update_dict.update(
                {
                    "context": {
                        **context,
                        "batch_contexts": batch_contexts,  # 新增字段
                    },
                    "runtime": {
                        **runtime,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.BATCH_CONTEXT_BUILDER.value,
                        ],
                        "current_step": NodeName.BATCH_CONTEXT_BUILDER.value,
                    },
                }
            )

            # 发送完成事件
            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": NodeName.BATCH_CONTEXT_BUILDER.value,
                    "progress": f"批量上下文构建完成 ({len(batch_contexts)} 个文件)",
                    "think_chain_item": {
                        "type": NodeName.BATCH_CONTEXT_BUILDER.value,
                        "title": "批量上下文构建",
                        "desc": f"收集了 {len(batch_contexts)} 个文件的上下文",
                        "urls": [],
                    },
                },
            )

            return Command(update=update_dict, goto=NodeName.REFACTORING_AGENT_BATCH.value)

        except Exception as e:
            logger.error(f"批量上下文构建失败: {e}", exc_info=True)
            runtime = state.get("runtime", {})
            update_dict.update(
                {
                    "runtime": {
                        **runtime,
                        "error": f"批量上下文构建失败: {str(e)}",
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.BATCH_CONTEXT_BUILDER.value,
                        ],
                        "current_step": NodeName.BATCH_CONTEXT_BUILDER.value,
                    },
                }
            )

            return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

    async def _build_batch_contexts(
        self,
        sandbox_id: str,
        inflight_batch: list[dict],
        state: IssueProcessState
    ) -> list[dict]:
        """
        为批次中的每个文件构建上下文
        
        Returns:
            批量上下文列表，每项包含 file_path, file_content, symbols, dependencies 等
        """
        batch_contexts = []

        try:
            file_service = FileService(self.sandbox_manager, sandbox_id)

            for task in inflight_batch:
                file_path = task.get("file_path", "")
                symbols = task.get("symbols", [])
                reasons = task.get("reasons", [])

                if not file_path:
                    continue

                # 从 sandbox 读取最新文件内容
                try:
                    file_content = await file_service.read_file(file_path)
                except Exception as e:
                    logger.warning(f"无法读取文件 {file_path}: {e}")
                    continue

                # 使用 AST 提取符号定义
                # TODO: 根据文件扩展名判断语言
                ast_info = self.tree_sitter.parse_code(
                    file_content,
                    language="python",
                    file_path=file_path
                )

                # 提取相关符号的定义
                symbol_definitions = []
                if ast_info and symbols:
                    for symbol_name in symbols:
                        # 查找符号定义
                        for sym in ast_info.symbols:
                            if sym.name == symbol_name:
                                symbol_definitions.append({
                                    "name": sym.name,
                                    "type": sym.type,
                                    "start_line": sym.start_line,
                                    "end_line": sym.end_line,
                                })
                                break

                # 构建上下文
                context_item = {
                    "file_path": file_path,
                    "file_content": file_content,
                    "symbols": symbols,
                    "symbol_definitions": symbol_definitions,
                    "reasons": reasons,
                    "task": task,  # 保留原始任务信息
                }

                batch_contexts.append(context_item)

            return batch_contexts

        except Exception as e:
            logger.error(f"构建批量上下文异常: {e}", exc_info=True)
            return []


# 导出
__all__ = ["BatchContextBuilderNode"]

