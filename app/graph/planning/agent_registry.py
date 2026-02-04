"""
Agent Registry - Agent 注册表

为每个可调度的 Agent 提供能力描述，用于：
1. 生成 Planner System Prompt 中的能力清单
2. 生成 run_agent 工具的 schema（枚举值）

设计原则：
- 注册字段：name/description/tool_whitelist/enabled
- AgentRegistry 注入 system prompt，LLM 形成"可用能力空间"
- LLM 通过工具调用发起调度，系统仅执行不决策
"""

from dataclasses import dataclass, field


@dataclass
class AgentCard:
    """Agent 注册卡片"""
    
    name: str  # 唯一标识（用于 run_agent 的 agent 参数）
    description: str  # 能力描述（一句话说明职责与适用场景）
    tool_whitelist: list[str] = field(default_factory=list)  # 可用工具白名单
    enabled: bool = True  # 是否允许被调用
    
    def to_capability_text(self) -> str:
        """转换为能力描述文本（用于注入 prompt）"""
        return f"- **{self.name}**: {self.description}"


class AgentRegistry:
    """Agent 注册表"""
    
    def __init__(self):
        """初始化注册表"""
        self._agents: dict[str, AgentCard] = {}
        self._register_default_agents()
    
    def _register_default_agents(self):
        """注册默认的 Agent"""
        
        # OmniExplorer - 全面检索与影响分析
        self.register(AgentCard(
            name="omni_explorer",
            description="由面到点、由点到网：Semantic Search（定方向）→ Symbolic Search（定位置）→ Structural Analysis（定影响），产出涟漪图与函数签名契约",
            tool_whitelist=["semantic_search", "symbolic_search", "structural_analysis", "read_file", "parse_ast", "run_command"],
            enabled=True,
        ))
        
        # CodeAgent - 代码生成与修复
        self.register(AgentCard(
            name="code_agent",
            description="代码生成能力：读+写代码，生成 unified diff 补丁，内部自愈（局部语法/拼写错误重试），遵守 contract_constraints 保证并行一致性",
            tool_whitelist=["read_file", "parse_ast", "search_symbol", "check_syntax", "run_command"],
            enabled=True,
        ))
        
        # Verification - 全量验证
        self.register(AgentCard(
            name="verification",
            description="全量静态检查：mypy + ruff，失败结果回传给 Planner 重新规划",
            tool_whitelist=["run_command"],
            enabled=True,
        ))
        
        # MRPublisher
        self.register(AgentCard(
            name="mr_publisher",
            description="生成结构化评审报告（含分支名、技术细节、风险评估）并提交 Merge Request：创建分支、推送代码、创建 GitLab MR",
            tool_whitelist=[],
            enabled=True,
        ))
    
    def register(self, agent: AgentCard):
        """注册一个 Agent"""
        self._agents[agent.name] = agent
    
    def get(self, name: str) -> AgentCard | None:
        """根据 name 获取 AgentCard"""
        return self._agents.get(name)
    
    def get_all(self) -> list[AgentCard]:
        """获取所有已注册的 Agent"""
        return list(self._agents.values())
    
    def get_enabled_agents(self) -> list[AgentCard]:
        """获取所有已启用的 Agent"""
        return [agent for agent in self._agents.values() if agent.enabled]
    
    def to_capability_list(self) -> str:
        """生成能力清单文本（用于注入 Planner System Prompt）"""
        lines = ["# 可用 Agent 能力清单", ""]
        for agent in self.get_enabled_agents():
            lines.append(agent.to_capability_text())
        return "\n".join(lines)
    
    def to_run_agent_enum(self) -> list[str]:
        """生成 run_agent 工具的 agent 枚举值"""
        return [agent.name for agent in self.get_enabled_agents()]


# 全局单例
agent_registry = AgentRegistry()
