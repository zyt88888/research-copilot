"""模块四（arXiv 追踪 + 参考文献下钻 + 引用含金量）单元测试。

全部使用临时 DB 与注入的假检索函数，不访问外网。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app import db, settings  # noqa: E402
from app.services import citation_quality, drill, insights, store, tracking  # noqa: E402


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "uploads").mkdir(parents=True)
    monkeypatch.setattr(db, "DB_PATH", str(data / "app.db"))
    monkeypatch.setattr(settings, "DATA_DIR", data)
    db.init_db()
    tracking._profile_cache.update({"key": None, "data": None})
    tracking._gh_cache.clear()
    store._cache.clear()
    store._sec_cache.clear()
    return data


def add_paper(pid: str, title: str, abstract: str = "",
              authors: list[str] | None = None, year: int = 2024,
              chunks: list[tuple[str, str, str]] | None = None,
              refs: list[dict] | None = None,
              arxiv_id: str | None = None) -> None:
    import json
    db.execute(
        "INSERT OR REPLACE INTO papers(id,title,authors,year,abstract,arxiv_id,filename,"
        "path,num_pages,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (pid, title, json.dumps(authors or ["Alice Smith"], ensure_ascii=False), year,
         abstract, arxiv_id, "", "", 3, "2026-01-01T00:00:00+00:00"))
    for i, (section, kind, text) in enumerate(chunks or []):
        db.execute(
            "INSERT OR REPLACE INTO chunks(id,paper_id,page,bbox,section,level,kind,text,seq) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (f"{pid}:c{i}", pid, 1 + i // 3, "[0,0,0,0]", section, 0, kind, text, i))
    for r in refs or []:
        db.execute("INSERT INTO refs(paper_id,raw,title,arxiv_id,year) VALUES(?,?,?,?,?)",
                   (pid, r.get("raw") or r.get("title"), r.get("title"),
                    r.get("arxiv_id"), r.get("year")))
    store.bump_version()


REF_USED = {"raw": "[1] Vaswani et al. Attention Is All You Need. NeurIPS 2017. arXiv:1706.03762",
            "title": "Attention Is All You Need", "arxiv_id": "1706.03762", "year": 2017}
REF_FAKE = {"raw": "[2] A Completely Unrelated Survey of Widget Engineering. 2019.",
            "title": "A Completely Unrelated Survey of Widget Engineering",
            "arxiv_id": None, "year": 2019}


def _refs_paper(pid: str = "p_refs") -> None:
    add_paper(
        pid, "A New Transformer for Translation", abstract="We study attention.",
        chunks=[
            ("Introduction", "paragraph",
             "Transformer architectures have changed translation. We propose a new "
             "attention mechanism and adopt the Transformer of [1] to improve quality."),
            ("Method", "paragraph",
             "Following [1], we extend the encoder with a gating module and compare "
             "our design against the baseline."),
            ("Experiments", "paragraph",
             "Experiments show our method outperforms [1] by 2 BLEU on WMT."),
            ("References", "reference", "[1] Vaswani et al. Attention Is All You Need."),
        ],
        refs=[REF_USED, REF_FAKE],
    )


# --------------------------------------------------------------------------
# 引用含金量
# --------------------------------------------------------------------------
def test_citation_quality_marks_perfunctory_reference(lib):
    _refs_paper()
    res = citation_quality.analyze("p_refs")
    assert res["summary"]["total"] == 2
    used, fake = res["refs"]
    assert used["title"] == "Attention Is All You Need"
    assert used["mentions"] >= 2, "正文多次提及应被识别"
    assert used["score"] >= 60 and used["label"] == "高价值"
    assert fake["mentions"] == 0 and fake["score"] == 0
    assert fake["label"] == "应付式"
    assert res["summary"]["perfunctory"] == 1


def test_select_for_drill_filters_perfunctory(lib):
    _refs_paper()
    res = citation_quality.analyze("p_refs")
    picked = citation_quality.select_for_drill(res, min_score=30, limit=5)
    assert [r["title"] for r in picked] == ["Attention Is All You Need"]
    assert citation_quality.select_for_drill(res, limit=5, quality_filter=False)


def test_citation_quality_unknown_paper(lib):
    assert citation_quality.analyze("missing")["error"]


# --------------------------------------------------------------------------
# 参考文献批量下钻
# --------------------------------------------------------------------------
def test_drill_refs_only_downloads_high_value(lib, monkeypatch):
    _refs_paper()
    called: list[str] = []

    def fake_fetch(aid, force=False):
        called.append(aid)
        return {"status": "ok", "paper_id": "new1", "title": "Attention Is All You Need"}

    monkeypatch.setattr(drill.ingest, "fetch_and_ingest", fake_fetch)
    started = drill.start("p_refs", limit=3, min_score=30)
    assert started["job_id"] and started["total"] == 1
    for _ in range(50):
        job = drill.get(started["job_id"])
        if job and job["status"] == "done":
            break
        time.sleep(0.05)
    job = drill.get(started["job_id"])
    assert job["status"] == "done"
    assert called == ["1706.03762"], "应付式引用不应被下钻"
    assert job["items"][0]["status"] == "ok"


def test_drill_title_uses_similarity_threshold(lib, monkeypatch):
    calls = []

    def fake_search(q, limit=20, sort_by="relevance"):
        return [{"arxiv_id": "1706.03762v7", "base_id": "1706.03762",
                 "title": "Attention Is All You Need", "year": 2017,
                 "published": "2017-06-12", "summary": "", "authors": [],
                 "pdf_url": "", "comment": "", "categories": []}]

    def fake_fetch(aid, force=False):
        calls.append(aid)
        return {"status": "ok", "paper_id": "x", "title": "Attention Is All You Need"}

    monkeypatch.setattr(drill.arxiv, "search_raw", fake_search)
    monkeypatch.setattr(drill.ingest, "fetch_and_ingest", fake_fetch)
    ok = drill.drill_by_title("Attention is all you need")
    assert ok["status"] == "ok" and calls == ["1706.03762v7"]
    miss = drill.drill_by_title("A Totally Different Paper About Cheese")
    assert miss["status"] == "not_found" and calls == ["1706.03762v7"]


# --------------------------------------------------------------------------
# arXiv 追踪与推荐
# --------------------------------------------------------------------------
def _seed_library() -> None:
    add_paper("lib1", "Retrieval Augmented Generation for Question Answering",
              "retrieval augmented generation improves question answering accuracy",
              authors=["Alice Smith"], year=2024)
    add_paper("lib2", "Efficient Retrieval Augmented Generation with Chunk Reranking",
              "retrieval augmented generation with reranking improves efficiency",
              authors=["Bob Lee"], year=2025)


def _fake_hits():
    return [
        {"arxiv_id": "2511.00001v1", "base_id": "2511.00001",
         "title": "Retrieval Augmented Generation with Better Chunk Selection",
         "summary": "We improve retrieval augmented generation; code at "
                    "https://github.com/acme/rag-chunks",
         "authors": ["Carol Wu"], "year": 2025, "published": "2025-11-01",
         "categories": ["cs.CL"], "comment": "", "pdf_url": "https://arxiv.org/pdf/2511.00001v1"},
        {"arxiv_id": "2511.00002v1", "base_id": "2511.00002",
         "title": "A Study of Protein Folding Hardware",
         "summary": "Unrelated topic about protein folding and lab hardware.",
         "authors": ["Dan Zhao"], "year": 2025, "published": "2025-11-02",
         "categories": ["q-bio"], "comment": "", "pdf_url": "https://arxiv.org/pdf/2511.00002v1"},
    ]


def test_refresh_scores_and_stores_tracks(lib, monkeypatch):
    _seed_library()

    def fake_search(q, limit=20, sort_by="relevance"):
        return _fake_hits()

    def fake_code(h):
        repo = tracking._repo_from_text(h.get("summary") or "")
        return {"repo": repo, "stars": None, "source": "paper"} if repo else None

    res = tracking.refresh(queries=["retrieval augmented generation"],
                           search_fn=fake_search, code_lookup=fake_code)
    assert res["status"] == "ok" and res["new"] == 2

    listing = tracking.list_tracks("u1")
    items = listing["items"]
    assert len(items) == 2
    rag = next(i for i in items if "Retrieval Augmented" in i["title"])
    bio = next(i for i in items if "Protein" in i["title"])
    assert rag["relevance"] > bio["relevance"], "主题接近的论文相关度应更高"
    assert rag["code_repo"] == "acme/rag-chunks"
    assert rag["reasons"], "推荐理由不能为空"
    assert items[0]["id"] == rag["id"], "附带开源代码的论文优先展示"


def test_feedback_reorders_and_persists(lib, monkeypatch):
    _seed_library()
    tracking.refresh(queries=["retrieval"], search_fn=lambda q, limit=20, sort_by="relevance":
                     _fake_hits(), code_lookup=lambda h: None)
    items = tracking.list_tracks("u1")["items"]
    rag = next(i for i in items if "Retrieval" in i["title"])
    bio = next(i for i in items if "Protein" in i["title"])

    tracking.set_feedback(rag["id"], -1, "u1")
    tracking.set_feedback(bio["id"], 1, "u1")
    listing = tracking.list_tracks("u1")
    assert listing["personalized"] is True
    by_id = {i["id"]: i for i in listing["items"]}
    assert by_id[rag["id"]]["my_vote"] == -1
    assert by_id[bio["id"]]["my_vote"] == 1
    assert by_id[bio["id"]]["score"] >= by_id[rag["id"]]["score"]

    # 多用户互不影响
    assert all(i["my_vote"] == 0 for i in tracking.list_tracks("u2")["items"])
    # 取消点赞
    tracking.set_feedback(bio["id"], 0, "u1")
    assert tracking.list_tracks("u1")["items"][0]["my_vote"] in (0, 1)


def test_refresh_skips_empty_library(lib):
    res = tracking.refresh()
    assert res["status"] == "skipped"
    assert "文献库为空" in res["message"]


def test_refresh_deduplicates_existing_library(lib, monkeypatch):
    _seed_library()
    add_paper("lib3", "Retrieval Augmented Generation with Better Chunk Selection",
              "same paper", arxiv_id="2511.00001v1")
    res = tracking.refresh(queries=["retrieval"], search_fn=lambda q, limit=20,
                           sort_by="relevance": _fake_hits(), code_lookup=lambda h: None)
    listing = tracking.list_tracks("u1")
    assert all("2511.00001" not in (i["id"] or "") for i in listing["items"])
    assert res["new"] == 1


def test_add_track_to_library_marks_added(lib, monkeypatch):
    _seed_library()
    tracking.refresh(queries=["retrieval"], search_fn=lambda q, limit=20, sort_by="relevance":
                     _fake_hits(), code_lookup=lambda h: None)
    monkeypatch.setattr(tracking.ingest, "fetch_and_ingest",
                        lambda aid, force=False: {"status": "ok", "paper_id": "p9"})
    res = tracking.add_to_library("2511.00001")
    assert res["status"] == "ok"
    rows = db.query("SELECT status,paper_id FROM arxiv_tracks WHERE id='2511.00001'")
    assert rows[0]["status"] == "added" and rows[0]["paper_id"] == "p9"


def test_topics_derived_from_library(lib):
    assert tracking.topics() == []
    _seed_library()
    groups = tracking.topics()
    assert groups, "有文献库时应能提炼出追踪主题"
    assert any("retrieval" in t for g in groups for t in g["terms"])


# --------------------------------------------------------------------------
# 相关推荐：推荐理由 / 代码优先
# --------------------------------------------------------------------------
def test_recommend_returns_reasons(lib):
    _refs_paper("p_cur")

    def save(pid: str, title: str, abstract: str, chunks: list[tuple[str, str]]):
        parsed = {
            "title": title, "authors": ["Alice Smith"], "year": 2024,
            "abstract": abstract, "filename": f"{pid}.pdf", "num_pages": 2,
            "chunks": [{"id": f"c{i}", "page": 1, "bbox": [0, 0, 0, 0],
                        "section": sec, "level": 0, "kind": "paragraph",
                        "text": text, "seq": i}
                       for i, (sec, text) in enumerate(chunks)],
            "refs": [],
        }
        store.save_paper(parsed, "", pid)

    save("p_code", "Gated Attention for Neural Machine Translation",
         "gated attention improves neural machine translation",
         [("Method", "Our implementation is available at "
                     "https://github.com/acme/gated-attn and improves translation quality.")])
    save("p_other", "Attention Variants for Translation",
         "attention variants for translation",
         [("Method", "We compare attention variants for translation quality.")])

    res = insights.recommend("p_cur", top_k=5)
    assert res["items"], "库内应有可推荐论文"
    assert all(i["reasons"] for i in res["items"])
    assert "开源代码加权" in res["basis"]
