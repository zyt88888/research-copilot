"""学术洞察：GitHub 复现性评估 + 基于偏好的相关推荐。

- 复现性：从论文正文/摘要中抽取 GitHub 仓库地址，查询 GitHub 公开 API
  获取 star、最近更新、语言、License，给出可复现性结论；
- 推荐：以当前论文的标题+摘要作为查询，复用混合检索在库内做相似推荐。
"""
from __future__ import annotations

import re
import time

import requests

from .store import (chunks_of, hybrid_search, list_papers, paper_meta,
                    refs_of)

GITHUB_RE = re.compile(r"https?://github\.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+")
NOISE = {"features", "topics", "about", "pricing", "login", "marketplace",
         "sponsors", "security", "explore", "collections", "trending"}

_cache: dict[str, tuple[float, dict]] = {}
CACHE_TTL = 600


def _norm_repo(url: str) -> str:
    parts = url.rstrip("/").removesuffix(".git").split("github.com/")[-1].split("/")
    return f"{parts[0]}/{parts[1]}" if len(parts) >= 2 else ""


def find_repos(paper_id: str, limit: int = 5) -> list[str]:
    """从论文正文与摘要中抽取去重后的 GitHub 仓库全名。"""
    meta = paper_meta(paper_id) or {}
    texts = [meta.get("abstract") or ""]
    texts += [c.get("text") or "" for c in chunks_of(paper_id)]
    seen: list[str] = []
    for t in texts:
        for m in GITHUB_RE.findall(t or ""):
            name = _norm_repo(m)
            if not name or name.split("/")[0].lower() in NOISE:
                continue
            if name not in seen:
                seen.append(name)
            if len(seen) >= limit:
                return seen
    return seen


def repo_stats(full_name: str) -> dict:
    """查询 GitHub 仓库公开信息（无 token 时限 60 次/小时）。"""
    now = time.time()
    hit = _cache.get(full_name)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    out: dict = {"repo": full_name, "url": f"https://github.com/{full_name}"}
    try:
        r = requests.get(f"https://api.github.com/repos/{full_name}",
                         timeout=15, headers={"Accept": "application/vnd.github+json"})
        if r.status_code == 200:
            d = r.json()
            out.update({
                "stars": d.get("stargazers_count"),
                "forks": d.get("forks_count"),
                "language": d.get("language"),
                "license": (d.get("license") or {}).get("spdx_id"),
                "archived": d.get("archived"),
                "pushed_at": (d.get("pushed_at") or "")[:10],
                "issues": d.get("open_issues_count"),
                "description": (d.get("description") or "")[:160],
                "found": True,
            })
        elif r.status_code == 404:
            out.update({"found": False, "error": "仓库不存在或已改名"})
        else:
            out.update({"found": False, "error": f"GitHub API {r.status_code}"})
    except requests.RequestException as exc:
        out.update({"found": False, "error": f"网络不可用：{exc.__class__.__name__}"})
    _cache[full_name] = (now, out)
    return out


def reproducibility(paper_id: str) -> dict:
    meta = paper_meta(paper_id) or {}
    if not meta:
        return {"error": f"未找到论文 {paper_id}"}
    repos = [repo_stats(n) for n in find_repos(paper_id)]
    live = [r for r in repos if r.get("found")]
    if not live:
        verdict, score = "正文中未发现可访问的官方代码仓库", 0
    else:
        stars = max((r.get("stars") or 0) for r in live)
        recent = any((r.get("pushed_at") or "") >= "2024-01-01" for r in live)
        licensed = any(r.get("license") not in (None, "NOASSERTION") for r in live)
        score = 40 + (30 if stars >= 100 else 20 if stars >= 20 else 10) \
            + (20 if recent else 0) + (10 if licensed else 0)
        bits = [f"找到 {len(live)} 个可访问仓库"]
        if stars:
            bits.append(f"最高 {stars} stars")
        bits.append("近期仍在维护" if recent else "近期无更新")
        bits.append("含明确 License" if licensed else "缺少 License")
        verdict = "；".join(bits)
    return {"paper_id": paper_id, "title": meta.get("title"),
            "repos": repos, "score": min(score, 100), "verdict": verdict,
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}


def _surnames(authors) -> set[str]:
    return {str(a).split()[-1].lower() for a in (authors or []) if str(a).strip()}


def _ref_base_id(arxiv_id: str | None) -> str:
    m = re.search(r"(\d{4}\.\d{4,5})", arxiv_id or "")
    return m.group(1) if m else ""


