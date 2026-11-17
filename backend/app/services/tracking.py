"""arXiv 动态追踪与个性化推荐（赛题模块四）。

- 从本地文献库自动提炼研究主题，定期检索 arXiv 最新论文；
- 用本地库的文本向量画像计算相关度，并优先推荐附带开源代码的论文；
- 推荐理由全部来自可核实字段（相似文献、共同作者、仓库、投稿时间），不做编造；
- 点赞/踩写入 feedback 表，按用户正向/负向画像重新排序，为多用户推荐留出扩展点；
- 定时刷新使用 SQLite 租约，多 uvicorn worker 下同一时间只有一个进程真正执行。
"""
from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import numpy as np
import requests

from ..db import execute, get_conn, query
from . import arxiv, embeddings, ingest, store
from ..utils import keyword_overlap, norm_ws

TRACKING_ENABLED = os.getenv("TRACKING_ENABLED", "1") != "0"
INTERVAL_HOURS = float(os.getenv("TRACKING_INTERVAL_HOURS", "6"))
LIMIT_PER_QUERY = int(os.getenv("TRACKING_LIMIT_PER_QUERY", "12"))
MAX_TRACKS = int(os.getenv("TRACKING_MAX_ITEMS", "40"))
GITHUB_SEARCH_TOP = int(os.getenv("TRACKING_GITHUB_TOP", "8"))

WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{3,}")
GITHUB_RE = re.compile(r"https?://github\.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+")
STOP = {
    "the", "and", "for", "with", "from", "that", "this", "these", "those", "using",
    "based", "into", "toward", "towards", "via", "under", "over", "are", "was",
    "were", "has", "have", "can", "not", "but", "its", "their", "our", "your",
    "new", "novel", "study", "analysis", "paper", "all", "you", "need", "towards",
    "proceedings", "conference", "international", "journal", "arxiv", "preprint",
    "et", "al", "pp", "vol", "no", "we", "show", "use", "used", "uses",
}

_profile_cache: dict = {"key": None, "data": None}
_refresh_lock = threading.Lock()
_last_run_error = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# 文献库画像
# --------------------------------------------------------------------------
def library_profile() -> dict:
    """本地库的主题画像：逐篇向量 + 均值向量 + 作者集合。"""
    rows = query("SELECT id,title,abstract,authors,year FROM papers")
    key = (len(rows), store._version)
    if _profile_cache.get("key") == key and _profile_cache.get("data"):
        return _profile_cache["data"]
    if not rows:
        data = {"ids": [], "titles": {}, "vectors": np.zeros((0, 0), np.float32),
                "profile": None, "authors": {}, "years": {},
                "bm25": store.BM25([])}
        _profile_cache.update({"key": key, "data": data})
        return data
    texts = [f"{r['title'] or ''} {(r['abstract'] or '')[:600]}" for r in rows]
    vectors = embeddings.embed_texts(texts)
    norm = np.linalg.norm(vectors, axis=1, keepdims=True)
    norm[norm == 0] = 1.0
    vectors = vectors / norm
    profile = vectors.mean(axis=0)
    pnorm = np.linalg.norm(profile)
    profile = profile / pnorm if pnorm else profile
    authors = {}
    for r in rows:
        try:
            names = json.loads(r["authors"] or "[]")
        except (TypeError, json.JSONDecodeError):
            names = []
        authors[r["id"]] = {str(n).split()[-1].lower() for n in names if str(n).strip()}
    data = {
        "ids": [r["id"] for r in rows],
        "titles": {r["id"]: r["title"] or r["id"] for r in rows},
        "vectors": vectors,
        "profile": profile,
        "authors": authors,
        "years": {r["id"]: r["year"] for r in rows},
    }
    _profile_cache.update({"key": key, "data": data})
    return data


