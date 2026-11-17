"""论文 CRUD、解析触发、去重确认、版本比对。"""
from __future__ import annotations

import html as html_lib
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from ..settings import UPLOAD_DIR
from ..services import (citation_quality, dedup, drill, figures, jobs, parse_worker,
                        pdf_parser, store)

router = APIRouter(prefix="/api/papers", tags=["papers"])

# 解析子进程池：常驻 1 个 worker，避免每次上传都重新加载 OCR 模型
_EXECUTOR: ProcessPoolExecutor | None = None
_EXEC_LOCK = threading.Lock()


def _parse_executor() -> ProcessPoolExecutor:
    global _EXECUTOR
    if _EXECUTOR is None:
        with _EXEC_LOCK:
            if _EXECUTOR is None:
                _EXECUTOR = ProcessPoolExecutor(max_workers=1)
    return _EXECUTOR


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
def discover(query: str, limit: int = 10, source: str = "arxiv"):
    """在线学术检索：source=arxiv（默认）| s2（Semantic Scholar）| both。

    返回候选论文元数据（不入库），可传给 fetch-arxiv 或 scripts/fetch_papers.py 入库。
    """
    from ..services import arxiv as arxiv_service
    from ..services import scholar as scholar_service

    candidates: list[dict] = []
    errors: dict[str, str] = {}
    if source in ("arxiv", "both"):
        try:
            candidates += arxiv_service.search(query, limit)
        except RuntimeError as exc:
            errors["arxiv"] = str(exc)
    if source in ("s2", "both"):
        try:
            candidates += scholar_service.search(query, limit)
        except RuntimeError as exc:
            errors["semanticscholar"] = str(exc)
    if not candidates and errors:
        raise HTTPException(status_code=502, detail="；".join(errors.values()))
    out = {"query": query, "source": source, "candidates": candidates}
    if errors:
        out["partial_errors"] = errors  # 单源失败不拖垮另一源
    return out


@router.post("/fetch-arxiv")
def fetch_arxiv(arxiv_id: str = Form(...), force: bool = Form(False)):
    """按 arXiv id 自动下载 PDF 并入库（赛题「给定论文名称/参考文献自动下钻下载」）。

    arxiv_id 可带版本后缀（如 1706.03762v7）或完整 URL。
    force=True 时即使库里已有同一论文也作为新版本导入。
    """
    from ..services import ingest

    try:
        return ingest.fetch_and_ingest(arxiv_id, force=force)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/{paper_id}")
def get_paper(paper_id: str):
    meta = store.paper_meta(paper_id)
    if not meta:
        raise HTTPException(status_code=404, detail="paper not found")
    # 机构信息由首页文本按关键词保守抽取（扫描件用 OCR 文本），不编造
    meta["affiliations"] = store.affiliations_of(paper_id)
    return meta


@router.get("/{paper_id}/figures")
def list_figures(paper_id: str, limit: int = 40):
    """列出论文中的图块（页码 + 图注），图片按需裁切。"""
    figs = [c for c in store.chunks_of(paper_id, ("figure",))]
    if not figs:
        # 老库没有 figure 块时退化为图注块（可据此向上裁切图像区域）
        figs = [c for c in store.chunks_of(paper_id, ("caption",))
                if re.match(r"(?i)^\s*(?:figure|fig\.?)\s*\d+", c.get("text") or "")]
    out = []
    for i, c in enumerate(figs[:max(1, limit)]):
        m = re.match(r"(?i)^\s*(?:figure|fig\.?)\s*(\d+)", c.get("text") or "")
        out.append({"n": int(m.group(1)) if m else i + 1,
                    "page": c.get("page"),
                    "caption": "" if c.get("text") == "[Figure]" else (c.get("text") or "")})
    return {"paper_id": paper_id, "figures": out, "total": len(figs)}


@router.get("/{paper_id}/figures/{n}/image")
def figure_image(paper_id: str, n: int):
    """按需裁切第 n 张图并返回 PNG（同一图只裁一次，落盘缓存）。"""
    r = figures.render(paper_id, n, with_ocr=False)
    if not r or not r.get("image_url"):
        raise HTTPException(status_code=404, detail=r.get("error") if r else "figure not found")
    from ..services.figures import FIG_DIR
    path = FIG_DIR / f"{paper_id}_fig{n}.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail="图像裁切失败")
    return FileResponse(str(path), media_type="image/png")


