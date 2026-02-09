"""
Tools Registry - 工具注册与白名单管理

为不同的 Agent 分配不同的工具集合，实现权限控制。
"""

from collections.abc import Callable

from app.core.logger_config import logger

# 导入所有工具
from app.tools.sandbox.read_file import read_file_from_sandbox
from app.tools.sandbox.write_file import write_file_to_sandbox
from app.tools.sandbox.run_cmd import run_command_in_sandbox
from app.tools.code_reading.ast_parse import parse_code_ast
from app.tools.code_quality.syntax_check import check_python_syntax
from app.tools.search.semantic_search import semantic_search
from app.tools.search.symbolic_search import symbolic_search
from app.tools.analysis.structural_analysis import structural_analysis


class ToolRegistry:
    """
    工具注册表
    
    管理所有可用工具，并为不同 Agent 提供白名单机制。
    """
    
    def __init__(self):
        """初始化工具注册表"""
        # 所有可用工具
        self._all_tools = {
            # Sandbox 工具
            "read_file": read_file_from_sandbox,
            "write_file": write_file_to_sandbox,
            "run_command": run_command_in_sandbox,
            
            # Code Reading 工具
            "parse_ast": parse_code_ast,
            
            # Code Quality 工具
            "check_syntax": check_python_syntax,
            
            # OmniExplorer 三类工具
            "semantic_search": semantic_search,
            "symbolic_search": symbolic_search,
            "structural_analysis": structural_analysis,
        }
        
        # Agent 工具白名单配置
        self._agent_tool_whitelist = {
            # Planner
            "planner": [
                "run_command",
            ],

            # OmniExplorer
            "omni_explorer": [
                "semantic_search",
                "read_file",
                "parse_ast",
                "run_command",
            ],
            
            # CodeAgent: 生成代码能力（读+写）
            "code_agent": [
                # "read_file",
                # "parse_ast",
                # "check_syntax",
                "run_command",
            ],
            
            # Verification: 全量验证
            "verification": [
                "run_command",
            ],
        }
    
    def get_tools_for_agent(self, agent_name: str) -> list[Callable]:
        """
        获取指定 Agent 可用的工具列表
        
        Args:
            agent_name: Agent 名称（omni_explorer, code_agent, verification）
        
        Returns:
            工具函数列表
        """
        tool_names = self._agent_tool_whitelist.get(agent_name, [])
        
        tools = []
        for tool_name in tool_names:
            tool_func = self._all_tools.get(tool_name)
            if tool_func:
                tools.append(tool_func)
            else:
                logger.warning(f"工具 '{tool_name}' 不存在于注册表中")
        
        return tools
    
    def get_all_tools(self) -> list[Callable]:
        """
        获取所有可用工具
        
        Returns:
            所有工具函数列表
        """
        return list(self._all_tools.values())
    
    def register_tool(self, name: str, tool_func: Callable):
        """
        注册新工具
        
        Args:
            name: 工具名称
            tool_func: 工具函数
        """
        if name in self._all_tools:
            logger.warning(f"工具 '{name}' 已存在，将被覆盖")
        
        self._all_tools[name] = tool_func
        logger.info(f"注册工具: {name}")
    
    def add_agent_whitelist(self, agent_name: str, tool_names: list[str]):
        """
        为 Agent 添加工具白名单
        
        Args:
            agent_name: Agent 名称
            tool_names: 工具名称列表
        """
        self._agent_tool_whitelist[agent_name] = tool_names
        logger.info(f"为 Agent '{agent_name}' 配置白名单: {tool_names}")


# 全局单例
_tool_registry = ToolRegistry()


def get_tool_registry() -> ToolRegistry:
    """获取工具注册表单例"""
    return _tool_registry


# 便捷函数
def get_tools_for_agent(agent_name: str) -> list[Callable]:
    """
    获取指定 Agent 可用的工具列表
    
    Args:
        agent_name: Agent 名称（omni_explorer, code_agent, verification）
    
    Returns:
        工具函数列表
    """
    return _tool_registry.get_tools_for_agent(agent_name)


# 导出
__all__ = [
    "ToolRegistry",
    "get_tool_registry",
    "get_tools_for_agent",
]

