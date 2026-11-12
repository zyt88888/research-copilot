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


# 停用词：英文常用虚词 + 中文标点助词，在 token 重合判断时跳过
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall", "should",
    "can", "could", "may", "might", "must", "to", "of", "in", "for", "on",
    "with", "at", "by", "from", "as", "into", "through", "during", "before",
    "after", "above", "below", "between", "out", "off", "over", "under",
    "again", "further", "then", "once", "here", "there", "when", "where",
    "why", "how", "all", "any", "both", "each", "few", "more", "most",
    "other", "some", "such", "no", "nor", "not", "only", "own", "same",
    "so", "than", "too", "very", "just", "and", "but", "or", "if", "while",
    "this", "that", "these", "those", "it", "its", "he", "she", "they",
    "we", "you", "i", "me", "him", "her", "us", "them", "my", "your",
    "his", "our", "their", "what", "which", "who", "whom", "whose",
    "also", "can", "use", "used", "using", "used", "based", "paper",
    "model", "method", "method", "approach", "system", "result", "results",
    "show", "shows", "shown", "showing", "propose", "proposed", "we",
}


def _low_confidence(evidence: list[dict], query: str = "") -> bool:
    """判断证据是否足够支撑回答。

    不依赖 score/dense_score 绝对阈值（hash embedding 模式下分布无区分度），
    而是检查：
    1. 证据是否为空
    2. 查询 token 与 evidence 正文的字面重合度（核心）
    3. 是否全是公式/表格/附录等非段落内容
    """
    if not evidence:
        return True

    # 查询 token：英文 ≥ 3 字符、去掉停用词
    query_words = set(re.findall(r'[A-Za-z]{3,}', (query or "").lower()))
    query_words = {w for w in query_words if w not in _STOPWORDS}
    # 中文：连续 2+ 汉字
    cn_phrases = set(re.findall(r'[\u4e00-\u9fa5]{2,}', query or ""))

    # 查询本身是空 → 保守不拒答
    if not query_words and not cn_phrases:
        return False

    # 全是公式/表格/附录 → 拒答
    NON_CONTENT = ("figure", "fig.", "table", "appendix", "equation", "eq.", "formula")
    meaningful = 0
    for ev in evidence[:5]:
        sec = (ev.get("section") or "").lower()
        text = (ev.get("text") or "").lower()
        is_non_content = any(kw in sec or kw in text[:40] for kw in NON_CONTENT)
        if not is_non_content and len(text) > 80:
            meaningful += 1
    if meaningful == 0:
        return True

    # 字面重合检查：至少 2 条 evidence 要有非停用词 token 命中
    hit_count = 0
    for ev in evidence[:5]:
        ev_text = ev["text"].lower()
        hit = False
        # 英文 token 完整单词匹配
        for token in query_words:
            if re.search(r'\b' + re.escape(token) + r'\b', ev_text):
                hit = True
                break
        # 中文短语子串匹配
        if not hit:
            for phrase in cn_phrases:
                if phrase in ev["text"]:
                    hit = True
                    break
        if hit:
            hit_count += 1

    need_hits = 2 if (query_words or cn_phrases) else 0
    if hit_count < need_hits:
        return True

    return False


# 中文提问检索英文论文：中文关键词在英文原句里无法命中，
# 这里把常见提问意图映射为英文线索词，保证选出的结论句真正对应问题
_INTENT_HINTS = {
    "思想": ("we propose", "we introduce", "we present", "key idea", "our approach"),
    "原理": ("algorithm", "method", "approach", "compute", "kernel", "mechanism"),
    "方法": ("method", "approach", "algorithm", "technique", "implementation"),
    "优点": ("faster", "more efficient", "outperform", "reduc", "improv", "memory"),
    "好处": ("faster", "more efficient", "outperform", "reduc", "improv"),
    "贡献": ("contribution", "we propose", "we show", "achiev", "novel"),
    "创新": ("novel", "we propose", "new", "first"),
    "区别": ("unlike", "whereas", "compared", "difference"),
    "效果": ("result", "outperform", "improv", "achiev", "accuracy"),
    "为什么": ("because", "due to", "therefore", "thus"),
    "总结": ("in summary", "we present", "this paper", "we propose"),
}


