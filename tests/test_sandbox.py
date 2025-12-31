"""
沙箱环境测试脚本

测试内容：
1. 沙箱创建与销毁
2. 命令执行
3. Git 操作（clone, branch, commit, push）
4. 文件操作

运行方式：
    uv run pytest tests/test_sandbox.py -v -s

注意：需要本地运行 Docker daemon
"""

import asyncio
import os
import tempfile

import pytest

from app.sandbox import (
    SandboxManager,
    SandboxConfig,
    GitAuthConfig,
    GitAuthType,
    SandboxStatus,
    GitService,
    FileService,
    SandboxError,
    SandboxNotFoundError,
)


# ============== Fixtures ==============


@pytest.fixture
def sandbox_manager():
    """创建沙箱管理器"""
    # 使用临时目录作为工作空间
    base_path = tempfile.mkdtemp(prefix="sandbox_test_")
    manager = SandboxManager(base_workspace_path=base_path)
    yield manager
    # 清理
    asyncio.get_event_loop().run_until_complete(manager.close())


@pytest.fixture
def sandbox_config():
    """基础沙箱配置"""
    return SandboxConfig(
        docker_image="video-sandbox:0.1",
        memory_limit="256m",
        cpu_limit=0.5,
        timeout=60,
    )


# ============== 沙箱管理测试 ==============


class TestSandboxManager:
    """沙箱管理器测试"""

    @pytest.mark.asyncio
    async def test_create_sandbox(self, sandbox_manager, sandbox_config):
        """测试创建沙箱"""
        sandbox = await sandbox_manager.create_sandbox(sandbox_config)

        assert sandbox is not None
        assert sandbox.id is not None
        assert sandbox.status == SandboxStatus.RUNNING
        assert sandbox.container_id is not None
        assert os.path.exists(sandbox.workspace_path)

        # 清理
        await sandbox_manager.destroy_sandbox(sandbox.id)

    @pytest.mark.asyncio
    async def test_execute_command(self, sandbox_manager, sandbox_config):
        """测试在沙箱中执行命令"""
        sandbox = await sandbox_manager.create_sandbox(sandbox_config)

        try:
            # 测试简单命令
            result = await sandbox_manager.execute_command(
                sandbox.id, "echo 'Hello, Sandbox!'"
            )
            assert result.success
            assert "Hello, Sandbox!" in result.stdout

            # 测试获取 Python 版本
            result = await sandbox_manager.execute_command(
                sandbox.id, "python --version"
            )
            assert result.success
            assert "Python 3.11" in result.stdout

            # 测试命令失败
            result = await sandbox_manager.execute_command(
                sandbox.id, "exit 1"
            )
            assert not result.success
            assert result.exit_code == 1

        finally:
            await sandbox_manager.destroy_sandbox(sandbox.id)

    @pytest.mark.asyncio
    async def test_destroy_sandbox(self, sandbox_manager, sandbox_config):
        """测试销毁沙箱"""
        sandbox = await sandbox_manager.create_sandbox(sandbox_config)
        workspace_path = sandbox.workspace_path

        await sandbox_manager.destroy_sandbox(sandbox.id)

        # 验证沙箱已被销毁
        with pytest.raises(SandboxNotFoundError):
            await sandbox_manager.get_sandbox(sandbox.id)

        # 验证工作目录已被清理
        assert not os.path.exists(workspace_path)

    @pytest.mark.asyncio
    async def test_list_sandboxes(self, sandbox_manager, sandbox_config):
        """测试列出沙箱"""
        # 创建多个沙箱
        sandbox1 = await sandbox_manager.create_sandbox(sandbox_config)
        sandbox2 = await sandbox_manager.create_sandbox(sandbox_config)

        try:
            sandboxes = await sandbox_manager.list_sandboxes()
            assert len(sandboxes) >= 2

            running_sandboxes = await sandbox_manager.list_sandboxes(
                status=SandboxStatus.RUNNING
            )
            assert len(running_sandboxes) >= 2

        finally:
            await sandbox_manager.destroy_sandbox(sandbox1.id)
            await sandbox_manager.destroy_sandbox(sandbox2.id)


