from fastapi.testclient import TestClient

from src.app import app

client = TestClient(app)


def test_health_returns_ok_when_db_connected():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "connected"}
