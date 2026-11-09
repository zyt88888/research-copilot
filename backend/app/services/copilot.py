"""学术写作 Copilot：无幻觉论文框架、Mermaid/TikZ 架构图、CSV→图表+Figure Caption。"""
from __future__ import annotations

import csv
import io
import math
import re
from typing import Any

from . import llm
from .store import chunks_of, hybrid_search, list_papers, paper_meta


OUTLINE_SYSTEM = """你是资深学术写作导师。输出可直接使用的论文框架：
- 结构：Abstract / Introduction / Related Work / Method / Experiments / Discussion / Conclusion
- 每个小节给出 2-4 条写作要点（bullet），Method 需给出可执行的技术路线
- 只能引用"可用文献"中的论文，禁止虚构参考文献；引用格式 [Author, Year]
- References 仅列可用文献真实条目，不得新增"""


def outline(idea: str, paper_ids: list[str] | None = None,
            language: str = "zh") -> dict:
    papers = [p for p in list_papers() if not paper_ids or p["id"] in paper_ids]
    evidence = hybrid_search(idea, [p["id"] for p in papers], top_k=6) if papers else []
    refs = []
    for p in papers[:12]:
        year = p.get("year") or ""
        first_author = (p.get("authors") or ["Unknown"])[0].split()[-1] if p.get("authors") else "Unknown"
        refs.append(f"{first_author}, {year}. {p.get('title')}. "
                    f"{p.get('venue') or ('arXiv:' + p['arxiv_id'] if p.get('arxiv_id') else '')}")
    if not llm.is_available():
        return _offline_outline(idea, papers, refs)
    prompt = f"""研究想法 / Idea：{idea}

可用文献（只能引用这些）：
{chr(10).join('- ' + r for r in refs) if refs else '（库中暂无文献，References 留空并注明待补充）'}

可参考的证据片段：
{llm.format_evidence([{**e, 'sid': f'S{i+1}', 'paper_title': (paper_meta(e['paper_id']) or {}).get('title', '')} for i, e in enumerate(evidence)]) if evidence else '（无）'}

输出 Markdown 论文框架：先给一段 120 字以内的 Abstract 草稿，再按小节展开；
每个小节下用 bullet 给写作要点；最后给 References（仅真实可用文献）。"""
    try:
        text = llm.chat([{"role": "system", "content": OUTLINE_SYSTEM},
                         {"role": "user", "content": prompt}], max_tokens=2200)
        mode = "llm"
    except Exception as exc:
        return {**_offline_outline(idea, papers, refs), "notice": str(exc)}
    text, dropped = _strip_fake_refs(text, refs)
    return {"markdown": text, "references": refs, "mode": mode,
            "dropped_references": dropped, "evidence": evidence[:3]}


def _strip_fake_refs(markdown: str, valid: list[str]) -> tuple[str, int]:
    """删除 References 段中不在可用文献列表内的条目，防止编造引用。"""
    if "## References" not in markdown and "参考文献" not in markdown:
        return markdown, 0
    head, sep, tail = re.split(r"(##\s*(?:References|参考文献))", markdown, maxsplit=1, flags=re.I) if False else (markdown, "", "")
    idx = re.search(r"\n#+\s*(References|参考文献)\s*\n", markdown, re.I)
    if not idx:
        return markdown, 0
    body = markdown[idx.end():]
    lines = [l for l in body.split("\n") if l.strip()]
    kept, dropped = [], 0
    for line in lines:
        if line.strip().startswith(("-", "*", "[")) or re.match(r"^\[\d+\]", line.strip()):
            if any(_ref_match(line, v) for v in valid) or not valid:
                kept.append(line)
            else:
                dropped += 1
        else:
            kept.append(line)
    return markdown[:idx.end()] + "\n" + "\n".join(kept), dropped


def _ref_match(line: str, ref: str) -> bool:
    title = ref.split(". ", 1)[-1]
    return title[:40].lower() in line.lower() or line.lower() in ref.lower()


