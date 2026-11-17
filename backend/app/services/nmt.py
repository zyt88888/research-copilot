"""本地离线翻译（Helsinki-NLP/opus-mt-en-zh）。

无需任何 API Key，模型随镜像预置（HF_HOME=/opt/hf）；
若未预置则首次使用时从镜像站下载到 DATA_DIR/hf 并缓存。
模型未就绪时返回 None，由调用方降级，不阻塞页面。
"""
from __future__ import annotations

import os
import re
import threading

from ..settings import DATA_DIR

_MODEL = "Helsinki-NLP/opus-mt-en-zh"
_lock = threading.Lock()
_state = {"tried": False, "ok": False, "error": "", "tok": None, "mdl": None}

# 通用 NMT 对 AI 术语常译错，这里做保守的术语修正（只改明确的多词组合，避免误伤）
_TERMS = (
    ("点产品关注", "点积注意力"), ("点产品注意", "点积注意力"),
    ("添加关注", "加性注意力"), ("添加注意", "加性注意力"),
    ("自主关注", "自注意力"), ("自我关注", "自注意力"),
    ("多头关注", "多头注意力"), ("多头注意", "多头注意力"),
    ("注意机制", "注意力机制"), ("关注机制", "注意力机制"),
    ("闪电注意", "FlashAttention"), ("电磁电磁", "FlashAttention"),
    ("变压器", "Transformer"), ("平铺", "分块"),
    ("记忆", "内存"),
)


def _fix_terms(text: str) -> str:
    for a, b in _TERMS:
        text = text.replace(a, b)
    return text


# 退化输出检测：NMT 在「纯人名列表」「无标点长串」上容易陷入重复循环，
# 典型表现是同一个字/片段被重复几十次（如「伊伊伊伊伊…」）。
_REPEAT_CHAR = re.compile(r"(.)\1{5,}")
_REPEAT_NGRAM = re.compile(r"(.{2,4}?)\1{3,}")


def is_degenerate(source: str, output: str) -> bool:
    """判断译文是否为重复循环 / 长度异常的垃圾输出。空串不算退化（按未翻译处理）。"""
    if not output.strip():
        return False
    if _REPEAT_CHAR.search(output) or _REPEAT_NGRAM.search(output):
        return True
    # 中文译文通常明显短于英文原文，长度暴涨说明模型跑飞了
    return len(output) > max(80, len(source) * 3)


def looks_like_name_list(text: str) -> bool:
    """人名列表 / 参考文献列表：短片段极多、几乎没有完整句子。

    这类输入逐段翻译只会编造并不存在的音译人名（比不译更有误导性），
    因此直接跳过，页面保留英文原文。
    """
    parts = [p for p in re.split(r"[,;]\s*", text or "") if p.strip()]
    if len(parts) < 12:
        return False
    short = sum(1 for p in parts if len(p.split()) <= 4)
    return short / len(parts) >= 0.8


def _generate(tok, mdl, texts: list[str], penalty: float, no_repeat: int) -> list[str]:
    import torch
    enc = tok(texts, return_tensors="pt", padding=True, truncation=True, max_length=512)
    max_new = min(512, max(48, int(max(len(t) for t in texts) * 2)))
    with torch.no_grad():
        ids = mdl.generate(**enc, max_new_tokens=max_new, num_beams=1,
                           repetition_penalty=penalty,
                           no_repeat_ngram_size=no_repeat)
    return tok.batch_decode(ids, skip_special_tokens=True)


def _translate_segments(tok, mdl, src: str) -> str:
    """把长串按标点切成短段分别翻译，绕开列表型输入的重复退化。"""
    parts = [p.strip() for p in re.split(r"[,;.]\s*", src) if p.strip()][:24]
    if len(parts) < 2:
        return ""
    outs = _generate(tok, mdl, [p[:300] for p in parts], 1.4, 3)
    return "，".join(s.strip() for s in outs if s.strip())


