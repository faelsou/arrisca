"""Pacote de ingestão de documentos.

Re-exporta a task `ingest_document` para permitir o import idiomático:

    from apps.worker.ingestion import ingest_document
    ingest_document.delay(document_id)
"""

from apps.worker.ingestion.celery_app import celery, ingest_document, healthcheck

__all__ = ["celery", "ingest_document", "healthcheck"]
