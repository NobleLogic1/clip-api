import sqlite3
import sys
import types
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient


# Stub optional ML dependencies so the app can be imported in the test environment.
if "torch" not in sys.modules:
    torch_stub = types.ModuleType("torch")

    class _DummyTensor:
        pass

    torch_stub.Tensor = _DummyTensor
    torch_stub.cuda = types.SimpleNamespace(is_available=lambda: False, empty_cache=lambda: None)
    torch_stub.device = lambda name: name

    @contextmanager
    def _no_grad():
        yield

    torch_stub.no_grad = _no_grad
    sys.modules["torch"] = torch_stub

if "transformers" not in sys.modules:
    transformers_stub = types.ModuleType("transformers")

    class _DummyModel:
        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            return cls()

        def to(self, *args, **kwargs):
            return self

        def eval(self):
            return self

    class _DummyProcessor:
        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            return cls()

    transformers_stub.CLIPModel = _DummyModel
    transformers_stub.CLIPProcessor = _DummyProcessor
    sys.modules["transformers"] = transformers_stub

import clip_api.app as app_module
from clip_api.app import app
from clip_api.services import key_manager


def _client(monkeypatch):
    monkeypatch.setattr(app_module.clip_model_service, "load_model", lambda: None)
    return TestClient(app)


def test_checkout_rejects_invalid_tier(monkeypatch):
    with _client(monkeypatch) as client:
        response = client.post("/billing/checkout", json={"tier": "bogus", "email": "user@example.com"})

    assert response.status_code == 400
    assert response.json()["error"]["message"].startswith("Invalid tier")


def test_portal_rejects_invalid_api_key(monkeypatch):
    with _client(monkeypatch) as client:
        response = client.post("/billing/portal", json={"api_key": "bad-key"})

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Invalid or inactive API key"


def test_webhook_ignores_missing_customer(monkeypatch):
    def fake_construct_webhook_event(payload, sig_header, webhook_secret):
        return {"type": "checkout.session.completed", "data": {"object": {}}}

    monkeypatch.setattr(app_module.billing, "construct_webhook_event", fake_construct_webhook_event)
    with _client(monkeypatch) as client:
        response = client.post("/billing/webhook", content=b"{}", headers={"stripe-signature": "v1=test"})

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_validate_api_key_rejects_revoked_key(monkeypatch, tmp_path):
    db_path = tmp_path / "clip_api.db"
    monkeypatch.setattr(key_manager, "DB_PATH", str(db_path))
    key_manager.init_db()
    _, api_key = key_manager.create_customer("cus_123", "user@example.com", "developer", "sub_123")

    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE api_keys SET revoked = 1 WHERE key = ?", (api_key,))
        conn.commit()

    assert key_manager.validate_api_key(api_key) is None


def test_database_errors_return_empty_usage_stats(monkeypatch):
    def raise_db_error():
        raise sqlite3.Error("db down")

    monkeypatch.setattr(key_manager, "_get_db", raise_db_error)
    assert key_manager.get_usage_stats("clip_test") == {}