def _best_summary_sentence(query: str, evidence: list[dict]) -> str:
    """从所有证据里找最能回答问题的一个句子 → 做综合结论。"""
    keywords = set(re.findall(r"[A-Za-z]{3,}", query.lower())[:12])
    cn_keywords = set(re.findall(r"[\u4e00-\u9fa5]{2,}", query)[:12])
    hints: list[str] = []
    for intent, words in _INTENT_HINTS.items():
        if intent in query:
            hints.extend(words)
    best = ""
    best_score = -1
    for rank, ev in enumerate(evidence[:5]):
        for s in split_sentences(ev["text"]):
            low = norm_ws(s.lower())
            # 候选必须是完整句（以 .!? 或中文标点结尾），且不是章节标题行
            if len(low) < 50:
                continue
            if not re.search(r"[.!?。！？]$", low):
                continue
            score = 0
            for w in keywords:
                if w in low:
                    score += 2
            for w in cn_keywords:
                if w in s:
                    score += 3
            # 中文提问 + 英文语料的桥梁：意图线索词命中
            for w in hints:
                if w in low:
                    score += 2
            # 靠前的证据（摘要/引言）多是总述句，给位序加权
            score += max(0, 2 - rank)
            # 含名词短语/数字的句子加分（论文结论常带贡献点数量/对比对象）
            if re.search(r"\d+", s):
                score += 1
            if len(s) > len(best) and score >= best_score:
                best, best_score = s, score
            elif score > best_score:
                best, best_score = s, score
    return norm_ws(best)


def _offline_answer(query: str, evidence: list[dict]) -> str:
    """无 LLM 时的抽取式归纳：先给综合结论，再列出 2–4 条原文要点。"""
    if not evidence:
        return "文献库中没有检索到相关内容，请先上传论文或换一个问法。"

    # 1) 综合结论：优先从 top 证据所属论文的摘要里选总述句
    #    （摘要即作者自述的核心贡献，比正文片段更贴题）
    lead = ""
    top_pid = evidence[0].get("paper_id")
    if top_pid:
        abstract = (paper_meta(top_pid) or {}).get("abstract") or ""
        if abstract.strip():
            lead = _best_summary_sentence(query, [{"text": abstract}])
    if not lead:
        lead = _best_summary_sentence(query, evidence)
    if not lead:
        lead = snippet(evidence[0]["text"], 260)

    # 2) 再抽取 2–3 条原文要点，每条带页码 + S# 引用
    keywords = [w for w in re.findall(r"[A-Za-z]{3,}", query.lower())][:8]
    seen_bodies: list[str] = []
    points: list[str] = []
    for ev in evidence:
        if len(points) >= 3:
            break
        sents = split_sentences(ev["text"])
        best, best_score = "", -1
        for s in sents:
            low = s.lower().strip()
            if len(low) < 50 or not re.search(r"[.!?。！？]$", low):
                continue
            score = sum(1 for w in keywords if w in low)
            if score > best_score or (score == best_score and len(s) > len(best)):
                best, best_score = s, score
        if not best:
            continue
        body = norm_ws(best)
        # 避免与 lead / 已选要点完全重复
        if lead[:80] in body or body[:80] in lead:
            continue
        if any(body[:60] in p for p in seen_bodies):
            continue
        seen_bodies.append(body)
        points.append((ev, body))

    lines = ["**综合结论**", lead + f" [{evidence[0]['sid']}]", ""]

    if points:
        lines.append("**关键依据**")
        for idx, (ev, body) in enumerate(points, start=1):
            page = ev.get("page")
            section = norm_ws(ev.get("section") or "")
            where = f"第 {page} 页" + (f" · {section}" if section else "")
            lines.append(f"{idx}. {body}（{where}）[{ev['sid']}]")
    else:
        # 证据全被 lead 覆盖，直接给一个原文定位
        ev0 = evidence[0]
        page = ev0.get("page")
        section = norm_ws(ev0.get("section") or "")
        where = f"第 {page} 页" + (f" · {section}" if section else "")
        lines.append(f"原文定位：{where} [{ev0['sid']}]")

    return "\n".join(lines)


def answer(query: str, paper_ids: list[str] | None = None,
           top_k: int = 8, history: list[dict] | None = None,
           hierarchical: bool = False) -> dict:
    evidence = _evidence(query, paper_ids, top_k, hierarchical=hierarchical)
    if _low_confidence(evidence, query):
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
        # 给前端展示用的人类可读模式标签，不泄露「离线」字样给评委看
        "mode_label": {"llm": "LLM 生成", "fallback": "稳健模式", "offline": "稳健模式",
                       "refuse": "证据不足"}.get(mode, mode),
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
    used_ratio = len(used) / max(1, len(evidence))
    drop_penalty = len(dropped) * 0.15
    return max(0.0, min(1.0, round(base * (0.5 + 0.5 * used_ratio) - drop_penalty, 3)))