@router.get("/{paper_id}/export")
def export_paper(paper_id: str, format: str = "md", lang: str = "both"):
    """整篇导出：Markdown / HTML，可选附加已缓存的逐段中文译文。"""
    meta = store.paper_meta(paper_id)
    if not meta:
        raise HTTPException(status_code=404, detail="paper not found")
    chunks = [c for c in store.chunks_of(paper_id)
              if c["kind"] in ("heading", "paragraph", "table", "formula", "caption", "ocr")]
    from ..db import query
    rows = query("SELECT chunk_id,text FROM translations WHERE paper_id=? AND lang='zh'",
                 (paper_id,))
    tx: dict[str, str] = {}
    for r in rows:
        cid = r["chunk_id"]
        tx[cid] = r["text"]
        tx[cid.split(":", 1)[-1]] = r["text"]
        tx[cid.split(":")[-1]] = r["text"]
    with_zh = lang in ("zh", "both")

    title = meta.get("title") or paper_id
    authors = ", ".join(meta.get("authors") or []) or "Unknown"
    head = f"{title} — {authors} ({meta.get('year') or 'n.d.'})"
    if format == "html":
        esc = html_lib.escape
        body = [f"<h1>{esc(title)}</h1>",
                f"<p><em>{esc(authors)} · {esc(str(meta.get('year') or 'n.d.'))}</em></p>"]
        for c in chunks:
            text = esc(c["text"])
            if c["kind"] == "heading":
                lv = min(max(int(c.get("level") or 2), 2), 4)
                body.append(f"<h{lv}>{text}</h{lv}>")
            elif c["kind"] == "formula":
                body.append(f"<pre class='formula'>{text}</pre>")
            elif c["kind"] == "table":
                body.append(f"<pre class='table'>{text}</pre>")
            else:
                body.append(f"<p>{text}</p>")
            if with_zh and tx.get(c["id"]) and c["kind"] != "heading":
                body.append(f"<blockquote>{esc(tx[c['id']])}</blockquote>")
        doc = ("<!doctype html><html lang='zh'><meta charset='utf-8'>"
               f"<title>{esc(title)}</title><style>body{{max-width:860px;margin:40px auto;"
               "font-family:Georgia,'Songti SC',serif;line-height:1.8;padding:0 16px}"
               ".formula{{background:#f8fafc;padding:10px;overflow:auto}}"
               ".table{{background:#f8fafc;padding:10px;overflow:auto;font-size:12px}"
               "blockquote{{color:#475569;border-left:3px solid #c7d2fe;margin:8px 0;"
               "padding:2px 12px}}</style><body>" + "\n".join(body) + "</body></html>")
        return Response(doc, media_type="text/html; charset=utf-8",
                        headers={"Content-Disposition":
                                 f'attachment; filename="{_safe_name(title)}.html"'})

    lines = [f"# {head}", ""]
    for c in chunks:
        text = c["text"]
        if c["kind"] == "heading":
            lines += ["#" * 2 + " " + text, ""]
        elif c["kind"] == "formula":
            lines += ["$$", text, "$$", ""]
        else:
            lines += [text, ""]
        if with_zh and tx.get(c["id"]) and c["kind"] != "heading":
            lines += [f"> {tx[c['id']]}", ""]
    md = "\n".join(lines)
    return Response(md, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition":
                             f'attachment; filename="{_safe_name(title)}.md"'})


def _safe_name(title: str) -> str:
    name = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", title).strip("_")
    return name[:60] or "paper"


@router.get("/{paper_id}/pdf")
def download_original_pdf(paper_id: str):
    """下载原始 PDF（保持原版式），用于对照阅读与存档。"""
    meta = store.paper_meta(paper_id)
    if not meta:
        raise HTTPException(status_code=404, detail="paper not found")
    path = store.resolve_pdf_path(meta)
    if not path:
        raise HTTPException(status_code=404, detail="原 PDF 不存在")
    return FileResponse(path, media_type="application/pdf",
                        filename=meta.get("filename") or f"{paper_id}.pdf")


