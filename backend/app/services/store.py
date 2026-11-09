"""论文存储与混合检索（BM25 + 稠密向量 + RRF 融合）。

索引在进程内缓存，写操作递增 version，检索端按 version 懒重建。
"""
from __future__ import annotations

import json
import math
import re
import threading
from collections import Counter
from datetime import datetime, timezone

import numpy as np

from ..db import execute, get_conn, loads, query
from ..settings import EMBEDDING_DIM, RETRIEVE_TOP_K
from ..utils import CJK, expand_greek, tokenize
from . import llm
from .embeddings import embed_query, embed_texts

_index_lock = threading.Lock()
_cache: dict = {"version": -1, "meta": [], "vectors": None, "bm25": None,
                "doc_len": [], "avgdl": 0.0}
_version = 0
_rewrite_cache: dict[str, str] = {}


def rewrite_query(query: str) -> str:
    """中文问题改写成英文检索关键词（语料以英文论文为主）。

    无 LLM 或改写失败时返回空串，检索退化为原始查询。结果做进程内缓存。
    """
    if not CJK.search(query or ""):
        return ""
    if query in _rewrite_cache:
        return _rewrite_cache[query]
    rewritten = ""
    if llm.is_available():
        try:
            out = llm.chat(
                [{"role": "system", "content":
                  "你是检索助手。把用户问题改写成 5-10 个具体的英文技术检索关键词"
                  "（空格分隔）。优先选择有区分度的术语（如具体模块名、方法名、"
                  "符号），避免泛词。只输出关键词，不要解释。"},
                 {"role": "user", "content": query}],
                temperature=0, max_tokens=64)
            rewritten = re.sub(r"\s+", " ", out).strip()[:200]
        except Exception:
            rewritten = ""
    _rewrite_cache[query] = rewritten
    return rewritten


def bump_version() -> None:
    global _version
    with _index_lock:
        _version += 1


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def bump_graph_version() -> None:
    """图谱版本号递增，使 graph_cache 失效；每次 papers/refs 写操作调用。"""
    # data 列是 NOT NULL，用 'null' 作为“无缓存”哨兵：
    # json.loads('null') 得到 None，graph_cache_get 调用方按未命中处理
    execute("INSERT INTO graph_cache(version,data,updated_at) "
            "VALUES((SELECT COALESCE(MAX(version),0)+1 FROM graph_cache), 'null', ?) "
            "ON CONFLICT(version) DO UPDATE SET updated_at=excluded.updated_at",
            (now(),))
    # 清掉旧缓存（只保留最新 version 的数据）
    execute("DELETE FROM graph_cache WHERE version != (SELECT MAX(version) FROM graph_cache)")


def graph_version() -> int:
    rows = query("SELECT COALESCE(MAX(version),0) as v FROM graph_cache")
    return rows[0]["v"] if rows else 0


def graph_cache_get(version: int) -> dict | None:
    rows = query("SELECT data FROM graph_cache WHERE version=? AND data IS NOT NULL",
                 (version,))
    if not rows:
        return None
    try:
        return json.loads(rows[0]["data"])
    except (TypeError, json.JSONDecodeError):
        return None


def graph_cache_put(version: int, data: dict) -> None:
    execute("INSERT INTO graph_cache(version,data,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(version) DO UPDATE SET data=excluded.data, "
            "updated_at=excluded.updated_at",
            (version, json.dumps(data, ensure_ascii=False, default=str), now()))


