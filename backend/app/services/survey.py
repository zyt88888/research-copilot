"""模块五：基于局部引用图谱自动生成领域简短综述。

数据全部来自库内真实论文（标题 / 摘要 / 年份 / 作者 / 被引 / PageRank）
与 Future Work 抽取结果：
- 在线：LLM 只允许基于给定 [P#] 条目写作，非法引用会被剔除；
- 离线：确定性抽取式模板（时间脉络 / 核心工作 / 主题词 / 开放问题），
  不做任何无数据支撑的推断，也不编造参考文献。
"""
from __future__ import annotations

import re
from collections import Counter

from . import graph, llm
from .store import list_papers, paper_meta
from ..utils import keyword_overlap, snippet, tokenize

# 关键词统计时的噪声词：学术写作通用词，不反映领域主题
STOP = {
    "this", "that", "with", "from", "have", "were", "been", "which", "their",
    "these", "those", "such", "than", "then", "also", "more", "most", "into",
    "over", "under", "between", "through", "using", "used", "use", "based",
    "approach", "method", "methods", "result", "results", "show", "shows",
    "paper", "work", "works", "propose", "proposed", "however", "while",
    "each", "both", "other", "some", "when", "where", "what", "does", "not",
    "can", "may", "our", "its", "they", "them", "there", "here", "about",
    "first", "second", "well", "large", "small", "high", "low", "new",
    "learning", "model", "models", "data", "training", "performance",
}
CJK_NOISE = {"方法", "模型", "系统", "论文", "研究", "实验", "结果", "基于", "本文",
             "提出", "一种", "我们", "问题", "分析", "应用", "技术", "数据"}

SURVEY_SYSTEM = """你是学术综述作者。只能使用用户提供的 [P#] 条目写作：
- 引用必须写成 [P1]、[P2] 这种编号，编号只能来自给定条目，禁止虚构；
- 不要输出参考文献列表（系统会自动附上真实条目）；
- 中文、学术语体、300-500 字，包含：研究脉络、核心工作对比、开放问题；
- 每条论断都要能在给定条目的标题/摘要中找到依据，查不到就不要写。"""


def _fmt_ref(entry: dict) -> str:
    authors = entry.get("authors") or []
    first = authors[0].split()[-1] if authors else "Unknown"
    venue = entry.get("venue") or (
        f"arXiv:{entry['arxiv_id']}" if entry.get("arxiv_id") else "n.d.")
    return f"[{entry['ref']}] {first}, {entry.get('year') or 'n.d.'}. " \
           f"{entry.get('title')}. {venue}"


def _keywords(entries: list[dict], top: int = 8) -> list[dict]:
    cnt: Counter = Counter()
    for e in entries:
        text = f"{e.get('title') or ''} {e.get('abstract') or ''}"
        for tok in tokenize(text):
            if len(tok) >= 4 and tok.isascii() and tok not in STOP and not tok.isdigit():
                cnt[tok] += 1
        for phrase in re.findall(r"[一-鿿]{2,5}", f"{e.get('title') or ''} {e.get('abstract') or ''}"):
            if phrase not in CJK_NOISE:
                cnt[phrase] += 1
    return [{"term": w, "count": c} for w, c in cnt.most_common(top) if c >= 2]


def _collect(topic: str | None, paper_ids: list[str] | None, max_papers: int):
    library = [p for p in list_papers() if not paper_ids or p["id"] in paper_ids]
    if not library:
        return [], {}, [], {}
    g = graph.build_graph(paper_ids)
    fut = graph.future_work(paper_ids, max_items=8)
    by_id = {p["id"]: p for p in library}
    ranked = [n for n in g.get("nodes", []) if n["id"] in by_id]
    if not ranked:
        ranked = [{"id": p["id"], "pagerank": 0, "in_degree": 0, "role": "normal"}
                  for p in library]
    if topic:
        # 有主题时按「与主题的词汇重合度 + PageRank」排序，避免综述跑题
        ranked.sort(key=lambda n: (
            -keyword_overlap(topic, (by_id[n["id"]].get("title") or "") + " " +
                             ((paper_meta(n["id"]) or {}).get("abstract") or "")),
            -n.get("pagerank", 0)))
    entries = []
    for i, node in enumerate(ranked[:max_papers]):
        p = by_id[node["id"]]
        meta = paper_meta(p["id"]) or {}
        entries.append({
            "ref": f"P{i + 1}", "paper_id": p["id"],
            "title": p.get("title"), "year": p.get("year"),
            "authors": p.get("authors") or [], "venue": p.get("venue"),
            "arxiv_id": p.get("arxiv_id"),
            "abstract": snippet(meta.get("abstract") or "", 320),
            "pagerank": node.get("pagerank", 0), "in_degree": node.get("in_degree", 0),
            "role": node.get("role", "normal"),
        })
    years = [e["year"] for e in entries if e.get("year")]
    stats = {
        "papers": len(entries), "library": len(library),
        "edges": g.get("stats", {}).get("edges", 0),
        "bedrock": sum(1 for e in entries if e["role"] == "bedrock"),
        "frontier": sum(1 for e in entries if e["role"] == "frontier"),
        "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
        "open_questions": fut.get("open_count", 0),
        "keywords": _keywords(entries),
    }
    return entries, stats, fut.get("items", []), fut


