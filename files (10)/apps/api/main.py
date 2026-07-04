"""FastAPI app principal.

Inicializa o pool de conexões, registra rotas, configura CORS.
Rodar com: uvicorn apps.api.main:app --reload
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.api.routers import admin, conversations, documents, me
from core.db.pool import close_pool, init_pool


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "info").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("arrisca")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("inicializando pool de conexões...")
    await init_pool()
    log.info("pronto")
    try:
        yield
    finally:
        log.info("fechando pool...")
        await close_pool()


app = FastAPI(
    title="Arrisca SAAS",
    version="0.1.0",
    description="Chat conversacional com IA para o Grupo Arrisca",
    lifespan=lifespan,
)

# CORS
origins = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rotas
app.include_router(me.router)
app.include_router(conversations.router)
app.include_router(documents.router)
app.include_router(admin.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
