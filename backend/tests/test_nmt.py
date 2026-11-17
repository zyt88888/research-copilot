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


# ---------- is_degenerate 边界阈值 ----------

def test_degenerate_exactly_at_repeat_threshold():
    """正则 (.)\\1{5,} 要求：捕获一个字符 + 再重复 ≥5 次 → 总共 ≥6 个连续相同字符。"""
    assert nmt.is_degenerate("some source", "伊" * 6)


def test_not_degenerate_below_repeat_threshold():
    """5 个连续（1+4）还没到 {5,} 的 6 个阈值，不算退化。"""
    assert not nmt.is_degenerate("some source", "伊" * 5)


def test_degenerate_length_exactly_at_boundary():
    """长度刚好等于 max(80, len(src)*3) 不算退化，必须严格大于。用乱码避免被重复 ngram 规则误判。"""
    src = "abc" * 30  # 90 字符，len*3 = 270
    # pool 有 21 个字符：21*13=273，截到 270
    pool = "的一是在了不和有大这主中人上们为被以我动他"
    out = (pool * 13)[:270]
    assert len(out) == 270
    assert not nmt.is_degenerate(src, out)


def test_degenerate_length_strictly_above_is_degenerate():
    """长度严格大于阈值才算退化。"""
    src = "abc" * 30  # 90，阈值 270
    pool = "的一是在了不和有大这主中人上们为被以我动他"
    out = (pool * 14)[:271]  # 271 > 270
    assert nmt.is_degenerate(src, out)


# ---------- looks_like_name_list 边界 ----------

def test_name_list_below_min_parts_not_detected():
    """少于 12 段直接返回 False，哪怕全是短片段。"""
    src = ", ".join(["Alice Smith"] * 11)  # 11 段，不够 12
    assert not nmt.looks_like_name_list(src)


def test_name_list_short_ratio_below_threshold():
    """短片段占比 < 0.8 不算列表。"""
    short_parts = ["Alice Smith"] * 9   # 9 段短
    long_part = ("this is a perfectly normal sentence with many words in it "
                 "to dilute the short fragment ratio")  # 1 段长
    src = ", ".join(short_parts + [long_part])  # 10 短 / 11 总 ≈ 0.82，哦不行
    # 换成精确一点：12 段里 9 短 3 长 → 9/12 = 0.75 < 0.8
    src = ", ".join(short_parts + [long_part] * 3)
    assert len([p for p in src.split(",") if p.strip()]) >= 12
    assert not nmt.looks_like_name_list(src)


# ---------- _translate_prose 边界 ----------

def test_prose_too_short_after_cut_returns_empty(monkeypatch):
    """正文只有 3 个词（cut 后 prose.split() < 5）→ 返回空串，不丢给模型。"""
    _patch_model(monkeypatch)
    called = []
    monkeypatch.setattr(nmt, "_generate",
                        lambda tok, mdl, texts, penalty, no_repeat:
                        called.append(texts) or ["whatever" for _ in texts])
    # 正文极短 + 后面跟着一长串名单
    src = ("To my family: Alice Smith, Bob Jones, Carol White, Dave Brown, "
           "Eve Green, Frank Black, Grace Hall, Henry King, Ivy Lee, Jack Ma, "
           "Kate Ng, Leo Park")
    assert nmt.looks_like_name_list(src)
    out = nmt.translate([src])[0]
    assert out == "", f"短正文应返回空串，实际：{out!r}"
    assert called == [], "短正文不应进入模型生成"


def test_prose_no_name_detected_translates_entire_head(monkeypatch):
    """正文根本不像列表（cut=len(parts)），冒号前整段都送去翻译。"""
    _patch_model(monkeypatch)
    seen = []

    def fake_generate(tok, mdl, texts, penalty, no_repeat):
        seen.extend(texts)
        return ["完整正文译文。" for _ in texts]

    monkeypatch.setattr(nmt, "_generate", fake_generate)
    src = "We present a novel attention mechanism, which is both efficient and accurate: Appendix follows."
    # 这个 src 看起来不像列表（parts 少且句子长）
    assert not nmt.looks_like_name_list(src)
    # 但它会走 usable 路径（不是 namey），直接进 _generate 批量
    out = nmt.translate([src])[0]
    assert "完整正文译文" in out


# ---------- translate 兜底：重试成功分支 ----------

def test_translate_retry_with_stronger_penalty_succeeds(monkeypatch):
    """首次生成退化 → 加强 penalty 后成功，不再走切段兜底。"""
    _patch_model(monkeypatch)
    attempts = []

    def fake_generate(tok, mdl, texts, penalty, no_repeat):
        p = max(len(t) for t in texts)
        attempts.append(penalty)
        # 第一次（penalty=1.25）：长串退化；第二次（penalty=1.6）：成功
        if penalty < 1.5:
            return ["伊" * 90 for _ in texts]
        return ["正常译文" for _ in texts]

    monkeypatch.setattr(nmt, "_generate", fake_generate)
    out = nmt.translate(["This is a test paragraph for retry logic."])
    assert out == ["正常译文"]
    assert 1.25 in attempts and 1.6 in attempts, "应出现两次 generate 调用（原 + 重试）"


# ---------- _translate_segments 边界 ----------

def test_segments_too_few_parts_returns_empty(monkeypatch):
    """切段后不足 2 段 → 返回空串。"""
    _patch_model(monkeypatch)
    # 直接调用内部函数测边界（translate 主函数走 usable 批量，这里测内部）
    tok, mdl = nmt._state["tok"], nmt._state["mdl"]
    # 只有一个逗号且前后都没内容的极端情况
    assert nmt._translate_segments(tok, mdl, "single chunk here") == ""


# ---------- 术语修正 _fix_terms ----------

def test_translate_applies_term_fix(monkeypatch):
    """模型常译错的 AI 术语（如「点产品关注」）会被替换回正确译法。"""
    _patch_model(monkeypatch)

    def fake_generate(tok, mdl, texts, penalty, no_repeat):
        return ["该点产品关注机制提升了性能。" for _ in texts]

    monkeypatch.setattr(nmt, "_generate", fake_generate)
    out = nmt.translate(["The dot-product attention improves performance."])
    assert out == ["该点积注意力机制提升了性能。"]


# ---------- translate 混合 batch ----------

def test_translate_mixed_batch_namey_and_usable(monkeypatch):
    """一个 batch 同时含列表型段落和普通段落，两类各自走对应路径。"""
    _patch_model(monkeypatch)
    calls = []

    def fake_generate(tok, mdl, texts, penalty, no_repeat):
        calls.append(list(texts))
        return ["译文。" for _ in texts]

    monkeypatch.setattr(nmt, "_generate", fake_generate)
    normal = "We propose a new attention mechanism for transformers."
    listy = ("Writing this book would not have been possible: Alice Smith, Bob Jones, "
             "Carol White, Dave Brown, Eve Green, Frank Black, Grace Hall, Henry King, "
             "Ivy Lee, Jack Ma, Kate Ng, Leo Park, Mona Ray")
    out = nmt.translate([normal, listy])
    assert out == ["译文。", "译文。"]
    # 应发生两次 generate：namey 的 prose（先处理）+ usable 的批量（后处理）
    # namey 循环在 usable 批量之前
    assert len(calls) == 2, f"expected 2 generate calls, got {len(calls)}"
    all_texts = [t for call in calls for t in call]
    # 所有 generate 输入中应包含 normal 的完整内容（无截断）
    assert any("attention mechanism" in t for t in all_texts)
    # 但不应包含 "Alice Smith"（prose 路径已在冒号处截断名单）
    assert all("Alice Smith" not in t for t in all_texts)
