"""局部引用图谱：参考文献匹配 → PageRank 核心节点 → 前沿 Idea 提取。

节点区分：
- bedrock：被库内多篇论文引用 / PageRank 高，属于领域基石；
- frontier：年份新且入度低，属于边缘衍生/前沿；
- normal：其余。
"""
from __future__ import annotations

import re
from collections import Counter

import networkx as nx

from ..settings import REF_MATCH_THRESHOLD
from ..utils import FUTURE_HINT, keyword_overlap, norm_ws, snippet, tokenize
from . import llm
from .dedup import reference_matches
from .store import (chunks_of, graph_cache_get, graph_cache_put, graph_version,
                    list_papers, paper_meta, refs_of)


def _node_label(p: dict) -> str:
    """节点显示名：标题 + 版本号，避免同系列多版本在图里标签重名。"""
    title = (p.get("title") or "").strip()
    m = re.search(r"v(\d+)$", (p.get("arxiv_id") or "").strip())
    return f"{title} · v{m.group(1)}" if m else title


def build_graph(paper_ids: list[str] | None = None,
                skip_cache: bool = False) -> dict:
    library = list_papers()

    # 版本化缓存：papers/refs 变了 bump_graph_version()，缓存自动失效
    gv = graph_version()
    if not skip_cache and not paper_ids:
        cached = graph_cache_get(gv)
        if cached:
            cached["cached"] = True
            cached.setdefault("cached_version", gv)
            return cached
    by_id = {p["id"]: p for p in library}
    selected = [p for p in library if not paper_ids or p["id"] in paper_ids]
    selected_ids = {p["id"] for p in selected}

    # 候选节点：选中论文 + 被它们引用的库内论文
    candidates = {p["id"]: p for p in selected}
    edges = []
    match_stats = {"refs_total": 0, "matched": 0}
    for p in selected:
        refs = refs_of(p["id"])
        for ref in refs:
            match_stats["refs_total"] += 1
            target = reference_matches(p["title"], {**ref, "paper_id": p["id"]}, library)
            if target:
                match_stats["matched"] += 1
                if target not in candidates and by_id.get(target):
                    candidates[target] = by_id[target]
                edges.append({"source": p["id"], "target": target,
                              "raw": ref.get("raw", ""),
                              "title": ref.get("title", "")})

    # 同一 arXiv 系列的不同版本：加一条“版本关联”边（旧版 -> 新版）
    families: dict[str, list[tuple[int, str]]] = {}
    for pid, p in candidates.items():
        aid = (p.get("arxiv_id") or "").strip()
        if not aid:
            continue
        m = re.search(r"v(\d+)$", aid)
        families.setdefault(re.sub(r"v\d+$", "", aid), []).append(
            (int(m.group(1)) if m else 0, pid))
    for base, items in families.items():
        if len(items) < 2:
            continue
        items.sort()
        for (va, pa), (vb, pb) in zip(items, items[1:]):
            edges.append({"source": pa, "target": pb, "kind": "version",
                          "raw": f"arXiv {base} v{va} -> v{vb}", "title": "版本关联"})

    graph = nx.DiGraph()
    for pid, p in candidates.items():
        graph.add_node(pid, **p)
    for e in edges:
        if e["source"] in graph and e["target"] in graph:
            graph.add_edge(e["source"], e["target"])

    pagerank = nx.pagerank(graph) if graph.number_of_nodes() else {}
    indeg = dict(graph.in_degree())
    years = [p.get("year") for p in candidates.values() if p.get("year")]
    max_year = max(years) if years else None
    pr_sorted = sorted(pagerank.values(), reverse=True)
    pr_cut = pr_sorted[max(0, min(len(pr_sorted) - 1, int(len(pr_sorted) * 0.25)))] \
        if pr_sorted else 0

    nodes = []
    for pid, p in candidates.items():
        pr = pagerank.get(pid, 0)
        role = "normal"
        if len(candidates) >= 3 and pr >= pr_cut and indeg.get(pid, 0) >= 1:
            role = "bedrock"
        if p.get("year") and max_year and p["year"] >= max_year - 1 and indeg.get(pid, 0) <= 1:
            role = "frontier"
        nodes.append({
            "id": pid, "title": p.get("title"), "year": p.get("year"),
            "label": _node_label(p),
            "authors": p.get("authors", []),
            "pagerank": round(pr, 5), "in_degree": indeg.get(pid, 0),
            "out_degree": graph.out_degree(pid) if pid in graph else 0,
            "role": role, "selected": pid in selected_ids,
            "num_pages": p.get("num_pages"),
            "arxiv_id": p.get("arxiv_id"),
        })
    nodes.sort(key=lambda n: n["pagerank"], reverse=True)
    max_pr = max([n["pagerank"] for n in nodes] or [1])
    for n in nodes:
        n["size"] = 14 + 46 * (n["pagerank"] / max_pr) ** 0.5 if max_pr else 14

    node_ids = {n["id"] for n in nodes}
    links, seen_links = [], set()
    for e in edges:
        if e["source"] not in node_ids or e["target"] not in node_ids:
            continue
        key = (e["source"], e["target"], e.get("kind", "citation"))
        if key in seen_links:  # 同一对论文的重复参考文献条目只保留一条边
            continue
        seen_links.add(key)
        links.append({"source": e["source"], "target": e["target"],
                      "title": e.get("title", ""), "kind": e.get("kind", "citation")})
    result = {
        "nodes": nodes,
        "links": links,
        "stats": {
            "papers": len(nodes), "edges": len(links),
            "bedrock": sum(1 for n in nodes if n["role"] == "bedrock"),
            "frontier": sum(1 for n in nodes if n["role"] == "frontier"),
            **match_stats,
        },
        "top": nodes[:3],
        "cached": False,
    }
    # 只有全库图谱（非 paper_ids 筛选）才进缓存
    if not paper_ids:
        graph_cache_put(gv, result)
    return result


