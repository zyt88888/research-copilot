"""Agent 基准测试：用 10 个固定任务压测 /api/agent/run，统计成功率、步数、耗时。

用法：
    python scripts/bench_agent.py            # 默认 http://localhost:8000
    python scripts/bench_agent.py --base http://localhost:8000 --out bench_result.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests

TASKS = [
    ("T1", "总结 Transformer 的核心创新点，并给出页码依据。", None),
    ("T2", "对比 BERT 与 GPT-2 在预训练目标上的差异。", None),
    ("T3", "引用图谱里哪些论文是基石？列出 PageRank 前 3。", None),
    ("T4", "从语料库中抽取 3 条仍未解决的 Future Work。", None),
    ("T5", "翻译 FlashAttention 论文第 2 页的核心段落。", None),
    ("T6", "为「多模态检索增强生成」生成一份论文框架大纲。", None),
    ("T7", "注意力机制有哪些变体？请基于证据回答。", None),
    ("T8", "检索关于位置编码的证据片段。", None),
    ("T9", "总结 LoRA 的动机与效果。", ["p210609685"]),
    ("T10", "基于检索结果写 200 字综述段落，必须注明页码。", None),
]


def run_task(base: str, task: str, paper_ids: list[str] | None, timeout: int = 240) -> dict:
    t0 = time.time()
    payload = {"task": task}
    if paper_ids:
        payload["paper_ids"] = paper_ids
    events: list[dict] = []
    final: dict | None = None
    error = ""
    try:
        with requests.post(f"{base}/api/agent/run", json=payload, stream=True,
                           timeout=(10, timeout)) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                try:
                    ev = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                events.append(ev)
                if ev.get("type") == "final":
                    final = ev
    except Exception as exc:  # 网络/超时/服务异常都要计入失败
        error = f"{type(exc).__name__}: {exc}"

    elapsed = round(time.time() - t0, 1)
    steps = [e for e in events if e.get("type") == "step_start"]
    tools = [e.get("tool") for e in steps]
    tool_errors = [e for e in events
                   if e.get("type") == "tool_result" and (e.get("result") or {}).get("error")]
    answer = (final or {}).get("answer") or ""
    citations = len(re.findall(r"第\s*\d+\s*页", answer))
    ok = bool(final) and not error and not tool_errors and len(answer) >= 150
    return {
        "task": task, "paper_ids": paper_ids, "ok": ok, "error": error,
        "elapsed": elapsed, "steps": len(steps), "tools": tools,
        "tool_errors": len(tool_errors), "answer_len": len(answer),
        "citations": citations, "events": len(events),
        "offline": (final or {}).get("offline"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--out", default=str(Path(__file__).with_name("bench_result.json")))
    args = ap.parse_args()

    try:
        health = requests.get(f"{args.base}/api/health", timeout=5).json()
    except Exception as exc:
        print(f"[FATAL] 后端不可达：{args.base} ({exc})")
        return 1
    print(f"后端 health: {health}")

    results = []
    for code, task, ids in TASKS:
        print(f"[{code}] {task} ...", end="", flush=True)
        r = run_task(args.base, task, ids)
        r["code"] = code
        results.append(r)
        print(f" {'OK' if r['ok'] else 'FAIL'} {r['elapsed']}s "
              f"steps={r['steps']} len={r['answer_len']} cites={r['citations']}")

    ok_count = sum(1 for r in results if r["ok"])
    total = len(results)
    avg_steps = round(sum(r["steps"] for r in results) / total, 2)
    avg_time = round(sum(r["elapsed"] for r in results) / total, 1)
    summary = {
        "base": args.base, "total": total, "passed": ok_count,
        "success_rate": round(ok_count / total, 3),
        "avg_steps": avg_steps, "avg_elapsed_s": avg_time,
        "with_citations": sum(1 for r in results if r["citations"] > 0),
        "results": results,
    }
    Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n成功率 {ok_count}/{total} = {summary['success_rate'] * 100:.0f}% | "
          f"平均步数 {avg_steps} | 平均耗时 {avg_time}s")
    print(f"结果已写入 {args.out}")
    return 0 if ok_count == total else 2


if __name__ == "__main__":
    sys.exit(main())
