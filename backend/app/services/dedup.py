"""语义级去重与版本比对。

- 精确/近似（MinHash）内容去重；
- arXiv 版本识别（v1 / v7）与修订差异摘要；
- 给出“保留/覆盖/跳过”建议，不替用户做决定。
"""
from __future__ import annotations

import difflib
import json
import re
from difflib import SequenceMatcher

from ..db import get_conn
from ..settings import DUP_THRESHOLD, REF_MATCH_THRESHOLD
from ..utils import keyword_overlap, norm_ws, signature_similarity, snippet


def _versions(arxiv_id: str | None) -> tuple[str, int] | None:
    if not arxiv_id:
        return None
    m = re.match(r"([0-9]{4}\.[0-9]{4,5})(v\d+)?", arxiv_id)
    if not m:
        return None
    return m.group(1), int((m.group(2) or "v1")[1:])


def find_duplicates(parsed: dict) -> dict | None:
    """返回最相似的库内论文及相似度；无命中返回 None。"""
    sig = parsed.get("minhash") or []
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, arxiv_id, year, num_pages, content_hash, minhash, filename, meta "
            "FROM papers").fetchall()
    best = None
    for row in rows:
        if not row["minhash"]:
            continue
        try:
            other = json.loads(row["minhash"])
        except (TypeError, json.JSONDecodeError):
            continue
        sim = signature_similarity(sig, other)
        exact = parsed.get("content_hash") == row["content_hash"]
        if exact:
            sim = 1.0
        # arXiv 同号不同版本时提高优先级
        pv, ov = _versions(parsed.get("arxiv_id")), _versions(row["arxiv_id"])
        same_family = bool(pv and ov and pv[0] == ov[0])
        if same_family:
            sim = max(sim, 0.55)
        if sim >= DUP_THRESHOLD and (best is None or sim > best["similarity"]):
            best = {
                "paper_id": row["id"],
                "title": row["title"],
                "filename": row["filename"],
                "arxiv_id": row["arxiv_id"],
                "year": row["year"],
                "similarity": round(sim, 3),
                "exact": exact,
                "same_arxiv_family": same_family,
                "existing_version": (_versions(row["arxiv_id"]) or (None, 0))[1] or None,
                "incoming_version": (pv or (None, 0))[1],
                "suggestion": _suggestion(pv, ov, row["year"], parsed.get("year")),
            }
    return best


def _suggestion(pv, ov, old_year, new_year) -> str:
    if pv and ov and pv[0] == ov[0]:
        if pv[1] > ov[1]:
            return "收到同一 arXiv 论文的更新版本，建议用新版本覆盖旧版"
        if pv[1] < ov[1]:
            return "收到较旧版本，建议保留库中已有版本"
        return "版本号相同，建议保留现有条目"
    if old_year and new_year and new_year > old_year:
        return "新上传文献年份更新，建议覆盖或保留新版"
    return "检测到高度重合内容，建议人工确认是否保留"


def version_diff(old_paper_id: str, parsed: dict, max_items: int = 6) -> dict:
    """对比章节标题与正文差异，输出可展示的结构化摘要（不调用 LLM 也能用）。"""
    with get_conn() as conn:
        old_chunks = [dict(r) for r in conn.execute(
            "SELECT section, page, kind, text || '' AS text FROM chunks "
            "WHERE paper_id=? AND kind IN ('paragraph','heading') ORDER BY seq",
            (old_paper_id,)).fetchall()]
        old_title = conn.execute("SELECT title FROM papers WHERE id=?",
                                 (old_paper_id,)).fetchone()
    old_sections = [c["section"].split(" > ")[-1] for c in old_chunks
                    if c["kind"] == "heading"]
    new_sections = [c["text"] for c in parsed.get("chunks", []) if c["kind"] == "heading"]
    old_text = " ".join(c["text"] for c in old_chunks if c["kind"] == "paragraph")
    new_text = " ".join(c["text"] for c in parsed.get("chunks", [])
                        if c["kind"] == "paragraph")
    ratio = SequenceMatcher(None, old_text[:80000], new_text[:80000]).ratio()
    added = [s for s in new_sections if s not in old_sections][:max_items]
    removed = [s for s in old_sections if s not in new_sections][:max_items]
    return {
        "old_title": old_title["title"] if old_title else "",
        "new_title": parsed.get("title"),
        "text_similarity": round(ratio, 3),
        "old_sections": len(old_sections),
        "new_sections": len(new_sections),
        "added_sections": added,
        "removed_sections": removed,
        "old_pages": None,
        "new_pages": parsed.get("num_pages"),
        "summary": _diff_sentence(ratio, added, removed, parsed),
    }


def _diff_sentence(ratio: float, added: list, removed: list, parsed: dict) -> str:
    parts = [f"正文重合度约 {ratio * 100:.1f}%"]
    if added:
        parts.append(f"新增 {len(added)} 个章节（如 {added[0]}）")
    if removed:
        parts.append(f"移除 {len(removed)} 个章节（如 {removed[0]}）")
    if not added and not removed:
        parts.append("章节结构基本一致，差异主要来自正文修订")
    return "；".join(parts) + "。"


def reference_matches(paper_title: str, ref: dict, library: list[dict]) -> str | None:
    """把参考文献匹配到库内论文（标题相似度 + arXiv 号），供图谱连边。"""
    if ref.get("arxiv_id"):
        base = ref["arxiv_id"].rstrip("v0123456789")
        for p in library:
            if (p.get("arxiv_id") or "").lower().startswith(base.lower()):
                return p["id"]
    rt = norm_ws(ref.get("title") or "").lower()
    if not rt:
        return None
    best_id, best_score = None, 0.0
    for p in library:
        if p["id"] == ref.get("paper_id") or p.get("title", "") == paper_title:
            continue
        score = SequenceMatcher(None, rt, norm_ws(p["title"]).lower()).ratio()
        score = max(score, keyword_overlap(rt, p["title"]) * 0.95)
        if score > best_score:
            best_id, best_score = p["id"], score
    return best_id if best_score >= REF_MATCH_THRESHOLD else None
