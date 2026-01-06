"""
Patch Generator Agent Node - 补丁生成节点
基于可编辑上下文切片生成代码补丁
"""

import difflib
import json
from typing import Literal, Optional

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command

from app.core.logger_config import logger
from app.core.prompt_manager import prompt_manager
from app.graph.state import IssueProcessState, NodeName, ProcessStage
from app.llms.llm_factory import get_gpt_model
from app.schemas.context_assembly import (
    EditableContextSlice,
    PatchResult,
    TargetContext,
    TargetStatus,
)
from app.utils.common_function import parse_json_response


class PatchGeneratorAgentNode:
    """补丁生成 Agent 节点"""

    def __init__(self):
        """初始化节点"""
        self.prompt_manager = prompt_manager

    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal[NodeName.PLAN.value]]:
        """
        基于可编辑上下文生成补丁

        Args:
            state: 当前工作流状态

        Returns:
            Command对象，返回 plan 节点
        """
        update_dict = {}

        try:
            # 发送进度事件
            await adispatch_custom_event(
                ProcessStage.PATCH_GENERATION.value,
                {
                    "status": NodeName.PATCH_GENERATOR.value,
                    "progress": "正在生成代码补丁...",
                    "think_chain_item": {
                        "type": NodeName.PATCH_GENERATOR.value,
                        "title": "补丁生成",
                        "desc": "基于上下文生成代码修改",
                        "urls": [],
                    },
                },
            )

            # 获取上下文
            editable_context_dict = state.get("editable_context")
            if not editable_context_dict:
                logger.error("没有可编辑上下文")
                update_dict.update(
                    {
                        "error": "没有可编辑上下文，无法生成补丁",
                        "executed_nodes": [
                            *state.get("executed_nodes", []),
                            NodeName.PATCH_GENERATOR.value,
                        ],
                        "current_step": NodeName.PATCH_GENERATOR.value,
                    }
                )
                return Command(update=update_dict, goto=NodeName.END.value)

            editable_context = EditableContextSlice(**editable_context_dict)

            # 生成补丁
            patch_result = await self._generate_patch(state, editable_context)

            if patch_result is None:
                logger.error("无法生成补丁")
                update_dict.update(
                    {
                        "error": "无法生成补丁",
                        "executed_nodes": [
                            *state.get("executed_nodes", []),
                            NodeName.PATCH_GENERATOR.value,
                        ],
                        "current_step": NodeName.PATCH_GENERATOR.value,
                    }
                )
                return Command(update=update_dict, goto=NodeName.END.value)

            # 更新已生成的补丁
            existing_patches = state.get("generated_patches", {})
            existing_patches[patch_result.file_path] = patch_result.unified_diff

            # 更新当前目标状态
            current_target_dict = state.get("current_target", {})
            current_target_dict["status"] = TargetStatus.COMPLETED.value

            update_dict.update(
                {
                    "generated_patches": existing_patches,
                    "current_patch": patch_result.unified_diff,
                    "current_modified_code": patch_result.modified_code,
                    "current_target": current_target_dict,
                    "executed_nodes": [
                        *state.get("executed_nodes", []),
                        NodeName.PATCH_GENERATOR.value,
                    ],
                    "current_step": NodeName.PATCH_GENERATOR.value,
                }
            )

            logger.info(
                f"补丁生成完成: {patch_result.file_path}, "
                f"置信度: {patch_result.confidence:.2f}"
            )

            # 发送完成事件
            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": NodeName.PATCH_GENERATOR.value,
                    "progress": "补丁生成完成",
                    "think_chain_item": {
                        "type": NodeName.PATCH_GENERATOR.value,
                        "title": "补丁生成",
                        "desc": f"文件: {patch_result.file_path}, "
                               f"{patch_result.change_summary}",
                        "urls": [],
                    },
                },
            )

            return Command(update=update_dict, goto=NodeName.PLAN.value)

        except Exception as e:
            logger.error(f"补丁生成失败: {e}", exc_info=True)
            update_dict.update(
                {
                    "error": f"补丁生成失败: {str(e)}",
                    "executed_nodes": [
                        *state.get("executed_nodes", []),
                        NodeName.PATCH_GENERATOR.value,
                    ],
                    "current_step": NodeName.PATCH_GENERATOR.value,
                }
            )

            return Command(update=update_dict, goto=NodeName.END.value)

    async def _generate_patch(
        self,
        state: IssueProcessState,
        context: EditableContextSlice
    ) -> Optional[PatchResult]:
        """
        生成补丁

        Args:
            state: 当前工作流状态
            context: 可编辑上下文切片

        Returns:
            补丁结果
        """
        issue_data = state.get("issue_data", {})
        issue_title = issue_data.get("title", "")
        issue_description = issue_data.get("description", "")

        try:
            # 构建 Prompt
            prompt = self._build_patch_prompt(
                issue_title,
                issue_description,
                context
            )

            llm = await get_gpt_model(temperature=0.2)
            response = await llm.ainvoke(prompt)
            result = parse_json_response(response.content)

            modified_code = result.get("modified_code", "")
            if not modified_code:
                logger.warning("LLM 未返回修改后的代码")
                return None

            # 生成 unified diff
            unified_diff = self._generate_unified_diff(
                context.full_code,
                modified_code,
                context.target.file_path
            )

            return PatchResult(
                file_path=context.target.file_path,
                original_code=context.full_code,
                modified_code=modified_code,
                unified_diff=unified_diff,
                change_summary=result.get("change_summary", ""),
                confidence=result.get("confidence", 0.8),
            )

        except Exception as e:
            logger.error(f"生成补丁失败: {e}", exc_info=True)
            return None

    def _build_patch_prompt(
        self,
        issue_title: str,
        issue_description: str,
        context: EditableContextSlice
    ) -> str:
        """
        构建补丁生成 Prompt

        Args:
            issue_title: Issue 标题
            issue_description: Issue 描述
            context: 可编辑上下文切片

        Returns:
            Prompt 字符串
        """
        # 格式化依赖签名
        deps_str = ""
        for sig in context.dependency_signatures:
            deps_str += f"\n### {sig.symbol_name} ({sig.file_path})\n"
            deps_str += f"```python\n{sig.signature}\n```\n"
            if sig.docstring:
                deps_str += f"Docstring: {sig.docstring}\n"

        # 格式化导入
        imports_str = "\n".join(context.imports) if context.imports else "无"

        # 格式化 Schema
        schemas_str = "\n\n".join(context.schema_definitions) if context.schema_definitions else "无"

        # 渲染 Prompt
        prompt = self.prompt_manager.render(
            "patch_generation",
            issue_title=issue_title,
            issue_description=issue_description or "无描述",
            target_symbol=context.target.symbol_name,
            target_file=context.target.file_path,
            target_type=context.target.symbol_type,
            editable_code=context.full_code,
            editable_start_line=context.editable_start_line,
            editable_end_line=context.editable_end_line,
            dependency_signatures=deps_str or "无依赖",
            imports=imports_str,
            schema_definitions=schemas_str,
        )

        return prompt

    def _generate_unified_diff(
        self,
        original: str,
        modified: str,
        file_path: str
    ) -> str:
        """
        生成 unified diff 格式的补丁

        Args:
            original: 原始代码
            modified: 修改后的代码
            file_path: 文件路径

        Returns:
            Unified diff 字符串
        """
        original_lines = original.splitlines(keepends=True)
        modified_lines = modified.splitlines(keepends=True)

        # 确保最后一行有换行符
        if original_lines and not original_lines[-1].endswith("\n"):
            original_lines[-1] += "\n"
        if modified_lines and not modified_lines[-1].endswith("\n"):
            modified_lines[-1] += "\n"

        diff = difflib.unified_diff(
            original_lines,
            modified_lines,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            lineterm=""
        )

        return "".join(diff)

