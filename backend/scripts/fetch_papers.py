"""批量抓取 arXiv 论文并入库。

用法（在 backend 目录执行）：
    python scripts/fetch_papers.py                 # 抓内置 20 篇经典论文
    python scripts/fetch_papers.py --query "vision transformer" --limit 10
    python scripts/fetch_papers.py --query "retrieval augmented generation" --days 7
        # 定期任务：只抓最近 7 天投稿的新论文（按投稿时间倒序）

容器内执行：
    docker exec research-copilot-backend python scripts/fetch_papers.py --query "..." --days 7
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db  # noqa: E402
from app.services import arxiv, dedup, pdf_parser, store  # noqa: E402
from app.settings import UPLOAD_DIR  # noqa: E402

# 内置清单：以 Transformer 为核心的经典论文，彼此之间大多有真实引用关系
CURATED = [
    "1706.03762",  # Attention Is All You Need
    "1409.0473",   # Neural MT (Bahdanau attention)
    "1409.3215",   # Sequence to Sequence
    "1810.04805",  # BERT
    "1907.11692",  # RoBERTa
    "1909.11942",  # ALBERT
    "1910.01108",  # DistilBERT
    "1910.10683",  # T5
    "2010.11929",  # ViT
    "2103.14030",  # Swin Transformer
    "2005.12872",  # DETR
    "2001.04451",  # Reformer
    "2005.14165",  # GPT-3
    "2203.02155",  # InstructGPT
    "2302.13971",  # LLaMA
    "2307.09288",  # Llama 2
    "2106.09685",  # LoRA
    "2201.11903",  # Chain-of-Thought
    "2210.03629",  # ReAct
    "2205.14135",  # FlashAttention
]


def existing_base_ids() -> set[str]:
    return {arxiv.base_id(p.get("arxiv_id")) for p in store.list_papers() if p.get("arxiv_id")}


def import_one(meta: dict) -> str:
    bid = meta["base_id"]
    known = {p["id"]: p for p in store.list_papers()}
    for p in known.values():
        if arxiv.base_id(p.get("arxiv_id")) == bid:
            return f"跳过 {bid}（库中已有 {p['id']}）"

    pdf = arxiv.download(meta["arxiv_id"], UPLOAD_DIR)
    parsed = pdf_parser.parse_pdf(pdf, filename=pdf.name)
    if not parsed.get("arxiv_id"):
        parsed["arxiv_id"] = meta["arxiv_id"]
    if not parsed.get("year"):
        parsed["year"] = meta["year"]
    if not parsed.get("authors"):
        parsed["authors"] = meta["authors"][:12]

    dup = dedup.find_duplicates(parsed)
    if dup and not dup.get("same_arxiv_family"):
        return f"跳过 {bid}（与 {dup['paper_id']} 高度相似 {dup['similarity']:.2f}）"

    pid = f"p{bid.replace('.', '')}"
    store.save_paper(parsed, str(pdf), pid)
    return (f"入库 {pid}  {parsed['title'][:58]}  "
            f"页数={parsed.get('num_pages')} refs={len(parsed.get('refs') or [])}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", help="改用关键词检索模式")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--days", type=int, default=0,
                    help="只入库最近 N 天投稿的论文（按投稿时间倒序，适合定期任务）")
    args = ap.parse_args()

    init_db()
    if args.query:
        if args.days > 0:
            # 定期抓取：按投稿时间倒序取一批，再按日期过滤
            metas = arxiv.search(args.query, max(args.limit, 50),
                                 sort_by="submittedDate")
            cutoff = (date.today() - timedelta(days=args.days)).isoformat()
            metas = [m for m in metas if (m.get("published") or "") >= cutoff][:args.limit]
            print(f"只保留 {cutoff} 之后投稿的论文")
        else:
            metas = arxiv.search(args.query, args.limit)
    else:
        metas = list(arxiv.metadata(CURATED).values())
        metas.sort(key=lambda m: CURATED.index(m["base_id"]) if m["base_id"] in CURATED else 99)

    print(f"待处理 {len(metas)} 篇，库中已有 {len(store.list_papers())} 篇\n")
    for i, meta in enumerate(metas, 1):
        try:
            print(f"[{i}/{len(metas)}] {import_one(meta)}")
        except Exception as exc:
            print(f"[{i}/{len(metas)}] 失败 {meta.get('base_id')}: {exc}")
        time.sleep(1.5)  # 对 arXiv 友好，避免被限流

    papers = store.list_papers()
    g = store.graph_summary() if hasattr(store, "graph_summary") else None
    print(f"\n完成：库中共 {len(papers)} 篇" + (f"；{g}" if g else ""))


if __name__ == "__main__":
    main()