def recommend(paper_id: str, top_k: int = 5) -> dict:
    """推荐库内相似论文：混合检索召回 → 引用关系/共同作者/开源代码加权重排。

    推荐理由只使用可核实信号（本文是否直接引用、是否共同作者、正文是否给出仓库、
    发表年份），并优先把附带开源代码的论文排到前面。
    """
    from ..utils import keyword_overlap

    meta = paper_meta(paper_id) or {}
    if not meta:
        return {"error": f"未找到论文 {paper_id}"}
    query = f"{meta.get('title', '')} {(meta.get('abstract') or '')[:400]}"
    # per_paper=1：每篇只取最相关的一段，保证候选覆盖多篇论文而不是被单篇长文淹没
    hits = hybrid_search(query, None, top_k=40, per_paper=1)
    agg: dict[str, dict] = {}
    papers = {p["id"]: p for p in list_papers()}
    for h in hits:
        pid = h.get("paper_id")
        if not pid or pid == paper_id:
            continue
        item = agg.get(pid)
        if not item:
            agg[pid] = {"paper_id": pid, "title": papers.get(pid, {}).get("title", h.get("title", "")),
                        "score": float(h.get("score") or 0), "pages": [h.get("page")]}
        elif len(item["pages"]) < 3 and h.get("page") not in item["pages"]:
            item["pages"].append(h.get("page"))
    # 同 arXiv 家族（v1/v2/...）只保留得分最高的一个版本，避免推荐位被同一论文占满
    family: dict[str, dict] = {}
    for item in sorted(agg.values(), key=lambda x: x["score"], reverse=True):
        arxiv = (papers.get(item["paper_id"], {}).get("arxiv_id") or "")
        base = re.sub(r"v\d+$", "", arxiv) or item["paper_id"]
        if base not in family:
            family[base] = item
    ranked = list(family.values())
    if not ranked:
        return {"paper_id": paper_id, "title": meta.get("title"),
                "basis": "标题+摘要的混合检索（BM25 + 向量，同系列版本去重）",
                "items": []}

    max_score = max(x["score"] for x in ranked) or 1.0
    cur_refs = refs_of(paper_id)
    ref_arxiv = {_ref_base_id(r.get("arxiv_id")) for r in cur_refs}
    ref_arxiv.discard("")
    ref_titles = [r.get("title") for r in cur_refs if r.get("title")]
    cur_authors = _surnames(meta.get("authors"))
    cur_year = meta.get("year") or 0

    pool = ranked[:max(top_k * 3, 12)]
    enriched = []
    for item in pool:
        cand = papers.get(item["paper_id"], {})
        sim = item["score"] / max_score
        reasons = []
        cand_base = _ref_base_id(cand.get("arxiv_id"))
        cited = bool(cand_base and cand_base in ref_arxiv) or any(
            keyword_overlap(item["title"], t) >= 0.72 for t in ref_titles)
        same_author = sorted(cur_authors & _surnames(cand.get("authors")))
        repos = find_repos(item["paper_id"], limit=2)  # 只做正文正则，不发网络请求
        recent = bool(cand.get("year") and cur_year and int(cand["year"]) >= int(cur_year) - 2)

        if cited:
            reasons.append("本文参考文献直接引用了该论文")
        if same_author:
            reasons.append(f"与本文有共同作者（{same_author[0]}）")
        if repos:
            reasons.append(f"正文提供开源仓库 {repos[0]}，可直接复现")
        if recent:
            reasons.append(f"较新工作（{cand['year']}）")
        if sim >= 0.5:
            reasons.append("标题与摘要主题高度相近（混合检索）")
        elif not reasons:
            reasons.append("研究主题相近（混合检索召回）")

        rank_score = (sim + (0.35 if cited else 0) + (0.12 if repos else 0)
                      + (0.08 if same_author else 0) + (0.05 if recent else 0))
        enriched.append({
            **item,
            "similarity": round(sim, 3),
            "rank_score": round(rank_score, 3),
            "year": cand.get("year"),
            "arxiv_id": cand.get("arxiv_id"),
            "has_code": bool(repos),
            "code_repos": repos,
            "reasons": reasons[:4],
        })
    enriched.sort(key=lambda x: (x["rank_score"], x["similarity"]), reverse=True)
    ranked = enriched[:top_k]
    return {"paper_id": paper_id, "title": meta.get("title"),
            "basis": "混合检索召回 + 引用关系/共同作者/开源代码加权（同系列版本去重）",
            "items": ranked}
