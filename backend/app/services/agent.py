"""科研 Agent：Task Planning → Tool Calling → 观察 → 再规划 → 汇总。

- 有 LLM：JSON 计划 + function calling 循环；
- 无 LLM：规则路由（仍可完整演示闭环），并在 trace 中明确标注 offline。
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Generator

from ..db import execute
from ..utils import CJK, snippet
from . import copilot, graph, llm, rag, translate
from .store import (chunks_of, hybrid_search, index_stats, list_papers,
                    now, paper_meta, refs_of)

SYSTEM = """你是"科研小助手 Agent"，可以调用工具完成文献分析任务。
工作方式：先给出 2-5 步计划，再逐步调用工具；每一步只调用一个工具。
输出 JSON：{"thought":"...","tool":"tool_name","args":{...}} 或 {"thought":"...","final":"..."}。
可用工具：
- search_library(query, paper_ids?) 检索文献证据
- paper_detail(paper_id) 查看论文元数据/章节
- compare_papers(paper_ids, aspect) 多篇论文对比
- citation_graph(paper_ids?) 引用图谱与核心节点
- future_work(paper_ids?) 前沿 Idea 提取
- translate_paper(paper_id, lang) 翻译论文要点
- draft_outline(idea, paper_ids?) 生成论文框架
- search_arxiv(query) 在线检索 arXiv 找新论文（不入库）
- fetch_arxiv(arxiv_id) 按 arXiv id 下载并加入文库
- run_python(code) 在沙箱执行 Python（可 import pandas/numpy/matplotlib 做数据处理与画图）
可用论文：{papers}
规则：不要编造工具与论文 id；信息足够时输出 final。
同一工具最多用一次，需要多步请换不同工具，不要重复相同调用。
统计、数值计算、绘图类任务请用 run_python：可 import pandas/numpy/matplotlib，
工作目录已提供 papers.json（库内论文题录，字段 id/title/year/num_pages），
图片用 plt.savefig('名称.png', dpi=130) 保存即可被系统回收展示。"""

TOOLS = {
    "search_library": "检索文献证据",
    "paper_detail": "查看论文详情",
    "compare_papers": "多篇论文对比",
    "citation_graph": "引用图谱",
    "future_work": "前沿 Idea",
    "answer_question": "基于证据回答问题",
    "translate_paper": "论文翻译",
    "draft_outline": "生成论文框架",
    "search_arxiv": "在线检索 arXiv",
    "fetch_arxiv": "下载 arXiv 论文入库",
    "run_python": "沙箱执行 Python",
}


def _papers_brief() -> list[dict]:
    return [{"id": p["id"], "title": (p["title"] or "")[:80], "year": p.get("year")}
            for p in list_papers()]


def _run_tool(name: str, args: dict) -> dict:
    if name == "search_library":
        query = args.get("query", "")
        ids = args.get("paper_ids") or None
        hits = hybrid_search(query, ids, top_k=6)
        return {"tool": name, "query": query,
                "items": [{"paper_id": h["paper_id"], "page": h["page"],
                           "bbox": h["bbox"], "section": h["section"],
                           "text": snippet(h["text"], 300)} for h in hits]}
    if name == "paper_detail":
        pid = args.get("paper_id", "")
        meta = paper_meta(pid)
        if not meta:
            return {"tool": name, "error": f"未找到论文 {pid}"}
        toc = meta.get("meta", {}).get("toc", [])[:20]
        return {"tool": name, "paper": {"id": pid, "title": meta["title"],
                                        "authors": meta["authors"], "year": meta["year"],
                                        "abstract": snippet(meta.get("abstract") or "", 500),
                                        "num_pages": meta.get("num_pages"),
                                        "toc": toc,
                                        "refs": len(refs_of(pid))}}
    if name == "compare_papers":
        ids = args.get("paper_ids") or [p["id"] for p in list_papers()[:3]]
        papers = [paper_meta(i) for i in ids]
        papers = [p for p in papers if p]
        if len(papers) < 2:
            return {"tool": name, "error": "至少需要 2 篇论文"}
        res = rag.compare(papers, args.get("aspect", "核心方法、参数规模与实验结论"))
        return {"tool": name, **res, "papers": [p["id"] for p in papers]}
    if name == "citation_graph":
        res = graph.build_graph(args.get("paper_ids") or None)
        return {"tool": name, "stats": res["stats"],
                "top": [{"title": n["title"], "role": n["role"],
                         "pagerank": n["pagerank"]} for n in res["top"]]}
    if name == "future_work":
        res = graph.future_work(args.get("paper_ids") or None)
        return {"tool": name, "items": [
            {"title": snippet(i["text"], 180), "status": i["status"],
             "paper": i["paper_title"], "page": i["page"]} for i in res["items"][:5]]}
    if name == "answer_question":
        res = rag.answer(args.get("query", ""), args.get("paper_ids") or None)
        return {"tool": name, "answer": res.get("answer", ""),
                "mode": res.get("mode"), "confidence": res.get("confidence"),
                "sources": [{"sid": s["sid"], "page": s.get("page"),
                             "section": s.get("section")} for s in res.get("sources", [])]}
    if name == "translate_paper":
        res = translate.translate_chunks(args.get("paper_id", ""),
                                         args.get("lang", "zh"), limit=4)
        return {"tool": name, "mode": res["mode"],
                "items": [{"source": snippet(i["source"], 200),
                           "translation": snippet(i["translation"], 220)}
                          for i in res["items"][:3]]}
    if name == "draft_outline":
        res = copilot.outline(args.get("idea", ""), args.get("paper_ids") or None)
        return {"tool": name, "mode": res["mode"],
                "markdown": res["markdown"][:2500],
                "references": res.get("references", [])}
    if name == "search_arxiv":
        from . import arxiv as arxiv_service
        from .store import rewrite_query
        q = args.get("query", "")
        # arXiv 只认英文：中文任务先改写为英文关键词，否则 all:<中文> 会返回无关结果
        if CJK.search(q):
            q = rewrite_query(q) or q
        cands = arxiv_service.search(q, limit=min(int(args.get("limit", 5)), 10))
        return {"tool": name, "query": q, "candidates": [
            {"arxiv_id": c["arxiv_id"], "title": c["title"][:120],
             "authors": (c["authors"] or [])[:3], "year": c.get("year"),
             "summary": snippet(c.get("summary", ""), 220)} for c in cands]}
    if name == "fetch_arxiv":
        from . import ingest
        try:
            return {"tool": name, **ingest.fetch_and_ingest(args.get("arxiv_id", ""))}
        except RuntimeError as exc:
            return {"tool": name, "error": str(exc)}
    if name == "run_python":
        from . import codeexec
        rows = [{"id": p["id"], "title": p["title"], "year": p.get("year"),
                 "num_pages": p.get("num_pages")} for p in list_papers()]
        res = codeexec.run_code(args.get("code", ""),
                                datasets={"papers.json": rows})
        return {"tool": name, **res}
    return {"tool": name, "error": f"未知工具 {name}"}


def _gen_python(task: str) -> str:
    """按任务生成一段可执行的 Python（沙箱内工作目录有 papers.json）。"""
    if not llm.is_available():
        return ""
    try:
        raw = llm.chat([
            {"role": "system", "content":
                "你是 Python 数据分析助手。只输出可执行代码，不要解释、不要 Markdown 代码块标记。"
                "工作目录有 papers.json（库内论文题录，字段 id/title/year/num_pages）。"
                "需要出图时用 matplotlib 并 plt.savefig('chart.png', dpi=130)。"
                "代码不要读取网络、不要访问工作目录以外的路径。"},
            {"role": "user", "content": task},
        ], max_tokens=900)
        code = (raw or "").strip()
        if code.startswith("```"):
            code = code.split("```")[1] if len(code.split("```")) > 1 else code
            code = code.split("\n", 1)[1] if "\n" in code and code.startswith("python") else code
        return code.strip()
    except Exception:
        return ""


def _system_prompt() -> str:
    """SYSTEM 内含字面量 JSON 花括号，不能用 str.format，只做占位替换。"""
    return SYSTEM.replace("{papers}", json.dumps(_papers_brief(), ensure_ascii=False))


def plan_steps(task: str, paper_ids: list[str] | None = None) -> dict:
    papers = _papers_brief()
    if llm.is_available():
        prompt = (f"任务：{task}\n候选论文：{json.dumps(papers, ensure_ascii=False)}\n"
                  f"默认论文范围：{paper_ids or '全部'}\n先输出计划（步骤数组），"
                  f'再按 {{"thought","tool","args"}} 逐步执行。')
        try:
            data = llm.json_chat(_system_prompt(),
                                 prompt,
                                 '{"steps":[{"title":"string","tool":"string"}],'
                                 '"next":{"thought":"string","tool":"string","args":{}}}')
            return data
        except Exception as exc:
            return {"_error": str(exc), "steps": _offline_plan(task),
                    "mode": "offline-fallback"}
    return {"steps": _offline_plan(task), "mode": "offline"}


def _offline_plan(task: str) -> list[dict]:
    t = task.lower()
    plan = []
    if re.search(r"对比|比较|compare|vs", t):
        plan.append({"title": "检索各论文相关证据", "tool": "search_library",
                     "args": {"query": task}})
        plan.append({"title": "生成多维对比表", "tool": "compare_papers", "args": {}})
    if re.search(r"图谱|引用|citation|核心|基石", t):
        plan.append({"title": "构建引用图谱并计算 PageRank", "tool": "citation_graph", "args": {}})
    if re.search(r"创新点|贡献|contribution|总结|summary|方法|原理|优点", t):
        plan.append({"title": "基于证据回答问题", "tool": "answer_question",
                     "args": {"query": task}})
    if re.search(r"未来|前沿|idea|创新点|future", t):
        plan.append({"title": "抽取 Future Work 并判断是否已解决", "tool": "future_work", "args": {}})
    if re.search(r"翻译", t):
        plan.append({"title": "翻译核心段落", "tool": "translate_paper", "args": {}})
    if re.search(r"框架|大纲|outline|写作|论文结构", t):
        plan.append({"title": "生成无幻觉论文框架", "tool": "draft_outline", "args": {"idea": task}})
    if re.search(r"arxiv|最新论文|新论文|下载|入库|检索外部", t):
        plan.append({"title": "在线检索 arXiv 候选论文", "tool": "search_arxiv",
                     "args": {"query": task, "limit": 5}})
    if not plan:
        plan.append({"title": "检索相关证据", "tool": "search_library",
                     "args": {"query": task}})
    plan.append({"title": "汇总证据与结论", "tool": "final", "args": {}})
    return plan


def run(task: str, paper_ids: list[str] | None = None,
        max_steps: int = 6) -> Generator[dict, None, None]:
    """生成器事件：plan / step / tool / token / final / error。
    生成器耗尽时，调用方必须调 persist_run() 把 trace 落库。"""
    run_id = uuid.uuid4().hex[:12]
    steps: list[dict] = []
    trace: list[dict] = []
    collected_final = ""
    offline = not llm.is_available()
    t0 = now()
    # 先插一条进行中的记录（status=running），失败时也有迹可循
    execute("INSERT OR IGNORE INTO runs (id,kind,payload,status,created_at) "
            "VALUES (?,?,?,?,?)",
            (run_id, "agent",
             json.dumps({"task": task, "paper_ids": paper_ids},
                        ensure_ascii=False),
             "running", t0))

    yield {"type": "start", "run_id": run_id, "task": task,
           "offline": offline}

    if llm.is_available():
        try:
            planner = llm.chat([
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content":
                    f"任务：{task}\n请先给 2-5 步计划 JSON："
                    '{"steps":[{"title":"...","tool":"..."}]}\n'
                    "工具与参数：search_library(query) / paper_detail(paper_id) / "
                    "compare_papers(paper_ids,aspect) / citation_graph() / future_work() / "
                    "answer_question(query) / translate_paper(paper_id,lang) / "
                    "draft_outline(idea) / search_arxiv(query) / fetch_arxiv(arxiv_id) / "
                    "run_python（统计、计算、画图一律用它）。"},
            ], max_tokens=500)
            plan = llm.parse_json(planner).get("steps", [])
            steps = plan[:max_steps] if plan else _offline_plan(task)
        except Exception as exc:
            steps = _offline_plan(task)
            trace.append({"step": 0, "note": f"规划失败，使用规则计划：{exc}"})
    else:
        steps = _offline_plan(task)

    yield {"type": "plan", "steps": steps}

    observations: list[dict] = []
    used: set[str] = set()
    for i, step in enumerate(steps, start=1):
        tool = step.get("tool") or "search_library"
        if tool == "final":
            break
        # 幂等工具（及同工具同参数）重复执行只会浪费额度
        key = tool + "|" + json.dumps(step.get("args") or {}, sort_keys=True,
                                      ensure_ascii=False)
        if key in used:
            trace.append({"step": i, "tool": tool, "skipped": "重复调用已跳过"})
            continue
        used.add(key)
        args = step.get("args") or {}
        if tool == "compare_papers" and not args.get("paper_ids"):
            args["paper_ids"] = paper_ids or [p["id"] for p in list_papers()[:3]]
        if tool == "answer_question":
            # 用户指定了论文范围时，回答必须限定在这几篇，避免检索到无关片段
            args.setdefault("query", task)
            if paper_ids and not args.get("paper_ids"):
                args["paper_ids"] = paper_ids
        if tool == "search_library" and not args.get("query"):
            args["query"] = task
        if tool == "translate_paper" and not args.get("paper_id"):
            papers = list_papers()
            args["paper_id"] = (papers[0]["id"] if papers else "")
        if tool == "run_python" and not args.get("code"):
            # 规划阶段通常只给工具名，代码在执行阶段由模型按任务生成
            args["code"] = _gen_python(task)
        yield {"type": "step_start", "index": i, "title": step.get("title", tool),
               "tool": tool, "args": args}
        try:
            result = _run_tool(tool, args)
        except Exception as exc:
            result = {"tool": tool, "error": str(exc)}
        observations.append(result)
        trace.append({"step": i, "tool": tool, "args": args, "result": result})
        yield {"type": "tool_result", "index": i, "tool": tool,
               "result": _compact(result)}

    # 汇总：工具结果直接交给模型（或离线拼接），并流式输出
    if llm.is_available():
        summary_prompt = (
            f"任务：{task}\n工具结果：\n{json.dumps(_compact(observations), ensure_ascii=False)[:9000]}\n\n"
            "请用中文输出结论：先给 3 条要点，再给一段解释；涉及原文必须注明论文与页码。"
        )
        try:
            collected = ""
            for delta in llm.stream_chat([
                {"role": "system", "content": "你是严谨的科研助手，只依据工具结果作答。"},
                {"role": "user", "content": summary_prompt},
            ], max_tokens=1400):
                collected += delta
                collected_final += delta
                yield {"type": "token", "text": delta}
            final_text = collected
        except Exception as exc:
            final_text = _offline_summary(task, observations) + f"\n\n（LLM 失败：{exc}）"
            collected_final = final_text
            yield {"type": "token", "text": final_text}
    else:
        final_text = _offline_summary(task, observations)
        collected_final = final_text
        yield {"type": "token", "text": final_text}

    finished = now()
    # 落库：把 trace + final 写 runs
    try:
        execute("UPDATE runs SET trace=?, final_text=?, finished_at=?, "
                "duration_ms=?, status='done' WHERE id=?",
                (json.dumps(trace, ensure_ascii=False, default=str),
                 final_text, finished,
                 int((__import__("datetime").datetime.fromisoformat(finished) -
                      __import__("datetime").datetime.fromisoformat(t0))
                     .total_seconds() * 1000),
                 run_id))
    except Exception:
        pass  # DB 落库失败不影响流式输出
    yield {"type": "final", "run_id": run_id, "answer": final_text,
           "trace": trace, "offline": offline}


def _compact(result: dict) -> dict:
    """裁剪工具输出，控制上下文长度（长文档场景必需）。"""
    data = json.loads(json.dumps(result, ensure_ascii=False, default=str))
    if "items" in data and isinstance(data["items"], list):
        data["items"] = data["items"][:4]
    if "sources" in data and isinstance(data["sources"], list):
        data["sources"] = [{k: v for k, v in s.items() if k != "text"}
                           for s in data["sources"][:4]]
    if "markdown" in data:
        data["markdown"] = data["markdown"][:1500]
    return data


def _offline_summary(task: str, observations: list[dict]) -> str:
    lines = [f"（离线模式汇总）任务：{task}", ""]
    for obs in observations:
        tool = obs.get("tool")
        if tool == "search_library":
            lines.append("检索到的证据：")
            for it in obs.get("items", [])[:3]:
                lines.append(f"- {snippet(it.get('text', ''), 240)}（第 {it.get('page')} 页）")
        elif tool == "citation_graph":
            top = obs.get("top", [])
            lines.append(f"引用图谱：{obs.get('stats', {}).get('papers')} 个节点，"
                         f"{obs.get('stats', {}).get('edges')} 条边；核心节点："
                         + "、".join(t["title"] for t in top[:2]))
        elif tool == "future_work":
            lines.append("前沿 Idea：")
            for it in obs.get("items", [])[:3]:
                lines.append(f"- [{it.get('status')}] {it.get('title')}")
        elif tool == "answer_question":
            lines.append("基于证据的要点：")
            lines.append(str(obs.get("answer", ""))[:600])
        elif tool == "compare_papers":
            lines.append(str(obs.get("answer", ""))[:500])
        elif tool == "draft_outline":
            lines.append("已生成论文框架（见右栏）。")
    lines.append("\n提示：配置 LLM_API_KEY 后，Agent 会输出经过证据校验的自然语言结论。")
    return "\n".join(lines)
