"""Agent 离线归纳逻辑单元测试。

覆盖 agent.py 中为「离线回答无逻辑」问题所做的修复：
- _extract_keywords / _relevance：关键词提取与相关性打分
- _looks_like_section_header：章节标题识别（避免把 "12.7.2 Masked self-attention" 当结论）
- _extract_conclusion：从 RAG answer 中提取 **综合结论** 段落
- _offline_summary：结论优先、证据过滤排序、对比段落、条数上限
- _offline_plan：compare_papers 传 aspect、answer_question 关键词命中

运行：pytest tests/test_agent_offline.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.agent import (
    _extract_conclusion,
    _extract_keywords,
    _looks_like_section_header,
    _offline_plan,
    _offline_summary,
    _relevance,
)


# ---------- 关键词提取 ----------

def test_extract_keywords_removes_stopwords():
    """停用词（对比/区别/如何等）应被剔除，技术词保留。"""
    kws = _extract_keywords("对比 self-attention 和 cross-attention 的区别")
    # 连字符词按英文单词切分（对 BM25 匹配更友好）
    assert {"self", "attention", "cross"} <= kws
    assert "对比" not in kws
    assert "区别" not in kws
    # 中文分词噪音（如「的区别」）不应进入关键词
    assert not any("区别" in w for w in kws)


def test_extract_keywords_case_insensitive():
    """英文关键词统一小写。"""
    kws = _extract_keywords("Transformer 的 Attention 机制")
    assert "transformer" in kws
    assert "attention" in kws


def test_extract_keywords_empty():
    """全是停用词时返回空集合（不报错）。"""
    assert _extract_keywords("什么 怎么 如何") == set()


# ---------- 相关性 ----------

def test_relevance_counts_overlap():
    kws = {"attention", "transformer"}
    assert _relevance("This paper uses attention inside a transformer.", kws) == 2
    assert _relevance("nothing related here", kws) == 0


def test_relevance_without_keywords_returns_one():
    """关键词为空时不参与过滤，统一给 1。"""
    assert _relevance("任意文本", set()) == 1


# ---------- 章节标题识别 ----------

def test_section_header_with_numbering():
    assert _looks_like_section_header("12.7.2 Masked self-attention") is True
    assert _looks_like_section_header("3.1 Model Architecture") is True


def test_section_header_with_caption_prefix():
    assert _looks_like_section_header("Figure 3: Scaled dot-product attention") is True
    assert _looks_like_section_header("Table 2 Results") is True


def test_section_header_all_caps_short():
    assert _looks_like_section_header("ATTENTION") is True


def test_normal_sentence_is_not_header():
    text = ("The Transformer allows BERT to model many downstream tasks by "
            "swapping out the appropriate inputs and outputs.")
    assert _looks_like_section_header(text) is False


# ---------- 结论提取 ----------

def test_extract_conclusion_prefers_marked_block():
    answer = ("**综合结论**\nTransformer 的核心创新是自注意力机制。\n\n"
              "**支撑证据**\n1. 某论文第 3 页")
    assert _extract_conclusion(answer) == "Transformer 的核心创新是自注意力机制。"


def test_extract_conclusion_skips_section_header():
    """结论段只有章节标题时，应继续向下找有意义的句子。"""
    answer = ("**综合结论**\n12.7.2 Masked self-attention\n\n"
              "Self-attention lets each position attend to all positions "
              "in the previous layer while computing the representation.")
    got = _extract_conclusion(answer)
    assert got.startswith("Self-attention lets each position")


def test_extract_conclusion_empty_answer():
    assert _extract_conclusion("") == ""


def test_extract_conclusion_no_marker_fallback():
    """没有 **综合结论** 标记时，回退到第一行有效正文（跳过标题行）。"""
    answer = "# 标题\n\n12.7.2 Masked self-attention\n\n真正的内容在这里，足够长的一句话。"
    assert _extract_conclusion(answer) == "真正的内容在这里，足够长的一句话。"


# ---------- 离线总结 ----------

LONG_TEXT = ("The Transformer allows BERT to model many downstream tasks by swapping "
             "out the appropriate inputs and outputs, which makes fine-tuning straightforward.")


def _obs_answer_answer(conclusion: str = "BERT 通过自注意力统一建模单句与句对任务。") -> dict:
    return {
        "tool": "answer_question",
        "answer": f"**综合结论**\n{conclusion}\n\n**支撑证据**\n1. …",
        "sources": [
            {"sid": "S1", "paper_title": "BERT", "page": 5,
             "section": "3.1 Fine-tuning", "text": LONG_TEXT},
            {"sid": "S2", "paper_title": "BERT", "page": 6,
             "section": "12.7.2 Masked self-attention", "text": ""},
        ],
    }


def test_summary_puts_conclusion_before_evidence():
    """输出顺序必须：综合结论 → 支撑证据。"""
    out = _offline_summary("Transformer 的核心创新是什么", [_obs_answer_answer()])
    assert out.index("**综合结论**") < out.index("**支撑证据**")
    assert "BERT 通过自注意力" in out


def test_summary_replaces_header_conclusion_with_evidence():
    """结论段是章节标题时，改用证据正文作为结论。"""
    obs = _obs_answer_answer(conclusion="12.7.2 Masked self-attention")
    out = _offline_summary("self-attention 如何工作", [obs])
    conclusion_block = out.split("**综合结论**")[1].split("**支撑证据**")[0]
    assert "12.7.2" not in conclusion_block
    assert "self-attention" in conclusion_block.lower() or "transformer" in conclusion_block.lower()


def test_summary_filters_short_and_header_evidence():
    """支撑证据中不出现纯标题或过短片段。"""
    obs = {
        "tool": "search_library",
        "items": [
            {"text": "ATTENTION", "page": 1, "paper_title": "X"},
            {"text": "12.7.2 Masked self-attention", "page": 2, "paper_title": "X"},
            {"text": LONG_TEXT, "page": 3, "paper_title": "Attention Is All You Need"},
        ],
    }
    out = _offline_summary("attention 机制是什么", [obs])
    evidence_block = out.split("**支撑证据**")[1]
    assert "ATTENTION" not in evidence_block
    assert "12.7.2 Masked self-attention" not in evidence_block
    assert "Attention Is All You Need" in evidence_block


def test_summary_sorts_evidence_by_relevance():
    """关键词重合多的证据排前面。"""
    obs = {
        "tool": "search_library",
        "items": [
            {"text": "Swin Transformer uses shifted windows and one attention variant here.",
             "page": 1, "paper_title": "Swin"},
            {"text": "Self-attention is the core of Transformer self-attention design here.",
             "page": 2, "paper_title": "Attention"},
        ],
    }
    out = _offline_summary("self-attention", [obs])
    evidence_block = out.split("**支撑证据**")[1]
    assert evidence_block.index("《Attention》") < evidence_block.index("《Swin》")


def test_summary_limits_evidence_to_six():
    items = [{"text": f"self-attention evidence number {i} " + LONG_TEXT,
              "page": i, "paper_title": f"P{i}"} for i in range(1, 12)]
    out = _offline_summary("self-attention", [{"tool": "search_library", "items": items}])
    evidence_block = out.split("**支撑证据**")[1]
    numbered = [ln for ln in evidence_block.split("\n") if ln.strip()[:2].rstrip(".").isdigit()]
    assert len(numbered) == 6


def test_summary_includes_compare_section_when_compare_task():
    """对比类任务且 compare_papers 有结果时，输出对比分析段。"""
    obs = [
        _obs_answer_answer(),
        {"tool": "compare_papers",
         "answer": "**对比概览**\n- 《UDL》…\n- 《BERT》…"},
    ]
    out = _offline_summary("对比 self-attention 和 cross-attention 的区别", obs)
    assert "**对比分析**" in out
    assert "对比概览" in out


def test_summary_without_evidence_still_has_task_and_hint():
    out = _offline_summary("随便问问", [])
    assert "### 任务：随便问问" in out
    assert "提示" in out


# ---------- 计划路由 ----------

def test_plan_compare_passes_aspect_not_empty():
    """修复点：compare_papers 必须带上 aspect，避免默认取前 3 篇无关论文。"""
    task = "对比 self-attention 和 cross-attention 的区别"
    plan = _offline_plan(task)
    compare_steps = [s for s in plan if s["tool"] == "compare_papers"]
    assert len(compare_steps) == 1
    assert compare_steps[0]["args"].get("aspect") == task


def test_plan_core_innovation_routes_to_answer():
    """修复点：'核心创新是什么' 必须命中 answer_question。"""
    plan = _offline_plan("Transformer 的核心创新是什么")
    assert any(s["tool"] == "answer_question" for s in plan)


def test_plan_always_ends_with_final():
    plan = _offline_plan("随便一个问题")
    assert plan[-1]["tool"] == "final"


def test_plan_fallback_when_no_keyword():
    plan = _offline_plan("嗯")
    assert any(s["tool"] == "search_library" for s in plan)


# ---------- 回归：测试暴露后修复的 4 个缺陷 ----------

def test_extract_keywords_drops_chinese_noise_fragment():
    """缺陷1：中文分词噪音「的区别」不得进入关键词。"""
    kws = _extract_keywords("两者的区别")
    assert kws == set()


def test_extract_keywords_keeps_valid_chinese_term():
    """反例保护：不含停用词的中文术语必须保留。"""
    kws = _extract_keywords("注意力机制")
    assert "注意力机制" in kws


def test_summary_evidence_not_prefixed_by_section():
    """缺陷2：证据正文不得拼接 section 名，否则长正文会被误判成章节标题而整条丢弃。"""
    out = _offline_summary("Transformer 的核心创新是什么", [_obs_answer_answer()])
    evidence_block = out.split("**支撑证据**")[1]
    # 正文保留
    assert "The Transformer allows BERT" in evidence_block
    # section 名不再混入证据文本
    assert "3.1 Fine-tuning" not in evidence_block


def test_summary_long_body_with_numbered_section_still_kept():
    """缺陷2 的具体表现：正文以 "3.1 ..." 这类编号开头时也不能被当成标题过滤。"""
    obs = {
        "tool": "answer_question",
        "answer": "",
        "sources": [{"sid": "S1", "paper_title": "BERT", "page": 5,
                     "section": "3.1 Fine-tuning", "text": LONG_TEXT}],
    }
    out = _offline_summary("fine-tuning", [obs])
    assert "**支撑证据**" in out
    assert "fine-tuning straightforward" in out


def test_extract_conclusion_skips_evidence_number_line():
    """缺陷3：证据列表的编号行「1. …」不是结论。"""
    answer = ("**综合结论**\n1. …\n\n"
              "Self-attention lets each position attend to all positions in the layer below.")
    got = _extract_conclusion(answer)
    assert got.startswith("Self-attention lets each position")


def test_extract_conclusion_numbered_line_with_content_kept():
    """反例保护：编号行如果本身是完整句子，剥掉编号后仍可作为结论。"""
    answer = "**综合结论**\n1. Self-attention is the only mechanism used in the Transformer block."
    assert _extract_conclusion(answer).startswith("Self-attention is the only mechanism")


def test_summary_keeps_chinese_conclusion_under_30_chars():
    """缺陷4：25 字中文结论不应被「过短」规则替换。"""
    conclusion = "BERT 通过自注意力统一建模单句与句对任务。"
    assert len(conclusion) < 30
    out = _offline_summary("Transformer 的核心创新是什么", [_obs_answer_answer(conclusion)])
    assert conclusion in out


def test_summary_replaces_number_only_conclusion():
    """缺陷4 的另一面：纯碎片「1. …」作为结论时必须回退到证据正文。"""
    out = _offline_summary("Transformer 的核心创新是什么", [_obs_answer_answer("1. …")])
    conclusion_block = out.split("**综合结论**")[1].split("**支撑证据**")[0]
    assert "1. …" not in conclusion_block
    assert "The Transformer allows BERT" in conclusion_block
