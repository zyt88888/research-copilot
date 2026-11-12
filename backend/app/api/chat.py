"""问答、多论文对比、Agent、图谱、翻译、Copilot 等交互接口。"""
from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from ..db import execute, loads, query
from ..services import agent, copilot, figures, graph, rag, translate
from ..services.rag import answer as rag_answer

router = APIRouter(tags=["chat"])


@router.post("/api/chat")
def chat(payload: dict):
    query = (payload.get("query") or "").strip()
    paper_ids = payload.get("paper_ids") or None
    if not query:
        raise HTTPException(status_code=400, detail="query required")
    t0 = time.time()
    # 图表理解快捷通道：「图 N / Figure N 展示了什么」→ 定位原图 + 图注/正文/OCR 依据
    figs = figures.figure_numbers(query)
    if figs:
        from ..services.store import list_papers
        scope = paper_ids or [p["id"] for p in list_papers()]
        for pid in scope:
            fig = figures.answer_figure(query, pid, figs[0])
            if fig and not fig.get("error") and (fig.get("caption") or fig.get("image_url")):
                fig["elapsed"] = round(time.time() - t0, 2)
                fig["mode"] = "figure"
                fig["answer"] = fig.get("explanation", "")
                fig["figure"] = {"n": fig["n"], "page": fig["page"],
                                 "image_url": fig.get("image_url"),
                                 "ocr_text": fig.get("ocr_text") or ""}
                fig["sources"] = [{"sid": "S1", "paper_id": pid, "page": fig["page"],
                                   "section": f"图 {figs[0]}",
                                   "text": fig.get("caption") or "（图块无文字图注）",
                                   "bbox": []}]
                return fig
    hierarchical = bool(payload.get("hierarchical"))
    result = rag_answer(query, paper_ids=paper_ids, top_k=payload.get("top_k", 8),
                       history=payload.get("history"), hierarchical=hierarchical)
    result["elapsed"] = round(time.time() - t0, 2)
    if hierarchical:
        result["mode"] = f'{result.get("mode", "rag")}+hierarchical'
    return result


@router.post("/api/compare")
def compare(payload: dict):
    paper_ids = payload.get("paper_ids")
    aspect = payload.get("aspect") or "核心方法、参数规模与实验结论"
    if not paper_ids or len(paper_ids) < 2:
        raise HTTPException(status_code=400, detail="至少 2 篇论文")
    papers = []
    from ..services.store import paper_meta
    for pid in paper_ids:
        meta = paper_meta(pid)
        if meta:
            papers.append(meta)
    return rag.compare(papers, aspect)


@router.get("/api/graph")
def build_graph(paper_ids: str | None = None):
    ids = [x for x in (paper_ids or "").split(",") if x] or None
    return graph.build_graph(ids)


@router.get("/api/future")
def future(paper_ids: str | None = None):
    ids = [x for x in (paper_ids or "").split(",") if x] or None
    return graph.future_work(ids)


@router.post("/api/translate")
def translate_chunk(payload: dict):
    if payload.get("mode") == "paper":
        return translate.translate_chunks(
            payload["paper_id"], payload.get("lang", "zh"),
            chunk_ids=payload.get("chunk_ids"), limit=payload.get("limit", 40))
    return translate.translate_text(payload.get("text", ""), payload.get("target", "zh"))


@router.post("/api/copilot/outline")
def outline(payload: dict):
    return copilot.outline(
        payload.get("idea", ""), payload.get("paper_ids"),
        language=payload.get("language", "zh"))


@router.post("/api/copilot/diagram")
def diagram(payload: dict):
    return copilot.diagram(payload.get("idea", ""), payload.get("kind", "mermaid"))


@router.post("/api/copilot/csv")
def csv_chart(payload: dict):
    path = payload.get("path")
    if not path or not Path(path).exists():
        raise HTTPException(status_code=400, detail="CSV 文件不存在")
    return copilot.csv_to_chart(Path(path).read_bytes(), Path(path).name)


