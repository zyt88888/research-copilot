"""参考文献批量下钻下载：给定论文的参考文献列表，自动检索并抓取高价值文献入库。

流程：引用含金量分析 → 过滤应付式引用 → 逐条解析 arXiv id / 按标题检索 →
下载 PDF 并解析入库。下载解析耗时较长，因此放到后台线程并暴露进度。
"""
from __future__ import annotations

import threading
import time
import uuid

from ..utils import keyword_overlap, norm_ws
from . import arxiv, citation_quality, ingest, scholar

_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}
_TTL = 3600


def _public(job: dict) -> dict:
    return {
        "job_id": job["id"],
        "paper_id": job["paper_id"],
        "status": job["status"],
        "stage": job["stage"],
        "done": job["done"],
        "total": job["total"],
        "items": job["items"],
        "elapsed": round(time.time() - job["started_at"], 1),
    }


def create_job(paper_id: str, total: int) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _LOCK:
        _JOBS[job_id] = {
            "id": job_id, "paper_id": paper_id, "status": "running",
            "stage": "分析引用含金量", "done": 0, "total": total,
            "items": [], "started_at": time.time(), "updated_at": time.time(),
        }
    _cleanup()
    return job_id


def update(job_id: str, **fields) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        job.update(fields)
        job["updated_at"] = time.time()


def append(job_id: str, item: dict) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        job["items"].append(item)
        job["done"] = len(job["items"])
        job["updated_at"] = time.time()


def get(job_id: str) -> dict | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        return _public(job) if job else None


def _cleanup() -> None:
    now = time.time()
    with _LOCK:
        for jid in [j for j, d in _JOBS.items()
                    if d["status"] in ("done", "error")
                    and now - d["updated_at"] > _TTL]:
            _JOBS.pop(jid, None)


def _pick_title_match(title: str, candidates: list[dict]) -> dict | None:
    """按标题相似度选最可信的 arXiv 命中（阈值 0.55，防止抓错文献）。"""
    best, best_score = None, 0.0
    for c in candidates:
        score = keyword_overlap(title, c.get("title") or "")
        if score > best_score:
            best, best_score = c, score
    return best if best_score >= 0.55 else None


def resolve_arxiv_id(title: str, arxiv_id: str | None = None,
                     year: int | None = None) -> tuple[str | None, str]:
    """把一条参考文献解析成 arXiv id，返回 (id, 依据说明)。"""
    if arxiv_id:
        return arxiv_id, "参考文献中直接给出 arXiv id"
    if not title:
        return None, "参考文献缺少可解析标题"
    cleaned = norm_ws(title).replace('"', " ")
    try:
        hits = arxiv.search_raw(f'ti:"{cleaned[:180]}"', limit=5)
    except RuntimeError:
        hits = []
    best = _pick_title_match(title, hits)
    if best and (year is None or not best.get("year")
                 or abs(int(best["year"]) - int(year)) <= 1):
        return best["arxiv_id"], f"arXiv 标题检索命中（{best.get('published') or '最近投稿'}）"
    # arXiv 未命中时尝试 Semantic Scholar（可能返回 arXiv id）
    try:
        for c in scholar.search(title, limit=3):
            if c.get("arxiv_id") and keyword_overlap(title, c.get("title") or "") >= 0.55:
                return c["arxiv_id"], "Semantic Scholar 检索命中"
    except RuntimeError:
        pass
    return None, "公开学术源未检索到匹配文献"


def _run(job_id: str, selected: list[dict]) -> None:
    ok = exists = failed = 0
    for ref in selected:
        title = ref.get("title") or ""
        update(job_id, stage=f"检索：{(title or ref.get('raw') or '')[:40]}")
        item = {
            "ref_id": ref.get("ref_id"), "index": ref.get("index"),
            "title": title or (ref.get("raw") or "")[:80],
            "score": ref.get("score"), "label": ref.get("label"),
            "arxiv_id": None, "status": "error", "paper_id": None, "message": "",
        }
        try:
            aid, why = resolve_arxiv_id(title, ref.get("arxiv_id"), ref.get("year"))
            item["arxiv_id"] = aid
            item["message"] = why
            if not aid:
                item["status"] = "not_found"
            else:
                res = ingest.fetch_and_ingest(aid)
                status = res.get("status")
                item["status"] = status if status in ("ok", "exists", "duplicate") else "error"
                item["paper_id"] = res.get("paper_id")
                item["title"] = res.get("title") or item["title"]
                item["message"] = {
                    "ok": "已下载并入库",
                    "exists": "库中已有该论文",
                    "duplicate": res.get("suggestion") or "内容与库内论文重复",
                }.get(status, item["message"])
                if status == "ok":
                    ok += 1
                elif status == "exists":
                    exists += 1
                else:
                    failed += 1
        except Exception as exc:  # 单条失败不影响其余条目
            item["status"] = "error"
            item["message"] = str(exc)[:200] or exc.__class__.__name__
            failed += 1
        append(job_id, item)
    update(job_id, status="done", stage=f"完成：新入库 {ok} 篇，已存在 {exists} 篇，失败 {failed} 条")


def start(paper_id: str, limit: int = 3, min_score: int = 30,
          quality_filter: bool = True, ref_ids: list[int] | None = None) -> dict:
    """启动批量下钻任务，立即返回 job_id。"""
    analysis = citation_quality.analyze(paper_id)
    if analysis.get("error"):
        return {"error": analysis["error"]}
    selected = citation_quality.select_for_drill(
        analysis, min_score=min_score, limit=limit, quality_filter=quality_filter)
    if ref_ids:
        wanted = {int(i) for i in ref_ids}
        selected = [r for r in analysis["refs"] if r.get("ref_id") in wanted]
    if not selected:
        return {
            "job_id": None, "total": 0,
            "message": "没有符合条件的高价值参考文献（可降低分数阈值或关闭含金量筛选）",
            "analysis": analysis["summary"],
        }
    job_id = create_job(paper_id, len(selected))
    threading.Thread(target=_run, args=(job_id, selected), daemon=True).start()
    return {"job_id": job_id, "total": len(selected),
            "selected": [{k: r.get(k) for k in ("ref_id", "title", "score", "label")}
                         for r in selected],
            "analysis": analysis["summary"]}


def drill_by_title(title: str) -> dict:
    """按论文名称直接下钻：检索 → 标题相似度校验 → 入库。"""
    title = norm_ws(title)
    if not title:
        raise RuntimeError("请提供论文名称")
    cleaned = title.replace('"', " ")
    try:
        cands = arxiv.search_raw(f'ti:"{cleaned[:180]}"', limit=8)
    except RuntimeError as exc:
        raise RuntimeError(f"arXiv 检索失败：{exc}") from exc
    best = _pick_title_match(title, cands)
    if not best:
        return {"status": "not_found", "query": title,
                "candidates": [{"title": c.get("title"), "arxiv_id": c.get("arxiv_id")}
                               for c in cands[:5]],
                "message": "arXiv 未找到标题足够接近的论文（阈值 0.55），可改用关键词检索后手动入库"}
    res = ingest.fetch_and_ingest(best["arxiv_id"])
    return {"query": title, "matched_title": best.get("title"),
            "similarity": round(keyword_overlap(title, best.get("title") or ""), 3),
            **res}
