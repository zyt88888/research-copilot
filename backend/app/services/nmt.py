"""本地离线翻译（Helsinki-NLP/opus-mt-en-zh）。

无需任何 API Key，模型随镜像预置（HF_HOME=/opt/hf）；
若未预置则首次使用时从镜像站下载到 DATA_DIR/hf 并缓存。
模型未就绪时返回 None，由调用方降级，不阻塞页面。
"""
from __future__ import annotations

import os
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
    """把英文段落批量译成中文；模型不可用时返回 None。"""
    if not _ensure_model():
        return None
    import torch

    tok, mdl = _state["tok"], _state["mdl"]
    out: list[str] = []
    try:
        for i in range(0, len(texts), batch_size):
            batch = [t[:1200] or " " for t in texts[i:i + batch_size]]
            enc = tok(batch, return_tensors="pt", padding=True,
                      truncation=True, max_length=512)
            with torch.no_grad():
                ids = mdl.generate(**enc, max_new_tokens=512, num_beams=1)
            out.extend(_fix_terms(s.strip()) for s in tok.batch_decode(ids, skip_special_tokens=True))
    except Exception as exc:
        _state["error"] = str(exc)[:300]
        return None
    return out
