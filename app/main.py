from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config.app_config import app_config


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """应用生命周期管理"""
    # 启动时执行
    print(f"🚀 {app_config.app_name} 正在启动...")
    yield
    # 关闭时执行
    print(f"👋 {app_config.app_name} 正在关闭...")


app = FastAPI(
    title=app_config.app_name,
    description="AI Code Assistant API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(router, prefix=app_config.api_prefix)


@app.get("/")
async def root() -> dict[str, str]:
    """根路径"""
    return {"message": f"Welcome to {app_config.app_name}!", "docs": "/docs"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=app_config.host,
        port=app_config.port,
        reload=app_config.debug,
    )