def _topic_terms() -> list[str]:
    rows = query("SELECT title,abstract FROM papers")
    docs = [f"{r['title'] or ''} {r['abstract'] or ''}" for r in rows]
    if not docs:
        return []
    df: Counter = Counter()
    for d in docs:
        df.update({t.lower() for t in WORD_RE.findall(d)})
    bigrams: Counter = Counter()
    for d in docs:
        toks = [t.lower() for t in WORD_RE.findall(d)]
        toks = [t for t in toks if t not in STOP]
        for a, b in zip(toks, toks[1:]):
            bigrams[f"{a} {b}"] += 1
    phrases = [p for p, c in bigrams.most_common(30) if c >= 2][:6]
    # 单字词容易抓到 model/task 这类泛词，只保留足够具体的学术词
    generic = {"models", "tasks", "method", "methods", "result", "results",
               "approach", "system", "systems", "training", "learning", "based",
               "performance", "problem", "framework", "dataset", "datasets",
               "language", "architecture", "state-of-the-art", "state", "art"}
    unigrams = [w for w, c in df.most_common(80)
                if c >= 2 and len(w) >= 8 and w not in STOP and w not in generic][:4]
    out: list[str] = []
    for t in phrases + unigrams:
        if t not in out:
            out.append(t)
    return out


def topics(max_groups: int = 3, per_group: int = 3) -> list[dict]:
    """把本地库的高频主题词拼成 arXiv OR 查询。"""
    terms = _topic_terms()[:max_groups * per_group]
    groups = []
    for i in range(0, len(terms), per_group):
        chunk = terms[i:i + per_group]
        if not chunk:
            break
        groups.append({
            "terms": chunk,
            "query": " OR ".join(f'all:"{t}"' for t in chunk),
        })
    return groups


# --------------------------------------------------------------------------
# 代码仓库识别
# --------------------------------------------------------------------------
def _repo_from_text(text: str) -> str | None:
    m = GITHUB_RE.search(text or "")
    if not m:
        return None
    parts = m.group(0).rstrip("/").removesuffix(".git").split("github.com/")[-1].split("/")
    if len(parts) < 2 or parts[0].lower() in ("features", "topics", "about", "login"):
        return None
    return f"{parts[0]}/{parts[1]}"


_gh_cache: dict[str, tuple[float, dict | None]] = {}


def github_lookup(title: str) -> dict | None:
    """按论文标题搜索 GitHub（真实 API，无 token 时限流），仅作「可能相关仓库」。"""
    title = norm_ws(title)
    if not title:
        return None
    hit = _gh_cache.get(title)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    q = " ".join([w for w in WORD_RE.findall(title) if w.lower() not in STOP][:8])
    out = None
    try:
        r = requests.get(
            "https://api.github.com/search/repositories",
            params={"q": q, "sort": "stars", "order": "desc", "per_page": 5},
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "research-copilot/0.1"},
            timeout=10)
        if r.status_code == 200:
            for item in r.json().get("items", []):
                name = f"{item['owner']['login']}/{item['name']}"
                desc = item.get("description") or ""
                if keyword_overlap(title, f"{name} {desc}") >= 0.45:
                    out = {"repo": name, "stars": item.get("stargazers_count"),
                           "source": "github_search"}
                    break
    except requests.RequestException:
        out = None
    _gh_cache[title] = (time.time(), out)
    return out


def _find_code(item: dict, allow_search: bool) -> dict | None:
    own = _repo_from_text(item.get("summary") or "") or \
        _repo_from_text(item.get("comment") or "")
    if own:
        return {"repo": own, "stars": None, "source": "paper"}
    if allow_search:
        return github_lookup(item.get("title") or "")
    return None


