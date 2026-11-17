"""为缺少中文译文的段落补齐译文（离线 NMT，可重复执行）。

用法（容器内）：
    docker exec research-copilot-backend python scripts/prewarm_translations.py
可选参数：--paper <paper_id> 只补某一篇。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import get_conn, query  # noqa: E402
from app.services import nmt  # noqa: E402

KINDS = ("paragraph", "ocr", "caption")
BATCH = 8


def main(only_paper: str | None = None) -> None:
    papers = query("SELECT id FROM papers")
    total = 0
    for p in papers:
        pid = p["id"]
        if only_paper and pid != only_paper:
            continue
        have = {r["chunk_id"] for r in query(
            "SELECT chunk_id FROM translations WHERE paper_id=? AND lang='zh' "
            "AND text GLOB '*[一-龥]*'", (pid,))}
        rows = query(
            f"SELECT id, text FROM chunks WHERE paper_id=? AND kind IN "
            f"({','.join('?' * len(KINDS))}) ORDER BY seq", (pid, *KINDS))
        todo = [(r["id"], r["text"]) for r in rows
                if r["text"] and f"{pid}:{r['id']}" not in have]
        if not todo:
            continue
        done = 0
        for i in range(0, len(todo), BATCH):
            batch = todo[i:i + BATCH]
            outs = nmt.translate([t for _, t in batch]) or []
            with get_conn() as conn:
                for (cid, _src), out in zip(batch, outs):
                    if not out:
                        continue
                    conn.execute(
                        "INSERT OR REPLACE INTO translations (paper_id,chunk_id,lang,text,created_at)"
                        " VALUES (?,?,?,?,?)",
                        (pid, f"{pid}:{cid}", "zh", out,
                         datetime.now(timezone.utc).isoformat(timespec="seconds")))
                    done += 1
        print(f"{pid}: 待补 {len(todo)} 条，成功 {done} 条")
        total += done
    print(f"合计补齐 {total} 条译文")


if __name__ == "__main__":
    arg = sys.argv
    main(arg[arg.index("--paper") + 1] if "--paper" in arg else None)