# ============== 文件服务测试 ==============


class TestFileService:
    """文件服务测试"""

    @pytest.mark.asyncio
    async def test_read_write_file(self, sandbox_manager, sandbox_config):
        """测试文件读写"""
        sandbox = await sandbox_manager.create_sandbox(sandbox_config)
        file_service = FileService(sandbox_manager, sandbox.id)

        try:
            # 写入文件
            test_content = "Hello, World!\n这是测试内容。"
            await file_service.write_file("test.txt", test_content)

            # 读取文件
            content = await file_service.read_file("test.txt")
            assert content == test_content

            # 追加内容
            await file_service.append_file("test.txt", "\n追加内容")
            content = await file_service.read_file("test.txt")
            assert "追加内容" in content

        finally:
            await sandbox_manager.destroy_sandbox(sandbox.id)

    @pytest.mark.asyncio
    async def test_file_operations(self, sandbox_manager, sandbox_config):
        """测试文件操作"""
        sandbox = await sandbox_manager.create_sandbox(sandbox_config)
        file_service = FileService(sandbox_manager, sandbox.id)

        try:
            # 创建目录
            await file_service.create_directory("subdir/nested")
            assert await file_service.is_directory("subdir/nested")

            # 写入嵌套文件
            await file_service.write_file("subdir/nested/file.txt", "nested content")
            assert await file_service.exists("subdir/nested/file.txt")
            assert await file_service.is_file("subdir/nested/file.txt")

            # 复制文件
            await file_service.copy_file("subdir/nested/file.txt", "subdir/copy.txt")
            assert await file_service.exists("subdir/copy.txt")

            # 移动文件
            await file_service.move_file("subdir/copy.txt", "subdir/moved.txt")
            assert await file_service.exists("subdir/moved.txt")
            assert not await file_service.exists("subdir/copy.txt")

            # 删除文件
            await file_service.delete_file("subdir/moved.txt")
            assert not await file_service.exists("subdir/moved.txt")

            # 列出文件
            files = await file_service.list_files("subdir", recursive=True)
            assert len(files) > 0

        finally:
            await sandbox_manager.destroy_sandbox(sandbox.id)

    @pytest.mark.asyncio
    async def test_apply_patch(self, sandbox_manager, sandbox_config):
        """测试应用 patch"""
        sandbox = await sandbox_manager.create_sandbox(sandbox_config)
        file_service = FileService(sandbox_manager, sandbox.id)

        try:
            # 创建原始文件
            original_content = """def hello():
    print("Hello")

def main():
    hello()
"""
            await file_service.write_file("example.py", original_content)

            # 创建 patch
            patch_content = """--- example.py
+++ example.py
@@ -1,5 +1,5 @@
 def hello():
-    print("Hello")
+    print("Hello, World!")
 
 def main():
     hello()
"""
            result = await file_service.apply_patch(patch_content)
            assert result.success
            assert "example.py" in result.applied_files

            # 验证文件已被修改
            content = await file_service.read_file("example.py")
            assert 'print("Hello, World!")' in content

        finally:
            await sandbox_manager.destroy_sandbox(sandbox.id)


# ============== Git 服务测试 ==============


