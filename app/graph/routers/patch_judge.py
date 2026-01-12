"""
Patch Judge - 补丁仲裁器

从多个候选补丁中选择最佳补丁的确定性规则引擎。
不使用 LLM，基于简单指标（置信度、diff 长度、目标文件匹配等）进行选择。
"""

from typing import Optional

from app.core.logger_config import logger
from app.schemas.context_assembly import PatchResult


class PatchJudge:
    """
    补丁仲裁器
    
    职责：
    - 从 patch_candidates 中选择最佳补丁
    - 使用确定性规则（不引入额外 LLM）
    - 预留扩展点：未来可接入验证结果或 LLM 仲裁
    """
    
    def __init__(self):
        """初始化仲裁器"""
        pass
    
    def select_best_patch(
        self,
        candidates: list[dict],
        target_file_path: str = None,
    ) -> Optional[dict]:
        """
        从候选补丁中选择最佳补丁
        
        选择规则（优先级从高到低）：
        1. 目标文件匹配：优先选择修改了目标文件的补丁
        2. 置信度：选择置信度最高的补丁
        3. Diff 长度：在置信度相同时，选择 diff 较小的补丁（风险更低）
        
        Args:
            candidates: 候选补丁列表（dict 格式）
            target_file_path: 目标文件路径（可选，用于优先选择）
        
        Returns:
            最佳补丁（dict 格式），如果没有候选则返回 None

        TODO: 改为 llm 仲裁
        """
        if not candidates:
            logger.warning("PatchJudge: 没有候选补丁可供选择")
            return None
        
        if len(candidates) == 1:
            logger.info("PatchJudge: 只有一个候选补丁，直接选择")
            return candidates[0]
        
        # 转换为 PatchResult 对象以便访问字段
        patch_results = []
        for candidate in candidates:
            try:
                patch_results.append(PatchResult(**candidate))
            except Exception as e:
                logger.warning(f"PatchJudge: 无效的候选补丁格式: {e}")
                continue
        
        if not patch_results:
            logger.error("PatchJudge: 所有候选补丁格式无效")
            return None
        
        # 规则 1: 优先选择修改了目标文件的补丁
        if target_file_path:
            target_patches = [
                p for p in patch_results
                if p.file_path == target_file_path
            ]
            if target_patches:
                logger.info(f"PatchJudge: 找到 {len(target_patches)} 个修改目标文件的补丁")
                patch_results = target_patches
        
        # 规则 2 & 3: 按置信度降序，diff 长度升序排序
        sorted_patches = sorted(
            patch_results,
            key=lambda p: (
                -p.confidence,  # 置信度越高越好（降序）
                len(p.unified_diff),  # diff 越短越好（升序）
            )
        )
        
        best_patch = sorted_patches[0]
        
        logger.info(
            f"PatchJudge: 选择最佳补丁 - "
            f"文件: {best_patch.file_path}, "
            f"diff 长度: {len(best_patch.unified_diff)}"
        )
        
        return best_patch.model_dump()
    
    def rank_patches(
        self,
        candidates: list[dict],
        target_file_path: str = None,
    ) -> list[dict]:
        """
        对候选补丁进行排序（从最佳到最差）
        
        Args:
            candidates: 候选补丁列表
            target_file_path: 目标文件路径（可选）
        
        Returns:
            排序后的补丁列表
        """
        if not candidates:
            return []
        
        # 转换为 PatchResult 对象
        patch_results = []
        for candidate in candidates:
            try:
                patch_results.append(PatchResult(**candidate))
            except Exception as e:
                logger.warning(f"PatchJudge: 无效的候选补丁格式: {e}")
                continue
        
        if not patch_results:
            return []
        
        # 计算每个补丁的得分
        scored_patches = []
        for patch in patch_results:
            score = patch.confidence  # 基础分数：置信度
            
            # 加分项：修改了目标文件
            if target_file_path and patch.file_path == target_file_path:
                score += 0.5
            
            # 减分项：diff 过长（风险较高）
            if len(patch.unified_diff) > 1000:
                score -= 0.1
            
            scored_patches.append((score, patch))
        
        # 按得分降序排序
        sorted_patches = sorted(scored_patches, key=lambda x: -x[0])
        
        return [p.model_dump() for _, p in sorted_patches]


# 全局单例
_patch_judge = PatchJudge()


def get_patch_judge() -> PatchJudge:
    """获取补丁仲裁器单例"""
    return _patch_judge


# 导出
__all__ = [
    "PatchJudge",
    "get_patch_judge",
]

