"""
代码索引服务 - 将代码解析、向量化、存入Milvus
"""

import os
from pathlib import Path

from app.rag.code_parser import CodeParser
from app.rag.embedding import embedding_service
from app.core.milvus import milvus_service
from app.core.logger_config import logger
from app.utils.gitignore_parser import (
    get_default_ignore_patterns,
    parse_gitignore_content,
    should_ignore_path,
)


class CodeIndexer:
    """代码索引器 - 负责将代码解析、向量化、存入数据库"""

    def __init__(self):
        self.parser = CodeParser()
        self.embedding_service = embedding_service
        self.milvus_service = milvus_service

    async def index_file(
        self,
        file_path: str,
        project_name: str,
        project_root: str | None = None,
    ) -> int:
        """
        索引单个代码文件

        Args:
            file_path: 代码文件路径
            project_name: 项目名称
            project_root: 项目根目录

        Returns:
            插入的代码片段数量
        """
        try:
            # 1. 解析代码文件
            logger.info(f"正在解析文件: {file_path}")
            snippets = self.parser.parse_file(file_path, project_name, project_root)

            if not snippets:
                logger.warning(f"文件中没有提取到代码片段: {file_path}")
                return 0

            # 2. 批量向量化 content 和 summary
            logger.info(f"正在向量化 {len(snippets)} 个代码片段的内容和摘要...")

            # 准备需要向量化的文本：先content，再summary
            content_texts = [snippet.content for snippet in snippets]
            summary_texts = [
                snippet.summary
                or snippet.symbol_name  # 如果没有summary，使用symbol_name
                for snippet in snippets
            ]

            # 合并所有文本进行批量向量化（提高效率）
            all_texts = content_texts + summary_texts
            all_embeddings = await self.embedding_service.embed_texts(all_texts)

            # 分离content和summary的向量
            content_embeddings = all_embeddings[: len(snippets)]
            summary_embeddings = all_embeddings[len(snippets) :]

            # 3. 将向量赋值给代码片段
            for i, snippet in enumerate(snippets):
                snippet.embedding = content_embeddings[i]
                snippet.summary_embedding = summary_embeddings[i]

            # 4. Upsert到Milvus（自动去重：删除旧数据，插入新数据）
            logger.info(f"正在更新 {len(snippets)} 个代码片段到Milvus...")
            ids = self.milvus_service.upsert_snippets(snippets)

            logger.info(f"成功索引文件 {file_path}, 更新 {len(ids)} 条记录")
            return len(ids)

        except Exception as e:
            logger.error(f"索引文件失败 {file_path}: {e}")
            raise

    async def index_directory(
        self,
        directory: str,
        project_name: str,
        file_extensions: list[str] | None = None,
        exclude_dirs: list[str] | None = None,
        use_gitignore: bool = True,
    ) -> int:
        """
        索引整个目录的代码文件

        Args:
            directory: 目录路径
            project_name: 项目名称
            file_extensions: 要索引的文件扩展名列表，None表示所有支持的类型
            exclude_dirs: 额外要排除的目录名列表，会合并到默认排除列表

        Returns:
            插入的代码片段总数
        """
        if file_extensions is None:
            file_extensions = list(CodeParser.SUPPORTED_LANGUAGES.keys())

        # 加载忽略规则
        ignore_patterns = self._load_ignore_patterns(
            directory=directory,
            use_gitignore=use_gitignore,
            extra_exclude_dirs=exclude_dirs
        )

        total_count = 0

        logger.info(f"开始索引目录: {directory}")
        logger.info(f"项目名称: {project_name}")
        logger.info(f"支持的文件类型: {file_extensions}")
        logger.info(f"加载 {len(ignore_patterns)} 条忽略规则")

        # 递归遍历目录
        for root, dirs, files in os.walk(directory):
            # 过滤排除的目录（需要在原地修改 dirs 列表）
            filtered_dirs = []
            for d in dirs:
                dir_path = os.path.join(root, d)
                # 跳过隐藏目录
                if d.startswith("."):
                    continue
                # 检查是否应该忽略
                if should_ignore_path(dir_path, ignore_patterns, directory):
                    logger.debug(f"忽略目录: {dir_path}")
                    continue
                filtered_dirs.append(d)
            
            dirs[:] = filtered_dirs

            # 处理每个代码文件
            for file in files:
                # 跳过隐藏文件
                if file.startswith("."):
                    continue

                file_path = os.path.join(root, file)
                
                # 检查是否应该忽略该文件
                if should_ignore_path(file_path, ignore_patterns, directory):
                    logger.debug(f"忽略文件: {file_path}")
                    continue
                
                file_ext = Path(file_path).suffix.lower()

                if file_ext in file_extensions:
                    try:
                        count = await self.index_file(
                            file_path=file_path,
                            project_name=project_name,
                            project_root=directory,
                        )
                        total_count += count
                    except Exception as e:
                        logger.error(f"索引文件 {file_path} 时出错: {e}")
                        continue

        logger.info(f"目录索引完成，共插入 {total_count} 条代码片段")
        return total_count

    async def search_code(
        self,
        query: str,
        top_k: int = 10,
        language: str | None = None,
        project_name: str | None = None,
        use_summary: bool = True,
    ) -> list[dict]:
        """
        搜索相似代码

        Args:
            query: 查询文本
            top_k: 返回前K个结果
            language: 过滤编程语言
            project_name: 过滤项目名称
            use_summary: 是否使用摘要向量搜索（默认True，推荐）

        Returns:
            搜索结果列表
        """
        try:
            # 1. 将查询文本向量化
            logger.info(f"正在搜索: {query}")
            query_vector = await self.embedding_service.embed_text(query)

            # 2. 构建过滤条件
            filter_conditions = []
            if language:
                filter_conditions.append(f'language == "{language}"')
            if project_name:
                filter_conditions.append(f'project_name == "{project_name}"')

            filter_expr = " && ".join(filter_conditions) if filter_conditions else None

            # 3. 向量搜索（优先使用摘要向量）
            if use_summary:
                results = self.milvus_service.search_by_summary(
                    query_vector=query_vector,
                    top_k=top_k,
                    filter_expr=filter_expr,
                )
            else:
                results = self.milvus_service.search_similar_code(
                    query_vector=query_vector,
                    top_k=top_k,
                    filter_expr=filter_expr,
                )

            logger.info(f"搜索完成，返回 {len(results)} 条结果")
            return results

        except Exception as e:
            logger.error(f"搜索代码失败: {e}")
            raise

    def _load_ignore_patterns(
        self,
        directory: str,
        use_gitignore: bool,
        extra_exclude_dirs: list[str] | None = None
    ) -> list[str]:
        """
        加载忽略规则
        
        Args:
            directory: 目录路径
            use_gitignore: 是否使用 .gitignore
            extra_exclude_dirs: 额外的排除目录
        
        Returns:
            忽略规则列表
        """
        # 从默认规则开始
        patterns = get_default_ignore_patterns()
        
        # 读取 .gitignore
        if use_gitignore:
            gitignore_path = os.path.join(directory, ".gitignore")
            if os.path.exists(gitignore_path):
                try:
                    with open(gitignore_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    
                    gitignore_patterns = parse_gitignore_content(content)
                    patterns.extend(gitignore_patterns)
                    
                    logger.info(f"从 .gitignore 读取到 {len(gitignore_patterns)} 条规则")
                except Exception as e:
                    logger.warning(f"读取 .gitignore 失败: {e}")
        
        # 添加额外的排除目录
        if extra_exclude_dirs:
            patterns.extend(extra_exclude_dirs)
            logger.info(f"添加 {len(extra_exclude_dirs)} 条额外排除规则")
        
        return patterns
    
    def initialize_database(self, drop_existing: bool = False):
        """
        初始化数据库（创建集合）

        Args:
            drop_existing: 是否删除已存在的集合
        """
        try:
            logger.info("正在初始化Milvus数据库...")
            self.milvus_service.connect()
            self.milvus_service.create_collection(drop_existing=drop_existing)
            logger.info("数据库初始化完成")
        except Exception as e:
            logger.error(f"初始化数据库失败: {e}")
            raise


# 全局单例
code_indexer = CodeIndexer()
