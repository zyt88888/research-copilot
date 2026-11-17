"""参考文献「引用含金量」评估：识别只是凑数、正文从未真正使用的应付式引用。

可解释规则（无 LLM 也能跑，断网可用）：
1. 正文提及：按参考文献标题关键词在正文章节里匹配，兼容数字引用标记 [12]；
2. 上下文语义：命中段落里是否出现「提出/改进/采用/对比/启发/指出局限」等线索；
3. 引用位置：方法/实验章节的引用比引言里的泛泛列举更有含金量。

三者加权得到 0-100 分：
- >=60 高价值：正文多次实质性使用；
- 30-59 一般引用：正文有提及但线索较弱；
- 1-29  弱引用：仅顺带提一句；
- 0      应付式：只出现在参考文献列表，正文零提及（下钻时自动剔除）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .store import chunks_of, paper_meta, refs_of

# 正文中可用于判断「这段在讲引用怎么被使用」的语义线索
CUE_PATTERNS: list[tuple[re.Pattern, str, int]] = [
    (re.compile(r"\b(propos\w*|introduc\w*|present\w*|design\w*|develop\w*)\b", re.I),
     "提出/设计", 3),
    (re.compile(r"\b(improv\w*|enhanc\w*|extend\w*|refin\w*|augment\w*)\b", re.I),
     "改进/扩展", 3),
    (re.compile(r"\b(compar\w*|outperform\w*|surpass\w*|better than|baseline)\b", re.I),
     "对比/基线", 3),
    (re.compile(r"\b(adopt\w*|employ\w*|leverage\w*|utili[sz]\w*|follow\w*|"
                r"build\w* on|borrow\w*)\b", re.I), "采用/沿用", 2),
    (re.compile(r"\b(inspir\w*|motivat\w*|shed light)\b", re.I), "启发", 2),
    (re.compile(r"\b(limitation\w*|drawback\w*|weakness\w*|fail\w*)\b", re.I),
     "批评/指出局限", 2),
    (re.compile(r"(提出|改进|扩展|采用|沿用|对比|优于|启发|局限|不足)"), "中文线索", 2),
]

# 章节位置权重：方法/实验里的引用通常是真在用，引言/相关工作多为铺陈
SECTION_WEIGHTS: list[tuple[re.Pattern, float]] = [
    (re.compile(r"method|approach|model|architecture|framework|algorithm|proposed", re.I), 1.0),
    (re.compile(r"experiment|evaluation|result|ablation|benchmark", re.I), 1.0),
    (re.compile(r"discussion|analysis", re.I), 0.85),
    (re.compile(r"conclusion|summary", re.I), 0.7),
    (re.compile(r"related work|background|preliminar", re.I), 0.6),
    (re.compile(r"introduction|intro", re.I), 0.55),
]

STOP = {
    "the", "and", "for", "with", "from", "that", "this", "these", "those", "using",
    "based", "into", "toward", "towards", "via", "under", "over", "are", "was",
    "were", "has", "have", "can", "not", "but", "its", "their", "our", "your",
    "new", "novel", "study", "analysis", "paper", "towards", "all", "you", "need",
    "proceedings", "conference", "international", "journal", "arxiv", "preprint",
    "et", "al", "pp", "vol", "no",
}

WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{3,}")


@dataclass
class _Hit:
    chunk: dict
    matched: int
    terms: list[str] = field(default_factory=list)


def _terms(title: str, limit: int = 8) -> list[str]:
    seen: list[str] = []
    for t in WORD_RE.findall(title or ""):
        low = t.lower()
        if low in STOP or low in seen:
            continue
        seen.append(low)
        if len(seen) >= limit:
            break
    return seen


def _section_weight(section: str | None) -> float:
    for pat, w in SECTION_WEIGHTS:
        if pat.search(section or ""):
            return w
    return 0.5


def _cues(text: str) -> list[tuple[str, int]]:
    out = []
    for pat, label, weight in CUE_PATTERNS:
        if pat.search(text or ""):
            out.append((label, weight))
    return out


def _snippet(text: str, terms: list[str], limit: int = 170) -> str:
    low = (text or "").lower()
    pos = -1
    for t in terms:
        pos = low.find(t)
        if pos >= 0:
            break
    if pos < 0:
        return (text or "")[:limit]
    start = max(0, pos - 60)
    return ("…" if start else "") + (text or "")[start:start + limit].strip() + "…"


def _body_chunks(paper_id: str) -> list[dict]:
    """正文章节（排除参考文献块与标题），用于判断引用是否被真正使用。"""
    return [c for c in chunks_of(paper_id)
            if c.get("kind") not in ("reference", "heading") and (c.get("text") or "").strip()]


def analyze(paper_id: str) -> dict:
    meta = paper_meta(paper_id) or {}
    if not meta:
        return {"error": f"未找到论文 {paper_id}"}
    refs = refs_of(paper_id)
    body = _body_chunks(paper_id)

    # 倒排索引：token → 命中正文块下标，避免 400 条参考文献 × 上千段落的全量比对
    postings: dict[str, set[int]] = {}
    for i, c in enumerate(body):
        for tok in set(_terms(c.get("text") or "", limit=10_000)):
            postings.setdefault(tok, set()).add(i)

    results = []
    for idx, ref in enumerate(refs):
        title = (ref.get("title") or "").strip()
        terms = _terms(title)
        best: dict[int, _Hit] = {}

        # 1) 标题关键词匹配
        if terms:
            counts: dict[int, int] = {}
            for t in terms:
                for ci in postings.get(t, ()):
                    counts[ci] = counts.get(ci, 0) + 1
            need = 2 if len(terms) >= 4 else 1
            for ci, n in counts.items():
                if n < need or n / len(terms) < 0.5:
                    continue
                best[ci] = _Hit(chunk=body[ci], matched=n, terms=terms)

        # 2) 数字引用标记 [n]（论文用顺序编码制时是确定性证据）
        marker = re.compile(r"\[\s*" + str(idx + 1) + r"\s*\]")
        for ci, c in enumerate(body):
            if ci in best:
                continue
            if marker.search(c.get("text") or ""):
                best[ci] = _Hit(chunk=c, matched=1, terms=terms)

        hits = sorted(best.values(), key=lambda h: h.matched, reverse=True)
        mentions = len(hits)
        cue_types: list[str] = []
        strongest = 0
        best_pos = 0.0
        evidence = []
        for h in hits[:4]:
            text = h.chunk.get("text") or ""
            cues = _cues(text)
            for label, weight in cues:
                if label not in cue_types:
                    cue_types.append(label)
                strongest = max(strongest, weight)
            best_pos = max(best_pos, _section_weight(h.chunk.get("section")))
            evidence.append({
                "page": h.chunk.get("page"),
                "section": h.chunk.get("section") or "",
                "matched_terms": h.matched,
                "cues": [label for label, _ in cues],
                "snippet": _snippet(text, h.terms or terms),
            })

        if mentions == 0:
            score, label = 0, "应付式"
        else:
            mention_score = min(45, 15 * min(mentions, 3))
            cue_score = min(35, 10 * strongest + 5 * max(0, len(cue_types) - 1))
            pos_score = round(20 * best_pos)
            score = min(100, mention_score + cue_score + pos_score)
            if score >= 60:
                label = "高价值"
            elif score >= 30:
                label = "一般引用"
            else:
                label = "弱引用"

        results.append({
            "ref_id": ref.get("id"),
            "index": idx + 1,
            "raw": ref.get("raw"),
            "title": title,
            "arxiv_id": ref.get("arxiv_id"),
            "doi": ref.get("doi"),
            "year": ref.get("year"),
            "score": score,
            "label": label,
            "mentions": mentions,
            "sections": sorted({h["section"] for h in evidence if h["section"]}),
            "cues": cue_types,
            "evidence": evidence,
            "confidence": "high" if len(terms) >= 3 else "low",
        })

    counts = {"高价值": 0, "一般引用": 0, "弱引用": 0, "应付式": 0}
    for r in results:
        counts[r["label"]] += 1
    return {
        "paper_id": paper_id,
        "title": meta.get("title"),
        "refs": results,
        "summary": {
            "total": len(results),
            "high": counts["高价值"],
            "medium": counts["一般引用"],
            "low": counts["弱引用"],
            "perfunctory": counts["应付式"],
            "method": "正文提及次数（数字引用标记 + 标题关键词）+ 上下文语义线索 + 引用章节位置，"
                      "三项加权 0-100；正文零提及判为应付式",
        },
    }


def select_for_drill(result: dict, min_score: int = 30,
                     limit: int = 5, quality_filter: bool = True) -> list[dict]:
    """从未通过滤的参考文献里挑出值得下钻的条目（按含金量降序）。"""
    refs = [r for r in result.get("refs") or []]
    if quality_filter:
        refs = [r for r in refs if r["score"] >= min_score]
    refs.sort(key=lambda r: r["score"], reverse=True)
    return refs[:max(0, limit)]
