"""Celery app + task de ingestão.

A lógica do pipeline mora em `apps.worker.ingestion.pipeline` (funções
puras e testáveis). Esta camada só cuida do Celery: fila, retry com
backoff exponencial e a ponte sync→async (tasks Celery são síncronas;
o pipeline usa asyncpg/httpx/openai assíncronos via asyncio.run).
"""

from __future__ import annotations

import asyncio
import os

from celery import Celery

from apps.worker.ingestion.pipeline import IngestionError, run_ingestion

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

celery = Celery(
    "ingestion",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    # Ingestão é I/O-bound e pesada — um doc por worker process por vez.
    worker_prefetch_multiplier=1,
)


@celery.task(name="ingestion.healthcheck")
def healthcheck() -> str:
    """Task de teste — confirma que o worker está vivo e processando."""
    return "ok"


@celery.task(
    name="ingestion.ingest_document",
    bind=True,
    autoretry_for=(Exception,),
    # Erros de "culpa do documento" (formato inválido, senha, vazio) não
    # se resolvem com retry — falham direto e ficam registrados no banco.
    dont_autoretry_for=(IngestionError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def ingest_document(self, document_id: str) -> dict:
    """Ingesta um documento: download → extração → chunking → embeddings
    → insert transacional em document_chunks.

    O status/erro fica sempre registrado em `documents` (o próprio
    pipeline marca processing/completed/failed), então o frontend pode
    fazer polling em GET /documents sem consultar o Celery.
    """
    return asyncio.run(run_ingestion(document_id))


celery.autodiscover_tasks(["apps.worker.ingestion"])
