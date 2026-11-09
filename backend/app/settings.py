"""全局配置：全部通过环境变量注入，密钥不落库、不进代码。"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent

if load_dotenv:
    load_dotenv(REPO_DIR / ".env")
    load_dotenv(BACKEND_DIR / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR", REPO_DIR / "data")).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = Path(os.getenv("DB_PATH", DATA_DIR / "app.db"))

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ---- LLM：任意 OpenAI 兼容网关（DeepSeek / Qwen / OpenAI / 本地 vLLM 均可）----
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "120"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2048"))

# ---- Embedding：auto=有 API Key 走 API，否则本地哈希（离线可用）----
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "auto")  # auto|api|local|hash
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", LLM_BASE_URL).rstrip("/")
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", LLM_API_KEY)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "384"))

# ---- 检索与 RAG ----
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "900"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "120"))
RETRIEVE_TOP_K = int(os.getenv("RETRIEVE_TOP_K", "8"))
DUP_THRESHOLD = float(os.getenv("DUP_THRESHOLD", "0.45"))
REF_MATCH_THRESHOLD = float(os.getenv("REF_MATCH_THRESHOLD", "0.72"))

# ---- OCR：可选依赖，未安装时扫描页会被标记而非静默失败 ----
OCR_ENABLED = os.getenv("OCR_ENABLED", "1") != "0"
OCR_LANG = os.getenv("OCR_LANG", "ch")

CORS_ORIGINS = [o.strip() for o in os.getenv(
    "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
).split(",") if o.strip()]

OFFLINE_MODE = not bool(LLM_API_KEY)
