# =============================================================================
# Imagem base
# =============================================================================
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependências de sistema para asyncpg, pgvector, pypdf
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# =============================================================================
# Stage: dependências
# =============================================================================
FROM base AS deps

COPY pyproject.toml ./
RUN pip install --upgrade pip && \
    pip install -e .

# =============================================================================
# Stage final
# =============================================================================
FROM base AS final

COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

COPY . /app

# A API roda na 8000; o worker é chamado por outro CMD no docker-compose
EXPOSE 8000

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