def _translate_prose(tok, mdl, src: str) -> str:
    """人名/文献列表型段落：只翻译列表之前的完整句子，丢弃姓名片段。

    这样「写这本书离不开以下同事的帮助，张三，李四…」仍能给出正文译文，
    又不会编造并不存在的音译人名。
    """
    # 冒号后的内容通常是名单：模型会在那里提前结束，产出半截译文，先截掉
    head = re.split(r"[:：]", src, maxsplit=1)[0]
    parts = [p for p in re.split(r"[,;]\s*", head) if p.strip()]
    # 找到列表起点：从这里开始连续出现短片段（人名/文献条目）；至少保留首段
    cut = len(parts)
    for i in range(1, len(parts)):
        tail = parts[i:]
        if len(tail) >= 5 and sum(
                1 for p in tail if len(p.split()) <= 4) / len(tail) >= 0.8:
            cut = i
            break
    prose = ", ".join(parts[:cut]).strip(" ,;")
    if len(prose.split()) < 5:
        return ""
    out = _generate(tok, mdl, [prose[:400]], 1.25, 3)[0].strip()
    out = re.sub(r"[，,、；;：:]+$", "", out)
    return "" if is_degenerate(prose, out) else out


def _ensure_model():
    if _state["tried"]:
        return _state["ok"]
    with _lock:
        if _state["tried"]:
            return _state["ok"]
        _state["tried"] = True
        # 国内直连 huggingface.co 会超时，默认走镜像站
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        os.environ.setdefault("HF_HOME", os.path.join(str(DATA_DIR), "hf"))
        try:
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
            _state["tok"] = AutoTokenizer.from_pretrained(_MODEL)
            mdl = AutoModelForSeq2SeqLM.from_pretrained(_MODEL)
            mdl.eval()
            _state["mdl"] = mdl
            _state["ok"] = True
        except Exception as exc:  # 下载/加载失败不抛出，交给调用方降级
            _state["error"] = str(exc)[:300]
            _state["ok"] = False
    return _state["ok"]


def ready() -> bool:
    return _state["ok"]


def warmup() -> dict:
    _ensure_model()
    return {"ok": _state["ok"], "error": _state["error"]}


def translate(texts: list[str], batch_size: int = 8) -> list[str] | None:
    """把英文段落批量译成中文；模型不可用时返回 None。

    输出做退化检测：重复循环先用更强的重复惩罚重译，
    仍失败则按标点分段翻译，最终仍为垃圾时返回空串（调用方不落库）。
    """
    if not _ensure_model():
        return None

    tok, mdl = _state["tok"], _state["mdl"]
    out: list[str] = []
    try:
        for i in range(0, len(texts), batch_size):
            group = texts[i:i + batch_size]
            results = [""] * len(group)
            namey = [j for j, t in enumerate(group) if looks_like_name_list(t)]
            usable = [j for j, t in enumerate(group) if j not in namey]
            for j in namey:
                # 列表型段落只翻其中的完整句子，不编造人名
                results[j] = _fix_terms(_translate_prose(tok, mdl, group[j]))
            if not usable:
                out.extend(results)
                continue
            batch = [group[j][:1200] or " " for j in usable]
            raw = _generate(tok, mdl, batch, 1.25, 3)
            for j, src, s in zip(usable, batch, raw):
                s = _fix_terms(s.strip())
                if is_degenerate(src, s):
                    # 加重重复惩罚重试一次
                    s = _fix_terms(_generate(tok, mdl, [src], 1.6, 2)[0].strip())
                if is_degenerate(src, s):
                    # 仍退化：按标点切段翻译
                    s = _fix_terms(_translate_segments(tok, mdl, src))
                results[j] = "" if is_degenerate(src, s) else s
            out.extend(results)
    except Exception as exc:
        _state["error"] = str(exc)[:300]
        return None
    return out
