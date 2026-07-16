import os
import time
import logging
import threading
from io import BytesIO
from typing import List

import torch
import requests
from PIL import Image
from transformers import CLIPModel, CLIPProcessor

LOGGER = logging.getLogger("clip-model")

MODEL_ID = os.environ.get("CLIP_MODEL_ID", "openai/clip-vit-base-patch32")
MAX_IMAGE_BYTES = int(os.environ.get("MAX_IMAGE_BYTES", 5_000_000))
IMAGE_TIMEOUT = int(os.environ.get("IMAGE_TIMEOUT", 10))
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

    def _download_image(self, url: str) -> Image.Image:
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("Only http(s) URLs are allowed.")
        resp = requests.get(url, timeout=IMAGE_TIMEOUT, stream=True, allow_redirects=False)
        resp.raise_for_status()
        content = BytesIO()
        total = 0
        for chunk in resp.iter_content(chunk_size=8192):
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_IMAGE_BYTES:
                raise ValueError(f"Image exceeds {MAX_IMAGE_BYTES} byte limit.")
            content.write(chunk)
        content.seek(0)
        return Image.open(content).convert("RGB")

    def encode_texts(self, texts: List[str]) -> dict:
        start = time.time()
        inputs = self.processor(text=texts, return_tensors="pt", padding=True, truncation=True)
        inputs = self._to_device(inputs)
        with torch.no_grad():
            features = self.model.get_text_features(**inputs)
            features = features / features.norm(dim=-1, keepdim=True)
        return {
            "embeddings": self._tensor_to_list(features),
            "count": len(texts),
            "processing_time_ms": (time.time() - start) * 1000,
            "model_variant": MODEL_VARIANT,
        }

    def encode_image(self, image_url: str) -> dict:
        start = time.time()
        image = self._download_image(image_url)
        inputs = self.processor(images=image, return_tensors="pt")
        inputs = self._to_device(inputs)
        with torch.no_grad():
            features = self.model.get_image_features(**inputs)
            features = features / features.norm(dim=-1, keepdim=True)
        result = {
            "embeddings": self._tensor_to_list(features),
            "count": 1,
            "processing_time_ms": (time.time() - start) * 1000,
            "model_variant": MODEL_VARIANT,
        }
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        return result

    def similarity_search(self, query: str, image_urls: List[str]) -> dict:
        start = time.time()
        text_inputs = self.processor(text=[query], return_tensors="pt", padding=True, truncation=True)
        text_inputs = self._to_device(text_inputs)
        with torch.no_grad():
            text_features = self.model.get_text_features(**text_inputs)
            text_features = text_features / text_features.norm(dim=-1, keepdim=True)
        text_vec = text_features.squeeze(0)

        similarities = []
        for i, url in enumerate(image_urls):
            try:
                image = self._download_image(url)
                img_inputs = self.processor(images=image, return_tensors="pt")
                img_inputs = self._to_device(img_inputs)
                with torch.no_grad():
                    img_features = self.model.get_image_features(**img_inputs)
                    img_features = img_features / img_features.norm(dim=-1, keepdim=True)
                sim = torch.cosine_similarity(text_features, img_features).item()
                similarities.append({"image_url": url, "similarity_score": sim, "rank": i + 1})
            except Exception as e:
                LOGGER.warning("Image %s failed: %s", url, str(e))
                similarities.append({"image_url": url, "similarity_score": 0.0, "rank": i + 1, "error": str(e)})

        similarities.sort(key=lambda x: x["similarity_score"], reverse=True)
        for idx, item in enumerate(similarities):
            item["rank"] = idx + 1

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        return {
            "query": query,
            "results": similarities,
            "processing_time_ms": (time.time() - start) * 1000,
            "model_variant": MODEL_VARIANT,
        }


clip_model_service = CLIPModelService()
