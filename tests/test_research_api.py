from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest

from api.deps import get_database_manager
from api.v1.endpoints.agent import router
from src.storage import DatabaseManager


@pytest.fixture
def client(tmp_path):
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'api.db'}")
    app = FastAPI()

    @app.middleware('http')
    async def test_identity(request: Request, call_next):
        request.state.tenant_id = 'tenant'
        request.state.owner_id = request.headers.get('x-test-owner', 'alice')
        return await call_next(request)

    app.include_router(router, prefix='/api/v1')
    app.dependency_overrides[get_database_manager] = lambda: db
    with TestClient(app) as result:
        yield result
    DatabaseManager.reset_instance()


def test_alert_routes_validate_inputs_and_isolate_owners(client):
    prefix = '/api/v1/agent/research-alerts'
    payload = {'name': '历史变化', 'target': '600519'}
    created = client.post(prefix, json=payload)
    assert created.status_code == 200
    rule = created.json()
    assert rule['enabled'] is False and rule['notification_enabled'] is False
    assert client.get(prefix).json()['items'][0]['id'] == rule['id']
    assert client.get(prefix, headers={'x-test-owner': 'bob'}).json()['items'] == []
    assert client.put(f"{prefix}/{rule['id']}", json=payload, headers={'x-test-owner': 'bob'}).status_code == 404
    assert client.post(f"{prefix}/{rule['id']}/check", headers={'x-test-owner': 'bob'}).status_code == 404
    assert client.post(f"{prefix}/{rule['id']}/check").json()['status'] == 'disabled'
    assert client.get(prefix + '/history').json()['items'] == []
    assert client.post(prefix, json={**payload, 'parameters': {'kinds': []}}).status_code == 422
    assert client.post(prefix, json={**payload, 'target_scope': 'watchlist_group', 'target': '999'}).status_code == 422
    assert client.post(prefix, json={**payload, 'owner_id': 'bob'}).status_code == 422


def test_research_routes_use_stored_data_without_model_or_notification_calls(client):
    assert client.get('/api/v1/agent/financial-conclusions').json() == {'items': []}
    refreshed = client.post('/api/v1/agent/financial-conclusions/refresh')
    assert refreshed.status_code == 200
    assert refreshed.json()['conclusions_scanned'] == 0
