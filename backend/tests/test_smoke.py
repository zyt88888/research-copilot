"""端到端冒烟测试：不用 LLM，只用 Attention v1+v7 验证解析、去重、RAG、图谱。

运行：pytest tests/ -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.db import init_db
from app.services import dedup, graph, pdf_parser, rag, store

PDF_V1 = Path(r"c:\Users\21772\.trae-cn\attachments\9a095feb-6376-4cad-9ff5-ba9e9eff5498_99b32fae-79ce-4ef4-aa07-e2ce380ffe45_1706.03762v1.pdf")
PDF_V7 = Path(r"c:\Users\21772\.trae-cn\attachments\587ec687-a4b6-4299-a0d0-4dd420e79af1_53f1d90d-a749-45f7-88f4-c4c6ee4b56a5_1706.03762v7.pdf")


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "data" / "app.db"))
    init_db()


@pytest.mark.skipif(not PDF_V1.exists() or not PDF_V7.exists(), reason="缺测试 PDF")
def test_parse_attention_v7():
    parsed = pdf_parser.parse_pdf(PDF_V7)
    assert "Attention" in parsed["title"] or "attention" in parsed["title"].lower()
    assert parsed["num_pages"] >= 10
    assert len(parsed["chunks"]) > 50
    assert len(parsed["refs"]) > 10
    assert any(c["kind"] == "table" for c in parsed["chunks"]) or any(
        c["kind"] == "paragraph" for c in parsed["chunks"])


@pytest.mark.skipif(not PDF_V1.exists() or not PDF_V7.exists(), reason="缺测试 PDF")
def test_detect_duplicate_version():
    parsed_v7 = pdf_parser.parse_pdf(PDF_V7)
    parsed_v1 = pdf_parser.parse_pdf(PDF_V1)
    store.save_paper(parsed_v7, str(PDF_V7), paper_id="paper_v7")
    dup = dedup.find_duplicates(parsed_v1)
    assert dup is not None, "同一论文 v1 上传时应检测到 v7"
    assert dup["same_arxiv_family"]
    assert dup["similarity"] >= 0.3
    diff = dedup.version_diff("paper_v7", parsed_v1)
    assert "正文" in diff["summary"] or "重合" in diff["summary"]


@pytest.mark.skipif(not PDF_V7.exists(), reason="缺测试 PDF")
def test_rag_offline():
    parsed = pdf_parser.parse_pdf(PDF_V7)
    store.save_paper(parsed, str(PDF_V7), paper_id="attn_v7")
    result = rag.answer("self-attention 的作用是什么", ["attn_v7"])
    assert result["mode"] in ("offline", "llm", "fallback", "refuse")
    assert isinstance(result["sources"], list)


@pytest.mark.skipif(not PDF_V7.exists(), reason="缺测试 PDF")
def test_graph_baseline():
    parsed = pdf_parser.parse_pdf(PDF_V7)
    store.save_paper(parsed, str(PDF_V7), paper_id="attn_v7")
    g = graph.build_graph(["attn_v7"])
    assert "nodes" in g and "links" in g
    assert g["stats"]["papers"] >= 1