def save_paper(parsed: dict, path: str, paper_id: str,
               overwrite_id: str | None = None) -> str:
    """写入论文与分块；overwrite 时替换同一 id 的旧内容。"""
    pid = overwrite_id or paper_id
    chunks = [c for c in parsed["chunks"]]
    keep = [c for c in chunks
            if c["kind"] in ("paragraph", "table", "ocr", "caption", "formula")
            and len(c["text"]) >= 20]
    texts = [c["text"] for c in keep]
    vectors = embed_texts(texts) if texts else np.zeros((0, EMBEDDING_DIM), np.float32)
    if vectors.shape[1] != EMBEDDING_DIM:
        vectors = vectors[:, :EMBEDDING_DIM]

    with get_conn() as conn:
        if overwrite_id:
            conn.execute("DELETE FROM chunks WHERE paper_id=?", (pid,))
            conn.execute("DELETE FROM refs WHERE paper_id=?", (pid,))
            conn.execute("DELETE FROM translations WHERE paper_id=?", (pid,))
        conn.execute(
            "INSERT OR REPLACE INTO papers (id,title,authors,year,venue,abstract,doi,"
            "arxiv_id,filename,path,num_pages,content_hash,minhash,status,meta,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, parsed["title"], json.dumps(parsed.get("authors") or [],
                                              ensure_ascii=False),
             parsed.get("year"), parsed.get("venue"), parsed.get("abstract"),
             parsed.get("doi"), parsed.get("arxiv_id"), parsed.get("filename"),
             path, parsed.get("num_pages"), parsed.get("content_hash"),
             json.dumps(parsed.get("minhash") or []), "ready",
             json.dumps({"pages": parsed.get("pages"),
                         "page_flags": parsed.get("page_flags", {}),
                         "toc": parsed.get("toc", []),
                         "file_sha256": parsed.get("file_sha256"),
                         "full_text_chars": parsed.get("full_text_chars")},
                        ensure_ascii=False), now()))
        conn.executemany(
            "INSERT OR REPLACE INTO chunks (id,paper_id,page,bbox,section,level,kind,text,seq,embedding) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(f"{pid}:{c['id']}", pid, c["page"], json.dumps(c["bbox"]),
             c.get("section"), c.get("level", 0), c["kind"], c["text"], c["seq"],
             vectors[i].astype(np.float32).tobytes() if i < len(vectors) else None)
             for i, c in enumerate(keep)])
        refs = parsed.get("refs") or []
        conn.executemany(
            "INSERT INTO refs (paper_id,raw,title,doi,arxiv_id,year) VALUES (?,?,?,?,?,?)",
            [(pid, r.get("raw"), r.get("title"), r.get("doi"),
              r.get("arxiv_id"), r.get("year")) for r in refs])
    bump_version()
    bump_graph_version()
    return pid


