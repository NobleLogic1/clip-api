"""Comprehensive test suite covering error paths, webhook edge cases, and resilience."""

import json
import sqlite3
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient


# Stub optional ML dependencies so the app can be imported in test environment
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


# WEBHOOK EDGE CASE TESTS
def test_webhook_missing_signature_header(monkeypatch):
    """Webhook rejects requests without stripe-signature header."""
    with _client(monkeypatch) as client:
        response = client.post("/billing/webhook", content=b"{}", headers={})

    assert response.status_code == 400
    assert "signature" in response.json()["error"]["message"].lower()


def test_webhook_rejects_invalid_signature(monkeypatch):
    """Webhook rejects requests with invalid signature."""

    def fake_construct_webhook_event(payload, sig_header, webhook_secret):
        raise ValueError("Signature verification failed")

    monkeypatch.setattr(app_module.billing, "construct_webhook_event", fake_construct_webhook_event)
    with _client(monkeypatch) as client:
        response = client.post("/billing/webhook", content=b"{}", headers={"stripe-signature": "invalid"})

    assert response.status_code == 400


def test_webhook_checkout_session_completed_full_flow(monkeypatch, tmp_path):
    """Webhook successfully creates customer on checkout.session.completed event."""
    db_path = tmp_path / "clip_api.db"
    monkeypatch.setattr(key_manager, "DB_PATH", str(db_path))
    key_manager.init_db()

    def fake_construct_webhook_event(payload, sig_header, webhook_secret):
        return {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "customer": "cus_test_123",
                    "customer_details": {"email": "user@example.com"},
                    "metadata": {"tier": "professional"},
                    "subscription": "sub_test_123",
                }
            },
        }

    monkeypatch.setattr(app_module.billing, "construct_webhook_event", fake_construct_webhook_event)
    with _client(monkeypatch) as client:
        response = client.post("/billing/webhook", content=b"{}", headers={"stripe-signature": "v1=test"})

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_webhook_subscription_deleted_marks_inactive(monkeypatch, tmp_path):
    """Webhook marks customer inactive on subscription.deleted event."""
    db_path = tmp_path / "clip_api.db"
    monkeypatch.setattr(key_manager, "DB_PATH", str(db_path))
    key_manager.init_db()

    # Create a customer first
    _, api_key = key_manager.create_customer("cus_test_123", "user@example.com", "professional", "sub_test_123")

    def fake_construct_webhook_event(payload, sig_header, webhook_secret):
        return {
            "type": "customer.subscription.deleted",
            "data": {"object": {"customer": "cus_test_123"}},
        }

    monkeypatch.setattr(app_module.billing, "construct_webhook_event", fake_construct_webhook_event)
    with _client(monkeypatch) as client:
        response = client.post("/billing/webhook", content=b"{}", headers={"stripe-signature": "v1=test"})

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert key_manager.validate_api_key(api_key) is None  # Should be inactive


def test_usage_stats_returns_empty_on_db_error(monkeypatch):
    """Usage stats gracefully handles database errors."""

    def raise_db_error():
        raise sqlite3.Error("database is locked")

    monkeypatch.setattr(key_manager, "_get_db", raise_db_error)
    stats = key_manager.get_usage_stats("clip_valid_key")
    assert stats == {}  # Returns empty dict on error


def test_checkout_handles_stripe_api_timeout(monkeypatch):
    """Checkout gracefully handles Stripe API timeouts."""

    def fake_create_session(*args, **kwargs):
        raise TimeoutError("Stripe API request timed out")

    monkeypatch.setattr(
        app_module.billing,
        "create_checkout_session",
        fake_create_session,
    )
    with _client(monkeypatch) as client:
        response = client.post("/billing/checkout", json={"tier": "professional"})

    assert response.status_code == 500
    assert "failed" in response.json()["error"]["message"].lower()


def test_similarity_search_rejects_empty_query(monkeypatch, tmp_path):
    """Similarity search rejects empty query strings."""
    db_path = tmp_path / "clip_api.db"
    monkeypatch.setattr(key_manager, "DB_PATH", str(db_path))
    key_manager.init_db()
    _, api_key = key_manager.create_customer("cus_123", "user@example.com", "professional", "sub_123")

    with _client(monkeypatch) as client:
        response = client.post(
            "/search",
            json={"query": "", "image_urls": ["https://example.com/image.jpg"]},
            headers={"Authorization": f"Bearer {api_key}"},
        )

    assert response.status_code == 422  # Pydantic validation error


def test_similarity_search_rejects_no_images(monkeypatch, tmp_path):
    """Similarity search rejects requests with no images."""
    db_path = tmp_path / "clip_api.db"
    monkeypatch.setattr(key_manager, "DB_PATH", str(db_path))
    key_manager.init_db()
    _, api_key = key_manager.create_customer("cus_123", "user@example.com", "professional", "sub_123")

    with _client(monkeypatch) as client:
        response = client.post(
            "/search",
            json={"query": "cats", "image_urls": []},
            headers={"Authorization": f"Bearer {api_key}"},
        )

    assert response.status_code == 422


def test_similarity_search_validates_url_format(monkeypatch, tmp_path):
    """Similarity search validates URL format."""
    db_path = tmp_path / "clip_api.db"
    monkeypatch.setattr(key_manager, "DB_PATH", str(db_path))
    key_manager.init_db()
    _, api_key = key_manager.create_customer("cus_123", "user@example.com", "professional", "sub_123")

    with _client(monkeypatch) as client:
        response = client.post(
            "/search",
            json={"query": "cats", "image_urls": ["not-a-valid-url"]},
            headers={"Authorization": f"Bearer {api_key}"},
        )

    assert response.status_code == 422  # Invalid URL format


def test_similarity_search_respects_query_length_limit(monkeypatch, tmp_path):
    """Similarity search rejects queries longer than 500 characters."""
    db_path = tmp_path / "clip_api.db"
    monkeypatch.setattr(key_manager, "DB_PATH", str(db_path))
    key_manager.init_db()
    _, api_key = key_manager.create_customer("cus_123", "user@example.com", "professional", "sub_123")

    with _client(monkeypatch) as client:
        response = client.post(
            "/search",
            json={
                "query": "a" * 501,  # Over limit
                "image_urls": ["https://example.com/image.jpg"],
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )

    assert response.status_code == 422
