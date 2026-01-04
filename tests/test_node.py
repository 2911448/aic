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
