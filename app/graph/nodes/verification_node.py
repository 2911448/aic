"""
Verification Flow Node - 全量静态检查验证节点（彻底重写版）

使用 mypy + ruff 对所有已应用补丁的文件进行全量静态类型检查。
只修复 error 级别问题，warning 不影响验证通过。
"""

import re
from typing import Literal, Optional

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.core.logger_config import logger
from app.graph.state import IssueProcessState, NodeName, ProcessStage
from app.sandbox.manager import get_sandbox_manager


class VerificationIssue(BaseModel):
    """验证问题项（统一格式）"""
    file_path: str = Field(description="文件路径")
    error_info: str = Field(description="错误详细信息")
    start_line: int = Field(description="错误起始行")
    end_line: Optional[int] = Field(None, description="错误结束行（多行错误时）")
    severity: Literal["error", "warning"] = Field(description="严重程度")
    error_code: Optional[str] = Field(None, description="错误代码，如 E501, arg-type")
    source: Literal["mypy", "ruff"] = Field(description="来源工具")


class VerificationResult(BaseModel):
    """验证结果（简化版）"""
    passed: bool = Field(description="是否通过验证（只统计 error）")
    all_issues: list[VerificationIssue] = Field(default=[], description="所有问题（含 warnings）")
    error_count: int = Field(description="error 数量")
    warning_count: int = Field(description="warning 数量")
    mypy_raw_output: str = Field(default="", description="mypy 原始输出")
    ruff_raw_output: str = Field(default="", description="ruff 原始输出")


