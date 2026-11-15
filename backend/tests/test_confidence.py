"""置信度计算单元测试。

覆盖 rag._confidence 的所有分支：
- 无证据 → 0
- 有证据 + 真实引用 → 高置信度
- 有证据 + 无引用 → 降档 ×0.6
- 虚构引用 dropped → 扣分
- 证据数量少 → 中等
- 边界：始终 ∈ [0, 1]

运行：pytest tests/test_confidence.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.services.rag import _confidence


def _ev(sid: str, score: float = 0.28, dense: float = 0.16) -> dict:
    return {"sid": sid, "score": score, "dense_score": dense}


def test_no_evidence_returns_zero():
    """无证据时置信度必须为 0。"""
    assert _confidence([], set(), []) == 0.0


def test_with_evidence_and_citations_high():
    """有证据 + 有真实引用 → 高置信度（应 > 0.7）。"""
    evidence = [_ev(f"S{i}") for i in range(1, 9)]  # 8 条证据
    used = {"S1", "S2", "S3"}  # 引用了 3 条
    conf = _confidence(evidence, used, [])
    assert conf > 0.7, f"有3条真实引用应高置信度，实际 {conf}"


def test_no_citations_downgraded():
    """有证据但答案无引用 → 降档（×0.6），应明显低于有引用的情况。"""
    evidence = [_ev(f"S{i}") for i in range(1, 9)]
    conf_with = _confidence(evidence, {"S1", "S2", "S3"}, [])
    conf_without = _confidence(evidence, set(), [])
    assert conf_without < conf_with
    # 无引用时降档后不应太高
    assert conf_without < 0.6


def test_dropped_citations_penalized():
    """虚构引用 dropped 越多，置信度越低。"""
    evidence = [_ev(f"S{i}") for i in range(1, 6)]
    used = {"S1", "S2"}
    conf_clean = _confidence(evidence, used, [])
    conf_dirty = _confidence(evidence, used, ["S9", "S10"])
    assert conf_dirty < conf_clean


def test_few_evidence_moderate():
    """证据少（1条）+ 有引用 → 中等置信度，不应过高也不应过低。"""
    evidence = [_ev("S1", score=0.3)]
    used = {"S1"}
    conf = _confidence(evidence, used, [])
    assert 0.3 < conf < 0.85


def test_confidence_in_unit_range():
    """置信度始终在 [0, 1] 区间内。"""
    # 极端情况：超多证据 + 超高分数
    evidence = [_ev(f"S{i}", score=1.0, dense=1.0) for i in range(1, 20)]
    used = {f"S{i}" for i in range(1, 20)}
    conf = _confidence(evidence, used, [])
    assert 0.0 <= conf <= 1.0

    # 极端情况：超多 dropped
    conf2 = _confidence(evidence, used, [f"S{i}" for i in range(100, 120)])
    assert 0.0 <= conf2 <= 1.0


def test_compare_mode_confidence():
    """多论文对比场景：多篇论文证据合并后有引用 → 合理置信度。"""
    # 模拟 compare 返回的 all_evidence（来自 2 篇论文各 3 条）
    evidence = [_ev(f"S{i}", score=0.25) for i in range(1, 7)]
    used = {"S1", "S4"}  # 两篇各引用 1 条
    conf = _confidence(evidence, used, [])
    assert conf > 0.4, f"对比模式有引用应给合理置信度，实际 {conf}"


def test_all_citations_used_max_coverage():
    """答案引用了全部（≥5 条）证据 → 覆盖率满分，置信度最高档。"""
    evidence = [_ev(f"S{i}", score=0.3) for i in range(1, 6)]
    used = {f"S{i}" for i in range(1, 6)}
    conf = _confidence(evidence, used, [])
    # 5 条证据 + 5 条引用 + 分数 0.3 → 接近满分
    assert conf > 0.85
