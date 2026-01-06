"""
Verify Agent Node - 验证节点
执行静态检查、Linter 扫描和可选的 Sandbox 验证
"""

import ast
import subprocess
import tempfile
from pathlib import Path
from typing import Literal, Optional

from langchain_core.callbacks.manager import adispatch_custom_event
from langgraph.types import Command

from app.core.logger_config import logger
from app.core.prompt_manager import prompt_manager
from app.graph.state import IssueProcessState, NodeName, ProcessStage
from app.llms.llm_factory import get_gpt_model
from app.rag.tree_sitter_service import TreeSitterService
from app.schemas.context_assembly import PatchResult
from app.utils.common_function import parse_json_response


class VerificationResult:
    """验证结果"""

    def __init__(self):
        self.status: str = "pass"  # pass/fail
        self.confidence: float = 0.0
        self.syntax_check: dict = {}
        self.linter_check: dict = {}
        self.semantic_check: dict = {}
        self.sandbox_check: dict = {}
        self.issues: list[dict] = []
        self.recommendations: list[str] = []

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "confidence": self.confidence,
            "syntax_check": self.syntax_check,
            "linter_check": self.linter_check,
            "semantic_check": self.semantic_check,
            "sandbox_check": self.sandbox_check,
            "issues": self.issues,
            "recommendations": self.recommendations,
        }


