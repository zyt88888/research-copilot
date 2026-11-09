"""arXiv 拉取并入库的共享流程（HTTP 接口与 Agent 工具共用）。

流程：识别 id → 判重（库内已有同 base_id 直接返回）→ 下载 PDF →
解析 → MinHash 去重 → 落库。所有异常以 RuntimeError 上抛，由调用方转成
HTTP / Agent 错误信息。
"""
from __future__ import annotations

import uuid

from ..settings import UPLOAD_DIR
from . import arxiv, dedup, pdf_parser, store


def fetch_and_ingest(arxiv_id: str, force: bool = False) -> dict:
    aid = (arxiv_id or "").strip()
    if not aid:
        raise RuntimeError("请提供 arXiv id")
    base = arxiv.base_id(aid)
    if not base:
        raise RuntimeError(f"无法识别 arXiv id：{aid}")

    if not force:
        for p in store.list_papers():
            if arxiv.base_id(p.get("arxiv_id")) == base:
                return {"status": "exists", "paper_id": p["id"],
                        "arxiv_id": p.get("arxiv_id"), "title": p.get("title")}

    try:
        path = arxiv.download(aid, UPLOAD_DIR)
    except RuntimeError as exc:
        raise RuntimeError(str(exc)) from exc

    parsed = pdf_parser.parse_pdf(path, filename=path.name)
    if not parsed.get("arxiv_id"):
        parsed["arxiv_id"] = aid
    dup = dedup.find_duplicates(parsed)
    if dup and not force:
        return {"status": "duplicate", "filename": path.name,
                "suggestion": dup.get("suggestion"),
                "paper_id": dup.get("paper_id"),
                "title": parsed.get("title")}
    pid = uuid.uuid4().hex[:12]
    store.save_paper(parsed, str(path), pid)
    return {"status": "ok", "paper_id": pid, "arxiv_id": parsed.get("arxiv_id"),
            "title": parsed.get("title"), "num_pages": parsed.get("num_pages"),
            "refs_count": len(parsed.get("refs") or [])}
