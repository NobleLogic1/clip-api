import asyncio
import ipaddress
import os
import socket
import time
import logging
import threading
from io import BytesIO
from typing import List
from urllib.parse import urlparse

import aiohttp
import torch
from PIL import Image
from transformers import CLIPModel, CLIPProcessor

LOGGER = logging.getLogger("clip-model")

MODEL_ID = os.environ.get("CLIP_MODEL_ID", "openai/clip-vit-base-patch32")
MAX_IMAGE_BYTES = int(os.environ.get("MAX_IMAGE_BYTES", 5_000_000))
IMAGE_TIMEOUT = int(os.environ.get("IMAGE_TIMEOUT", 10))
IMAGE_DOWNLOAD_CONCURRENCY = int(os.environ.get("IMAGE_DOWNLOAD_CONCURRENCY", 10))
ENABLE_CUDA_CACHE_CLEAR = os.environ.get("ENABLE_CUDA_CACHE_CLEAR", "false").lower() == "true"
MODEL_VARIANT = "clip-maintained-2026"


class CLIPModelService:
    def __init__(self):
        self.model = None
        self.processor = None
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self._lock = threading.Lock()

    def load_model(self):
        try:
            LOGGER.info(f"Loading CLIP model '{MODEL_ID}' on device '{self.device}'...")
            self.model = CLIPModel.from_pretrained(MODEL_ID)
            self.processor = CLIPProcessor.from_pretrained(MODEL_ID)
            self.model.to(self.device)
            self.model.eval()
            LOGGER.info("CLIP model loaded successfully.")
        except Exception as exc:
            LOGGER.exception("CLIP model load failed: %s", exc)
            self.model = None
            self.processor = None

    @property
    def is_loaded(self) -> bool:
        return self.model is not None and self.processor is not None

    def _to_device(self, tensor_dict: dict) -> dict:
        return {k: v.to(self.device) for k, v in tensor_dict.items()}

    def _tensor_to_list(self, tensor: torch.Tensor) -> list:
        return tensor.detach().cpu().numpy().tolist()

    def _decode_image(self, raw_bytes: bytes) -> Image.Image:
        return Image.open(BytesIO(raw_bytes)).convert("RGB")

    def _validate_image_url(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("Only http(s) URLs are allowed.")
        host = parsed.hostname
        if not host:
            raise ValueError("Image URL host is required.")
        normalized_host = host.lower()
        if normalized_host in {"localhost", "127.0.0.1", "::1"} or normalized_host.endswith(".local"):
            raise ValueError("Localhost/private network URLs are not allowed.")
        try:
            host_ip = ipaddress.ip_address(normalized_host)
        except ValueError:
            host_ip = None
        if host_ip and (host_ip.is_private or host_ip.is_loopback or host_ip.is_link_local or host_ip.is_reserved or host_ip.is_multicast):
            raise ValueError("Private network IPs are not allowed.")
        return url

    async def _validate_public_host_resolution(self, url: str) -> None:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            raise ValueError("Image URL host is required.")
        try:
            addr_info = await asyncio.to_thread(socket.getaddrinfo, host, parsed.port, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise ValueError(f"Unable to resolve image host: {host}") from exc

        for entry in addr_info:
            resolved_ip = entry[4][0]
            host_ip = ipaddress.ip_address(resolved_ip)
            if host_ip.is_private or host_ip.is_loopback or host_ip.is_link_local or host_ip.is_reserved or host_ip.is_multicast:
                raise ValueError("Image host resolves to a private network address.")

    async def _download_image(self, url: str, session: aiohttp.ClientSession) -> Image.Image:
        url = self._validate_image_url(url)
        await self._validate_public_host_resolution(url)
        timeout = aiohttp.ClientTimeout(total=IMAGE_TIMEOUT, sock_read=IMAGE_TIMEOUT)
        async with session.get(url, timeout=timeout, allow_redirects=False) as resp:
            resp.raise_for_status()
            total = 0
            content = bytearray()
            async for chunk in resp.content.iter_chunked(8192):
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    raise ValueError(f"Image exceeds {MAX_IMAGE_BYTES} byte limit.")
                content.extend(chunk)
        return await asyncio.to_thread(self._decode_image, bytes(content))

    def _encode_texts_sync(self, texts: List[str]) -> list:
        with self._lock:
            inputs = self.processor(text=texts, return_tensors="pt", padding=True, truncation=True)
            inputs = self._to_device(inputs)
            with torch.no_grad():
                features = self.model.get_text_features(**inputs)
                features = features / features.norm(dim=-1, keepdim=True)
            return self._tensor_to_list(features)

    async def encode_texts(self, texts: List[str]) -> dict:
        start = time.time()
        embeddings = await asyncio.to_thread(self._encode_texts_sync, texts)
        return {
            "embeddings": embeddings,
            "count": len(texts),
            "processing_time_ms": (time.time() - start) * 1000,
            "model_variant": MODEL_VARIANT,
        }

    def _encode_images_batch_sync(self, images: List[Image.Image]) -> list:
        with self._lock:
            inputs = self.processor(images=images, return_tensors="pt")
            inputs = self._to_device(inputs)
            with torch.no_grad():
                features = self.model.get_image_features(**inputs)
                features = features / features.norm(dim=-1, keepdim=True)
            return self._tensor_to_list(features)

    async def encode_image(self, image_url: str) -> dict:
        start = time.time()
        async with aiohttp.ClientSession() as session:
            image = await self._download_image(image_url, session)
        embeddings = await asyncio.to_thread(self._encode_images_batch_sync, [image])
        result = {
            "embeddings": embeddings,
            "count": 1,
            "processing_time_ms": (time.time() - start) * 1000,
            "model_variant": MODEL_VARIANT,
        }
        if ENABLE_CUDA_CACHE_CLEAR and torch.cuda.is_available():
            torch.cuda.empty_cache()
        return result

    def _similarity_scores_sync(self, query: str, images: List[Image.Image]) -> List[float]:
        with self._lock:
            text_inputs = self.processor(text=[query], return_tensors="pt", padding=True, truncation=True)
            text_inputs = self._to_device(text_inputs)
            image_inputs = self.processor(images=images, return_tensors="pt")
            image_inputs = self._to_device(image_inputs)
            with torch.no_grad():
                text_features = self.model.get_text_features(**text_inputs)
                text_features = text_features / text_features.norm(dim=-1, keepdim=True)
                image_features = self.model.get_image_features(**image_inputs)
                image_features = image_features / image_features.norm(dim=-1, keepdim=True)
                scores = torch.matmul(image_features, text_features.squeeze(0))
            return scores.detach().cpu().tolist()

    async def similarity_search(self, query: str, image_urls: List[str]) -> dict:
        start = time.time()
        semaphore = asyncio.Semaphore(max(1, IMAGE_DOWNLOAD_CONCURRENCY))
        similarities = []

        async def _fetch(index: int, url: str, session: aiohttp.ClientSession):
            try:
                async with semaphore:
                    image = await self._download_image(url, session)
                return index, url, image, None
            except Exception as exc:
                return index, url, None, str(exc)

        async with aiohttp.ClientSession() as session:
            fetched = await asyncio.gather(*[_fetch(i, url, session) for i, url in enumerate(image_urls)])

        valid_images = []
        valid_indexes = []
        for index, url, image, error in fetched:
            if error is not None:
                LOGGER.warning("Image %s failed: %s", url, error)
                similarities.append({"image_url": url, "similarity_score": 0.0, "rank": index + 1, "error": error})
                continue
            valid_indexes.append(index)
            valid_images.append(image)

        if valid_images:
            scores = await asyncio.to_thread(self._similarity_scores_sync, query, valid_images)
            score_by_index = {valid_indexes[i]: float(scores[i]) for i in range(len(valid_indexes))}
            for i, url in enumerate(image_urls):
                if i in score_by_index:
                    similarities.append({"image_url": url, "similarity_score": score_by_index[i], "rank": i + 1})

        similarities.sort(key=lambda x: x["similarity_score"], reverse=True)
        for idx, item in enumerate(similarities):
            item["rank"] = idx + 1

        if ENABLE_CUDA_CACHE_CLEAR and torch.cuda.is_available():
            torch.cuda.empty_cache()

        return {
            "query": query,
            "results": similarities,
            "processing_time_ms": (time.time() - start) * 1000,
            "model_variant": MODEL_VARIANT,
        }


clip_model_service = CLIPModelService()
