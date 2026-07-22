# Multi-stage build for optimized, secure production image
FROM python:3.11-slim as builder

WORKDIR /build

# Install build dependencies only in builder stage
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install CPU-only PyTorch first (saves significant space)
RUN pip install --no-cache-dir --user torch torchvision \
    --index-url https://download.pytorch.org/whl/cpu

# Install remaining dependencies
RUN pip install --no-cache-dir --user -r requirements.txt

# Runtime stage
FROM python:3.11-slim

WORKDIR /app

# Create non-root user for security
RUN useradd -m -u 1000 appuser && \
    apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /root/.local /home/appuser/.local

# Copy application code
COPY --chown=appuser:appuser . .

# Set environment
ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/app/model-cache \
    PORT=7860

# Create directories with correct permissions
RUN mkdir -p /app/model-cache /app/logs /data && \
    chown -R appuser:appuser /app /data

# Switch to non-root user
USER appuser

# Pre-download CLIP model during build
RUN python -c "from transformers import CLIPModel, CLIPProcessor; \
    CLIPModel.from_pretrained('openai/clip-vit-base-patch32'); \
    CLIPProcessor.from_pretrained('openai/clip-vit-base-patch32')"

EXPOSE 7860

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -f http://localhost:${PORT:-7860}/health || exit 1

# Production server (Gunicorn + Uvicorn workers)
# Falls back to simple uvicorn if gunicorn not preferred
CMD ["sh", "-c", "gunicorn clip_api.app:app --bind 0.0.0.0:${PORT:-7860} --workers ${WORKERS:-2} --worker-class uvicorn.workers.UvicornWorker --timeout 120 --access-logfile - --error-logfile - || python app.py"]
