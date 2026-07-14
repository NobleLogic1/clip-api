from fastapi import HTTPException


class InvalidAPIKeyError(HTTPException):
    def __init__(self):
        super().__init__(status_code=401, detail="Invalid API key")


class RateLimitError(HTTPException):
    def __init__(self, message: str = "Rate limit exceeded. Upgrade to increase limits."):
        super().__init__(status_code=429, detail=message)


class ImageFetchError(HTTPException):
    def __init__(self, message: str = "Failed to fetch or process image"):
        super().__init__(status_code=400, detail=message)


class ModelNotLoadedError(HTTPException):
    def __init__(self):
        super().__init__(status_code=503, detail="CLIP model is not loaded")
