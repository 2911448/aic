import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_root(client: TestClient) -> None:
    """测试根路径"""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "docs" in data


def test_health_check(client: TestClient) -> None:
    """测试健康检查接口"""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


def test_chat(client: TestClient) -> None:
    """测试聊天接口"""
    response = client.post("/api/v1/chat", json={"message": "Hello"})
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "Hello" in data["message"]