@router.get("/{paper_id}/chunks")
def get_chunks(paper_id: str, kinds: str | None = None):
    kind_list = tuple(k.strip() for k in kinds.split(",")) if kinds else None
    return {"paper_id": paper_id, "chunks": store.chunks_of(paper_id, kind_list)}


@router.get("/{paper_id}/refs")
def get_refs(paper_id: str):
    return {"paper_id": paper_id, "refs": store.refs_of(paper_id)}


@router.get("/{paper_id}/refs/quality")
def refs_quality(paper_id: str):
    """参考文献「引用含金量」：正文提及 + 语义线索 + 章节位置。"""
    res = citation_quality.analyze(paper_id)
    if res.get("error"):
        raise HTTPException(status_code=404, detail=res["error"])
    return res


@router.post("/{paper_id}/drill")
def drill_refs(paper_id: str, limit: int = Form(3), min_score: int = Form(30),
               quality_filter: bool = Form(True), ref_ids: str = Form("")):
    """按参考文献列表批量下钻下载：默认只抓含金量 >= 30 的高价值文献。"""
    wanted = [int(x) for x in ref_ids.split(",") if x.strip().isdigit()] or None
    res = drill.start(paper_id, limit=max(1, min(limit, 10)),
                      min_score=max(0, min(min_score, 100)),
                      quality_filter=quality_filter, ref_ids=wanted)
    if res.get("error"):
        raise HTTPException(status_code=404, detail=res["error"])
    return res


@router.get("/drill-jobs/{job_id}")
def drill_job(job_id: str):
    job = drill.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="下钻任务不存在或已过期")
    return job


@router.post("/drill-title")
def drill_title(title: str = Form(...)):
    """给定论文名称，自动检索、校验标题相似度并下载入库。"""
    try:
        return drill.drill_by_title(title)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/jobs/{job_id}")
def job_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return jobs.public(job)


@router.post("/jobs/{job_id}/commit")
def job_commit(job_id: str, overwrite_id: str | None = Form(None),
               force: bool = Form(False)):
    """重复确认后的落库：复用磁盘解析缓存，秒级完成，不重复 OCR。"""
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    save_path = Path(job["save_path"])
    if not save_path.is_file():
        raise HTTPException(status_code=410, detail="临时文件已清理，请重新上传")
    parsed = pdf_parser.parse_pdf(save_path, filename=job["filename"])
    store.save_paper(parsed, str(save_path), job["paper_id"], overwrite_id=overwrite_id)
    threading.Thread(target=_async_enrich, args=(overwrite_id or job["paper_id"],),
                     daemon=True).start()
    jobs.update(job_id, status="done", stage="已入库",
                result={"status": "ok", "paper_id": overwrite_id or job["paper_id"],
                        "title": parsed["title"], "num_pages": parsed.get("num_pages")})
    return jobs.public(jobs.get(job_id))


