import json
import os
import subprocess
import sys

from fastapi.testclient import TestClient
import httpx
import pytest

from app import config, llm
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_MODEL", "")
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.example/v1")
    config.get_settings.cache_clear()
    with TestClient(app) as client:
        yield client
    config.get_settings.cache_clear()


def payload(client, **overrides):
    return {"base_url": "https://provider.example/v1", "model": "test-model",
            "api_key": "test-only-secret", "revision": client.get("/api/model-config").json()["revision"], **overrides}


def test_save_secret_write_only_and_worker_process_reload(client):
    before = client.get("/api/model-config").json()
    assert not before["has_api_key"]
    response = client.put("/api/model-config", json=payload(client))
    assert response.status_code == 200
    assert "test-only-secret" not in response.text and "api_key" not in response.json()
    assert json.loads(config.model_config_path().read_text())["api_key"] == "test-only-secret"
    assert config.get_settings().llm_api_key == "test-only-secret"
    # A fresh process represents the separately running worker's dynamic settings read.
    result = subprocess.run([sys.executable, "-c",
        "from app.config import get_settings; s=get_settings(); assert s.llm_model=='test-model'; assert bool(s.llm_api_key); print('loaded')"],
        env=os.environ.copy(), capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "loaded"
    for route in ["/api/model-config", "/api/health", "/api/skills"]:
        assert "test-only-secret" not in client.get(route).text
    assert client.get("/api/health").json()["model_configured"]


def test_blank_preserves_clear_disables_and_stale_revision_conflicts(client):
    original = payload(client)
    assert client.put("/api/model-config", json=original).status_code == 200
    assert client.put("/api/model-config", json=original).status_code == 409
    assert client.put("/api/model-config", json=payload(client, api_key=None, model="second")).status_code == 200
    assert config.get_settings().llm_api_key == "test-only-secret"
    assert client.put("/api/model-config", json=payload(client, api_key=None, clear_key=True)).status_code == 200
    assert not client.get("/api/health").json()["model_configured"]
    config.get_settings.cache_clear()
    assert not config.get_settings().llm_api_key


def test_changed_destination_never_reuses_existing_key(client, monkeypatch):
    client.put("/api/model-config", json=payload(client))
    monkeypatch.setattr(llm, "request_json", lambda *a, **k: pytest.fail("must not send old key"))
    for endpoint, method in [("/api/model-config", client.put), ("/api/model-config/test", client.post)]:
        response = method(endpoint, json=payload(client, base_url="https://another.example/v1", api_key=None))
        assert response.status_code == 422
    assert config.get_settings().llm_base_url == "https://provider.example/v1"


@pytest.mark.parametrize("address", ["https://user:secret@example.com/v1", "http://remote.example/v1",
    "https://example.com/v1?api_key=secret", "file:///tmp/model", "https://example.com/v1/chat/completions"])
def test_invalid_address_never_echoes_values(client, address):
    response = client.put("/api/model-config", json=payload(client, base_url=address))
    assert response.status_code == 422
    assert "test-only-secret" not in response.text and address not in response.text
    assert not config.model_config_path().exists()


def test_malformed_secret_validation_and_cross_origin(client):
    for bad in [123, {"key": "test-only-secret"}, "test-only-secret\nheader"]:
        response = client.put("/api/model-config", json=payload(client, api_key=bad))
        assert response.status_code == 422
        assert "test-only-secret" not in response.text
    assert client.put("/api/model-config", json=payload(client), headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/model-config/test", content="api_key=secret", headers={"Content-Type": "text/plain"}).status_code == 415


def test_connection_tests_draft_without_saving_and_uses_real_transport(client, monkeypatch):
    requests = []
    real_client = httpx.Client
    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert body["model"] == "draft-model" and body["max_tokens"] == 128
        assert request.headers["Authorization"] == "Bearer test-only-secret"
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}], "usage": {"total_tokens": 20}})
    monkeypatch.setattr(llm.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    result = client.post("/api/model-config/test", json=payload(client, model="draft-model"))
    assert result.status_code == 200 and result.json()["ok"]
    assert len(requests) == 1 and requests[0].url.path == "/v1/chat/completions"
    assert not config.model_config_path().exists()
    assert not client.get("/api/health").json()["model_configured"]
    assert "test-only-secret" not in result.text


@pytest.mark.parametrize("status", [401, 429, 500, 302])
def test_connection_failure_redacts_provider_response(client, monkeypatch, status):
    real_client = httpx.Client
    def handler(request):
        return httpx.Response(status, text="provider echoed test-only-secret", headers={"Location": "https://other.example"})
    monkeypatch.setattr(llm.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    response = client.post("/api/model-config/test", json=payload(client))
    assert response.status_code == 502 and "test-only-secret" not in response.text
    assert not config.model_config_path().exists()


def test_missing_key_and_timeout(client, monkeypatch):
    assert client.post("/api/model-config/test", json=payload(client, api_key=None)).status_code == 422
    real_client = httpx.Client
    def handler(request):
        raise httpx.ReadTimeout("test-only-secret", request=request)
    monkeypatch.setattr(llm.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    response = client.post("/api/model-config/test", json=payload(client))
    assert response.status_code == 502 and "test-only-secret" not in response.text
    assert "重复计费" in response.json()["detail"]


@pytest.mark.parametrize("host,expected", [("api.deepseek.com", {"type": "disabled"}), ("compatible.example", None)])
def test_deepseek_bounded_output_mode_only_for_official_host(client, monkeypatch, host, expected):
    monkeypatch.delenv("LLM_DEEPSEEK_THINKING", raising=False)
    real_client = httpx.Client
    def handler(request):
        assert json.loads(request.content).get("thinking") == expected
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]})
    monkeypatch.setattr(llm.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    response = client.post("/api/model-config/test", json=payload(client, base_url=f"https://{host}", model="deepseek-flash"))
    assert response.status_code == 200