# --------------------------------------------------------------------------
# 推荐理由
# --------------------------------------------------------------------------
def _reasons(item: dict, vec: np.ndarray, profile: dict, rel: float) -> list[str]:
    out: list[str] = []
    vectors = profile.get("vectors")
    ids = profile.get("ids") or []
    if vectors is not None and len(ids):
        sims = vectors @ vec
        order = np.argsort(-sims)[:6]
        shown = 0
        for idx in order:
            sim = float(sims[idx])
            title = (profile["titles"].get(ids[idx]) or "").strip()
            # 有的 PDF 解析失败会把整段摘要当成标题，避免把这类脏标题写进推荐理由
            if sim < 0.12 or len(title) > 120:
                continue
            if shown == 0:
                out.append(f"与本地库《{title[:44]}》研究方向最接近（文本相似度 {sim*100:.0f}%）")
            elif sim >= 0.18:
                out.append(f"同时接近《{title[:44]}》（{sim*100:.0f}%）")
            shown += 1
            if shown >= 2:
                break
    item_authors = {str(a).split()[-1].lower() for a in item.get("authors") or []
                    if str(a).strip()}
    shared = []
    for pid, names in (profile.get("authors") or {}).items():
        t = (profile["titles"].get(pid) or "").strip()
        if len(t) > 120:
            continue
        common = item_authors & names
        if common:
            shared.append((t, sorted(common)[0]))
    if shared:
        t, name = shared[0]
        out.append(f"作者 {name} 也出现在你的文献库《{t[:36]}》中")
    code = item.get("_code")
    if code and code.get("source") == "paper":
        stars = code.get("stars")
        out.append(f"论文自带开源代码 {code['repo']}" + (f"（⭐{stars}）" if stars else ""))
    elif code:
        out.append(f"GitHub 存在可能相关仓库 {code['repo']}（标题匹配，需人工确认）")
    if item.get("published"):
        out.append(f"arXiv 最新提交 {item['published']}")
    if rel >= 0.7:
        out.append(f"与文献库主题画像匹配度 {rel*100:.0f}/100（相对排名）")
    return out[:5]


# --------------------------------------------------------------------------
# 刷新追踪
# --------------------------------------------------------------------------
def _record_run(started: str, found: int, added: int, status: str, message: str) -> None:
    execute("INSERT INTO track_runs(started_at,finished_at,found,added,status,message) "
            "VALUES(?,?,?,?,?,?)",
            (started, _now(), found, added, status, message[:500]))