class TestGitService:
    """Git 服务测试"""

    @pytest.mark.asyncio
    async def test_git_init_and_commit(self, sandbox_manager, sandbox_config):
        """测试 Git 初始化和提交"""
        sandbox = await sandbox_manager.create_sandbox(sandbox_config)
        file_service = FileService(sandbox_manager, sandbox.id)
        git_service = GitService(sandbox_manager, sandbox.id)

        try:
            # 初始化 git 仓库
            result = await sandbox_manager.execute_command(
                sandbox.id, "git init test_repo"
            )
            assert result.success

            # 创建文件
            await file_service.write_file("test_repo/README.md", "# Test Repo\n")

            # 添加并提交
            commit_hash = await git_service.commit(
                message="Initial commit",
                all_files=True,
                repo_path="test_repo",
            )
            assert commit_hash is not None
            assert len(commit_hash) >= 7

            # 检查状态
            status = await git_service.status(repo_path="test_repo")
            assert status.branch in ("main", "master")
            assert status.is_clean

        finally:
            await sandbox_manager.destroy_sandbox(sandbox.id)

    @pytest.mark.asyncio
    async def test_git_branch_operations(self, sandbox_manager, sandbox_config):
        """测试 Git 分支操作"""
        sandbox = await sandbox_manager.create_sandbox(sandbox_config)
        file_service = FileService(sandbox_manager, sandbox.id)
        git_service = GitService(sandbox_manager, sandbox.id)

        try:
            # 初始化仓库
            await sandbox_manager.execute_command(sandbox.id, "git init test_repo")
            await file_service.write_file("test_repo/README.md", "# Test\n")
            await git_service.commit("Initial commit", all_files=True, repo_path="test_repo")

            # 创建新分支
            await git_service.checkout_branch(
                "feature/test", create=True, repo_path="test_repo"
            )

            # 验证当前分支
            status = await git_service.status(repo_path="test_repo")
            assert status.branch == "feature/test"

            # 在新分支上提交
            await file_service.write_file("test_repo/feature.txt", "New feature\n")
            await git_service.commit("Add feature", all_files=True, repo_path="test_repo")

            # 切换回主分支
            await git_service.checkout_branch("master", repo_path="test_repo")
            status = await git_service.status(repo_path="test_repo")
            assert status.branch in ("main", "master")

            # 列出所有分支
            branches = await git_service.list_branches(repo_path="test_repo")
            assert "feature/test" in branches

        finally:
            await sandbox_manager.destroy_sandbox(sandbox.id)

    @pytest.mark.asyncio
    async def test_git_diff(self, sandbox_manager, sandbox_config):
        """测试 Git diff"""
        sandbox = await sandbox_manager.create_sandbox(sandbox_config)
        file_service = FileService(sandbox_manager, sandbox.id)
        git_service = GitService(sandbox_manager, sandbox.id)

        try:
            # 初始化仓库
            await sandbox_manager.execute_command(sandbox.id, "git init test_repo")
            await file_service.write_file("test_repo/file.txt", "line 1\nline 2\n")
            await git_service.commit("Initial", all_files=True, repo_path="test_repo")

            # 修改文件
            await file_service.write_file("test_repo/file.txt", "line 1\nline 2 modified\n")

            # 获取 diff
            diff = await git_service.diff(repo_path="test_repo")
            assert "-line 2" in diff
            assert "+line 2 modified" in diff

            # 暂存后获取 diff
            await git_service.add(repo_path="test_repo")
            staged_diff = await git_service.diff(staged=True, repo_path="test_repo")
            assert "line 2 modified" in staged_diff

        finally:
            await sandbox_manager.destroy_sandbox(sandbox.id)

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not os.environ.get("TEST_GIT_REPO"),
        reason="需要设置 TEST_GIT_REPO 环境变量来测试远程克隆"
    )
    async def test_git_clone(self, sandbox_manager):
        """测试 Git 克隆（需要网络）"""
        # 使用公开仓库测试
        repo_url = os.environ.get("TEST_GIT_REPO", "https://github.com/octocat/Hello-World.git")

        config = SandboxConfig(
            docker_image="video-sandbox:0.1",
            timeout=120,
        )
        sandbox = await sandbox_manager.create_sandbox(config)
        git_service = GitService(sandbox_manager, sandbox.id)

        try:
            result = await git_service.clone(repo_url, branch="master")
            assert result.success
            assert result.repo_path is not None
            assert result.commit_hash is not None

        finally:
            await sandbox_manager.destroy_sandbox(sandbox.id)


# ============== 集成测试 ==============