def delete_paper(paper_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM papers WHERE id=?", (paper_id,))
        conn.execute("DELETE FROM chunks WHERE paper_id=?", (paper_id,))
        conn.execute("DELETE FROM refs WHERE paper_id=?", (paper_id,))
        conn.execute("DELETE FROM translations WHERE paper_id=?", (paper_id,))
    bump_version()
    bump_graph_version()


def paper_meta(paper_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["authors"] = loads(data.get("authors"), [])
    data["meta"] = loads(data.get("meta"), {})
    return data


def list_papers() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id,title,authors,year,venue,arxiv_id,filename,num_pages,"
            "created_at,status,meta FROM papers ORDER BY created_at DESC").fetchall()
    out = []
    for row in rows:
        data = dict(row)
        data["authors"] = loads(data.get("authors"), [])
        meta = loads(data.get("meta"), {})
        data["page_flags"] = meta.get("page_flags", {})
        data["toc"] = meta.get("toc", [])
        data["is_scanned"] = bool(meta.get("page_flags"))
        out.append(data)
    return out


def chunks_of(paper_id: str, kinds: tuple[str, ...] | None = None) -> list[dict]:
    sql = "SELECT * FROM chunks WHERE paper_id=?"
    params: list = [paper_id]
    if kinds:
        placeholders = ",".join("?" * len(kinds))
        sql += f" AND kind IN ({placeholders})"
        params += list(kinds)
    sql += " ORDER BY seq"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    out = []
    for row in rows:
        d = dict(row)
        d["bbox"] = loads(d.get("bbox"), [0, 0, 0, 0])
        d.pop("embedding", None)
        out.append(d)
    return out


def refs_of(paper_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM refs WHERE paper_id=? ORDER BY id",
                            (paper_id,)).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# 索引
# --------------------------------------------------------------------------
class BM25:
    def __init__(self, docs: list[list[str]]):
        self.docs = docs
        self.doc_len = [len(d) for d in docs]
        self.avgdl = (sum(self.doc_len) / len(docs)) if docs else 0.0
        self.df = Counter()
        for d in docs:
            self.df.update(set(d))
        self.n = len(docs)

    def score(self, query: list[str]) -> np.ndarray:
        if not self.docs:
            return np.zeros(0)
        k1, b = 1.5, 0.75
        scores = np.zeros(self.n)
        for term in query:
            df = self.df.get(term, 0)
            if df == 0:
                continue
            idf = math.log(1 + (self.n - df + 0.5) / (df + 0.5))
            for i, doc in enumerate(self.docs):
                tf = doc.count(term)
                if not tf:
                    continue
                dl = self.doc_len[i] or 1
                scores[i] += idf * (tf * (k1 + 1)) / (
                    tf + k1 * (1 - b + b * dl / (self.avgdl or 1)))
        return scores


def _load_index():
    global _cache
    with _index_lock:
        with get_conn() as conn:
            max_rowid = conn.execute(
                "SELECT COALESCE(MAX(rowid),0) FROM chunks").fetchone()[0]
            count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        key = (count, max_rowid)
        if _cache.get("key") == key and _cache.get("vectors") is not None:
            return _cache
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id,paper_id,page,bbox,section,kind,text,embedding,seq "
                "FROM chunks ORDER BY seq").fetchall()
        meta, vectors, docs = [], [], []
        for row in rows:
            d = dict(row)
            vec = d.pop("embedding", None)
            if vec is None:
                continue
            try:
                arr = np.frombuffer(vec, dtype=np.float32)
            except ValueError:
                continue
            if arr.size != EMBEDDING_DIM:
                fixed = np.zeros(EMBEDDING_DIM, dtype=np.float32)
                fixed[:min(EMBEDDING_DIM, arr.size)] = arr[:EMBEDDING_DIM]
                arr = fixed
            norm = np.linalg.norm(arr)
            meta.append(d)
            vectors.append(arr / norm if norm else arr)
            docs.append(tokenize(d["text"]))
        _cache = {
            "key": key,
            "meta": meta,
            "vectors": np.vstack(vectors) if vectors else np.zeros((0, EMBEDDING_DIM)),
            "bm25": BM25(docs),
            "docs": docs,
        }
        return _cache