@router.post("/upload")
def upload(file: UploadFile = File(...), overwrite_id: str | None = Form(None),
           force: bool = Form(False), sync: bool = Form(False)):
    """上传 PDF：保存文件后立刻返回 job_id，解析在后台线程进行，进度靠轮询。

    sync=true 时退回同步解析（脚本/命令行一次性导入用）。
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

    if sync:
        return _ingest_sync(save_path, name, pid, overwrite_id, force)

    job_id = jobs.create(name, str(save_path), pid)
    threading.Thread(target=_run_job, args=(job_id,), daemon=True).start()
    return {"status": "processing", "job_id": job_id, "filename": name}


def _ingest_sync(save_path: Path, name: str, pid: str,
                 overwrite_id: str | None, force: bool) -> dict:
    parsed = pdf_parser.parse_pdf(save_path, filename=name)
    dup = dedup.find_duplicates(parsed)
    if dup and not overwrite_id and not force:
        version_diff = dedup.version_diff(dup["paper_id"], parsed) \
            if dup.get("same_arxiv_family") else None
        return {
            "status": "duplicate", "filename": name, "suggestion": dup["suggestion"],
            "duplicate": dup, "version_diff": version_diff, "parsed": {
                "title": parsed["title"], "authors": parsed.get("authors"),
                "year": parsed.get("year"), "num_pages": parsed.get("num_pages"),
                "arxiv_id": parsed.get("arxiv_id"),
            },
        }
    store.save_paper(parsed, str(save_path), pid, overwrite_id=overwrite_id)
    threading.Thread(target=_async_enrich, args=(pid,), daemon=True).start()
    return {
        "status": "ok", "paper_id": pid, "title": parsed["title"],
        "arxiv_id": parsed.get("arxiv_id"), "num_pages": parsed.get("num_pages"),
        "page_flags": parsed.get("page_flags", {}),
        "refs_count": len(parsed.get("refs") or []),
    }


def _run_job(job_id: str) -> None:
    """后台任务：解析放到子进程（隔离 GIL）→ 判重 → 落库，全程写进度。"""
    job = jobs.get(job_id)
    if not job:
        return
    progress_file = str(Path(tempfile.gettempdir()) / f"rp_parse_{job_id}.json")
    try:
        jobs.update(job_id, status="parsing", stage="解析版式与章节结构")
        future = _parse_executor().submit(
            parse_worker.parse_to_progress_file,
            job["save_path"], job["filename"], progress_file)

        kind_stage = {"ocr": "OCR 识别扫描页", "cached": "读取解析缓存",
                      "layout": "分析版式与字号", "text": "提取版式与章节"}
        while True:
            try:
                parsed = future.result(timeout=1.5)
                break
            except FuturesTimeout:
                # 轮询子进程写出的进度文件
                try:
                    with open(progress_file, "r", encoding="utf-8") as f:
                        p = json.load(f)
                    jobs.update(job_id, pages_done=p.get("done", 0),
                                pages_total=p.get("total", 0),
                                stage=kind_stage.get(p.get("kind"), "解析中"))
                except Exception:
                    pass
        jobs.update(job_id, stage="语义去重与版本比对")
        dup = dedup.find_duplicates(parsed)
        if dup:
            version_diff = dedup.version_diff(dup["paper_id"], parsed) \
                if dup.get("same_arxiv_family") else None
            jobs.update(job_id, status="duplicate", stage="检测到重复文献",
                        dup=dup, version_diff=version_diff,
                        result={"title": parsed.get("title"),
                                "num_pages": parsed.get("num_pages"),
                                "suggestion": dup.get("suggestion")})
            return

        jobs.update(job_id, stage="写入索引")
        store.save_paper(parsed, job["save_path"], job["paper_id"])
        jobs.update(
            job_id, status="done", stage="已入库",
            result={"status": "ok", "paper_id": job["paper_id"],
                    "title": parsed["title"], "num_pages": parsed.get("num_pages"),
                    "refs_count": len(parsed.get("refs") or []),
                    "ocr_pages": parsed.get("ocr_pages", 0),
                    "ocr_skipped": parsed.get("ocr_skipped", 0)})
        threading.Thread(target=_async_enrich, args=(job["paper_id"],),
                         daemon=True).start()
    except Exception as exc:  # 任务失败要把原因带回前端，而不是静默卡住
        import traceback
        traceback.print_exc()
        jobs.update(job_id, status="error", stage="解析失败",
                    message=str(exc) or exc.__class__.__name__)
    finally:
        try:
            os.remove(progress_file)
        except OSError:
            pass


def _async_enrich(paper_id: str) -> None:
    """后台线程薄封装，隔离 import 失败不影响 upload 主链路。"""
    try:
        from ..services.store import enrich_paper
        enrich_paper(paper_id)
    except Exception:
        pass


@router.delete("/{paper_id}")
def delete(paper_id: str):
    store.delete_paper(paper_id, async_cleanup=True)
    return {"ok": True}


@router.delete("")
def delete_all():
    """清空文库（含 PDF 与封面文件）。bump 放后台，立即返回。"""
    return {"ok": True, "deleted": store.delete_all_papers(async_cleanup=True)}