def future_work(paper_ids: list[str] | None = None, max_items: int = 12) -> dict:
    """抽取 Future Work / Limitation，并按“是否已被后续论文解决”打标。"""
    papers = [p for p in list_papers() if not paper_ids or p["id"] in paper_ids]
    items = []
    for p in papers:
        for chunk in chunks_of(p["id"], ("paragraph",)):
            text = chunk["text"]
            if not FUTURE_HINT.search(text):
                continue
            for sent in re.split(r"(?<=[.!?])\s+", text):
                if not FUTURE_HINT.search(sent):
                    continue
                # 跳过章节标题（如 "5.2 Limitations and Ethical Considerations."），只保留成句的描述
                if re.match(r"^\d+(\.\d+)*\s", sent) or len(sent) < 70:
                    continue
                if len(sent) <= 600:
                    items.append({
                        "paper_id": p["id"], "paper_title": p.get("title"),
                        "year": p.get("year"), "page": chunk["page"],
                        "bbox": chunk["bbox"], "text": norm_ws(sent),
                    })
                    break
    # 同一 idea 去重（关键词重叠）
    deduped = []
    for item in sorted(items, key=lambda x: (x["year"] or 0), reverse=True):
        if any(keyword_overlap(item["text"], kept["text"]) > 0.70 for kept in deduped):
            continue
        deduped.append(item)

    # 是否被更新论文解决：与更晚论文的标题/摘要关键词高度重合
    later = [p for p in list_papers()
             if (p.get("year") or 0) >= max([i["year"] or 0 for i in deduped] or [0]) - 1]
    for item in deduped:
        resolved_by = None
        for p in later:
            if (p.get("year") or 0) <= (item["year"] or 0):
                continue
            if p["id"] == item["paper_id"]:
                continue
            if keyword_overlap(item["text"], (p.get("title", "") + " " +
                                              (paper_meta(p["id"]) or {}).get("abstract", "")) ) > 0.4:
                resolved_by = {"id": p["id"], "title": p.get("title")}
                break
        item["status"] = "resolved" if resolved_by else "open"
        item["resolved_by"] = resolved_by
        item["summary"] = snippet(item["text"], 220)

    open_items = [i for i in deduped if i["status"] == "open"][:max_items]
    return {
        "items": deduped[:max_items],
        "open_count": len(open_items),
        "total": len(deduped),
        "mode": "heuristic",
    }
