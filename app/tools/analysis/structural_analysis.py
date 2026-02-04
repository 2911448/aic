"""
Structural Analysis Tool - 结构分析工具

构建调用涟漪图：基于 rg 初筛调用点 + tree-sitter AST 提取符号边（定影响）。
"""


from langchain_core.tools import tool

from app.core.logger_config import logger
from app.sandbox.file_service import FileService
from app.sandbox.manager import get_sandbox_manager
from app.tools.search.symbolic_search import symbolic_search_core
from app.utils.common_function import detect_language
from app.utils.tree_sitter_service import tree_sitter_service


async def structural_analysis_core(
    sandbox_id: str,
    target_symbol: str,
    max_depth: int = 2,
) -> dict:
    """
    结构分析核心函数：构建调用涟漪图
    
    Args:
        sandbox_id: Sandbox ID
        target_symbol: 目标符号（作为涟漪中心）
        max_depth: 最大扫描深度（默认 2 层）
    
    Returns:
        涟漪图字典，包含：
        - center: 中心符号
        - nodes: 符号节点列表 [{"name": str, "file": str, "type": str}]
        - edges: 调用边列表 [{"caller": str, "callee": str, "file": str, "line": int}]
    """
    try:
        logger.info(f"[structural_analysis] 构建涟漪图: symbol={target_symbol}, max_depth={max_depth}")
        
        sandbox_manager = get_sandbox_manager()
        file_service = FileService(sandbox_manager, sandbox_id)
        
        # 1. 使用 symbolic_search 找到目标符号的定义和所有引用
        search_results = await symbolic_search_core(
            sandbox_id=sandbox_id,
            symbol_name=target_symbol,
            search_type="all",
        )
        
        definitions = search_results.get("definitions", [])
        references = search_results.get("references", [])
        
        if not definitions:
            logger.warning(f"[structural_analysis] 未找到符号定义: {target_symbol}")
            return {
                "center": target_symbol,
                "nodes": [],
                "edges": [],
            }
        
        # 2. 构建节点和边
        nodes = []
        edges = []
        visited_files = set()
        
        # 添加中心节点
        center_def = definitions[0]
        nodes.append({
            "name": target_symbol,
            "file": center_def["file"],
            "type": "target",
        })
        
        # 3. 分析所有引用该符号的文件
        for ref in references:
            ref_file = ref["file"]
            ref_line = ref["line"]
            
            if ref_file in visited_files:
                continue
            visited_files.add(ref_file)
            
            try:
                # 读取文件
                content = await file_service.read_file(ref_file)
                language = detect_language(ref_file)
                
                # 解析 AST
                ast_info = tree_sitter_service.parse_code(content, language, ref_file)
                if not ast_info:
                    continue
                
                # 找到包含该引用的符号（caller）
                for symbol in ast_info.symbols:
                    if symbol.start_line <= ref_line <= symbol.end_line:
                        # 找到调用方
                        caller_name = f"{symbol.parent}.{symbol.name}" if symbol.parent else symbol.name
                        
                        # 添加节点
                        nodes.append({
                            "name": caller_name,
                            "file": ref_file,
                            "type": symbol.type,
                        })
                        
                        # 添加边
                        edges.append({
                            "caller": caller_name,
                            "callee": target_symbol,
                            "file": ref_file,
                            "line": ref_line,
                        })
                        
                        break
            
            except Exception as e:
                logger.warning(f"[structural_analysis] 分析文件失败 {ref_file}: {e}")
                continue
        
        # 去重节点
        unique_nodes = []
        seen_nodes = set()
        for node in nodes:
            node_key = f"{node['name']}:{node['file']}"
            if node_key not in seen_nodes:
                seen_nodes.add(node_key)
                unique_nodes.append(node)
        
        logger.info(
            f"[structural_analysis] 涟漪图构建完成: "
            f"nodes={len(unique_nodes)}, edges={len(edges)}"
        )
        
        return {
            "center": target_symbol,
            "nodes": unique_nodes,
            "edges": edges,
        }
    
    except Exception as e:
        logger.error(f"[structural_analysis] 执行失败: {e}", exc_info=True)
        raise


@tool
async def structural_analysis(
    sandbox_id: str,
    target_symbol: str,
    max_depth: int = 2,
) -> dict:
    """
    结构分析：构建调用涟漪图，找出所有调用目标符号的地方（定影响）
    
    工作流程：
    1. 使用 symbolic_search 找到目标符号的所有引用
    2. 对每个引用文件，解析 AST 找到包含该引用的函数/类
    3. 构建调用关系图（涟漪图）
    
    Args:
        sandbox_id: Sandbox ID
        target_symbol: 目标符号（作为涟漪中心）
        max_depth: 最大扫描深度（默认 2 层，暂未实现递归）
    
    Returns:
        涟漪图字典：
        - center: 中心符号
        - nodes: 符号节点列表 [{"name": str, "file": str, "type": str}]
        - edges: 调用边列表 [{"caller": str, "callee": str, "file": str, "line": int}]
    """
    return await structural_analysis_core(sandbox_id, target_symbol, max_depth)


# 导出
__all__ = ["structural_analysis", "structural_analysis_core"]
