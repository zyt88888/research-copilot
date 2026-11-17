"""文库删除 / 清空功能的单元测试（临时 DB，不触碰演示库）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app import db, settings  # noqa: E402
from app.services import store  # noqa: E402


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    """把 DB 与 data 目录整体重定向到临时目录。"""
    data = tmp_path / "data"
    (data / "uploads").mkdir(parents=True)
    monkeypatch.setattr(db, "DB_PATH", str(data / "app.db"))
    monkeypatch.setattr(settings, "DATA_DIR", data)
    db.init_db()
    return data


def add_paper(data: Path, pid: str, fname: str | None = "paper.pdf",
              create_file: bool = True) -> Path | None:
    """插入一篇论文及其分块 / 参考文献 / 译文，返回 PDF 路径。"""
    pdf = None
    if fname and create_file:
        pdf = data / "uploads" / f"{pid}_{fname}"
        pdf.write_bytes(b"%PDF-1.4 fake")
    db.execute(
        "INSERT INTO papers(id,title,filename,path,num_pages,created_at) "
        "VALUES(?,?,?,?,?,?)",
        (pid, f"Title {pid}", fname or "", str(pdf) if pdf else "", 2,
         "2026-01-01T00:00:00+00:00"))
    db.execute("INSERT INTO chunks(id,paper_id,page,text) VALUES(?,?,?,?)",
               (f"{pid}:c1", pid, 1, "hello world"))
    db.execute("INSERT INTO refs(paper_id,raw) VALUES(?,?)", (pid, "[1] ref"))
    db.execute("INSERT INTO translations(paper_id,chunk_id,lang,text) VALUES(?,?,?,?)",
               (pid, f"{pid}:c1", "zh", "你好世界"))
    return pdf


def count(table: str) -> int:
    return db.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]


def test_delete_paper_removes_rows_and_pdf(lib):
    pdf = add_paper(lib, "p1")
    assert pdf and pdf.is_file()

    store.delete_paper("p1")

    assert store.paper_meta("p1") is None
    assert count("chunks") == 0
    assert count("refs") == 0
    assert count("translations") == 0
    assert not pdf.exists(), "删除论文必须同时清理磁盘上的 PDF"


def test_delete_paper_removes_cover_thumbnail(lib):
    add_paper(lib, "p2")
    cover = lib / "thumbnails" / "p2_cover.png"
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(b"png")

    store.delete_paper("p2")

    assert not cover.exists(), "封面缓存也应一并删除"


def test_delete_paper_keeps_other_papers(lib):
    pdf1 = add_paper(lib, "p1")
    pdf2 = add_paper(lib, "p2")

    store.delete_paper("p1")

    assert [p["id"] for p in store.list_papers()] == ["p2"]
    assert pdf2 and pdf2.is_file(), "删一篇不能影响另一篇的 PDF"
    assert count("chunks") == 1


def test_delete_paper_without_file_does_not_raise(lib):
    """记录存在但 PDF 已丢失（换机器/手工清理）时，删除应正常完成。"""
    add_paper(lib, "p3", create_file=False)

    store.delete_paper("p3")

    assert store.paper_meta("p3") is None


def test_delete_paper_unknown_id_is_noop(lib):
    add_paper(lib, "p1")
    store.delete_paper("not-exist")
    assert count("papers") == 1


def test_delete_all_papers_clears_everything(lib):
    pdfs = [add_paper(lib, f"p{i}") for i in range(3)]

    deleted = store.delete_all_papers()

    assert deleted == 3
    for t in ("papers", "chunks", "refs", "translations"):
        assert count(t) == 0, f"{t} 应被清空"
    assert all(not p.exists() for p in pdfs if p), "所有 PDF 都应删除"
    assert store.list_papers() == []


def test_delete_all_on_empty_library_returns_zero(lib):
    assert store.delete_all_papers() == 0


def test_delete_bumps_index_and_graph_version(lib):
    """删除后检索索引与图谱缓存必须失效。"""
    add_paper(lib, "p1")
    v0, g0 = store._version, db.query(
        "SELECT COALESCE(MAX(version),0) AS v FROM graph_cache")[0]["v"]

    store.delete_paper("p1")

    assert store._version > v0
    g1 = db.query("SELECT COALESCE(MAX(version),0) AS v FROM graph_cache")[0]["v"]
    assert g1 > g0
