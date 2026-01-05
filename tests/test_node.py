"""
Tests for Issue processing workflow with Command-based routing
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.graph.state import IssueProcessState, NodeName


class TestIssueInsightAgentNode:
    """Test IssueInsightAgentNode functionality"""

    @pytest.mark.asyncio
    async def test_node_initialization(self):
        """Test that IssueInsightAgentNode can be initialized"""
        from app.graph.nodes.issue_insight_agent_node import IssueInsightAgentNode

        node = IssueInsightAgentNode()
        assert node is not None
        assert node.prompt_manager is not None

    @pytest.mark.asyncio
    async def test_analyze_and_generate_queries_success(self):
        """Test successful analysis and query generation"""
        from app.graph.nodes.issue_insight_agent_node import IssueInsightAgentNode

        # Create node
        node = IssueInsightAgentNode()

        # Create mock state
        state: IssueProcessState = {
            "issue_data": {
                "title": "Login fails with 500 error",
                "description": "UserService.login throws AttributeError when password is wrong",
                "labels": ["bug"],
            },
            "project_info": {
                "name": "test-project",
                "path_with_namespace": "org/test-project",
            },
            "issue_type": None,
            "search_queries": [],
            "retrieved_code": [],
            "executed_nodes": [],
            "current_step": "init",
        }

        # Mock LLM response
        mock_response = MagicMock()
        mock_response.content = {
            "issue_type": "bug",
            "search_queries": [
                {
                    "query": "UserService login method implementation",
                    "context": "定位UserService的login方法",
                    "weight": 1.0,
                },
                {
                    "query": "password validation authentication logic",
                    "context": "查找密码验证逻辑",
                    "weight": 0.9,
                },
                {
                    "query": "handle AttributeError exception",
                    "context": "查找异常处理代码",
                    "weight": 0.8,
                },
            ],
        }

        # Mock get_gpt_model
        with patch(
            "app.graph.nodes.issue_insight_agent_node.get_gpt_model"
        ) as mock_get_llm:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = mock_response
            mock_get_llm.return_value = mock_llm

            # Mock adispatch_custom_event
            with patch(
                "app.graph.nodes.issue_insight_agent_node.adispatch_custom_event"
            ) as mock_dispatch:
                mock_dispatch.return_value = None

                # Execute node
                result = await node(state)

                # Verify result structure
                assert result is not None
                assert hasattr(result, "update")
                assert hasattr(result, "goto")

                # Verify goto
                assert result.goto == NodeName.PLAN.value

                # Verify update dict
                update = result.update
                assert "issue_type" in update
                assert update["issue_type"] == "bug"
                assert "search_queries" in update
                assert len(update["search_queries"]) == 3
                assert "executed_nodes" in update
                assert NodeName.ISSUE_INSIGHT.value in update["executed_nodes"]
                assert "current_step" in update
                assert update["current_step"] == NodeName.ISSUE_INSIGHT.value

                # Verify search queries are strings
                for query in update["search_queries"]:
                    assert isinstance(query, str)
                    assert len(query) > 0

    @pytest.mark.asyncio
    async def test_analyze_and_generate_queries_feature(self):
        """Test feature type issue analysis"""
        from app.graph.nodes.issue_insight_agent_node import IssueInsightAgentNode

        node = IssueInsightAgentNode()

        state: IssueProcessState = {
            "issue_data": {
                "title": "Add batch delete functionality",
                "description": "Need to add batch delete for user management",
                "labels": ["feature"],
            },
            "project_info": {
                "name": "test-project",
                "path_with_namespace": "org/test-project",
            },
            "issue_type": None,
            "search_queries": [],
            "retrieved_code": [],
            "executed_nodes": [],
            "current_step": "init",
        }

        # Mock LLM response
        mock_response = MagicMock()
        mock_response.content = {
            "issue_type": "feature",
            "search_queries": [
                {
                    "query": "user management delete operations",
                    "context": "查找现有的删除操作",
                    "weight": 1.0,
                },
                {
                    "query": "batch operations multiple selection",
                    "context": "查找批量操作实现",
                    "weight": 0.8,
                },
            ],
        }

        with patch(
            "app.graph.nodes.issue_insight_agent_node.get_gpt_model"
        ) as mock_get_llm:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = mock_response
            mock_get_llm.return_value = mock_llm

            with patch(
                "app.graph.nodes.issue_insight_agent_node.adispatch_custom_event"
            ):
                result = await node(state)

                assert result.goto == NodeName.PLAN.value
                update = result.update
                assert update["issue_type"] == "feature"
                assert len(update["search_queries"]) == 2

    @pytest.mark.asyncio
    async def test_node_handles_error(self):
        """Test that node handles errors gracefully"""
        from app.graph.nodes.issue_insight_agent_node import IssueInsightAgentNode

        node = IssueInsightAgentNode()

        state: IssueProcessState = {
            "issue_data": {
                "title": "Test issue",
                "description": "Test description",
            },
            "project_info": {"name": "test"},
            "issue_type": None,
            "search_queries": [],
            "retrieved_code": [],
            "executed_nodes": [],
            "current_step": "init",
        }

        # Mock LLM to raise an error
        with patch(
            "app.graph.nodes.issue_insight_agent_node.get_gpt_model"
        ) as mock_get_llm:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.side_effect = Exception("LLM Error")
            mock_get_llm.return_value = mock_llm

            with patch(
                "app.graph.nodes.issue_insight_agent_node.adispatch_custom_event"
            ):
                result = await node(state)

                # Should route to END on error
                assert result.goto == NodeName.END.value
                assert "error" in result.update
                assert "Issue分析失败" in result.update["error"]


class TestCodeRetrieverAgentNode:
    """Test CodeRetrieverAgentNode functionality"""

    @pytest.mark.asyncio
    async def test_retrieve_and_rerank_success(self):
        """Test successful code retrieval and reranking"""
        from app.graph.nodes.code_retriever_agent_node import CodeRetrieverAgentNode

        # Create node
        node = CodeRetrieverAgentNode()

        # Create mock state with search queries
        state: IssueProcessState = {
            "search_queries": [
                "UserService login method implementation",
                "password validation authentication logic",
            ],
            "issue_data": {
                "title": "Login fails with 500 error",
                "description": "UserService.login throws AttributeError when password is wrong",
            },
            "project_info": {
                "name": "test-project",
                "path_with_namespace": "org/test-project",
            },
            "executed_nodes": [NodeName.ISSUE_INSIGHT.value],
            "current_step": NodeName.ISSUE_INSIGHT.value,
        }

        # Mock embedding service
        mock_vectors = [
            [0.1] * 1024,  # Mock 1024-dim vector for query 1
            [0.2] * 1024,  # Mock 1024-dim vector for query 2
        ]

        # Mock Milvus search results
        mock_search_results = [
            {
                "id": 1,
                "distance": 0.95,
                "entity": {
                    "project_name": "test-project",
                    "file_path": "services/user_service.py",
                    "symbol_name": "UserService.login",
                    "language": "python",
                    "start_line": 10,
                    "end_line": 25,
                    "content": "def login(self, username, password): ...",
                    "summary": "User login method",
                    "use_count": 5,
                },
            },
            {
                "id": 2,
                "distance": 0.88,
                "entity": {
                    "project_name": "test-project",
                    "file_path": "utils/auth.py",
                    "symbol_name": "validate_password",
                    "language": "python",
                    "start_line": 5,
                    "end_line": 15,
                    "content": "def validate_password(password, hash): ...",
                    "summary": "Password validation function",
                    "use_count": 3,
                },
            },
        ]

        # Mock rerank results
        mock_rerank_results = [
            {
                "id": 1,
                "distance": 0.95,
                "project_name": "test-project",
                "file_path": "services/user_service.py",
                "symbol_name": "UserService.login",
                "language": "python",
                "start_line": 10,
                "end_line": 25,
                "content": "def login(self, username, password): ...",
                "summary": "User login method",
                "use_count": 5,
                "relevance_score": 0.98,
            },
            {
                "id": 2,
                "distance": 0.88,
                "project_name": "test-project",
                "file_path": "utils/auth.py",
                "symbol_name": "validate_password",
                "language": "python",
                "start_line": 5,
                "end_line": 15,
                "content": "def validate_password(password, hash): ...",
                "summary": "Password validation function",
                "use_count": 3,
                "relevance_score": 0.85,
            },
        ]

        # Mock services
        with patch(
            "app.graph.nodes.code_retriever_agent_node.embedding_service"
        ) as mock_embedding:
            mock_embedding.embed_texts = AsyncMock(return_value=mock_vectors)

            with patch(
                "app.graph.nodes.code_retriever_agent_node.milvus_service"
            ) as mock_milvus:
                mock_milvus.search_similar_code = MagicMock(
                    return_value=mock_search_results
                )

                with patch(
                    "app.graph.nodes.code_retriever_agent_node.rerank_service"
                ) as mock_rerank:
                    mock_rerank.rerank_with_metadata = AsyncMock(
                        return_value=mock_rerank_results
                    )

                    with patch(
                        "app.graph.nodes.code_retriever_agent_node.adispatch_custom_event"
                    ):
                        # Execute node
                        result = await node(state)

                        # Verify result structure
                        assert result is not None
                        assert hasattr(result, "update")
                        assert hasattr(result, "goto")

                        # Verify goto
                        assert result.goto == NodeName.PLAN.value

                        # Verify update dict
                        update = result.update
                        assert "retrieved_code" in update
                        assert len(update["retrieved_code"]) == 2
                        assert "executed_nodes" in update
                        assert NodeName.CODE_RETRIEVER.value in update["executed_nodes"]
                        assert "current_step" in update
                        assert update["current_step"] == NodeName.CODE_RETRIEVER.value

                        # Verify retrieved code has relevance scores
                        for code in update["retrieved_code"]:
                            assert "relevance_score" in code
                            assert "file_path" in code
                            assert "content" in code

                        # Verify services were called correctly
                        mock_embedding.embed_texts.assert_called_once_with(
                            state["search_queries"]
                        )
                        assert mock_milvus.search_similar_code.call_count == 2
                        mock_rerank.rerank_with_metadata.assert_called_once()
