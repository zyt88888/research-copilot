"""检索质量公开基准评测：Recall@k / MRR@10，兼容 BEIR 风格 qrels 数据。

用法（在 backend 容器内执行）：
    # 内置冒烟集（针对演示语料的 12 条标注查询）
    python /app/scripts/bench_retrieval.py

    # 公开基准：BEIR / SciFact 等数据集转成 JSONL 后评测
    #   每行：{"qid": "...", "query": "...", "relevant_papers": ["paper_id", ...]}
    python /app/scripts/bench_retrieval.py --qrels /app/scripts/bench_data/scifact.jsonl

    # 评测分层检索（长文档场景）
    python /app/scripts/bench_retrieval.py --mode hierarchical

公开数据集接入说明：
    BEIR（https://github.com/beir-cellar/beir）的 qrels 是 TSV（qid, doc_id, score），
    queries 是 TSV/JSONL。用任意脚本按下述格式合并即可：
      对每条 query，收集 qrels 中 score>=1 的 doc_id 作为 relevant_papers。
    若 doc_id 与本系统 paper_id 不一致，先用对应表映射；本系统 paper_id
    可在 /api/papers 返回中查看。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import store  # noqa: E402

# 内置冒烟集：针对演示语料（LLM 基础论文 20 篇 + 版本对 + 扫描件）标注的 20 条查询。
# relevant_papers 为论文级相关性标注（命中任一即算相关）。
SMOKE_QUERIES = [
    {"qid": "s1", "query": "self-attention mechanism scaled dot-product multi-head",
     "relevant_papers": ["76e0efdb6b85", "90e1322f4e6b"]},
    {"qid": "s2", "query": "flash attention IO-aware tiling memory efficient",
     "relevant_papers": ["p220514135"]},
    {"qid": "s3", "query": "low rank adaptation fine-tuning injected matrices",
     "relevant_papers": ["p210609685"]},
    {"qid": "s4", "query": "BERT masked language model pretraining bidirectional",
     "relevant_papers": ["p181004805"]},
    {"qid": "s5", "query": "sequence to sequence learning encoder decoder LSTM",
     "relevant_papers": ["p14093215"]},
    {"qid": "s6", "query": "neural machine translation jointly learning align translate",
     "relevant_papers": ["p14090473"]},
    {"qid": "s7", "query": "RoBERTa robustly optimized pretraining approach",
     "relevant_papers": ["p190711692"]},
    {"qid": "s8", "query": "ALBERT lite parameter reduction factorized embedding",
     "relevant_papers": ["p190911942"]},
    {"qid": "s9", "query": "DistilBERT knowledge distillation smaller faster",
     "relevant_papers": ["p191001108"]},
    {"qid": "s10", "query": "text-to-text transfer transformer unified framework T5",
     "relevant_papers": ["p191010683"]},
    {"qid": "s11", "query": "vision transformer image worth 16x16 words patches",
     "relevant_papers": ["p201011929"]},
    {"qid": "s12", "query": "Swin transformer shifted windows hierarchical vision",
     "relevant_papers": ["p210314030"]},
    {"qid": "s13", "query": "end-to-end object detection transformers bipartite matching",
     "relevant_papers": ["p200512872"]},
    {"qid": "s14", "query": "Reformer locality sensitive hashing reversible layers efficient",
     "relevant_papers": ["p200104451"]},
    {"qid": "s15", "query": "language models few-shot learners GPT-3 in-context",
     "relevant_papers": ["p200514165"]},
    {"qid": "s16", "query": "training language models follow instructions human feedback RLHF",
     "relevant_papers": ["p220302155"]},
    {"qid": "s17", "query": "LLaMA open efficient foundation language models",
     "relevant_papers": ["p230213971"]},
    {"qid": "s18", "query": "Llama 2 open foundation fine-tuned chat models safety",
     "relevant_papers": ["p230709288"]},
    {"qid": "s19", "query": "chain-of-thought prompting elicits reasoning",
     "relevant_papers": ["p220111903"]},
    {"qid": "s20", "query": "self-instruct aligning language model self-generated instructions",
     "relevant_papers": ["p221003629"]},
]


def evaluate(hits_fn, queries: list[dict], ks: tuple[int, ...] = (1, 5, 10)) -> dict:
    recall = {k: 0.0 for k in ks}
    mrr = 0.0
    n = 0
    details = []
    for q in queries:
        rel = set(q["relevant_papers"])
        hits = hits_fn(q["query"])
        ranked = []
        seen = set()
        for h in hits:
            pid = h.get("paper_id")
            if pid and pid not in seen:
                seen.add(pid)
                ranked.append(pid)
        n += 1
        row = {"qid": q["qid"], "top": ranked[:5]}
        for k in ks:
            ok = any(p in rel for p in ranked[:k])
            recall[k] += 1.0 if ok else 0.0
            row[f"hit@{k}"] = ok
        rr = 0.0
        for i, pid in enumerate(ranked[:10]):
            if pid in rel:
                rr = 1.0 / (i + 1)
                break
        mrr += rr
        row["rr"] = round(rr, 3)
        details.append(row)
    return {
        "n_queries": n,
        **{f"recall@{k}": round(recall[k] / max(n, 1), 3) for k in ks},
        "mrr@10": round(mrr / max(n, 1), 3),
        "details": details,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="检索质量基准评测（Recall@k / MRR）")
    ap.add_argument("--qrels", help="JSONL：每行 {qid, query, relevant_papers}")
    ap.add_argument("--mode", choices=["hybrid", "hierarchical"], default="hybrid")
    ap.add_argument("--top-k", type=int, default=20)
    args = ap.parse_args()

    if args.qrels:
        queries = [json.loads(line) for line in Path(args.qrels).read_text(
            encoding="utf-8").splitlines() if line.strip()]
    else:
        queries = SMOKE_QUERIES
        print(f"未指定 --qrels，使用内置冒烟集（{len(SMOKE_QUERIES)} 条，演示语料标注）")

    def hits_fn(query: str):
        if args.mode == "hierarchical":
            return store.hierarchical_search(query, None, top_k=args.top_k)
        return store.hybrid_search(query, None, top_k=args.top_k)

    stats = store.index_stats()
    papers = len(store.list_papers())
    print(f"索引：{stats.get('chunks', 0)} 块 / {papers} 篇 | 模式：{args.mode}")
    result = evaluate(hits_fn, queries)
    for k in ("n_queries", "recall@1", "recall@5", "recall@10", "mrr@10"):
        print(f"  {k}: {result[k]}")
    out = Path(__file__).parent / "bench_retrieval_result.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"明细已写入 {out}")


if __name__ == "__main__":
    main()