class TestIntegration:
    """集成测试 - 模拟完整的代码修改流程"""

    @pytest.mark.asyncio
    async def test_code_modification_workflow(self, sandbox_manager, sandbox_config):
        """测试完整的代码修改工作流"""
        sandbox = await sandbox_manager.create_sandbox(sandbox_config)
        file_service = FileService(sandbox_manager, sandbox.id)
        git_service = GitService(sandbox_manager, sandbox.id)

        try:
            # 1. 初始化仓库
            await sandbox_manager.execute_command(sandbox.id, "git init project")

            # 2. 创建初始代码
            await file_service.write_file(
                "project/main.py",
                '''def add(a, b):
    return a + b

def main():
    result = add(1, 2)
    print(f"Result: {result}")

if __name__ == "__main__":
    main()
''',
            )
            await git_service.commit("Initial commit", all_files=True, repo_path="project")

            # 3. 创建功能分支
            await git_service.checkout_branch("feature/multiply", create=True, repo_path="project")

            # 4. 添加新功能
            await file_service.write_file(
                "project/main.py",
                '''def add(a, b):
    return a + b

def multiply(a, b):
    """新增乘法功能"""
    return a * b

def main():
    result = add(1, 2)
    print(f"Add Result: {result}")
    
    mul_result = multiply(3, 4)
    print(f"Multiply Result: {mul_result}")

if __name__ == "__main__":
    main()
''',
            )

            # 5. 检查状态
            status = await git_service.status(repo_path="project")
            assert not status.is_clean
            assert len(status.unstaged_files) > 0 or len(status.untracked_files) > 0

            # 6. 查看 diff
            diff = await git_service.diff(repo_path="project")
            assert "multiply" in diff

            # 7. 提交
            commit_hash = await git_service.commit(
                "Add multiply function",
                all_files=True,
                repo_path="project",
            )
            assert commit_hash

            # 8. 验证最终状态
            status = await git_service.status(repo_path="project")
            assert status.is_clean
            assert status.branch == "feature/multiply"

            # 9. 查看日志
            log = await git_service.log(count=5, repo_path="project")
            assert "Add multiply function" in log
            assert "Initial commit" in log

            print(f"\n✅ 工作流测试完成！")
            print(f"   - 分支: {status.branch}")
            print(f"   - 最新提交: {commit_hash[:8]}")

        finally:
            await sandbox_manager.destroy_sandbox(sandbox.id)


# ============== 独立运行脚本 ==============


async def run_demo():
    """运行演示脚本"""
    print("=" * 60)
    print("🚀 沙箱环境演示")
    print("=" * 60)

    manager = SandboxManager()

    try:
        # 创建沙箱
        print("\n📦 创建沙箱...")
        config = SandboxConfig(
            docker_image="video-sandbox:0.1",
            memory_limit="256m",
        )
        sandbox = await manager.create_sandbox(config)
        print(f"   ✅ 沙箱已创建: {sandbox.id}")
        print(f"   📂 工作目录: {sandbox.workspace_path}")

        # 执行命令
        print("\n🔧 测试命令执行...")
        result = await manager.execute_command(sandbox.id, "python --version")
        print(f"   Python 版本: {result.stdout.strip()}")

        # 文件操作
        print("\n📝 测试文件操作...")
        file_service = FileService(manager, sandbox.id)
        await file_service.write_file("hello.py", 'print("Hello from sandbox!")\n')
        result = await manager.execute_command(sandbox.id, "python hello.py")
        print(f"   执行结果: {result.stdout.strip()}")

        # Git 操作
        print("\n🔀 测试 Git 操作...")
        git_service = GitService(manager, sandbox.id)

        await manager.execute_command(sandbox.id, "git init demo_repo")
        await file_service.write_file("demo_repo/README.md", "# Demo\n")
        commit = await git_service.commit("Init", all_files=True, repo_path="demo_repo")
        print(f"   初始提交: {commit[:8]}")

        await git_service.checkout_branch("dev", create=True, repo_path="demo_repo")
        status = await git_service.status(repo_path="demo_repo")
        print(f"   当前分支: {status.branch}")

        print("\n🧹 清理沙箱...")
        await manager.destroy_sandbox(sandbox.id)
        print("   ✅ 沙箱已销毁")

    except Exception as e:
        print(f"\n❌ 错误: {e}")
        raise
    finally:
        await manager.close()

    print("\n" + "=" * 60)
    print("✨ 演示完成！")
    print("=" * 60)


if __name__ == "__main__":
    # 直接运行此文件进行演示
    asyncio.run(run_demo())