def _offline_markdown(topic: str | None, entries: list[dict], stats: dict,
                      future_items: list[dict]) -> str:
    title = topic.strip() if topic and topic.strip() else "库内论文主题"
    span = (f"{stats['year_min']}–{stats['year_max']}" if stats["year_min"]
            else "年份未知")
    lines = [f"# 领域综述：{title}", "",
             f"> 基于库内 {stats['library']} 篇论文的引用图谱自动生成"
             f"（离线抽取式，无大模型参与，仅陈述库内可见事实）。", "",
             f"图谱共 {stats['papers']} 个节点、{stats['edges']} 条引用边，"
             f"覆盖 {span}；其中基石论文 {stats['bedrock']} 篇、前沿论文 "
             f"{stats['frontier']} 篇。", "", "## 一、时间脉络"]

    by_year: dict[int | str, list[dict]] = {}
    for e in entries:
        by_year.setdefault(e.get("year") or "n.d.", []).append(e)
    for year in sorted(by_year, key=lambda y: (y == "n.d.", y if isinstance(y, int) else 0)):
        group = sorted(by_year[year], key=lambda e: -e["pagerank"])
        names = "；".join(f"[{e['ref']}] {e['title']}" for e in group[:3])
        lines.append(f"- **{year}**（{len(group)} 篇）：{names}")

    lines += ["", "## 二、核心工作（按 PageRank）"]
    for e in sorted(entries, key=lambda x: -x["pagerank"])[:5]:
        abstract = e.get("abstract") or "（库内无摘要）"
        lines.append(f"- **[{e['ref']}] {e['title']}**（{e.get('year') or 'n.d.'}，"
                     f"被引 {e['in_degree']}）：{snippet(abstract, 160)}")

    lines += ["", "## 三、主题分布"]
    if stats["keywords"]:
        lines.append("库内标题与摘要中的高频主题词：")
        lines.append("　".join(f"`{k['term']}`×{k['count']}" for k in stats["keywords"]))
    else:
        lines.append("库内文本不足以统计出稳定主题词（需更多论文或摘要）。")

    lines += ["", "## 四、开放问题（Future Work 抽取）"]
    open_items = [i for i in future_items if i.get("status") == "open"][:5]
    if open_items:
        for it in open_items:
            lines.append(f"- {snippet(it['text'], 180)}（{it.get('paper_title')}"
                         f"，{it.get('year') or 'n.d.'}）")
    else:
        lines.append("库内论文未抽取到明确且尚未解决的 Future Work 句子。")

    lines += ["", "## 参考文献"]
    lines += [f"- {_fmt_ref(e)}" for e in entries]
    return "\n".join(lines)


def _strip_invalid_citations(markdown: str, allowed: set[str]) -> tuple[str, int]:
    dropped = 0

    def repl(m):
        nonlocal dropped
        if m.group(1) in allowed:
            return m.group(0)
        dropped += 1
        return ""

    return re.sub(r"\[(P\d+)\]", repl, markdown), dropped


def _cut_refs_section(markdown: str) -> str:
    m = re.search(r"\n#+\s*(参考文献|References)\s*\n", markdown, re.I)
    return markdown[:m.start()] if m else markdown


def generate(topic: str | None = None, paper_ids: list[str] | None = None,
             max_papers: int = 12) -> dict:
    entries, stats, future_items, fut = _collect(topic, paper_ids, max_papers)
    if not entries:
        return {"markdown": "# 领域综述\n\n库中暂无论文，无法生成综述。请先上传 PDF 文献。",
                "mode": "empty", "citations": [], "stats": stats,
                "dropped_citations": 0, "notice": "库中暂无论文"}

    refs_md = "\n".join(f"- {_fmt_ref(e)}" for e in entries)
    if not llm.is_available():
        return {"markdown": _offline_markdown(topic, entries, stats, future_items),
                "mode": "offline", "citations": entries, "stats": stats,
                "dropped_citations": 0, "open_questions": fut.get("open_count", 0)}

    entry_txt = "\n\n".join(
        f"[{e['ref']}] {e['title']}（{e.get('year') or 'n.d.'}；"
        f"作者：{', '.join(e['authors'][:4]) or '未知'}；"
        f"venue：{e.get('venue') or 'n.d.'}；PageRank：{e['pagerank']}；"
        f"库内被引：{e['in_degree']}；角色：{e['role']}）\n"
        f"摘要：{e['abstract'] or '（无摘要）'}" for e in entries)
    open_txt = "\n".join(
        f"- [{next((x['ref'] for x in entries if x['paper_id'] == i['paper_id']), '?')}] "
        f"{snippet(i['text'], 160)}" for i in future_items
        if i.get("status") == "open") or "（未抽取到明确的开放问题）"
    prompt = (f"综述主题：{topic or '库内论文所构成的领域'}\n\n"
              f"可用条目（只能引用这些编号）：\n{entry_txt}\n\n"
              f"库内抽取到的开放问题：\n{open_txt}\n\n"
              "请写一篇 300-500 字的中文领域综述，按「研究脉络 → 核心工作对比 → 开放问题」组织，"
              "引用写成 [P#]。")
    try:
        text = llm.chat([{"role": "system", "content": SURVEY_SYSTEM},
                         {"role": "user", "content": prompt}], max_tokens=1600)
    except Exception as exc:
        offline = _offline_markdown(topic, entries, stats, future_items)
        return {"markdown": offline, "mode": "offline", "citations": entries,
                "stats": stats, "dropped_citations": 0,
                "open_questions": fut.get("open_count", 0), "notice": str(exc)[:160]}

    text = _cut_refs_section(text)
    text, dropped = _strip_invalid_citations(text, {e["ref"] for e in entries})
    cited = {m for m in re.findall(r"\[(P\d+)\]", text)}
    md = f"{text.strip()}\n\n## 参考文献\n{refs_md}"
    return {"markdown": md, "mode": "llm",
            "citations": [e for e in entries if e["ref"] in cited] or entries,
            "stats": stats, "dropped_citations": dropped,
            "open_questions": fut.get("open_count", 0)}
