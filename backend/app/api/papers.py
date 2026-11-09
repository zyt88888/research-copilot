"""论文 CRUD、解析触发、去重确认、版本比对。"""
from __future__ import annotations

import os
import shutil
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from ..settings import UPLOAD_DIR
from ..services import dedup, pdf_parser, store

router = APIRouter(prefix="/api/papers", tags=["papers"])


@router.get("")
def list_():
    return {"papers": store.list_papers()}


@router.get("/stats")
def stats():
    papers = store.list_papers()
    ids = [p["id"] for p in papers]
    return {
        "papers": len(papers),
        "chunks": sum(1 for pid in ids for _ in store.chunks_of(pid)),
        "index": store.index_stats(),
    }


@router.get("/discover")
def discover(query: str, limit: int = 10):
    """arXiv 关键词检索：返回候选论文元数据，可传给 scripts/fetch_papers.py 批量入库。"""
    from ..services import arxiv as arxiv_service

    return {"query": query, "candidates": arxiv_service.search(query, limit)}


@router.get("/{paper_id}")
def get_paper(paper_id: str):
    meta = store.paper_meta(paper_id)
    if not meta:
        raise HTTPException(status_code=404, detail="paper not found")
    return meta


@router.get("/{paper_id}/chunks")
def get_chunks(paper_id: str, kinds: str | None = None):
    kind_list = tuple(k.strip() for k in kinds.split(",")) if kinds else None
    return {"paper_id": paper_id, "chunks": store.chunks_of(paper_id, kind_list)}


@router.get("/{paper_id}/refs")
def get_refs(paper_id: str):
    return {"paper_id": paper_id, "refs": store.refs_of(paper_id)}


@router.post("/upload")
async def upload(file: UploadFile = File(...), overwrite_id: str | None = Form(None),
                 force: bool = Form(False)):
    """上传 PDF，自动解析并入库；若检测到重复返回 duplicate 提示。
    overwrite_id: 覆盖指定论文（同一 id 替换内容）；force: 无视重复提示，作为新条目导入。
    """
    name = Path(file.filename or "paper.pdf").name
    if not name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF")
    out_dir = UPLOAD_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    pid = overwrite_id or uuid.uuid4().hex[:12]
    save_path = out_dir / f"{pid}_{name}"
    with save_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    parsed = pdf_parser.parse_pdf(save_path, filename=name)
    dup = dedup.find_duplicates(parsed)
    if dup and not overwrite_id and not force:
        version_diff = dedup.version_diff(dup["paper_id"], parsed) if dup.get("same_arxiv_family") else None
        return {
            "status": "duplicate", "filename": name, "suggestion": dup["suggestion"],
            "duplicate": dup, "version_diff": version_diff, "parsed": {
                "title": parsed["title"], "authors": parsed.get("authors"),
                "year": parsed.get("year"), "num_pages": parsed.get("num_pages"),
                "arxiv_id": parsed.get("arxiv_id"),
            },
        }
    store.save_paper(parsed, str(save_path), pid, overwrite_id=overwrite_id)
    # 异步补全 arXiv 元数据（不阻塞 upload 响应）
    threading.Thread(target=_async_enrich, args=(pid,), daemon=True).start()
    return {
        "status": "ok", "paper_id": pid, "title": parsed["title"],
        "arxiv_id": parsed.get("arxiv_id"), "num_pages": parsed.get("num_pages"),
        "page_flags": parsed.get("page_flags", {}),
        "refs_count": len(parsed.get("refs") or []),
    }


def _async_enrich(paper_id: str) -> None:
    """后台线程薄封装，隔离 import 失败不影响 upload 主链路。"""
    try:
        from ..services.store import enrich_paper
        enrich_paper(paper_id)
    except Exception:
        pass


@router.delete("/{paper_id}")
def delete(paper_id: str):
    store.delete_paper(paper_id)
    return {"ok": True}
