"""Celery app + tasks de ingestão.

Estado atual: stubs. A implementação real do pipeline (download,
extração de texto, chunking com overlap, embeddings em lote via OpenAI,
inserção transacional) entra aqui depois.

Por ora a task `ingest_document` existe apenas para que
`apps.api.routers.documents` consiga importar e enfileirar — a execução
em si retorna NotImplementedError.
"""

from __future__ import annotations

import os
from uuid import UUID

from celery import Celery

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
)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@celery.task(name="ingestion.healthcheck")
def healthcheck() -> str:
    """Task de teste — confirma que o worker está vivo e processando."""
    return "ok"


@celery.task(
    name="ingestion.ingest_document",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def ingest_document(self, document_id: str) -> dict:
    """STUB do pipeline de ingestão de um documento.

    Pipeline real (a implementar):
        1. Marca documento como `ingesting` no banco.
        2. Baixa o arquivo do source_uri (S3, URL pública, etc.).
        3. Extrai texto (PyMuPDF para PDF, python-docx para DOCX,
           leitura direta para TXT).
        4. Chunka com overlap (~800 tokens / 100 overlap).
        5. Gera embeddings em lote (OpenAI text-embedding-3-small).
        6. INSERT transacional em document_chunks com tenant_id,
           area_id, sensitivity, is_current vindos do parent document
           (trigger no Postgres mantém sync depois).
        7. Marca documento como `ready`.

    Por enquanto: levanta NotImplementedError. O retry exponencial vai
    estourar `max_retries` e a task vai para o estado FAILURE — o
    endpoint que enfileirou já retornou 202 ao usuário, então a falha
    fica registrada no Celery e no audit log sem afetar a UI.
    """
    _ = (self, document_id)
    raise NotImplementedError(
        f"Pipeline de ingestão do documento {document_id} ainda não implementado."
    )


celery.autodiscover_tasks(["apps.worker.ingestion"])
