# AIC - AI Assistant

基于 FastAPI 和 LangGraph 构建的 AI 助手项目。

## 技术栈

- **Python 3.11**
- **FastAPI** - 高性能异步 Web 框架
- **LangGraph** - 基于图的 LLM 工作流编排
- **httpx** - 现代异步 HTTP 客户端
- **uv** - 快速 Python 包管理器

## 快速开始

### 1. 安装 uv (如果尚未安装)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. 创建虚拟环境并安装依赖

```bash
uv sync
```

### 3. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 文件配置你的环境变量
```

### 4. 启动开发服务器

```bash
uv run uvicorn src.app.main:app --reload
```

或者使用快捷命令：

```bash
uv run dev
```

访问 http://localhost:8000/docs 查看 API 文档。

## 项目结构

```
aic/
├── src/
│   └── app/
│   │   ├── api/          # API 路由
│   │   ├── core/         # 核心配置
│   │   ├── graph/        # LangGraph 工作流
│   │   ├── models/       # Pydantic 数据模型
│   │   ├── services/     # 业务逻辑服务
│   │   └── main.py       # 应用入口
│   └── config/           # 配置文件
├── tests/                # 测试文件
├── pyproject.toml        # 项目配置
└── README.md
```

## 开发命令

```bash
# 安装开发依赖
uv sync --all-extras

# 运行测试
uv run pytest

# 代码格式化
uv run ruff format .

# 代码检查
uv run ruff check .

# 类型检查
uv run mypy src/
```

## License

MIT