def _cosine(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    if matrix.size == 0:
        return np.zeros(0)
    return matrix @ query_vec


def hybrid_search(query: str, paper_ids: list[str] | None = None,
                  top_k: int = RETRIEVE_TOP_K, per_paper: int | None = None) -> list[dict]:
    """BM25 稀疏 + Dense 稠密，RRF 融合；返回带页码/bbox 的证据块。

    检索前做两类查询扩展：
    1. 希腊字母英文名 → 符号（beta1 → β1），匹配论文里的数学写法；
    2. 中文问题经 LLM 改写为英文关键词，解决中英文语料不匹配。
    """
    index = _load_index()
    meta = index["meta"]
    if not meta:
        return []
    greek = expand_greek(query)
    rewrite = rewrite_query(query)
    base = " ".join(x for x in (query, greek) if x)
    bm25 = index["bm25"].score(tokenize(base))
    dense = _cosine(embed_query(" ".join(x for x in (base, rewrite) if x)),
                    index["vectors"])
    bm25_norm = bm25 / (bm25.max() or 1)
    dense_norm = dense / (dense.max() or 1)
    rrf = 1 / (60 + _rank(bm25)) + 1 / (60 + _rank(dense))
    combined = rrf + 0.15 * bm25_norm + 0.25 * dense_norm
    if rewrite:
        # 改写后的英文关键词单独跑一遍 BM25 并参与融合：
        # 中文原查询的单字 token 在英文语料里无区分度，会稀释聚焦术语
        bm25_rw = index["bm25"].score(tokenize(
            " ".join(x for x in (rewrite, greek) if x)))
        rw_norm = bm25_rw / (bm25_rw.max() or 1)
        combined = combined + 1 / (60 + _rank(bm25_rw)) + 0.20 * rw_norm

    results = []
    for i, score in enumerate(combined):
        m = meta[i]
        if paper_ids and m["paper_id"] not in paper_ids:
            continue
        if m["kind"] in ("heading", "reference"):
            continue
        results.append({
            "chunk_id": m["id"],
            "paper_id": m["paper_id"],
            "page": m["page"],
            "bbox": json.loads(m["bbox"]) if isinstance(m["bbox"], str) else m["bbox"],
            "section": m["section"],
            "kind": m["kind"],
            "text": m["text"],
            "score": float(score),
            "dense_score": float(dense[i]),
            "bm25_score": float(bm25_norm[i]),
        })
    results.sort(key=lambda r: r["score"], reverse=True)
    if per_paper:
        counts: Counter = Counter()
        filtered = []
        for r in results:
            if counts[r["paper_id"]] >= per_paper:
                continue
            counts[r["paper_id"]] += 1
            filtered.append(r)
        results = filtered
    return results[:top_k]


def _rank(scores: np.ndarray) -> np.ndarray:
    order = np.argsort(-scores)
    ranks = np.empty_like(order)
    ranks[order] = np.arange(len(scores))
    return ranks


def index_stats() -> dict:
    index = _load_index()
    return {
        "chunks": len(index["meta"]),
        "vectors": int(index["vectors"].shape[0]) if index["vectors"] is not None else 0,
        "dim": EMBEDDING_DIM,
    }


def enrich_paper(paper_id: str) -> None:
    """后台线程：用 arXiv 元数据补全 title/authors/year/abstract。失败不影响主流程。"""
    from .arxiv import base_id, metadata as arxiv_metadata

    meta = paper_meta(paper_id)
    if not meta:
        return
    aid = (meta.get("arxiv_id") or "").strip()
    if not aid:
        return
    bid = base_id(aid)
    if not bid:
        return
    try:
        result = arxiv_metadata([bid])
    except Exception as exc:
        # 网络/限流失败只记 meta，不打日志（后台线程）
        _mark_enrich_failed(paper_id, str(exc))
        return
    entry = result.get(bid)
    if not entry:
        _mark_enrich_failed(paper_id, "arXiv 未命中")
        return
    patch = {}
    if entry.get("title"):
        patch["title"] = entry["title"]
    if entry.get("authors"):
        patch["authors"] = json.dumps(entry["authors"], ensure_ascii=False)
    if entry.get("year"):
        patch["year"] = entry["year"]
    if entry.get("summary"):
        patch["abstract"] = entry["summary"]
    if patch:
        # 合成新 meta：保留原有 toc/page_flags，加 enriched=true
        old_meta = meta.get("meta") or {}
        new_meta = {**old_meta, "enriched": True, "enriched_at": now()}
        with get_conn() as conn:
            cols = ",".join(patch.keys()) + ",meta"
            placeholders = ",".join(["?"] * (len(patch) + 1))
            conn.execute(
                f"UPDATE papers SET {cols}=? WHERE id=?",
                tuple(list(patch.values()) + [json.dumps(new_meta, ensure_ascii=False),
                                               paper_id]))


def _mark_enrich_failed(paper_id: str, reason: str) -> None:
    with get_conn() as conn:
        row = conn.execute("SELECT meta FROM papers WHERE id=?", (paper_id,)).fetchone()
        if not row:
            return
        try:
            old = json.loads(row["meta"] or "{}")
        except (TypeError, json.JSONDecodeError):
            old = {}
        old["enriched"] = False
        old["enriched_reason"] = reason[:200]
        conn.execute("UPDATE papers SET meta=? WHERE id=?",
                     (json.dumps(old, ensure_ascii=False), paper_id))