@router.post("/api/agent/plan")
def agent_plan(payload: dict):
    return agent.plan_steps(payload.get("task", ""), payload.get("paper_ids"))


@router.post("/api/agent/run")
def agent_run(payload: dict):
    task = payload.get("task", "").strip()
    if not task:
        raise HTTPException(status_code=400, detail="task required")

    def gen():
        for event in agent.run(task, payload.get("paper_ids")):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Agent 执行历史（P2-3）
# ---------------------------------------------------------------------------
@router.get("/api/agent/runs")
def list_runs(limit: int = Query(20, ge=1, le=100)):
    rows = query(
        "SELECT id, kind, payload, created_at, finished_at, duration_ms, status "
        "FROM runs ORDER BY created_at DESC LIMIT ?", (limit,))
    out = []
    for r in rows:
        payload = loads(r["payload"], {})
        out.append({
            "id": r["id"],
            "kind": r["kind"],
            "task": payload.get("task", ""),
            "paper_ids": payload.get("paper_ids"),
            "status": r["status"],
            "created_at": r["created_at"],
            "finished_at": r["finished_at"],
            "duration_ms": r["duration_ms"],
        })
    return {"runs": out}


@router.get("/api/agent/runs/{run_id}")
def get_run(run_id: str):
    rows = query("SELECT * FROM runs WHERE id=?", (run_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="run not found")
    r = rows[0]
    return {
        "id": r["id"],
        "kind": r["kind"],
        "payload": loads(r["payload"], {}),
        "status": r["status"],
        "created_at": r["created_at"],
        "finished_at": r["finished_at"],
        "duration_ms": r["duration_ms"],
        "final_text": r["final_text"],
        "trace": loads(r["trace"], []),
    }


@router.delete("/api/agent/runs/{run_id}")
def delete_run(run_id: str):
    execute("DELETE FROM runs WHERE id=?", (run_id,))
    return {"ok": True}


# ---------------------------------------------------------------------------
# 版本比对（P2-4）：arXiv 同系列两篇论文的结构化差异
# ---------------------------------------------------------------------------
@router.post("/api/compare/version-diff")
def version_diff(payload: dict):
    """已入库两篇论文的版本差异（章节结构 + 正文相似度 + 关键词）。"""
    from ..services.dedup import version_diff as dedup_diff
    from ..services.store import chunks_of, paper_meta

    old_id = payload.get("old_id") or payload.get("paper_ids", [None, None])[0]
    new_id = payload.get("new_id") or payload.get("paper_ids", [None, None])[1]
    if not old_id or not new_id:
        raise HTTPException(status_code=400, detail="需要 old_id 和 new_id")

    old_meta = paper_meta(old_id)
    new_meta = paper_meta(new_id)
    if not old_meta or not new_meta:
        raise HTTPException(status_code=404, detail="论文不存在")

    # 复用 dedup.version_diff：它接受 old_paper_id 和一个 parsed dict
    # 我们从两篇已入库论文构造两个 parsed 形式
    def _parsed_from_chunks(pid: str, meta: dict) -> dict:
        chunks = chunks_of(pid)
        import json as _json
        return {
            "title": meta["title"],
            "num_pages": meta.get("num_pages"),
            "arxiv_id": meta.get("arxiv_id"),
            "chunks": chunks,
            "meta": meta.get("meta") and _json.dumps(meta["meta"], ensure_ascii=False),
        }

    new_parsed = _parsed_from_chunks(new_id, new_meta)
    diff = dedup_diff(old_id, new_parsed)
    # 追加一些前端有用的补充信息
    diff["old_id"] = old_id
    diff["new_id"] = new_id
    diff["old_arxiv"] = old_meta.get("arxiv_id")
    diff["new_arxiv"] = new_meta.get("arxiv_id")
    return diff
