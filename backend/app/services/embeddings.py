"""Embedding 抽象：API / 本地 BGE / 哈希向量，三层降级保证离线可跑。"""
from __future__ import annotations

import hashlib
import logging
import re
import threading

import numpy as np
import requests

from ..settings import (EMBEDDING_API_KEY, EMBEDDING_BASE_URL, EMBEDDING_DIM,
                        EMBEDDING_MODEL, EMBEDDING_PROVIDER)

log = logging.getLogger(__name__)
_lock = threading.Lock()
_local_model = None
_api_failed = False

WORD = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


def _resolve_provider() -> str:
    if EMBEDDING_PROVIDER in ("api", "local", "hash"):
        return EMBEDDING_PROVIDER
    return "api" if EMBEDDING_API_KEY else "hash"


def _hash_embed_one(text: str) -> np.ndarray:
    """signed feature hashing：词 + 字符 n-gram，兼容中英文，无需下载模型。"""
    vec = np.zeros(EMBEDDING_DIM, dtype=np.float32)
    tokens = [t.lower() for t in WORD.findall(text or "")]

    def add(feature: str, weight: float):
        h = int.from_bytes(hashlib.blake2b(feature.encode("utf-8", "ignore"),
                                           digest_size=8).digest(), "big")
        idx = h % EMBEDDING_DIM
        sign = 1.0 if (h >> 63) & 1 else -1.0
        vec[idx] += sign * weight

    for tok in tokens:
        add(f"w:{tok}", 1.0)
        if re.match(r"[\u4e00-\u9fff]", tok):
            for ch in tok:
                add(f"c:{ch}", 0.6)
    for a, b in zip(tokens, tokens[1:]):
        add(f"b:{a}_{b}", 0.8)
    raw = "".join(tokens)
    for i in range(0, max(len(raw) - 2, 0), 2):
        add(f"t:{raw[i:i + 3]}", 0.3)
    norm = np.linalg.norm(vec)
    return vec / norm if norm else vec


def _api_embed(texts: list[str]) -> list[np.ndarray]:
    global _api_failed
    if _api_failed:
        raise RuntimeError("embedding api disabled after previous failure")
    headers = {"Authorization": f"Bearer {EMBEDDING_API_KEY}",
               "Content-Type": "application/json"}
    out: list[np.ndarray] = []
    for i in range(0, len(texts), 16):
        batch = texts[i:i + 16]
        resp = requests.post(
            f"{EMBEDDING_BASE_URL}/embeddings",
            headers=headers,
            json={"model": EMBEDDING_MODEL, "input": batch},
            timeout=60,
        )
        if resp.status_code != 200:
            _api_failed = True
            raise RuntimeError(f"embedding HTTP {resp.status_code}: {resp.text[:160]}")
        data = resp.json()["data"]
        out.extend(np.asarray(d["embedding"], dtype=np.float32) for d in data)
    return out


def _local_embed(texts: list[str]) -> list[np.ndarray]:
    global _local_model
    with _lock:
        if _local_model is None:
            from sentence_transformers import SentenceTransformer  # 可选依赖
            _local_model = SentenceTransformer(EMBEDDING_MODEL)
    vecs = _local_model.encode(texts, normalize_embeddings=True, batch_size=16)
    return [np.asarray(v, dtype=np.float32) for v in vecs]


def embed_texts(texts: list[str]) -> np.ndarray:
    """返回 (n, dim) 的 L2 归一化矩阵；API/本地失败自动回退哈希向量。"""
    if not texts:
        return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
    provider = _resolve_provider()
    if provider == "api":
        try:
            vecs = _api_embed(texts)
            return _normalize_matrix(vecs)
        except Exception as exc:
            log.warning("embedding api failed, fallback to hash: %s", exc)
    elif provider == "local":
        try:
            vecs = _local_embed(texts)
            return _normalize_matrix(vecs)
        except Exception as exc:
            log.warning("local embedding unavailable, fallback to hash: %s", exc)
    return _normalize_matrix([_hash_embed_one(t) for t in texts])


def _normalize_matrix(vecs: list[np.ndarray]) -> np.ndarray:
    dim = len(vecs[0]) if vecs else EMBEDDING_DIM
    mat = np.zeros((len(vecs), dim), dtype=np.float32)
    for i, v in enumerate(vecs):
        if len(v) != dim:  # 服务端维度不一致时截断/补零，避免崩溃
            fixed = np.zeros(dim, dtype=np.float32)
            fixed[:min(dim, len(v))] = v[:min(dim, len(v))]
            v = fixed
        n = np.linalg.norm(v)
        mat[i] = v / n if n else v
    return mat


def embed_query(text: str) -> np.ndarray:
    return embed_texts([text])[0]


def provider_name() -> str:
    return _resolve_provider()
