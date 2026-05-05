FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --upgrade pip \
    && pip install \
        "fastapi>=0.115" "uvicorn[standard]>=0.30" \
        "motor>=3.5" "pymongo>=4.8" "httpx>=0.27" \
        "python-multipart>=0.0.9" "pydantic>=2.7" \
        "bcrypt>=4.1" "PyJWT>=2.8" \
        "redis>=5.0" "prometheus-client>=0.20" \
        "yake>=0.4.8"

COPY app ./app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8080/api/healthz || exit 1

CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
