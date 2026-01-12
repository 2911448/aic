#!/usr/bin/env python3
"""
代码索引管理脚本
用于初始化数据库、索引代码等操作
"""

import asyncio
import argparse
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.rag.indexer import code_indexer
from app.core.logger_config import logger
from app.core.milvus import milvus_service


async def init_database(drop_existing: bool = False):
    """初始化Milvus数据库"""
    logger.info("=" * 60)
    logger.info("初始化Milvus数据库")
    logger.info("=" * 60)

    try:
        code_indexer.initialize_database(drop_existing=drop_existing)
        logger.info("✅ 数据库初始化成功")
    except Exception as e:
        logger.error(f"❌ 数据库初始化失败: {e}")
        raise
    finally:
        # 关闭数据库连接
        milvus_service.close()


async def index_project(
    project_path: str,
    project_name: str,
    file_extensions: list[str] = None,
    exclude_dirs: list[str] = None,
    use_gitignore: bool = True,
):
    """索引项目代码"""
    logger.info("=" * 60)
    logger.info(f"开始索引项目: {project_name}")
    logger.info(f"项目路径: {project_path}")
    logger.info(f"使用 .gitignore: {use_gitignore}")
    if exclude_dirs:
        logger.info(f"额外排除的目录: {', '.join(exclude_dirs)}")
    logger.info("=" * 60)

    try:
        count = await code_indexer.index_directory(
            directory=project_path,
            project_name=project_name,
            file_extensions=file_extensions,
            exclude_dirs=exclude_dirs,
            use_gitignore=use_gitignore,
        )
        logger.info(f"✅ 索引完成，共插入 {count} 条代码片段")
    except Exception as e:
        logger.error(f"❌ 索引项目失败: {e}")
        raise
    finally:
        # 关闭数据库连接
        milvus_service.close()


async def search_code(
    query: str, top_k: int = 5, language: str = None, use_summary: bool = True
):
    """搜索代码"""
    logger.info("=" * 60)
    logger.info(f"搜索查询: {query}")
    logger.info(f"搜索模式: {'摘要向量' if use_summary else '完整代码向量'}")
    logger.info("=" * 60)

    try:
        results = await code_indexer.search_code(
            query=query,
            top_k=top_k,
            language=language,
            use_summary=use_summary,
        )

        print(f"\n找到 {len(results)} 条相似代码:\n")
        for i, result in enumerate(results, 1):
            entity = result["entity"]
            distance = result["distance"]
            print(f"{'=' * 60}")
            print(f"结果 #{i} (相似度: {distance:.4f})")
            print(f"项目: {entity.get('project_name')}")
            print(f"文件: {entity.get('file_path')}")
            print(f"符号: {entity.get('symbol_name')}")
            print(f"语言: {entity.get('language')}")
            print(f"行号: {entity.get('start_line')}-{entity.get('end_line')}")
            print(f"使用次数: {entity.get('use_count')}")

            # 显示摘要（如果有）
            summary = entity.get("summary")
            if summary:
                print(f"摘要: {summary[:100]}...")

            print(f"\n代码片段:\n{entity.get('content')[:200]}...")
            print()

    except Exception as e:
        logger.error(f"❌ 搜索失败: {e}")
        raise
    finally:
        # 关闭数据库连接
        milvus_service.close()


async def main():
    parser = argparse.ArgumentParser(description="代码索引管理工具")
    subparsers = parser.add_subparsers(dest="command", help="命令")

    # 初始化数据库命令
    init_parser = subparsers.add_parser("init", help="初始化Milvus数据库")
    init_parser.add_argument(
        "--drop",
        action="store_true",
        help="删除已存在的集合（危险操作）",
    )

    # 索引项目命令
    index_parser = subparsers.add_parser("index", help="索引项目代码")
    index_parser.add_argument("path", help="项目路径")
    index_parser.add_argument("--name", required=True, help="项目名称")
    index_parser.add_argument(
        "--ext",
        nargs="+",
        help="文件扩展名（如: .py .js .ts）",
    )
    index_parser.add_argument(
        "--exclude",
        nargs="+",
        help="额外排除的目录（会与 .gitignore 合并）",
    )
    index_parser.add_argument(
        "--no-gitignore",
        action="store_true",
        help="不使用 .gitignore 规则",
    )

    # 搜索代码命令
    search_parser = subparsers.add_parser("search", help="搜索代码")
    search_parser.add_argument("query", help="搜索查询")
    search_parser.add_argument("--top-k", type=int, default=5, help="返回结果数量")
    search_parser.add_argument("--lang", help="过滤编程语言")
    search_parser.add_argument(
        "--mode",
        choices=["summary", "content"],
        default="summary",
        help="搜索模式：summary使用摘要向量（推荐），content使用完整代码向量",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == "init":
        await init_database(drop_existing=args.drop)
    elif args.command == "index":
        await index_project(
            project_path=args.path,
            project_name=args.name,
            file_extensions=args.ext,
            exclude_dirs=args.exclude,
            use_gitignore=not args.no_gitignore,
        )
    elif args.command == "search":
        await search_code(
            query=args.query,
            top_k=args.top_k,
            language=args.lang,
            use_summary=(args.mode == "summary"),
        )


if __name__ == "__main__":
    asyncio.run(main())
