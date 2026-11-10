"""Semantic Scholar 检索（第二学术数据源，与 arXiv 互补）。

免 API Key（公共 Graph API，共享速率限制约 100 次/5 分钟，429 时指数退避）；
如需更高配额可在 backend/.env 配置 S2_API_KEY（https://www.semanticscholar.org/product/api）。
返回结构与 services.arxiv.search 对齐，额外带 source="semanticscholar" 标记。
"""
from __future__ import annotations

import os
import time

import requests

API = "https://api.semanticscholar.org/graph/v1/paper/search"
FIELDS = "title,authors,year,abstract,externalIds,url,openAccessPdf,citationCount,venue"
UA = {"User-Agent": "research-copilot/0.1 (academic demo)"}

# 检索结果短缓存：429 限流场景下显著降低重复请求
_cache: dict[str, tuple[float, list[dict]]] = {}
CACHE_TTL = 300


def search(query: str, limit: int = 10) -> list[dict]:
    """按关键词检索 Semantic Scholar，返回候选论文列表（不入库）。"""
    key = f"{query.strip().lower()}|{limit}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]

    headers = dict(UA)
    api_key = os.environ.get("S2_API_KEY", "").strip()
    if api_key:
        headers["x-api-key"] = api_key
    resp = None
    last = None
    for attempt in range(3):
        try:
            resp = requests.get(API, params={
                "query": query, "limit": max(1, min(limit, 20)), "fields": FIELDS,
            }, headers=headers, timeout=30)
            if resp.status_code == 200:
                break
            last = f"HTTP {resp.status_code}"
            if resp.status_code == 429:
                # 共享配额被打满：退避 5s/15s/30s
                time.sleep(5 + attempt * 10)
                continue
        except Exception as exc:
            last = str(exc)
        time.sleep(2 + attempt * 2)
    else:
        raise RuntimeError(f"Semantic Scholar 请求失败: {last}")
    if resp is None or resp.status_code != 200:
        raise RuntimeError(f"Semantic Scholar 请求失败: {last}")

    out = []
    for p in resp.json().get("data", []):
        ext = p.get("externalIds") or {}
        pdf = (p.get("openAccessPdf") or {}).get("url")
        arxiv_id = ext.get("ArXiv")
        out.append({
            "arxiv_id": arxiv_id,                     # 有 arXiv id 时直接走 arXiv 下载链
            "title": p.get("title") or "",
            "summary": p.get("abstract") or "",
            "authors": [a.get("name", "") for a in p.get("authors") or []],
            "year": p.get("year"),
            "published": str(p.get("year") or ""),
            "pdf_url": (f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else pdf),
            "venue": p.get("venue") or "",
            "citations": p.get("citationCount"),
            "s2_url": p.get("url"),
            "source": "semanticscholar",
        })
    _cache[key] = (time.time(), out)
    return out
