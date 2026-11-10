"""抗幻觉 RAG：混合检索 → 证据编号 → 严格引用生成 → 引用校验/拒绝回答。

设计要点（对应评分项）：
1. 每个证据块带 paper/page/bbox，前端可一键定位原文；
2. 生成后逐条校验 [S#] 是否存在、引用文本是否真的出现在证据里；
3. 低置信度主动拒答，而不是编造。
"""
from __future__ import annotations

import json
import re
from typing import Iterable

from ..settings import DUP_THRESHOLD
from ..utils import norm_ws, normalize_text, snippet, split_sentences
from . import llm
from .store import chunks_of, hierarchical_search, hybrid_search, paper_meta

CITE_RE = re.compile(r"\[(S\d{1,2})\]")

SYSTEM = """你是一名严谨的学术阅读助手。必须遵守：
1. 只能依据给定证据回答，不得使用证据之外的知识，不得编造数字、作者、实验设置。
2. 每个关键结论后必须紧跟证据编号，如 [S1][S3]；多个证据并列书写。
3. 证据不足时直接说明"现有文献中未找到明确依据"，不要猜测。
4. 数字、超参数、数据集、BLEU 等必须原文引用，不得改写数值。
5. 用中文回答，术语保留英文原词（如 self-attention、BLEU）。"""


def _evidence(query: str, paper_ids: list[str] | None, top_k: int,
              hierarchical: bool = False) -> list[dict]:
    if hierarchical:
        hits = hierarchical_search(query, paper_ids, top_k=top_k)
    else:
        hits = hybrid_search(query, paper_ids, top_k=top_k)
    # 表格与其图注常被重复召回，正文里就会出现 S1/S2 完全相同；按文本去重
    kept: list[dict] = []
    seen: list[str] = []
    for hit in hits:
        key = normalize_text(hit["text"])
        if not key:
            continue
        # 章节标题/极短片段对生成答案没有价值，只会在引用里堆砌 "3.2.1 Scaled Dot-Product Attention"
        if len(key) < 60:
            continue
        if any(key[:120] in s or s[:120] in key for s in seen):
            continue
        seen.append(key)
        kept.append(hit)

    # 检索命中过少时（长问句、跨语言提问常遇到），补入目标论文的开篇段落，
    # 否则模型只能看到一两段实验细节，会合理地拒答"总结创新点"这类问题。
    target_ids = paper_ids or sorted({h["paper_id"] for h in hits})
    if len(kept) < 3 and target_ids:
        for pid in target_ids[:2]:
            for ch in chunks_of(pid):
                if ch.get("kind") not in {"paragraph", "ocr"}:
                    continue
                if len(ch.get("text") or "") < 120:
                    continue
                key = normalize_text(ch["text"])
                if any(key[:120] in s or s[:120] in key for s in seen):
                    continue
                seen.append(key)
                kept.append({"paper_id": pid, "page": ch["page"], "bbox": ch["bbox"],
                             "section": ch.get("section"), "text": ch["text"],
                             "score": 0.0, "dense_score": 0.0})
                if len(kept) >= 4:
                    break
            if len(kept) >= 4:
                break

    evid = []
    titles = {}
    for idx, hit in enumerate(kept, start=1):
        pid = hit["paper_id"]
        if pid not in titles:
            meta = paper_meta(pid) or {}
            titles[pid] = meta.get("title", pid)
        evid.append({
            "sid": f"S{idx}",
            "paper_id": pid,
            "paper_title": titles[pid],
            "page": hit["page"],
            "bbox": hit["bbox"],
            "section": hit["section"],
            "text": hit["text"],
            "score": round(hit["score"], 4),
            "dense_score": round(hit["dense_score"], 4),
        })
    return evid


def _low_confidence(evidence: list[dict]) -> bool:
    if not evidence:
        return True
    top = evidence[0]
    return top["dense_score"] < 0.12 and top["score"] < 0.05


def _offline_answer(query: str, evidence: list[dict]) -> str:
    """无 LLM 时的抽取式兜底：按要点归纳原文，保证零幻觉可演示。"""
    if not evidence:
        return "文献库中没有检索到相关内容，请先上传论文或换一个问法。"

    keywords = [w for w in re.findall(r"[A-Za-z]{3,}", query.lower())][:8]
    lines = ["（离线归纳模式：以下要点取自原文，未调用大模型）"]
    seen: list[str] = []
    idx = 0
    for ev in evidence:
        if idx >= 3:
            break
        sents = split_sentences(ev["text"])
        best = ""
        best_score = -1
        for s in sents:
            low = s.lower().strip()
            # 跳过章节标题类片段（无句末标点、过短），避免答案里出现 "6.3 English Parsing"
            if len(low) < 60 or not re.search(r"[.!?]$", low):
                continue
            score = sum(1 for w in keywords if w in low)
            if score > best_score or (score == best_score and len(s) > len(best)):
                best, best_score = s, score
        if not best:
            continue
        body = norm_ws(best)
        if any(body[:60] in prev for prev in seen):
            continue
        seen.append(body)
        idx += 1
        page = ev.get("page")
        section = norm_ws(ev.get("section") or "")
        where = f"第 {page} 页" + (f" · {section}" if section else "")
        lines.append(f"**要点 {idx}**（{where}）：{body} [{ev['sid']}]")

    if idx == 0:
        lines.append(f"- {snippet(evidence[0]['text'], 300)} [{evidence[0]['sid']}]")
    lines.append("> 以上为原文要点摘取；配置 LLM_API_KEY 后可生成连贯的论述式回答。")
    return "\n".join(lines)


