"""
Sandbox Scanner - Sandbox 全量代码扫描工具

在 Sandbox 环境中递归扫描所有代码文件，
用于全局影响分析。
"""

import os
from typing import Optional

from app.core.logger_config import logger
from app.sandbox.file_service import FileService
from app.utils.common_function import detect_language
from app.utils.gitignore_parser import get_default_ignore_patterns, should_ignore_path


class SandboxScanner:
    """Sandbox 扫描器"""
    
    # 支持的代码文件扩展名
    CODE_EXTENSIONS = {
        ".py",  # Python
        ".js",  # JavaScript
        ".ts",  # TypeScript
        ".jsx", # React
        ".tsx", # React TypeScript
        ".java", # Java
        ".go",  # Go
        ".rs",  # Rust
        ".c",   # C
        ".cpp", # C++
        ".h",   # C/C++ header
        ".hpp", # C++ header
    }
    
    def __init__(self, file_service: FileService, ignore_patterns: Optional[list[str]] = None):
        """
        初始化扫描器
        
        Args:
            file_service: 文件服务实例
            ignore_patterns: 忽略规则列表（从 state.sandbox.ignore_patterns 传入）
        """
        self.file_service = file_service
        self.ignore_patterns = ignore_patterns or get_default_ignore_patterns()
    
    async def scan_all_code_files(
        self,
        repo_path: str = ".",
        max_files: int = 500,
        extensions: Optional[set[str]] = None
    ) -> list[dict]:
        """
        扫描所有代码文件
        
        Args:
            repo_path: 仓库根路径
            max_files: 最大扫描文件数（防止扫描过大）
            extensions: 文件扩展名过滤（默认使用 CODE_EXTENSIONS）
        
        Returns:
            文件信息列表，每项包含 file_path, content, language
        """
        extensions = extensions or self.CODE_EXTENSIONS
        
        try:
            logger.info(f"开始扫描 Sandbox 代码库: {repo_path}")
            
            # 使用 find 命令列出所有文件
            file_infos = await self.file_service.list_files(
                path=repo_path,
                recursive=True
            )
            
            # 过滤代码文件
            code_files = []
            for file_info in file_infos:
                file_path = file_info.path
                
                # 跳过目录
                if file_info.is_dir:
                    continue
                
                # 跳过忽略的路径
                if should_ignore_path(file_path, self.ignore_patterns, repo_path):
                    continue
                
                # 检查扩展名
                _, ext = os.path.splitext(file_path)
                if ext not in extensions:
                    continue
                
                # 检查文件大小（跳过过大的文件）
                if file_info.size > 1024 * 1024:  # 1MB
                    logger.debug(f"跳过过大文件: {file_path} ({file_info.size} bytes)")
                    continue
                
                code_files.append(file_path)
                
                # 限制数量
                if len(code_files) >= max_files:
                    logger.warning(f"达到最大扫描文件数 {max_files}，停止扫描")
                    break
            
            # 读取文件内容
            snippets = []
            for file_path in code_files:
                try:
                    # 工作目录由 Sandbox 自动管理，直接使用相对路径
                    content = await self.file_service.read_file(file_path)
                    
                    # 推断语言
                    language = detect_language(file_path)
                    
                    # 在返回的数据中使用规范化的相对路径（不包含 repo_path 前缀）
                    snippets.append({
                        "file_path": file_path,  # 相对路径，用于补丁和任务队列
                        "content": content,
                        "language": language,
                    })
                    
                except Exception as e:
                    logger.warning(f"读取文件失败 {file_path}: {e}")
                    continue
            
            logger.info(f"成功读取 {len(snippets)} 个代码文件")
            return snippets
        
        except Exception as e:
            logger.error(f"扫描 Sandbox 失败: {e}", exc_info=True)
            return []
    
    async def scan_files_by_pattern(
        self,
        repo_path: str = ".",
        pattern: str = "*.py"
    ) -> list[dict]:
        """
        按模式扫描文件
        
        Args:
            repo_path: 仓库根路径
            pattern: 文件模式（如 *.py）
        
        Returns:
            文件信息列表
        """
        try:
            logger.info(f"按模式扫描: {pattern}")
            
            file_infos = await self.file_service.list_files(
                path=repo_path,
                recursive=True,
                pattern=pattern
            )
            
            snippets = []
            for file_info in file_infos:
                if file_info.is_dir:
                    continue
                
                file_path = file_info.path
                
                # 跳过忽略的路径
                if should_ignore_path(file_path, self.ignore_patterns, repo_path):
                    continue
                
                try:
                    content = await self.file_service.read_file(file_path)
                    language = detect_language(file_path)
                    
                    snippets.append({
                        "file_path": file_path,
                        "content": content,
                        "language": language,
                    })
                except Exception as e:
                    logger.warning(f"读取文件失败 {file_path}: {e}")
                    continue
            
            logger.info(f"扫描完成: {len(snippets)} 个文件")
            return snippets
        
        except Exception as e:
            logger.error(f"按模式扫描失败: {e}", exc_info=True)
            return []