def refresh(queries: list[str] | None = None, limit_per_query: int | None = None,
            search_fn=None, code_lookup=None) -> dict:
    """检索 arXiv 最新论文 → 计算相关度 → 写入追踪表。

    search_fn / code_lookup 可注入，便于离线单测；默认走真实 arXiv / GitHub API。
    """
    global _last_run_error
    if not _refresh_lock.acquire(blocking=False):
        return {"status": "busy", "message": "已有追踪任务正在执行，请稍后再试"}
    started = _now()
    try:
        groups = ([{"query": q, "terms": [q]} for q in queries if q.strip()]
                  if queries else topics())
        profile = library_profile()
        if not groups:
            _record_run(started, 0, 0, "skipped", "本地文献库为空，无法提炼追踪主题")
            return {"status": "skipped", "found": 0, "new": 0,
                    "message": "本地文献库为空：先上传论文，系统才能按你的研究方向追踪 arXiv"}
        if profile.get("profile") is None:
            _record_run(started, 0, 0, "skipped", "文献库缺少可用于计算相关度的文本")
            return {"status": "skipped", "found": 0, "new": 0,
                    "message": "文献库缺少标题/摘要，无法计算相关度"}

        search = search_fn or arxiv.search_raw
        limit = limit_per_query or LIMIT_PER_QUERY
        found: dict[str, dict] = {}
        errors: list[str] = []
        for g in groups:
            try:
                hits = search(g["query"], limit=limit, sort_by="submittedDate")
            except Exception as exc:
                errors.append(f"{g['query'][:40]}: {exc}")
                continue
            for h in hits:
                bid = h.get("base_id") or arxiv.base_id(h.get("arxiv_id"))
                if not bid or bid in found:
                    continue
                h["_topic"] = g["terms"]
                found[bid] = h

        existing = {arxiv.base_id(p.get("arxiv_id")) for p in store.list_papers()}
        items = [h for bid, h in found.items() if bid not in existing][:MAX_TRACKS]
        if not items:
            msg = "没有检索到新的 arXiv 论文" + ("；部分请求失败：" + "；".join(errors[:2])
                                                 if errors else "")
            _record_run(started, len(found), 0, "ok" if not errors else "partial", msg)
            return {"status": "ok" if not errors else "partial", "found": len(found),
                    "new": 0, "message": msg, "topics": [g["terms"] for g in groups],
                    "items": []}

        texts = [f"{h.get('title') or ''} {(h.get('summary') or '')[:900]}" for h in items]
        vectors = embeddings.embed_texts(texts)
        norm = np.linalg.norm(vectors, axis=1, keepdims=True)
        norm[norm == 0] = 1.0
        vectors = vectors / norm

        # 相关度 = 主题词覆盖（0.75）+ 与库内单篇论文的最佳相似度（0.25）。
        # 只用哈希向量时无关文本也会有三四成相似度，词面覆盖能有效压掉噪声。
        terms: list[str] = []
        for g in groups:
            for t in g["terms"]:
                if t.lower() not in terms:
                    terms.append(t.lower())
        covs = []
        for h in items:
            title_low = (h.get("title") or "").lower()
            body_low = f"{title_low} {(h.get('summary') or '').lower()}"
            hit = 0.0
            for t in terms:
                words = t.split()
                if all(w in title_low for w in words):
                    hit += 1.5
                elif all(w in body_low for w in words):
                    hit += 1.0
            covs.append(min(1.0, hit / max(len(terms), 1) * 2.5))
        covs = np.array(covs, dtype=np.float32)

        per_paper = profile["vectors"] @ vectors.T          # (库内论文, 候选)
        best = per_paper.max(axis=0) if per_paper.size else np.zeros(len(items))
        cos_rel = np.clip((best - 0.18) / 0.30, 0.0, 1.0)
        rels = np.clip(0.75 * covs + 0.25 * cos_rel, 0.0, 1.0)

        added = 0
        rows = []
        for i, h in enumerate(items):
            if code_lookup is not None:
                code = code_lookup(h)
            else:
                code = _find_code(h, i < GITHUB_SEARCH_TOP)
            h["_code"] = code
            rel = float(rels[i])
            reasons = _reasons(h, vectors[i], profile, rel)
            bid = arxiv.base_id(h.get("arxiv_id"))
            row = {
                "id": bid,
                "arxiv_id": h.get("arxiv_id"),
                "title": h.get("title") or "",
                "summary": h.get("summary") or "",
                "authors": h.get("authors") or [],
                "year": h.get("year"),
                "published": h.get("published") or "",
                "pdf_url": h.get("pdf_url") or "",
                "categories": h.get("categories") or [],
                "comment": h.get("comment") or "",
                "code_repo": (code or {}).get("repo"),
                "code_stars": (code or {}).get("stars"),
                "code_source": (code or {}).get("source"),
                "relevance": round(rel, 4),
                "reasons": reasons,
                "topics": h.get("_topic") or [],
                "embedding": vectors[i].astype(np.float32).tobytes(),
                "fetched_at": _now(),
            }
            existed = query("SELECT 1 FROM arxiv_tracks WHERE id=?", (bid,))
            _upsert(row)
            if not existed:
                added += 1
            rows.append(row)

        msg = f"检索到 {len(found)} 篇最新论文，新增 {added} 篇候选" + \
              ("；部分请求失败：" + "；".join(errors[:2]) if errors else "")
        _record_run(started, len(found), added, "ok" if not errors else "partial", msg)
        _last_run_error = ""
        return {
            "status": "ok" if not errors else "partial",
            "found": len(found), "new": added,
            "message": msg,
            "topics": [g["terms"] for g in groups],
            "items": [{"id": r["id"], "title": r["title"],
                       "relevance": r["relevance"], "code_repo": r["code_repo"],
                       "reasons": r["reasons"]}
                      for r in sorted(rows, key=lambda x: -x["relevance"])[:10]],
        }
    except Exception as exc:
        _last_run_error = str(exc)
        _record_run(started, 0, 0, "error", str(exc))
        return {"status": "error", "message": f"追踪失败：{exc}"}
    finally:
        _refresh_lock.release()


