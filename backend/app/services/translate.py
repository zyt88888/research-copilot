"""中英双向翻译：保护公式/代码/引用编号，按段对齐，结果落库缓存。"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from ..db import get_conn
from . import llm, nmt
from .store import chunks_of

# 需要原样保留的片段：$...$、\(...\)、代码、方括号引用、URL、数学符号串
PROTECT = re.compile(
    r"(\$\$.*?\$\$|\$[^$]{1,300}\$|\\\(.*?\\\)|`[^`]{1,300}`|"
    r"\[[0-9,\s\-–]{1,40}\]|https?://\S+|\\begin\{.*?\}.*?\\end\{.*?\})",
    re.S)
PLACEHOLDER = "__KEEP{}__"


def _protect(text: str) -> tuple[str, list[str]]:
    keep: list[str] = []

    def repl(m):
        keep.append(m.group(0))
        return PLACEHOLDER.format(len(keep) - 1)

    return PROTECT.sub(repl, text), keep


def _restore(text: str, keep: list[str]) -> str:
    for i, token in enumerate(keep):
        text = text.replace(PLACEHOLDER.format(i), token)
        text = text.replace(f"__KEEP{i}__", token)
    return text


SYSTEM = """你是学术翻译专家。要求：
1. 忠实学术语体，术语使用领域通行译法，首次出现可保留英文；
2. 公式、代码、引用编号 [12]、URL、占位符 __KEEPn__ 必须原样保留，不得翻译或改写；
3. 只输出译文，不要解释、不要加引号。"""


def translate_chunks(paper_id: str, lang: str = "zh",
                     chunk_ids: list[str] | None = None,
                     limit: int = 40) -> dict:
    chunks = [c for c in chunks_of(paper_id, ("paragraph", "ocr", "caption"))
              if c["kind"] != "heading"]
    if chunk_ids:
        wanted = set(chunk_ids)
        chunks = [c for c in chunks if c["id"].split(":")[-1] in wanted or c["id"] in wanted]
    chunks = chunks[:limit]
    if not chunks:
        return {"items": [], "mode": "empty", "cached": 0}

    with get_conn() as conn:
        cached_rows = conn.execute(
            f"SELECT chunk_id, text FROM translations WHERE paper_id=? AND lang=?",
            (paper_id, lang)).fetchall()
    cached = {r["chunk_id"]: r["text"] for r in cached_rows}

    todo = [c for c in chunks if f"{paper_id}:{c['id']}" not in cached
            and c["id"] not in cached]
    mode = "cached"
    if todo and llm.is_available():
        try:
            _translate_batch(paper_id, lang, todo)
            mode = "llm"
        except Exception as exc:
            mode = f"error:{exc}"
    elif todo:
        # 无在线模型：走本地离线 NMT（镜像内预置，无需任何 Key）
        translated = nmt.translate([c["text"] for c in todo]) if lang == "zh" else None
        if translated and len(translated) == len(todo):
            mode = "nmt"
            with get_conn() as conn:
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                for c, text in zip(todo, translated):
                    if not text:
                        continue
                    conn.execute(
                        "INSERT OR REPLACE INTO translations (paper_id,chunk_id,lang,text,created_at)"
                        " VALUES (?,?,?,?,?)",
                        (paper_id, f"{paper_id}:{c['id']}", lang, text, now))
        else:
            # 模型尚未就绪：只返回原文，不落库（避免把原文伪造成译文）
            mode = "offline"

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT chunk_id, text FROM translations WHERE paper_id=? AND lang=?",
            (paper_id, lang)).fetchall()
    latest = {r["chunk_id"]: r["text"] for r in rows}
    items = []
    for c in chunks:
        cid = c["id"]
        key = f"{paper_id}:{cid}"
        trans = latest.get(key) or latest.get(cid)
        items.append({
            "chunk_id": cid, "page": c["page"], "bbox": c["bbox"],
            "section": c["section"], "source": c["text"],
            "translation": trans or "",
            "translated": bool(trans),
        })
    return {"items": items, "mode": mode, "cached": len(chunks) - len(todo)}


def _translate_batch(paper_id: str, lang: str, chunks: list[dict]) -> None:
    """一次请求最多 10 段，JSON 数组返回，失败整批回退逐段。"""
    target = "中文" if lang == "zh" else "英文"
    for start in range(0, len(chunks), 10):
        batch = chunks[start:start + 10]
        payload = []
        for c in batch:
            protected, keep = _protect(c["text"])
            payload.append({"id": c["id"], "text": protected, "keep": keep})
        user = (f"把下列学术段落翻译成{target}，输出 JSON："
                f'{{"items":[{{"id":"...","text":"译文"}}]}}。\n\n'
                + json.dumps([{k: v for k, v in p.items() if k != "keep"} for p in payload],
                             ensure_ascii=False))
        data = llm.json_chat(SYSTEM, user, '{"items":[{"id":"string","text":"string"}]}')
        mapping = {i.get("id"): i.get("text", "") for i in data.get("items", [])}
        keep_map = {p["id"]: p["keep"] for p in payload}
        with get_conn() as conn:
            for c in batch:
                text = mapping.get(c["id"])
                if not text:
                    continue
                text = _restore(text, keep_map.get(c["id"], []))
                conn.execute(
                    "INSERT OR REPLACE INTO translations (paper_id,chunk_id,lang,text,created_at)"
                    " VALUES (?,?,?,?,?)",
                    (paper_id, f"{paper_id}:{c['id']}", lang, text,
                     datetime.now(timezone.utc).isoformat(timespec="seconds")))


def translate_text(text: str, target: str = "zh") -> dict:
    """划词翻译：单段即时翻译，返回保护还原后的结果。"""
    if not text.strip():
        return {"text": "", "mode": "empty"}
    protected, keep = _protect(text)
    if not llm.is_available():
        local = nmt.translate([text])
        if local and local[0]:
            return {"text": local[0], "mode": "nmt"}
        return {"text": text, "mode": "offline",
                "notice": "翻译模型加载中，请稍后重试"}
    try:
        lang = "中文" if target == "zh" else "英文"
        out = llm.chat([{"role": "system", "content": SYSTEM},
                        {"role": "user", "content": f"翻译成{lang}：\n{protected}"}],
                       max_tokens=600)
        return {"text": _restore(out.strip(), keep), "mode": "llm"}
    except Exception as exc:
        return {"text": text, "mode": "error", "notice": str(exc)}
