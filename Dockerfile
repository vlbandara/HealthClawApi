# syntax=docker/dockerfile:1
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir uv

# Pre-install build backend so it's cached before the package build
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system hatchling

# Install heavy transitive deps first (numpy from pgvector, uvloop) —
# separate layer so a timeout only re-fetches what's missing from the uv cache.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system \
        "numpy>=1.26,<3" \
        "pgvector>=0.3.0" \
        "arq>=0.26.0" \
        "croniter>=2.0.0" \
        "uvloop>=0.19"

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic

# --no-build-isolation reuses the already-installed hatchling; all heavy deps
# are already present so this layer only fetches the remaining lighter packages.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --no-build-isolation .

EXPOSE 8000

CMD ["uvicorn", "healthclaw.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
