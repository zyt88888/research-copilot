"""后端主入口。启动：python -m uvicorn app.main:app --reload --port 8000"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from .api.chat import router as chat_router
from .api.code import router as code_router
from .api.figures import router as figures_router
from .api.insights import router as insights_router
from .api.papers import router as papers_router
from .api.thumbnails import router as thumbnails_router
from .db import init_db
from .settings import CORS_ORIGINS
from .services import llm, embeddings

app = FastAPI(title="科研小助手 Agent", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _start():
    init_db()


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "llm": llm.is_available(),
        "embedding": embeddings.provider_name(),
        "offline": not llm.is_available(),
    }


app.include_router(papers_router)
app.include_router(chat_router)
app.include_router(code_router)
app.include_router(figures_router)
app.include_router(insights_router)
app.include_router(thumbnails_router)
