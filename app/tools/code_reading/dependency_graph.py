"""
Dependency Graph Tool - 分析代码依赖关系

这是一个可被 LangChain Agent 调用的工具。
"""

from typing import Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.core.logger_config import logger
from app.utils.dependency_analyzer import DependencyAnalyzer
from app.utils.tree_sitter_service import tree_sitter_service


class CodeSnippet(BaseModel):
    """代码片段模型"""
    content: str = Field(description="代码内容")
    file_path: str = Field(description="文件路径")
    symbol_name: Optional[str] = Field(default=None, description="符号名（可选）")


def analyze_dependencies_core(
    code_snippets: list[dict],
    language: str = "python"
) -> dict:
    """
    核心依赖分析函数（可直接调用）
    
    Args:
        code_snippets: 代码片段列表，每个包含:
            - content: 代码内容
            - file_path: 文件路径
            - symbol_name: 符号名（可选）
        language: 编程语言
    
    Returns:
        依赖图字典，包含:
        - nodes: 符号列表
        - edges: 依赖关系列表 [{caller: str, callee: str}]
        - callees_by_symbol: 每个符号调用的其他符号
    
    Raises:
        Exception: 分析失败
    """
    try:
        analyzer = DependencyAnalyzer()
        
        # 解析所有代码片段的 AST
        ast_map = {}
        for snippet in code_snippets:
            content = snippet.get("content", "")
            file_path = snippet.get("file_path", "unknown")
            
            ast_info = tree_sitter_service.parse_code(content, language, file_path)
            if ast_info:
                ast_map[file_path] = ast_info
        
        # 分析依赖
        dep_graph = analyzer.analyze_dependencies(code_snippets, ast_map)
        
        # 收集所有符号（从 forward_deps 和 reverse_deps）
        all_symbols = set()
        all_symbols.update(dep_graph.forward_deps.keys())
        all_symbols.update(dep_graph.reverse_deps.keys())
        
        # 构建边列表
        edges = []
        for caller, callees in dep_graph.forward_deps.items():
            for callee in callees:
                edges.append({"caller": caller, "callee": callee})
        
        # 转换为可序列化的字典
        result = {
            "nodes": list(all_symbols),
            "edges": edges,
            "callees_by_symbol": {
                symbol: list(callees)
                for symbol, callees in dep_graph.forward_deps.items()
            },
        }
        
        logger.info(
            f"[Tool] analyze_dependencies: "
            f"nodes={len(result['nodes'])}, edges={len(result['edges'])}"
        )
        
        return result
    except Exception as e:
        error_msg = f"分析依赖失败: {str(e)}"
        logger.error(error_msg)
        raise Exception(error_msg)


@tool
def analyze_dependencies(
    file_path: str,
    code_content: str,
    language: str = "python"
) -> dict:
    """
    分析代码依赖关系（LangChain Agent 工具版本）
    
    Args:
        file_path: 文件路径
        code_content: 代码内容
        language: 编程语言，默认为 python
    
    Returns:
        依赖图字典，包含符号列表、依赖边和调用关系
    
    Raises:
        Exception: 分析失败
    """
    # 构造代码片段
    code_snippets = [{
        "content": code_content,
        "file_path": file_path,
    }]
    
    return analyze_dependencies_core(code_snippets, language)


# 导出工具
__all__ = ["analyze_dependencies", "analyze_dependencies_core"]

