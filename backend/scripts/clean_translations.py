"""清理并重译退化的中文译文（重复循环垃圾输出）。

背景：通用 NMT 在「纯人名列表」等无标点长串上会陷入重复生成（如「伊伊伊伊…」）。
现在 nmt.translate 已加重复惩罚与退化检测，本脚本负责把历史坏译文删掉并重译。

用法（容器内）：
    docker exec research-copilot-backend python scripts/clean_translations.py --dry-run
    docker exec research-copilot-backend python scripts/clean_translations.py
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import get_conn  # noqa: E402
from app.services import nmt  # noqa: E402


def _source(chunks: dict[str, str], paper_id: str, chunk_id: str) -> str:
    if chunk_id in chunks:
        return chunks[chunk_id]
    prefix = f"{paper_id}:"
    if chunk_id.startswith(prefix):
        return chunks.get(chunk_id[len(prefix):], "")
    return ""


def main(dry_run: bool = False) -> None:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, paper_id, chunk_id, text FROM translations WHERE lang='zh'").fetchall()
        chunks = {r["id"]: (r["text"] or "") for r in conn.execute("SELECT id, text FROM chunks")}

    bad = []
    for r in rows:
        src = _source(chunks, r["paper_id"], r["chunk_id"])
        if nmt.is_degenerate(src, r["text"] or "") or nmt.looks_like_name_list(src):
            bad.append(r)
    print(f"中文译文共 {len(rows)} 条，检测到退化/人名列表 {len(bad)} 条")
    if dry_run or not bad:
        return

    with get_conn() as conn:
        conn.executemany("DELETE FROM translations WHERE id=?", [(r["id"],) for r in bad])
    print(f"已删除退化译文 {len(bad)} 条，开始重译…")

    by_paper: dict[str, list[str]] = defaultdict(list)
    for r in bad:
        by_paper[r["paper_id"]].append(r["chunk_id"])

    fixed = 0
    for pid, keys in by_paper.items():
        srcs, ids = [], []
        for cid in keys:
            src = _source(chunks, pid, cid)
            if src.strip():
                srcs.append(src)
                ids.append(cid)
        for i in range(0, len(srcs), 8):
            outs = nmt.translate(srcs[i:i + 8]) or []
            with get_conn() as conn:
                for cid, out in zip(ids[i:i + 8], outs):
                    if not out:
                        continue
                    conn.execute(
                        "INSERT OR REPLACE INTO translations (paper_id,chunk_id,lang,text,created_at)"
                        " VALUES (?,?,?,?,?)",
                        (pid, cid, "zh", out,
                         datetime.now(timezone.utc).isoformat(timespec="seconds")))
                    fixed += 1
    print(f"重译成功 {fixed} 条（仍无法翻译的保持无译文，页面不会显示垃圾文本）")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
