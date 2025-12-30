"""
日志配置模块 - 支持 JSON 格式输出
"""

from loguru import logger
import sys
from pathlib import Path
from typing import Any
import os
import json
from app.config.app_config import app_config


# 设置标准输出编码为UTF-8
if sys.platform == "win32":
    import codecs

    # 使用更兼容的方式设置编码
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        # Python 3.6 兼容性
        sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())
        sys.stderr = codecs.getwriter("utf-8")(sys.stderr.detach())
    # 设置环境变量
    os.environ["PYTHONIOENCODING"] = "utf-8"

# 日志文件目录
LOG_DIR = Path(app_config.log.path)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# 环境配置
DEBUG_MODE = os.getenv("DEBUG", "false").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
# 日志格式：json 或 text（默认text，便于本地开发）
LOG_FORMAT = os.getenv("LOG_FORMAT", "text").lower()
# 文件日志格式：json 或 text（默认json，便于日志分析）
LOG_FILE_FORMAT = os.getenv("LOG_FILE_FORMAT", "json").lower()

# 移除默认输出
logger.remove()


# 控制台输出 - 使用格式化函数
def console_format_text(record):
    """控制台日志格式化函数 - 彩色文本格式"""
    # 预处理 message，将其存入 extra 以避免花括号被解析
    record["extra"]["console_msg"] = str(record["message"])

    # 返回带颜色标签的模板字符串
    return (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{extra[console_msg]}</level>\n"
        "{exception}"
    )


def console_format_json(record):
    """控制台日志格式化函数 - 简洁JSON格式"""
    # 构建简洁的JSON结构
    log_entry = {
        "timestamp": record["time"].strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "level": record["level"].name,
        "logger": f"{record['name']}.{record['function']}:{record['line']}",
        "message": str(record["message"]),
    }

    # 添加异常信息
    if record["exception"]:
        log_entry["exception"] = {
            "type": record["exception"].type.__name__
            if record["exception"].type
            else None,
            "value": str(record["exception"].value)
            if record["exception"].value
            else None,
            "traceback": record["exception"].traceback
            if record["exception"].traceback
            else None,
        }

    # 添加其他extra字段（排除内部字段）
    extra_fields = {
        k: v
        for k, v in record["extra"].items()
        if k not in ["console_msg", "file_line", "no_console"]
    }
    if extra_fields:
        log_entry["extra"] = extra_fields

    return json.dumps(log_entry, ensure_ascii=False) + "\n"


# 根据配置选择控制台格式
if LOG_FORMAT == "json":
    console_format = console_format_json
    console_colorize = False
else:
    console_format = console_format_text
    console_colorize = True

logger.add(
    sys.stdout,
    level=LOG_LEVEL,
    colorize=console_colorize,
    format=console_format,
    filter=lambda record: not record["extra"].get("no_console", False),
    backtrace=True,
    diagnose=True,
    catch=True,
)


# 文件输出 - INFO 及以上
def file_format_text(record):
    """文件日志格式化函数 - 文本格式"""
    # 将完整的日志行存入 extra，避免message中的花括号被解析
    record["extra"]["file_line"] = (
        f"{record['time'].strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} | "
        f"{record['level'].name: <8} | "
        f"{record['name']}:{record['function']}:{record['line']} | "
        f"{record['message']}"
    )

    # 返回模板字符串，exception会被自动追加
    return "{extra[file_line]}\n{exception}"


# 根据配置选择文件格式
if LOG_FILE_FORMAT == "json":
    # 使用自定义 sink 函数，按天轮转

    def create_json_sink(base_path: Path):
        """创建自定义 JSON sink"""

        def json_sink(message):
            """自定义JSON sink - 直接写入JSON格式"""
            record = message.record

            path = record["extra"].get("path", "N/A")
            request_time = record["extra"].get("request_time", 0.0)

            # 构建符合要求的JSON格式
            log_entry = {
                "time": record["time"].strftime("%Y-%m-%d %H:%M:%S"),
                "level": record["level"].name.lower(),
                "path": path,
                "file": f"{record['name']}.{record['function']}",
                "line": record["line"],
                "request_time": round(request_time, 3),
                "msg": str(record["message"]),
            }

            # 添加异常信息（如果有）
            if record["exception"] is not None:
                exc_type, exc_value, exc_tb = record["exception"]
                if exc_type is not None:
                    log_entry["exception"] = {
                        "type": exc_type.__name__,
                        "value": str(exc_value),
                    }

            # 添加其他额外字段
            extra_fields = {
                k: v
                for k, v in record["extra"].items()
                if k
                not in [
                    "path",
                    "request_time",
                    "console_msg",
                    "file_line",
                    "no_console",
                ]
            }
            if extra_fields:
                log_entry["extra"] = extra_fields

            # 生成文件名（按天）
            date_str = record["time"].strftime("%Y%m%d")
            file_path = base_path / f"aic.log_json.{date_str}"

            # 写入文件
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

        return json_sink

    logger.add(
        create_json_sink(LOG_DIR),
        level="INFO",
        format="{message}",  # 使用简单格式，实际内容在sink中处理
        filter=lambda record: record["level"].name
        in ["INFO", "WARNING", "ERROR", "CRITICAL"],
    )
else:
    # 传统文本格式
    logger.add(
        LOG_DIR / "app_info.log",
        level="INFO",
        rotation="10 MB",
        retention="30 days",
        compression="zip",
        enqueue=True,
        format=file_format_text,
        filter=lambda record: record["level"].name
        in ["INFO", "WARNING", "ERROR", "CRITICAL"],
    )


def log_structured(level: str, message: str, **kwargs: Any) -> None:
    """结构化日志记录"""
    logger.bind(**kwargs).log(level, message)


def log_request(
    method: str,
    path: str,
    status_code: int,
    duration_ms: float,
    user_id: str | None = None,
) -> None:
    """记录请求日志"""
    logger.info(
        f"{method} {path} - {status_code} ({duration_ms:.2f}ms)",
        extra={
            "type": "request",
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration_ms": duration_ms,
            "user_id": user_id,
        },
    )


def log_database_operation(
    operation: str, table: str, duration_ms: float, success: bool = True
) -> None:
    """记录数据库操作日志"""
    level = "INFO" if success else "ERROR"
    logger.log(
        level,
        f"DB {operation} on {table} ({duration_ms:.2f}ms)",
        extra={
            "type": "database",
            "operation": operation,
            "table": table,
            "duration_ms": duration_ms,
            "success": success,
        },
    )


# 异常捕获配置
logger.catch(
    lambda e: logger.exception(
        f"未捕获的异常: {type(e).__name__}: {e}",
        extra={"exception_type": type(e).__name__},
    )
)

__all__ = ["logger", "log_structured", "log_request", "log_database_operation"]
