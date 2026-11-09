"""arXiv 检索与下载。

- `search()`     关键词检索（对应赛题"论文自动检索"模块）
- `metadata()`   按 arXiv id 批量取元数据（标题/作者/年份/摘要）
- `download()`   下载 PDF 到本地
- `base_id()`    去掉版本后缀，用于同一论文的家族判断
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

API = "https://export.arxiv.org/api/query"
PDF_URL = "https://arxiv.org/pdf/{id}"
UA = {"User-Agent": "research-copilot/0.1 (academic demo)"}
NS = {"a": "http://www.w3.org/2005/Atom"}
ID_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")


def base_id(arxiv_id: str | None) -> str:
    m = ID_RE.search(arxiv_id or "")
    return m.group(1) if m else (arxiv_id or "")


def _get(url: str, params: dict | None = None, timeout: int = 60) -> requests.Response:
    last = None
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, headers=UA, timeout=timeout)
            if resp.status_code == 200:
                return resp
            last = f"HTTP {resp.status_code}"
        except Exception as exc:  # 网络抖动重试
            last = str(exc)
        time.sleep(2 + attempt * 2)
    raise RuntimeError(f"arXiv 请求失败: {last}")


def _entry_to_dict(entry: ET.Element) -> dict:
    raw_id = (entry.findtext("a:id", "", NS) or "").rsplit("/", 1)[-1]
    published = entry.findtext("a:published", "", NS) or ""
    return {
        "arxiv_id": raw_id,
        "base_id": base_id(raw_id),
        "title": " ".join((entry.findtext("a:title", "", NS) or "").split()),
        "summary": " ".join((entry.findtext("a:summary", "", NS) or "").split()),
        "authors": [a.findtext("a:name", "", NS) for a in entry.findall("a:author", NS)],
        "year": int(published[:4]) if published[:4].isdigit() else None,
        "published": published[:10],
        "pdf_url": PDF_URL.format(id=raw_id),
    }


def search(query: str, limit: int = 20) -> list[dict]:
    """按关键词检索 arXiv，返回候选论文列表（不入库）。"""
    resp = _get(API, {"search_query": f"all:{query}", "start": 0,
                      "max_results": max(1, min(limit, 50)),
                      "sortBy": "relevance"})
    root = ET.fromstring(resp.text)
    return [_entry_to_dict(e) for e in root.findall("a:entry", NS)]


def metadata(ids: list[str]) -> dict[str, dict]:
    """按 id 批量取元数据，返回 {base_id: meta}。"""
    if not ids:
        return {}
    resp = _get(API, {"id_list": ",".join(ids), "max_results": len(ids)})
    root = ET.fromstring(resp.text)
    out: dict[str, dict] = {}
    for e in root.findall("a:entry", NS):
        d = _entry_to_dict(e)
        out[d["base_id"]] = d
    return out


def download(arxiv_id: str, dest_dir: Path) -> Path:
    """下载 PDF；同名文件已存在则直接复用（避免重复下载）。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"{base_id(arxiv_id)}.pdf"
    if target.exists() and target.stat().st_size > 20_000:
        return target
    resp = _get(PDF_URL.format(id=arxiv_id), timeout=120)
    if not resp.content.startswith(b"%PDF"):
        raise RuntimeError("返回内容不是 PDF（可能被限流）")
    target.write_bytes(resp.content)
    return target
