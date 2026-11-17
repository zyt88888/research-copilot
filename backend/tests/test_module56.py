"""模块五 / 六：领域综述生成、Graphviz/Matplotlib 图、学术英译入口。

全部使用临时 DB 与假的 LLM 函数，不访问外网。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app import db, settings  # noqa: E402
from app.services import copilot, llm, store, survey, translate  # noqa: E402


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "uploads").mkdir(parents=True)
    monkeypatch.setattr(db, "DB_PATH", str(data / "app.db"))
    monkeypatch.setattr(settings, "DATA_DIR", data)
    db.init_db()
    store._cache.clear()
    store._sec_cache.clear()
    store.bump_graph_version()
    monkeypatch.setattr(llm, "is_available", lambda: False)
    return data


def add_paper(pid: str, title: str, abstract: str, year: int,
              chunks: list[tuple[str, str, str]] | None = None,
              refs: list[dict] | None = None) -> None:
    db.execute(
        "INSERT OR REPLACE INTO papers(id,title,authors,year,abstract,arxiv_id,filename,"
        "path,num_pages,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (pid, title, json.dumps(["Alice Smith"]), year, abstract, None, "", "", 3,
         "2026-01-01T00:00:00+00:00"))
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
    store.bump_graph_version()


def seed(lib) -> list[str]:
    add_paper(
        "p1", "Attention Is All You Need",
        "We propose the Transformer, a model based entirely on attention mechanisms.",
        2017,
        chunks=[("Introduction", "paragraph",
                 "The Transformer replaces recurrence with self-attention."),
                ("Limitations", "paragraph",
                 "Future work should extend the Transformer to long documents and "
                 "multimodal inputs, which remains open for efficient attention design.")],
    )
    add_paper(
        "p2", "Efficient Transformers for Long Documents",
        "We study sparse attention and retrieval for long document transformers.",
        2023,
        chunks=[("Method", "paragraph",
                 "We combine sparse attention with dense retrieval for long documents.")],
        refs=[{"raw": "Vaswani et al. Attention Is All You Need. NeurIPS 2017.",
               "title": "Attention Is All You Need", "year": 2017}],
    )
    add_paper(
        "p3", "Retrieval Augmented Generation for Science",
        "A retrieval augmented generation system for scientific question answering.",
        2024,
    )
    return ["p1", "p2", "p3"]


def test_survey_offline_uses_real_metadata_only(lib):
    ids = seed(lib)
    out = survey.generate(topic=None, paper_ids=ids)
    assert out["mode"] == "offline"
    md = out["markdown"]
    assert "## 参考文献" in md
    assert "Attention Is All You Need" in md
    assert "[P1]" in md
    # 只允许出现库内编号，且参考文献条目数与论文数一致
    assert out["stats"]["papers"] == 3
    assert len(out["citations"]) == 3
    assert "[P9]" not in md
    # 开放问题来自真实 Future Work 句子
    assert "long documents" in md.lower() or "开放问题" in md


def test_survey_llm_drops_invalid_citations(lib, monkeypatch):
    ids = seed(lib)
    monkeypatch.setattr(llm, "is_available", lambda: True)
    monkeypatch.setattr(
        llm, "chat",
        lambda *a, **k: ("Transformer 已成为主流 [P1]，长文档效率仍是开放问题 [P9]。\n\n"
                         "## 参考文献\n- Fake, 2020. A Fabricated Reference."))
    out = survey.generate(topic="长文档建模", paper_ids=ids)
    assert out["mode"] == "llm"
    assert "[P1]" in out["markdown"]
    assert "[P9]" not in out["markdown"]
    assert "Fabricated Reference" not in out["markdown"]
    assert out["dropped_citations"] == 1
    assert "Attention Is All You Need" in out["markdown"]  # 自动附真实参考文献


def test_diagram_graphviz_and_matplotlib_offline(lib):
    dot = copilot.diagram("长文档检索增强", "graphviz")
    assert dot["kind"] == "graphviz" and dot["mode"] == "offline"
    assert dot["code"].startswith("digraph")
    assert "->" in dot["code"]

    py = copilot.diagram("长文档检索增强", "matplotlib")
    assert py["kind"] == "matplotlib" and py["mode"] == "offline"
    assert "import matplotlib" in py["code"]
    assert "pipeline.png" in py["code"]
    assert "FancyBboxPatch" in py["code"]


def test_translate_zh_to_en_offline_never_fakes(lib):
    out = translate.translate_text("本文提出一种基于稀疏注意力的长文档建模方法。", "en")
    assert out["mode"] == "offline"
    assert out["text"] == ""
    assert "LLM_API_KEY" in out["notice"]