class VerificationNode:
    """
    验证流程节点（全量静态检查）
    
    在 sandbox 中运行 mypy + ruff 检查所有已应用补丁的文件。
    只统计 error 级别问题，warning 不影响 passed 状态。
    """

    def __init__(self):
        """初始化节点"""
        self.sandbox_manager = get_sandbox_manager()

    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal["main_router", "sandbox_teardown"]]:
        """
        执行全量静态检查
        
        流程：
        1. 获取所有已应用补丁的文件列表（从 patching.generated_patches）
        2. 在 sandbox 中运行 mypy + ruff
        3. 解析输出，只统计 error 级别问题
        4. 更新 state.verification.final_verification
        5. 返回 main_router

        Args:
            state: 当前工作流状态

        Returns:
            Command对象，返回 main_router
        """
        update_dict = {}

        try:
            # 发送进度事件
            await adispatch_custom_event(
                ProcessStage.VERIFICATION.value,
                {
                    "status": NodeName.VERIFICATION_FLOW.value,
                    "progress": "正在执行全量静态检查...",
                    "think_chain_item": {
                        "type": NodeName.VERIFICATION_FLOW.value,
                        "title": "全量静态检查",
                        "desc": "运行 mypy + ruff 检查代码质量",
                        "urls": [],
                    },
                },
            )

            # 获取已应用补丁的文件列表
            patching = state.get("patching", {})
            generated_patches = patching.get("generated_patches", {})
            file_paths = list(generated_patches.keys())
            
            if not file_paths:
                logger.info("没有文件需要验证，直接通过")
                result = VerificationResult(
                    passed=True,
                    all_issues=[],
                    error_count=0,
                    warning_count=0,
                    mypy_raw_output="",
                    ruff_raw_output=""
                )
            else:
                # 运行全量检查
                result = await self._run_full_static_check(state, file_paths)
            
            # 更新状态
            runtime = state.get("runtime", {})
            verification = state.get("verification", {})
            
            update_dict.update(
                {
                    "verification": {
                        **verification,
                        "final_verification": result.model_dump(),
                    },
                    "runtime": {
                        **runtime,
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.VERIFICATION_FLOW.value,
                        ],
                        "current_step": NodeName.VERIFICATION_FLOW.value,
                    },
                }
            )

            passed = result.passed
            logger.info(
                f"验证完成: passed={passed}, "
                f"errors={result.error_count}, warnings={result.warning_count}"
            )

            # 发送完成事件
            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": NodeName.VERIFICATION_FLOW.value,
                    "progress": "验证完成",
                    "think_chain_item": {
                        "type": NodeName.VERIFICATION_FLOW.value,
                        "title": "全量静态检查",
                        "desc": f"结果: {'通过' if passed else '失败'} "
                                f"({result.error_count} errors, {result.warning_count} warnings)",
                        "urls": [],
                    },
                },
            )

            return Command(update=update_dict, goto=NodeName.MAIN_ROUTER.value)

        except Exception as e:
            logger.error(f"验证流程失败: {e}", exc_info=True)
            runtime = state.get("runtime", {})
            update_dict.update(
                {
                    "runtime": {
                        **runtime,
                        "error": f"验证流程失败: {str(e)}",
                        "executed_nodes": [
                            *runtime.get("executed_nodes", []),
                            NodeName.VERIFICATION_FLOW.value,
                        ],
                        "current_step": NodeName.VERIFICATION_FLOW.value,
                    },
                }
            )

            return Command(update=update_dict, goto=NodeName.SANDBOX_TEARDOWN.value)

    async def _run_full_static_check(
        self,
        state: IssueProcessState,
        file_paths: list[str]
    ) -> VerificationResult:
        """
        运行 mypy + ruff 全量检查
        
        Args:
            state: 当前状态
            file_paths: 要检查的文件路径列表
        
        Returns:
            VerificationResult: 验证结果
        """
        sandbox = state.get("sandbox", {})
        sandbox_id = sandbox.get("sandbox_id")
        
        if not sandbox_id:
            logger.error("缺少 sandbox_id，无法执行验证")
            return VerificationResult(
                passed=False,
                all_issues=[],
                error_count=1,
                warning_count=0,
                mypy_raw_output="",
                ruff_raw_output="Error: Missing sandbox_id"
            )
        
        # 1. 运行 mypy
        mypy_output, mypy_issues = await self._run_mypy(sandbox_id, file_paths)
        
        # 2. 运行 ruff
        ruff_output, ruff_issues = await self._run_ruff(sandbox_id, file_paths)
        
        # 3. 合并结果
        all_issues = mypy_issues + ruff_issues
        
        # 4. 统计 error 和 warning
        error_issues = [i for i in all_issues if i.severity == "error"]
        warning_issues = [i for i in all_issues if i.severity == "warning"]
        
        logger.info(
            f"静态检查完成: mypy={len(mypy_issues)}, ruff={len(ruff_issues)}, "
            f"total_errors={len(error_issues)}, total_warnings={len(warning_issues)}"
        )
        
        return VerificationResult(
            passed=(len(error_issues) == 0),
            all_issues=all_issues,
            error_count=len(error_issues),
            warning_count=len(warning_issues),
            mypy_raw_output=mypy_output,
            ruff_raw_output=ruff_output
        )

    async def _run_mypy(
        self,
        sandbox_id: str,
        file_paths: list[str]
    ) -> tuple[str, list[VerificationIssue]]:
        """
        运行 mypy 类型检查
        
        Returns:
            (raw_output, issues_list)
        """
        try:
            files_str = " ".join(file_paths)
            command = f"mypy {files_str} --ignore-missing-imports --follow-imports=silent --no-error-summary --show-column-numbers"
            
            result = await self.sandbox_manager.execute_command(
                sandbox_id=sandbox_id,
                command=command
            )
            
            stdout = result.stdout
            stderr = result.stderr
            output = stdout + stderr
            
            # 解析 mypy 输出
            issues = self._parse_mypy_output(output)
            
            return output, issues
            
        except Exception as e:
            logger.error(f"Mypy 执行失败: {e}", exc_info=True)
            return f"Error: {str(e)}", []

    async def _run_ruff(
        self,
        sandbox_id: str,
        file_paths: list[str]
    ) -> tuple[str, list[VerificationIssue]]:
        """
        运行 ruff 代码质量检查
        
        Returns:
            (raw_output, issues_list)
        """
        try:
            files_str = " ".join(file_paths)
            command = f"ruff check {files_str} --output-format=concise"
            
            result = await self.sandbox_manager.execute_command(
                sandbox_id=sandbox_id,
                command=command
            )
            
            stdout = result.stdout
            stderr = result.stderr
            output = stdout + stderr
            
            # 解析 ruff 输出
            issues = self._parse_ruff_output(output)
            
            return output, issues
            
        except Exception as e:
            logger.error(f"Ruff 执行失败: {e}", exc_info=True)
            return f"Error: {str(e)}", []

    def _parse_mypy_output(self, output: str) -> list[VerificationIssue]:
        """
        解析 mypy 输出
        """
        issues = []
        
        pattern = r'^(.+?):(\d+)(?::\d+)?\s*:\s*(error|warning):\s*(.+?)\s*\[([^\]]+)\]'
        
        for line in output.split('\n'):
            line = line.strip()
            if not line:
                continue
            
            match = re.match(pattern, line)
            if match:
                file_path = match.group(1)
                line_num = int(match.group(2))
                severity = match.group(3).lower()
                message = match.group(4).strip()
                error_code = match.group(5).strip()
                
                if severity in ["error", "warning"]:
                    issues.append(VerificationIssue(
                        file_path=file_path,
                        error_info=message,
                        start_line=line_num,
                        end_line=None,
                        severity=severity,
                        error_code=error_code,
                        source="mypy"
                    ))
        
        return issues

    def _parse_ruff_output(self, output: str) -> list[VerificationIssue]:
        """
        解析 ruff 输出
        """
        issues = []
        
        pattern = r'^(.+?):(\d+):(\d+):\s*([A-Z]\d+)\s+(.+?)$'
        
        for line in output.split('\n'):
            line = line.strip()
            if not line:
                continue
            
            match = re.match(pattern, line)
            if match:
                file_path = match.group(1)
                line_num = int(match.group(2))
                error_code = match.group(4)
                message = match.group(5).strip()
                
                severity = self._parse_ruff_severity(error_code)
                
                issues.append(VerificationIssue(
                    file_path=file_path,
                    error_info=message,
                    start_line=line_num,
                    end_line=None,
                    severity=severity,
                    error_code=error_code,
                    source="ruff"
                ))
        
        return issues

    def _parse_ruff_severity(self, error_code: str) -> Literal["error", "warning"]:
        """
        根据 ruff 错误代码判断 severity
        
        降级为 warning 的代码（代码风格/整洁度问题，不影响运行）：
        - F401: unused import
        - F541: f-string without placeholders
        - F841: unused variable
        
        其他 E/F 开头：error
        其他（W/I/N/D/S/B/A/C/T等）：warning
        """
        # 代码风格问题，降级为 warning
        STYLE_CODES = {"F401", "F541", "F841"}
        
        if error_code in STYLE_CODES:
            return "warning"
        
        if error_code.startswith(("E", "F")):
            return "error"
        
        return "warning"


# 导出
__all__ = ["VerificationNode"]