def _offline_outline(idea: str, papers: list[dict], refs: list[str]) -> dict:
    titles = "\n".join(f"- {p.get('title')}（{p.get('year') or 'n.d.'}）" for p in papers[:8])
    md = f"""# 论文框架草稿（离线模板）

## 摘要（Abstract）
本文围绕「{idea}」展开，基于库内 {len(papers)} 篇相关工作，提出…（待补充方法与结果）。

## 1. Introduction
- 研究背景与痛点：说明为什么「{idea}」值得解决
- 现有方法缺口：结合 Related Work 指出现有方案的局限
- 本文贡献：列出 2-3 条可验证的贡献点

## 2. Related Work
- 梳理库内工作：
{titles or '- 暂无可用文献'}
- 明确指出本文与上述工作的差异

## 3. Method
- 问题形式化定义
- 模型/系统总体架构（可配合 Mermaid 图）
- 关键模块与训练/推理流程

## 4. Experiments
- 数据集与评价指标
- Baselines 与消融实验设计
- 预期结果与误差分析

## 5. Discussion / Limitations

## 6. Conclusion

## References
{chr(10).join('- ' + r for r in refs) if refs else '- （库中暂无文献，需补充真实引用）'}
"""
    return {"markdown": md, "references": refs, "mode": "offline",
            "dropped_references": 0, "evidence": []}


DIAGRAM_SYSTEM = "你是学术图表工程师，只输出代码，不输出解释。"


def diagram(idea: str, kind: str = "mermaid") -> dict:
    if not llm.is_available():
        if kind == "tikz":
            return {"code": _tikz_template(idea), "mode": "offline"}
        return {"code": _mermaid_template(idea), "mode": "offline"}
    if kind == "tikz":
        prompt = (f"为研究方案「{idea}」画 LaTeX TikZ 架构图（可直接编译），"
                  "使用 node + arrows 表示数据流，风格简洁学术，只输出 tikz 代码块。")
    else:
        prompt = (f"为研究方案「{idea}」画 Mermaid flowchart（TD 方向），"
                  "包含输入、检索、模型、评估四个层次，节点文字简短，只输出代码块。")
    try:
        code = llm.chat([{"role": "system", "content": DIAGRAM_SYSTEM},
                         {"role": "user", "content": prompt}], max_tokens=900)
        code = re.sub(r"^```[a-zA-Z]*\n|```$", "", code.strip())
        return {"code": code, "mode": "llm"}
    except Exception as exc:
        return {"code": _mermaid_template(idea), "mode": f"fallback:{exc}"}


def _mermaid_template(idea: str) -> str:
    safe = idea.replace('"', "'")[:40]
    return f"""flowchart TD
    A[输入: 研究问题/文献库] --> B[PDF 解析与去重]
    B --> C[混合检索 BM25 + Dense]
    C --> D[RAG 证据与溯源]
    D --> E[方法设计: {safe}]
    E --> F[实验与评估]
    F --> G[论文撰写与图表]"""


def _tikz_template(idea: str) -> str:
    return r"""\begin{tikzpicture}[node distance=1.4cm,
  box/.style={draw,rounded corners,align=center,minimum width=3cm,minimum height=0.9cm}]
  \node[box] (a) {Research Problem};
  \node[box,right=of a] (b) {Retrieval (BM25+Dense)};
  \node[box,right=of b] (c) {Evidence \& Grounding};
  \node[box,below=of c] (d) {Method Design};
  \node[box,left=of d] (e) {Experiments};
  \draw[->] (a)--(b); \draw[->] (b)--(c);
  \draw[->] (c)--(d); \draw[->] (d)--(e);
\end{tikzpicture}"""


# --------------------------------------------------------------------------
# CSV → 图表
# --------------------------------------------------------------------------
def csv_to_chart(data: bytes, filename: str = "data.csv") -> dict:
    text = data.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if r and any(str(c).strip() for c in r)]
    if len(rows) < 2:
        raise ValueError("CSV 至少需要表头 + 1 行数据")
    header, body = rows[0], rows[1:]
    cols = [h.strip() or f"col{i}" for i, h in enumerate(header)]
    numeric = _numeric_columns(body, len(cols))
    table = {"columns": cols[:12], "rows": body[:200]}

    if len(numeric) >= 3 and len(body) <= 12:
        chart_type = "radar"
    elif numeric and _looks_like_time(body, numeric[0]):
        chart_type = "line"
    else:
        chart_type = "bar"

    option, chart_type = _echarts_option(cols, body, numeric, chart_type)
    summary = _describe(cols, body, numeric)
    caption = _caption(cols, body, numeric, chart_type)
    if llm.is_available():
        try:
            caption = llm.chat([
                {"role": "system", "content": "你是学术图表编辑，写一句话 Figure Caption，简洁客观。"},
                {"role": "user", "content": f"数据摘要：{summary}\n图表类型：{chart_type}。"
                                            f"输出一句英文 Figure Caption（不超过 35 词）。"}],
                max_tokens=120).strip()
        except Exception:
            pass
    return {"chart": option, "type": chart_type, "table": table,
            "caption": caption, "summary": summary, "filename": filename}