def _upsert(row: dict) -> None:
    execute(
        "INSERT INTO arxiv_tracks(id,arxiv_id,title,summary,authors,year,published,pdf_url,"
        "categories,comment,code_repo,code_stars,code_source,relevance,reasons,topics,"
        "embedding,status,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'new',?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "arxiv_id=excluded.arxiv_id,title=excluded.title,summary=excluded.summary,"
        "authors=excluded.authors,year=excluded.year,published=excluded.published,"
        "pdf_url=excluded.pdf_url,categories=excluded.categories,comment=excluded.comment,"
        "code_repo=COALESCE(excluded.code_repo,arxiv_tracks.code_repo),"
        "code_stars=COALESCE(excluded.code_stars,arxiv_tracks.code_stars),"
        "code_source=COALESCE(excluded.code_source,arxiv_tracks.code_source),"
        "relevance=excluded.relevance,reasons=excluded.reasons,topics=excluded.topics,"
        "embedding=excluded.embedding,fetched_at=excluded.fetched_at",
        (row["id"], row["arxiv_id"], row["title"], row["summary"],
         json.dumps(row["authors"], ensure_ascii=False), row["year"], row["published"],
         row["pdf_url"], json.dumps(row["categories"], ensure_ascii=False),
         row["comment"], row["code_repo"], row["code_stars"], row["code_source"],
         row["relevance"], json.dumps(row["reasons"], ensure_ascii=False),
         json.dumps(row["topics"], ensure_ascii=False), row["embedding"],
         row["fetched_at"]))


# --------------------------------------------------------------------------
# 查询与反馈
# --------------------------------------------------------------------------
def _vec(blob) -> np.ndarray | None:
    if not blob:
        return None
    try:
        v = np.frombuffer(blob, dtype=np.float32)
    except ValueError:
        return None
    n = float(np.linalg.norm(v))
    return v / n if n else v


def _votes(user_id: str) -> dict[str, int]:
    rows = query("SELECT target_id,vote FROM feedback "
                 "WHERE user_id=? AND target_type='track'", (user_id,))
    return {r["target_id"]: r["vote"] for r in rows}


def list_tracks(user_id: str = "default", limit: int = 30) -> dict:
    rows = query("SELECT * FROM arxiv_tracks WHERE status != 'ignored'")
    mine = _votes(user_id)
    totals = {r["target_id"]: {"likes": r["likes"], "dislikes": r["dislikes"]}
              for r in query(
                  "SELECT target_id, SUM(vote=1) AS likes, SUM(vote=-1) AS dislikes "
                  "FROM feedback WHERE target_type='track' GROUP BY target_id")}

    liked_vecs, disliked_vecs = [], []
    for r in rows:
        v = _vec(r["embedding"])
        if v is None:
            continue
        if mine.get(r["id"]) == 1:
            liked_vecs.append(v)
        elif mine.get(r["id"]) == -1:
            disliked_vecs.append(v)
    pos = np.mean(liked_vecs, axis=0) if liked_vecs else None
    neg = np.mean(disliked_vecs, axis=0) if disliked_vecs else None

    items = []
    for r in rows:
        v = _vec(r["embedding"])
        relevance = float(r["relevance"] or 0)
        # 反馈权重独立于基础相关度：用户明确点赞的方向要能压过纯内容相似度，
        # 踩过的方向即使主题相近也下沉（多用户各自的画像互不影响）。
        pref = 0.0
        if v is not None and pos is not None:
            pref += 0.90 * float(np.clip(v @ pos, 0, 1))
        if v is not None and neg is not None:
            pref -= 0.70 * float(np.clip(v @ neg, 0, 1))
        final = max(0.0, 0.70 * relevance + pref)
        # 开源代码是「易复现」的强信号，作为加分项而不是硬分组，避免低相关论文靠代码挤到最前
        rank = final + (0.18 if r["code_repo"] else 0.0)
        v_tot = totals.get(r["id"], {})
        items.append({
            "id": r["id"],
            "arxiv_id": r["arxiv_id"],
            "title": r["title"],
            "summary": r["summary"],
            "authors": json.loads(r["authors"] or "[]"),
            "year": r["year"],
            "published": r["published"],
            "pdf_url": r["pdf_url"],
            "comment": r["comment"],
            "code_repo": r["code_repo"],
            "code_stars": r["code_stars"],
            "code_source": r["code_source"],
            "relevance": round(relevance, 3),
            "score": round(final, 3),
            "rank": round(rank, 4),
            "reasons": json.loads(r["reasons"] or "[]"),
            "topics": json.loads(r["topics"] or "[]"),
            "status": r["status"],
            "paper_id": r["paper_id"],
            "fetched_at": r["fetched_at"],
            "my_vote": mine.get(r["id"], 0),
            "likes": int(v_tot.get("likes") or 0),
            "dislikes": int(v_tot.get("dislikes") or 0),
        })
    items.sort(key=lambda x: (x["rank"], x["published"]), reverse=True)
    return {"user_id": user_id, "items": items[:max(1, min(limit, 100))],
            "total": len(items),
            "personalized": bool(liked_vecs or disliked_vecs)}


