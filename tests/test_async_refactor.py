import asyncio
import sys
import types
from contextlib import contextmanager
from types import SimpleNamespace

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

from clip_api.routes import encode as encode_routes
from clip_api.routes import similarity as similarity_routes
from clip_api.services import key_manager
from clip_api.services.clip_model import CLIPModelService, clip_model_service
from clip_api.utils import auth


def test_encode_text_route_awaits_async_service(monkeypatch):
    async def fake_encode_texts(texts):
        return {"embeddings": [[1.0, 2.0]], "count": 1}

    monkeypatch.setattr(clip_model_service, "model", object())
    monkeypatch.setattr(clip_model_service, "processor", object())
    monkeypatch.setattr(clip_model_service, "encode_texts", fake_encode_texts)

    req = encode_routes.EncodeTextRequest(text="hello")
    result = asyncio.run(encode_routes.encode_text(req))

    assert result == {"embedding": [1.0, 2.0], "dimensions": 2}


def test_similarity_search_route_awaits_async_service(monkeypatch):
    async def fake_similarity_search(query, image_urls):
        return {"query": query, "results": [{"image_url": image_urls[0], "similarity_score": 0.9, "rank": 1}]}

    monkeypatch.setattr(clip_model_service, "model", object())
    monkeypatch.setattr(clip_model_service, "processor", object())
    monkeypatch.setattr(clip_model_service, "similarity_search", fake_similarity_search)

    req = similarity_routes.SimilaritySearchRequest(query="cat", image_urls=["https://example.com/cat.jpg"])
    result = asyncio.run(similarity_routes.similarity_search(req))

    assert result["query"] == "cat"
    assert result["results"][0]["similarity_score"] == 0.9


def test_key_manager_reuses_persistent_connection(monkeypatch, tmp_path):
    db_path = tmp_path / "clip_api.db"
    monkeypatch.setattr(key_manager, "DB_PATH", str(db_path))
    key_manager.close_db()

    conn_a = key_manager._get_db()
    conn_b = key_manager._get_db()

    assert conn_a is conn_b

    key_manager.close_db()
    conn_c = key_manager._get_db()
    assert conn_c is not conn_a

    key_manager.close_db()


def test_rate_limit_in_memory_fallback(monkeypatch):
    monkeypatch.setattr(auth, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(auth, "RATE_LIMIT_REQUESTS_PER_MINUTE", 1)
    monkeypatch.setattr(auth, "_redis_rate_limit_client", None)
    monkeypatch.setattr(auth, "_redis_rate_limit_enabled", False)
    monkeypatch.setattr(auth, "_redis_init_attempted", True)
    auth._recent_requests.clear()

    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    assert asyncio.run(auth._check_rate_limit(request)) is False
    assert asyncio.run(auth._check_rate_limit(request)) is True


def test_similarity_search_batches_successful_images(monkeypatch):
    service = CLIPModelService()
    seen = {}

    async def fake_download(url, session):
        if "bad" in url:
            raise ValueError("download failed")
        return object()

    def fake_scores(query, images):
        seen["query"] = query
        seen["batch_size"] = len(images)
        return [0.9, 0.4]

    monkeypatch.setattr(service, "_download_image", fake_download)
    monkeypatch.setattr(service, "_similarity_scores_sync", fake_scores)

    result = asyncio.run(
        service.similarity_search(
            "cat",
            ["https://example.com/a.jpg", "https://example.com/bad.jpg", "https://example.com/c.jpg"],
        )
    )

    assert seen == {"query": "cat", "batch_size": 2}
    assert len(result["results"]) == 3
    failed = [item for item in result["results"] if "error" in item]
    assert len(failed) == 1
    assert failed[0]["similarity_score"] == 0.0
    assert result["results"][0]["image_url"] == "https://example.com/a.jpg"
    assert result["results"][0]["rank"] == 1
    assert result["results"][1]["image_url"] == "https://example.com/c.jpg"
    assert result["results"][1]["rank"] == 2
