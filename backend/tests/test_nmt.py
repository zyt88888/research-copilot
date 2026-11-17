"""NMT 退化输出检测与重译兜底的单元测试（不加载真实模型）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.services import nmt  # noqa: E402


# ---------- is_degenerate ----------

def test_detect_repeated_char_loop():
    """单字重复几十次是典型退化（截图里「伊伊伊伊…」）。"""
    assert nmt.is_degenerate("Avishai Mondal", "伊" * 60)


def test_detect_repeated_ngram_loop():
    assert nmt.is_degenerate("some author list", "伊利伊里" * 8)


def test_normal_translation_not_degenerate():
    src = "FlashAttention is an IO-aware exact attention algorithm."
    out = "FlashAttention 是一种 IO 感知的精确注意力算法。"
    assert not nmt.is_degenerate(src, out)


def test_empty_output_not_degenerate():
    """空串代表「没有译文」，由调用方跳过，不算退化。"""
    assert not nmt.is_degenerate("text", "")


def test_abnormally_long_output_is_degenerate():
    assert nmt.is_degenerate("short", "很长的输出" * 40)


# ---------- 人名列表识别 ----------

def test_detect_name_list():
    """人名列表逐段翻译会编造音译人名，应整体跳过。"""
    src = ", ".join(["Alice Smith", "Bob Jones", "Carol White"] * 5)
    assert nmt.looks_like_name_list(src)


def test_normal_prose_not_name_list():
    src = ("We propose a new attention kernel, which is IO aware, faster, "
           "and reduces memory traffic on long sequences.")
    assert not nmt.looks_like_name_list(src)


def test_translate_name_list_only_returns_empty(monkeypatch):
    """纯人名列表没有可翻的句子：返回空串，且不把整串丢给模型。"""
    _patch_model(monkeypatch)
    calls = []
    monkeypatch.setattr(nmt, "_generate",
                        lambda tok, mdl, texts, penalty, no_repeat:
                        calls.append(list(texts)) or ["张三" for _ in texts])
    src = ", ".join(["Alice Smith", "Bob Jones", "Carol White"] * 5)
    assert nmt.translate([src]) == [""]
    assert calls == [], "纯人名列表不应进入模型生成"


def test_translate_name_list_keeps_prose_sentence(monkeypatch):
    """带正文的人名列表：正文句子要翻译，姓名片段丢弃。"""
    _patch_model(monkeypatch)
    seen = []

    def fake_generate(tok, mdl, texts, penalty, no_repeat):
        seen.extend(texts)
        return ["写这本书离不开许多同事和朋友的慷慨帮助。" for _ in texts]

    monkeypatch.setattr(nmt, "_generate", fake_generate)
    src = ("Writing this book would not have been possible without the generous help "
           "of the following people, Alice Smith, Bob Jones, Carol White, Dave Brown, "
           "Eve Green, Frank Black, Grace Hall, Henry King, Ivy Lee, Jack Ma, "
           "Kate Ng, Leo Park, Mona Ray")
    assert nmt.looks_like_name_list(src)
    out = nmt.translate([src])[0]
    assert "写这本书" in out
    assert seen and "generous help" in seen[0], "应翻译列表之前的完整句子"
    assert all("Alice" not in t for t in seen), "姓名片段不应送去翻译"


def test_translate_name_list_cuts_at_colon(monkeypatch):
    """名单前的冒号要截断：否则模型在名单处提前结束，产出半截译文。"""
    _patch_model(monkeypatch)
    seen = []

    def fake_generate(tok, mdl, texts, penalty, no_repeat):
        seen.extend(texts)
        return ["如果没有许多同事和朋友的慷慨帮助，这本书是不可能写成的。" for _ in texts]

    monkeypatch.setattr(nmt, "_generate", fake_generate)
    src = ("Writing this book would not have been possible without the generous help and "
           "advice of these individuals: Kathryn Hume, Kevin Murphy, Christopher Bishop, "
           "Peng Xu, Yann Dubois, Justin Domke, Chris Fletcher, Yanshuai Cao, Wendy Tay, "
           "Corey Toler-Franklin, Dmytro Mishkin, Guy McCusker, Daniel Worrall")
    assert nmt.looks_like_name_list(src)
    out = nmt.translate([src])[0]
    assert "这本书" in out
    assert seen and "Kathryn Hume" not in seen[0], "冒号后的名单不应送去翻译"


# ---------- translate 兜底链路 ----------

def _patch_model(monkeypatch):
    monkeypatch.setattr(nmt, "_ensure_model", lambda: True)
    monkeypatch.setitem(nmt._state, "tok", object())
    monkeypatch.setitem(nmt._state, "mdl", object())


def test_translate_falls_back_to_segments(monkeypatch):
    """首次生成退化 → 加强惩罚重试仍退化 → 按标点切段翻译成功。"""
    _patch_model(monkeypatch)

    def fake_generate(tok, mdl, texts, penalty, no_repeat):
        if max(len(t) for t in texts) < 12:      # 切段后的短人名，正常翻译
            return ["张三" for _ in texts]
        return ["伊" * 90 for _ in texts]        # 长串一律退化

    monkeypatch.setattr(nmt, "_generate", fake_generate)
    out = nmt.translate(["Alice, Bob, Carol"])
    assert out == ["张三，张三，张三"]


def test_translate_drops_output_when_all_fallbacks_degenerate(monkeypatch):
    """所有兜底都退化时返回空串，调用方不会把垃圾译文落库。"""
    _patch_model(monkeypatch)
    monkeypatch.setattr(nmt, "_generate",
                        lambda tok, mdl, texts, penalty, no_repeat:
                        ["伊" * 90 for _ in texts])
    out = nmt.translate(["Alice, Bob, Carol"])
    assert out == [""]


def test_translate_returns_none_when_model_unavailable(monkeypatch):
    monkeypatch.setattr(nmt, "_ensure_model", lambda: False)
    assert nmt.translate(["hello"]) is None