def set_feedback(track_id: str, vote: int, user_id: str = "default") -> dict:
    vote = 1 if vote > 0 else (-1 if vote < 0 else 0)
    if vote == 0:
        execute("DELETE FROM feedback WHERE user_id=? AND target_type='track' "
                "AND target_id=?", (user_id, track_id))
    else:
        execute("INSERT INTO feedback(user_id,target_type,target_id,vote,created_at) "
                "VALUES(?,'track',?,?,?) "
                "ON CONFLICT(user_id,target_type,target_id) DO UPDATE SET "
                "vote=excluded.vote, created_at=excluded.created_at",
                (user_id, track_id, vote, _now()))
    return {"ok": True, "track_id": track_id, "vote": vote}


def add_to_library(track_id: str) -> dict:
    rows = query("SELECT * FROM arxiv_tracks WHERE id=?", (track_id,))
    if not rows:
        return {"error": "追踪记录不存在"}
    aid = rows[0]["arxiv_id"]
    if not aid:
        return {"error": "该记录缺少 arXiv id，无法自动下载"}
    res = ingest.fetch_and_ingest(aid)
    if res.get("status") in ("ok", "exists", "duplicate"):
        execute("UPDATE arxiv_tracks SET status='added', paper_id=? WHERE id=?",
                (res.get("paper_id"), track_id))
    return res


def status(user_id: str = "default") -> dict:
    runs = query("SELECT * FROM track_runs ORDER BY id DESC LIMIT 1")
    last = dict(runs[0]) if runs else None
    total = query("SELECT COUNT(*) AS n FROM arxiv_tracks")[0]["n"]
    fresh = query("SELECT COUNT(*) AS n FROM arxiv_tracks WHERE status='new'")[0]["n"]
    with_ctx = query("SELECT COUNT(*) AS n FROM papers")[0]["n"]
    lease = query("SELECT expires_at FROM scheduler_lease WHERE key='arxiv_tracking'")
    return {
        "enabled": TRACKING_ENABLED,
        "interval_hours": INTERVAL_HOURS,
        "library_papers": with_ctx,
        "topics": [g["terms"] for g in topics()],
        "tracks": total,
        "new": fresh,
        "last_run": last,
        "last_error": _last_run_error,
        "next_due": lease[0]["expires_at"] if lease else None,
        "my_likes": sum(1 for v in _votes(user_id).values() if v == 1),
        "my_dislikes": sum(1 for v in _votes(user_id).values() if v == -1),
    }


# --------------------------------------------------------------------------
# 定时刷新：SQLite 租约保证多 worker 不重复执行
# --------------------------------------------------------------------------
def _acquire_lease(key: str, ttl_seconds: float) -> bool:
    now = _now()
    expires = _iso(time.time() + ttl_seconds)
    owner = f"{socket.gethostname()}:{os.getpid()}"
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO scheduler_lease(key,owner,expires_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET owner=excluded.owner, "
            "expires_at=excluded.expires_at WHERE scheduler_lease.expires_at < ?",
            (key, owner, expires, now))
        return cur.rowcount > 0


def _loop() -> None:
    time.sleep(20)  # 等 NMT/OCR 预热，避免和启动请求抢资源
    while True:
        try:
            if _acquire_lease("arxiv_tracking", INTERVAL_HOURS * 3600):
                refresh()
        except Exception as exc:  # 后台线程不能静默退出
            global _last_run_error
            _last_run_error = str(exc)
        time.sleep(300)


def start_scheduler() -> None:
    if not TRACKING_ENABLED:
        return
    threading.Thread(target=_loop, daemon=True).start()
