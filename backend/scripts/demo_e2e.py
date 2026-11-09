"""端到端演示脚本：无需前端、无需 LLM，直接用 requests 跑完整闭环。

运行：python scripts/demo_e2e.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
PDF_V1 = Path(r"c:\Users\21772\.trae-cn\attachments\9a095feb-6376-4cad-9ff5-ba9e9eff5498_99b32fae-79ce-4ef4-aa07-e2ce380ffe45_1706.03762v1.pdf")
PDF_V7 = Path(r"c:\Users\21772\.trae-cn\attachments\587ec687-a4b6-4299-a0d0-4dd420e79af1_53f1d90d-a749-45f7-88f4-c4c6ee4b56a5_1706.03762v7.pdf")
BASE = "http://127.0.0.1:8000"


def show(title: str, j):
    print(f"\n{'='*70}")
    print(f"▶ {title}")
    print("-" * 70)
    print(json.dumps(j, ensure_ascii=False, indent=2)[:3000])


def main():
    sess = requests.Session()

    # 1. 健康
    show("健康检查", sess.get(f"{BASE}/api/health").json())

    # 2. 上传 Attention v7
    t0 = time.time()
    with PDF_V7.open("rb") as f:
        r = sess.post(f"{BASE}/api/papers/upload",
                      files={"file": (PDF_V7.name, f, "application/pdf")})
    show("上传 v7", {**r.json(), "elapsed": round(time.time() - t0, 2)})

    # 3. 上传 Attention v1（应当命中重复）
    t0 = time.time()
    with PDF_V1.open("rb") as f:
        r = sess.post(f"{BASE}/api/papers/upload",
                      files={"file": (PDF_V1.name, f, "application/pdf")})
    show("上传 v1（触发去重）", {**r.json(), "elapsed": round(time.time() - t0, 2)})

    # 4. 覆盖 v7 为 v1（测试 overwrite 流程）
    v7_dup = r.json().get("duplicate")
    if v7_dup:
        with PDF_V1.open("rb") as f:
            r2 = sess.post(f"{BASE}/api/papers/upload",
                           files={"file": (PDF_V1.name, f, "application/pdf")},
                           data={"overwrite": v7_dup["paper_id"]})
        show("覆盖 v1 为新版本（overwrite 流程）", r2.json())

    # 5. 列文库
    papers = sess.get(f"{BASE}/api/papers").json()["papers"]
    show("文库列表（%d 篇）" % len(papers), papers)
    if not papers:
        print("库空，结束")
        return
    pid = papers[0]["id"]

    # 6. 单篇问答（离线抽取式）
    q = "self-attention 的作用是什么？"
    t0 = time.time()
    r = sess.post(f"{BASE}/api/chat",
                  json={"query": q, "paper_ids": [pid]}).json()
    show(f"RAG 问答：{q}", {k: v for k, v in r.items() if k != "sources"})
    show("    证据溯源 Sources（前 3）", [
        {"sid": s["sid"], "page": s["page"], "section": s["section"],
         "text": s["text"][:180]} for s in (r.get("sources") or [])[:3]])

    # 7. 图谱
    g = sess.get(f"{BASE}/api/graph").json()
    show("引用图谱 stats", g["stats"])
    show("    Top 核心节点（PageRank）", g["top"])

    # 8. Future Work
    fw = sess.get(f"{BASE}/api/future").json()
    show("Future Work 提取", {
        "total": fw["total"], "open_count": fw["open_count"],
        "items": [{"title": i["text"][:120], "status": i["status"]}
                  for i in fw["items"][:4]]})

    # 9. Copilot 论文框架
    cp = sess.post(f"{BASE}/api/copilot/outline",
                   json={"idea": "基于稀疏检索与稠密重排的抗幻觉 RAG 系统",
                         "paper_ids": [pid]}).json()
    show("Copilot 论文框架（前 800 字符）", cp["markdown"][:800])

    # 10. Agent 端到端（SSE 流式）
    print("\n▶ Agent 执行（SSE 事件流）：")
    t0 = time.time()
    with sess.post(f"{BASE}/api/agent/run",
                   json={"task": "帮我梳理 Transformer 架构的核心组件与 self-attention 工作原理，"
                                 "并提取库中 Future Work",
                         "paper_ids": [pid]},
                   stream=True) as resp:
        buf = b""
        count = 0
        final = ""
        for chunk in resp.iter_content():
            buf += chunk
            while b"\n\n" in buf:
                evt_text, buf = buf.split(b"\n\n", 1)
                for line in evt_text.decode("utf-8", "ignore").splitlines():
                    if line.startswith("data:"):
                        try:
                            ev = json.loads(line[5:])
                        except json.JSONDecodeError:
                            continue
                        count += 1
                        t = ev["type"]
                        if t == "token":
                            final += ev["text"]
                        elif t == "plan":
                            print(f"  🧭 规划 {len(ev['steps'])} 步")
                        elif t == "step_start":
                            print(f"  ▶ Step {ev['index']}: {ev['title']} ({ev['tool']})")
                        elif t == "tool_result":
                            print(f"    ↳ {ev['tool']} 返回 OK")
                        elif t == "final":
                            pass
        print(f"  ⏱ 总计 {count} 个事件，耗时 {round(time.time()-t0, 2)}s")
        print("\n▶ Agent 最终结论（前 600 字符）：")
        print(final[:600])

    print("\n✅ 端到端演示完成")


if __name__ == "__main__":
    main()