def _numeric_columns(body: list[list[str]], n: int) -> list[int]:
    out = []
    for i in range(n):
        vals = [r[i] for r in body if len(r) > i and r[i].strip()]
        if not vals:
            continue
        ok = 0
        for v in vals:
            try:
                float(v.replace(",", "").replace("%", ""))
                ok += 1
            except ValueError:
                pass
        if ok >= max(2, len(vals) * 0.7):
            out.append(i)
    return out


def _looks_like_time(body: list[list[str]], col: int) -> bool:
    vals = [r[col] for r in body[:5] if len(r) > col]
    return any(re.search(r"(19|20)\d{2}|Q[1-4]|epoch|step|month|year", v, re.I)
               for v in vals)


def _echarts_option(cols, body, numeric, chart_type) -> tuple[dict, str]:
    label_col = next((i for i in range(len(cols)) if i not in numeric), 0)
    labels = [r[label_col] if len(r) > label_col else f"row{i+1}"
              for i, r in enumerate(body)]
    palette = ["#6366f1", "#22d3ee", "#f59e0b", "#f472b6", "#34d399", "#a78bfa"]

    def num(row, idx):
        try:
            return float(str(row[idx]).replace(",", "").replace("%", ""))
        except (ValueError, IndexError):
            return 0.0

    if chart_type == "radar":
        indicators = [{"name": cols[i], "max": max(
            [num(r, i) for r in body] + [1]) * 1.1} for i in numeric[:8]]
        return {
            "radar": {"indicator": indicators},
            "series": [{
                "type": "radar", "areaStyle": {"opacity": 0.15},
                "data": [{"name": labels[j], "value": [num(r, i) for i in numeric[:8]]}
                         for j, r in enumerate(body[:8])],
            }],
            "legend": {"show": True}, "color": palette,
        }, "radar"

    series = [{
        "name": cols[i], "type": "line" if chart_type == "line" else "bar",
        "smooth": chart_type == "line", "data": [num(r, i) for r in body],
        **({"areaStyle": {"opacity": 0.15}} if chart_type == "line" else
           {"itemStyle": {"borderRadius": [4, 4, 0, 0]}}),
    } for i in numeric[:6]]
    return {
        "tooltip": {"trigger": "axis"},
        "legend": {"show": len(series) > 1},
        "grid": {"left": 40, "right": 20, "top": 40, "bottom": 50},
        "xAxis": {"type": "category", "data": labels, "axisLabel": {"rotate": 30}},
        "yAxis": {"type": "value"},
        "series": series, "color": palette,
    }, chart_type


def _describe(cols, body, numeric) -> str:
    if not numeric:
        return f"共 {len(body)} 行、{len(cols)} 列，未检测到数值列"
    parts = [f"共 {len(body)} 行、{len(cols)} 列，数值列 {len(numeric)} 个"]
    for i in numeric[:3]:
        vals = [float(str(r[i]).replace(",", "").replace("%", "")) for r in body
                if len(r) > i and _is_num(r[i])]
        if vals:
            parts.append(f"{cols[i]} 范围 {min(vals):.4g}–{max(vals):.4g}，均值 {sum(vals)/len(vals):.4g}")
    return "；".join(parts)


def _is_num(v: str) -> bool:
    try:
        float(str(v).replace(",", "").replace("%", ""))
        return True
    except ValueError:
        return False


def _caption(cols, body, numeric, chart_type) -> str:
    if not numeric:
        return "Table 1. Summary of the provided dataset."
    col = cols[numeric[0]]
    vals = [float(str(r[numeric[0]]).replace(",", "").replace("%", ""))
            for r in body if len(r) > numeric[0] and _is_num(r[numeric[0]])]
    trend = "increase" if vals and vals[-1] >= vals[0] else "decrease"
    kind = {"line": "trend", "bar": "comparison", "radar": "multi-dimensional profile"}[chart_type]
    return (f"Figure 1. {kind.capitalize()} of {col} across {len(body)} samples, "
            f"showing an overall {trend} (range {min(vals):.4g}–{max(vals):.4g})."
            if vals else "Figure 1. Overview of the dataset.")