def answer(query: str, paper_ids: list[str] | None = None,
           top_k: int = 8, history: list[dict] | None = None,
           hierarchical: bool = False) -> dict:
    evidence = _evidence(query, paper_ids, top_k, hierarchical=hierarchical)
    if _low_confidence(evidence):
        return {
            "answer": "现有文献库中没有找到与该问题足够相关的原文依据，暂不能给出可靠回答。"
                      "建议先上传相关论文，或把问题具体到某篇文献的章节。",
            "sources": [],
            "confidence": 0.0,
            "mode": "refuse",
        }
    messages = [{"role": "system", "content": SYSTEM}]
    for turn in (history or [])[-4:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": turn["content"][:800]})
    messages.append({
        "role": "user",
        "content": f"问题：{query}\n\n可用证据（仅可使用这些内容）：\n"
                   f"{llm.format_evidence(evidence)}",
    })
    if llm.is_available():
        try:
            text = llm.chat(messages, max_tokens=1400)
            mode = "llm"
        except Exception as exc:
            text = _offline_answer(query, evidence) + f"\n\n（LLM 调用失败：{exc}）"
            mode = "fallback"
    else:
        text = _offline_answer(query, evidence)
        mode = "offline"

    cleaned, used, dropped = verify_citations(text, evidence)
    sources = [ev for ev in evidence if ev["sid"] in used]
    confidence = _confidence(evidence, used, dropped)
    return {
        "answer": cleaned,
        "sources": sources,
        "confidence": confidence,
        "mode": mode,
        "dropped_citations": dropped,
        "evidence_count": len(evidence),
    }


def verify_citations(text: str, evidence: list[dict]) -> tuple[str, set[str], list[str]]:
    """删除不存在的引用编号；把无效编号记录为 dropped，便于前端提示。"""
    valid = {ev["sid"] for ev in evidence}
    found = set(CITE_RE.findall(text or ""))
    dropped = sorted(found - valid)
    for sid in dropped:
        text = text.replace(f"[{sid}]", "")
    used = {sid for sid in found if sid in valid}
    return text.strip(), used, dropped


def _confidence(evidence: list[dict], used: set[str], dropped: list[str]) -> float:
    if not evidence:
        return 0.0
    base = min(1.0, 0.35 + 0.5 * min(evidence[0]["dense_score"], 1.0))
    if len(used) >= 2:
        base += 0.1
    if dropped:
        base -= min(0.25, 0.08 * len(dropped))
    return round(max(0.05, min(base, 0.98)), 2)


def compare(papers: list[dict], aspect: str,
            top_k: int = 3) -> dict:
    """多论文跨文献对比：逐篇检索证据后统一生成对照表。"""
    blocks, per_paper = [], {}
    for idx, p in enumerate(papers, start=1):
        query = f"{aspect} {p.get('title', '')}"
        hits = hybrid_search(query, [p["id"]], top_k=top_k)
        evid = []
        for j, hit in enumerate(hits, start=1):
            ev = {
                "sid": f"P{idx}S{j}", "paper_id": p["id"],
                "paper_title": p.get("title", p["id"]), "page": hit["page"],
                "bbox": hit["bbox"], "section": hit["section"],
                "text": hit["text"], "score": round(hit["score"], 4),
                "dense_score": round(hit["dense_score"], 4),
            }
            evid.append(ev)
        per_paper[p["id"]] = evid
        blocks.extend(evid)

    if not blocks:
        return {"answer": "没有检索到可对比的内容。", "sources": [], "confidence": 0.0,
                "mode": "refuse"}
    if not llm.is_available():
        rows = ["| 论文 | 原文证据（摘录） |", "|---|---|"]
        for p in papers:
            evs = per_paper.get(p["id"], [])
            cell = snippet(evs[0]["text"], 220) if evs else "未检索到"
            ref = f" [{evs[0]['sid']}]" if evs else ""
            rows.append(f"| {p.get('title', p['id'])} | {cell}{ref} |")
        return {"answer": "\n".join(rows), "sources": blocks, "confidence": 0.4,
                "mode": "offline"}

    prompt = (
        f"对比主题：{aspect}\n\n请输出 Markdown 表格，列分别为：对比维度 | "
        + " | ".join(p.get("title", p["id"])[:40] for p in papers) +
        "\n行至少覆盖：核心方法、关键参数/规模、数据集、实验结论、局限性。"
        "每个单元格末尾必须标注证据编号。\n\n证据：\n" + llm.format_evidence(blocks)
    )
    try:
        text = llm.chat([{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": prompt}], max_tokens=1600)
        mode = "llm"
    except Exception as exc:
        text = f"对比生成失败：{exc}"
        mode = "error"
    cleaned, used, dropped = verify_citations(text, blocks)
    return {"answer": cleaned, "sources": [b for b in blocks if b["sid"] in used],
            "confidence": 0.8 if used else 0.35, "mode": mode, "dropped_citations": dropped}