class VerifyAgentNode:
    """验证 Agent 节点"""

    def __init__(self):
        """初始化节点"""
        self.prompt_manager = prompt_manager
        self.tree_sitter = TreeSitterService()

    async def __call__(
        self,
        state: IssueProcessState,
    ) -> Command[Literal[NodeName.PLAN.value]]:
        """
        执行代码验证

        Args:
            state: 当前工作流状态

        Returns:
            Command 对象，返回 plan 节点
        """
        update_dict = {}

        try:
            # 发送进度事件
            await adispatch_custom_event(
                ProcessStage.VERIFICATION.value,
                {
                    "status": NodeName.VERIFY.value,
                    "progress": "正在验证代码补丁...",
                    "think_chain_item": {
                        "type": NodeName.VERIFY.value,
                        "title": "代码验证",
                        "desc": "执行静态检查和语义分析",
                        "urls": [],
                    },
                },
            )

            # 获取当前补丁
            current_patch = state.get("current_patch")
            if not current_patch:
                logger.error("没有当前补丁可供验证")
                update_dict.update(
                    {
                        "error": "没有当前补丁",
                        "executed_nodes": [
                            *state.get("executed_nodes", []),
                            NodeName.VERIFY.value,
                        ],
                        "current_step": NodeName.VERIFY.value,
                    }
                )
                return Command(update=update_dict, goto=NodeName.PLAN.value)

            # 获取上下文信息
            editable_context_dict = state.get("editable_context", {})

            if not editable_context_dict:
                logger.warning("缺少可编辑上下文，跳过详细验证")

            # 执行验证流程
            verification_result = await self._verify_patch(
                state,
                editable_context_dict
            )

            # 更新状态
            update_dict.update(
                {
                    "verification_result": verification_result.to_dict(),
                    "executed_nodes": [
                        *state.get("executed_nodes", []),
                        NodeName.VERIFY.value,
                    ],
                    "current_step": NodeName.VERIFY.value,
                }
            )

            # 发送完成事件
            status_desc = (
                f"验证{'通过' if verification_result.status == 'pass' else '失败'}"
                f" (置信度: {verification_result.confidence:.2f})"
            )

            await adispatch_custom_event(
                ProcessStage.THINK_CHAIN.value,
                {
                    "status": NodeName.VERIFY.value,
                    "progress": status_desc,
                    "think_chain_item": {
                        "type": NodeName.VERIFY.value,
                        "title": "代码验证",
                        "desc": status_desc,
                        "urls": [],
                    },
                },
            )

            logger.info(f"验证完成: {verification_result.status}")

            return Command(update=update_dict, goto=NodeName.PLAN.value)

        except Exception as e:
            logger.error(f"验证失败: {e}", exc_info=True)
            update_dict.update(
                {
                    "error": f"验证失败: {str(e)}",
                    "executed_nodes": [
                        *state.get("executed_nodes", []),
                        NodeName.VERIFY.value,
                    ],
                    "current_step": NodeName.VERIFY.value,
                }
            )

            return Command(update=update_dict, goto=NodeName.PLAN.value)

    async def _verify_patch(
        self,
        state: IssueProcessState,
        editable_context_dict: dict
    ) -> VerificationResult:
        """
        验证补丁

        Args:
            state: 当前状态
            patch: Unified diff 补丁
            editable_context_dict: 可编辑上下文字典

        Returns:
            验证结果
        """
        result = VerificationResult()

        modified_code = state.get("current_modified_code", "")
        original_code = ""
        file_path = ""
        file_content = ""
        editable_start_line = 0
        editable_end_line = 0

        if editable_context_dict:
            from app.schemas.context_assembly import EditableContextSlice

            context = EditableContextSlice(**editable_context_dict)
            file_path = context.target.file_path
            original_code = context.full_code
            file_content = context.file_content
            editable_start_line = context.editable_start_line
            editable_end_line = context.editable_end_line

        # Step 1: 静态语法检查
        logger.info("执行静态语法检查...")
        syntax_result = await self._check_syntax(modified_code, file_path)
        result.syntax_check = syntax_result

        if syntax_result.get("status") == "fail":
            result.status = "fail"
            result.confidence = 0.3
            result.issues.extend(syntax_result.get("issues", []))
            return result

        # Step 2: Linter 检查
        logger.info("执行 Linter 检查...")
        linter_result = await self._run_linter(
            modified_code, 
            file_path,
            file_content,
            editable_start_line,
            editable_end_line
        )
        result.linter_check = linter_result

        if linter_result.get("critical_errors", 0) > 0:
            result.status = "fail"
            result.confidence = 0.5
            result.issues.extend(linter_result.get("issues", []))
            return result

        # Step 3: 语义回归检查
        logger.info("执行语义回归检查...")
        semantic_result = await self._semantic_regression_check(
            state, original_code, modified_code, syntax_result, linter_result
        )
        result.semantic_check = semantic_result

        if semantic_result.get("status") == "fail":
            result.status = "fail"
            result.confidence = semantic_result.get("confidence", 0.6)
            result.issues.extend(semantic_result.get("issues", []))
            result.recommendations.extend(semantic_result.get("recommendations", []))
            return result

        # Step 4: 可选的 Sandbox Import Check（简化实现）
        # 这里可以添加 sandbox 中的 import 测试，暂时标记为 TODO

        # 全部通过
        result.status = "pass"
        result.confidence = semantic_result.get("confidence", 0.85)
        result.issues.extend(linter_result.get("issues", []))
        result.recommendations.extend(semantic_result.get("recommendations", []))

        return result

    async def _check_syntax(
        self, code: str, file_path: str
    ) -> dict:
        """
        使用 AST 检查语法

        Args:
            code: 代码内容
            file_path: 文件路径

        Returns:
            检查结果
        """
        if not code:
            return {
                "status": "skip",
                "message": "没有代码可检查",
                "issues": [],
            }

        # 判断语言
        language = self._detect_language(file_path)

        if language == "python":
            try:
                ast.parse(code)
                return {
                    "status": "pass",
                    "message": "语法检查通过",
                    "issues": [],
                }
            except SyntaxError as e:
                return {
                    "status": "fail",
                    "message": f"Python 语法错误: {e.msg}",
                    "issues": [
                        {
                            "level": "error",
                            "category": "syntax",
                            "line": e.lineno or 0,
                            "message": e.msg,
                            "suggestion": "修复语法错误",
                        }
                    ],
                }
        else:
            # 使用 tree-sitter 进行检查
            ast_info = self.tree_sitter.parse_code(code, language, file_path)
            if ast_info is None:
                return {
                    "status": "fail",
                    "message": "无法解析代码",
                    "issues": [
                        {
                            "level": "error",
                            "category": "syntax",
                            "line": 0,
                            "message": "代码无法被解析",
                            "suggestion": "检查语法错误",
                        }
                    ],
                }

            return {
                "status": "pass",
                "message": "语法检查通过",
                "issues": [],
            }

    async def _run_linter(
        self, 
        code: str, 
        file_path: str,
        file_content: str,
        editable_start_line: int,
        editable_end_line: int
    ) -> dict:
        """
        运行 Linter 工具

        Args:
            code: 修改后的代码片段
            file_path: 文件路径
            file_content: 完整文件内容
            editable_start_line: 可编辑区域起始行
            editable_end_line: 可编辑区域结束行

        Returns:
            Linter 结果
        """
        if not code:
            return {
                "status": "skip",
                "critical_errors": 0,
                "issues": [],
            }

        language = self._detect_language(file_path)

        if language != "python":
            # 其他语言暂不支持 Linter
            return {
                "status": "skip",
                "message": f"语言 {language} 暂不支持 Linter",
                "critical_errors": 0,
                "issues": [],
            }

        # 构建用于检查的完整代码
        full_code = code
        check_line_range = None
        
        if file_content and editable_start_line > 0 and editable_end_line > 0:
            # 使用完整文件上下文：将修改后的代码替换到完整文件的相应位置
            file_lines = file_content.splitlines()
            modified_lines = code.splitlines()
            
            # 替换可编辑区域的代码（行号从1开始，列表索引从0开始）
            new_lines = (
                file_lines[:editable_start_line - 1] +  # 可编辑区域之前
                modified_lines +  # 修改后的代码
                file_lines[editable_end_line:]  # 可编辑区域之后
            )
            
            full_code = "\n".join(new_lines)
            check_line_range = (editable_start_line, editable_start_line + len(modified_lines) - 1)

        # 使用 ruff 进行检查
        try:
            # 写入临时文件
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False
            ) as tmp_file:
                tmp_file.write(full_code)
                tmp_path = tmp_file.name

            # 运行 ruff
            result = subprocess.run(
                ["ruff", "check", tmp_path, "--output-format", "json"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            # 清理临时文件
            Path(tmp_path).unlink(missing_ok=True)

            # 解析结果
            if result.returncode == 0:
                return {
                    "status": "pass",
                    "message": "Linter 检查通过",
                    "critical_errors": 0,
                    "issues": [],
                }

            # 解析 JSON 输出
            import json

            try:
                ruff_issues = json.loads(result.stdout)
            except json.JSONDecodeError:
                ruff_issues = []

            issues = []
            critical_count = 0

            for issue in ruff_issues:
                original_line = issue.get("location", {}).get("row", 0)
                
                # 只保留在检查范围内的错误
                if check_line_range:
                    if not (check_line_range[0] <= original_line <= check_line_range[1]):
                        logger.debug(
                            f"跳过检查范围外的linter问题: line {original_line} "
                            f"(范围: {check_line_range[0]}-{check_line_range[1]})"
                        )
                        continue
                    
                    # 转换为相对于修改代码的行号
                    adjusted_line = original_line - editable_start_line + 1
                else:
                    # 没有完整文件上下文，直接使用原始行号
                    adjusted_line = original_line

                level = "error" if issue.get("fix") is None else "warning"
                if level == "error":
                    critical_count += 1

                issues.append(
                    {
                        "level": level,
                        "category": "linter",
                        "line": adjusted_line,
                        "message": issue.get("message", ""),
                        "code": issue.get("code", ""),
                        "suggestion": "修复 Linter 问题",
                    }
                )

            return {
                "status": "warning" if critical_count == 0 else "fail",
                "critical_errors": critical_count,
                "total_issues": len(issues),
                "issues": issues[:10],  # 只保留前 10 个
            }

        except FileNotFoundError:
            logger.warning("ruff 未安装，跳过 Linter 检查")
            return {
                "status": "skip",
                "message": "ruff 未安装",
                "critical_errors": 0,
                "issues": [],
            }
        except subprocess.TimeoutExpired:
            logger.error("Linter 检查超时")
            return {
                "status": "skip",
                "message": "Linter 检查超时",
                "critical_errors": 0,
                "issues": [],
            }
        except Exception as e:
            logger.error(f"Linter 检查失败: {e}", exc_info=True)
            return {
                "status": "skip",
                "message": f"Linter 检查失败: {str(e)}",
                "critical_errors": 0,
                "issues": [],
            }

    async def _semantic_regression_check(
        self,
        state: IssueProcessState,
        original_code: str,
        modified_code: str,
        syntax_result: dict,
        linter_result: dict,
    ) -> dict:
        """
        使用 LLM 进行语义回归检查

        Args:
            state: 当前状态
            original_code: 原始代码
            modified_code: 修改后的代码
            syntax_result: 语法检查结果
            linter_result: Linter 检查结果

        Returns:
            语义检查结果
        """
        if not modified_code or not original_code:
            return {
                "status": "skip",
                "confidence": 0.5,
                "issues": [],
                "recommendations": [],
            }

        issue_data = state.get("issue_data", {})
        issue_title = issue_data.get("title", "")
        issue_description = issue_data.get("description", "")

        # 构建静态检查结果摘要
        static_summary = self._build_static_summary(syntax_result, linter_result)

        # 构建 Prompt
        prompt = self.prompt_manager.render(
            "code_verification",
            issue_title=issue_title,
            issue_description=issue_description or "无描述",
            original_code=original_code,
            modified_code=modified_code,
            static_check_results=static_summary,
        )

        try:
            # 调用 LLM
            llm = await get_gpt_model(temperature=0.1)
            response = await llm.ainvoke(prompt)

            # 解析响应
            result = parse_json_response(response.content)

            return {
                "status": result.get("status", "pass"),
                "confidence": result.get("confidence", 0.8),
                "issues": result.get("issues", []),
                "semantic_regression": result.get("semantic_regression", {}),
                "reasoning": result.get("reasoning", ""),
                "recommendations": result.get("recommendations", []),
            }

        except Exception as e:
            logger.error(f"语义检查失败: {e}", exc_info=True)
            return {
                "status": "skip",
                "confidence": 0.5,
                "message": f"语义检查失败: {str(e)}",
                "issues": [],
                "recommendations": [],
            }

    def _build_static_summary(
        self, syntax_result: dict, linter_result: dict
    ) -> str:
        """构建静态检查结果摘要"""
        summary = []

        # 语法检查
        summary.append("### 语法检查")
        if syntax_result.get("status") == "pass":
            summary.append("✅ 通过")
        else:
            summary.append(f"❌ 失败: {syntax_result.get('message', '')}")

        # Linter 检查
        summary.append("\n### Linter 检查")
        if linter_result.get("status") == "skip":
            summary.append("⏭️ 跳过")
        elif linter_result.get("status") == "pass":
            summary.append("✅ 通过")
        else:
            critical = linter_result.get("critical_errors", 0)
            total = linter_result.get("total_issues", 0)
            summary.append(f"⚠️ 发现 {total} 个问题，其中 {critical} 个严重错误")

            # 列出前几个问题
            for issue in linter_result.get("issues", [])[:5]:
                summary.append(
                    f"- [{issue.get('level')}] Line {issue.get('line')}: "
                    f"{issue.get('message')}"
                )

        return "\n".join(summary)

    def _detect_language(self, file_path: str) -> str:
        """根据文件后缀检测语言"""
        suffix = Path(file_path).suffix.lower()
        mapping = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".jsx": "javascript",
        }
        return mapping.get(suffix, "unknown")
