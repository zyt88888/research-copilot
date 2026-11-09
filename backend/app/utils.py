"""文本处理、MinHash 去重签名、分块等通用工具。"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Iterable

CJK = re.compile(r"[一-鿿]")
# 拉丁字母/数字/希腊字母合并为一个 token（"β1" 保持完整），中文按单字切
WORD = re.compile(r"[A-Za-z0-9_Ͱ-Ͽ]+|[一-鿿]")

# 希腊字母英文名 → 符号（查询扩展用；epsilon 同时给两种写法 ϵ/ε）
GREEK_NAMES = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ",
    "epsilon": "ε ϵ", "zeta": "ζ", "eta": "η", "theta": "θ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π",
    "rho": "ρ", "sigma": "σ", "tau": "τ", "phi": "φ ϕ",
    "chi": "χ", "psi": "ψ", "omega": "ω",
}
_GREEK_RE = re.compile(
    r"\b(" + "|".join(GREEK_NAMES) + r")\s*(\d+)?\b", re.I | re.A)


def expand_greek(text: str) -> str:
    """把查询里的 'beta1' 扩展出 'β1'，解决中英文符号不一致导致的检索失效。"""
    extra: list[str] = []
    for name, digits in _GREEK_RE.findall(text or ""):
        symbols = GREEK_NAMES[name.lower()].split()
        for sym in symbols:
            extra.append(sym + (digits or ""))
    return " ".join(extra)


SENT_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])|(?<=[。！？；])\s*")
MATH_HINT = re.compile(r"(\\frac|\\sum|\\int|\$|∑|∫|α|β|γ|δ|θ|λ|μ|σ|ε|∂|∇|=|→|≥|≤)")
REF_HEADING = re.compile(r"^\s*(references|bibliography|参考文献|参考书目)\s*$", re.I)
CAPTION = re.compile(r"^\s*(figure|table|fig\.|tab\.|图|表)\s*(\d+|S\d+)", re.I)
FUTURE_HINT = re.compile(
    r"(future work|future research|limitation|limitations|we leave|open question|"
    r"remains open|in the future|未 来 工 作|局 限|展望)", re.I)


def norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_text(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    return norm_ws(text)


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in WORD.findall(text or "")]


def shingles(text: str, k: int = 5) -> set[str]:
    tokens = tokenize(text)
    if len(tokens) < k:
        return {" ".join(tokens)} if tokens else set()
    return {" ".join(tokens[i:i + k]) for i in range(len(tokens) - k + 1)}


def _hash_int(value: str, seed: int = 0) -> int:
    data = f"{seed}:{value}".encode("utf-8", "ignore")
    return int.from_bytes(hashlib.blake2b(data, digest_size=8).digest(), "big")


NUM_PERM = 64
_MERSENNE = (1 << 61) - 1


def minhash_signature(text: str, num_perm: int = NUM_PERM) -> list[int]:
    """64 个置换的 MinHash 签名，存库用于近似 Jaccard 去重。"""
    sh = shingles(text, 5)
    if not sh:
        return [0] * num_perm
    sig = [_MERSENNE] * num_perm
    for token in sh:
        for i in range(num_perm):
            h = _hash_int(token, i) % _MERSENNE
            if h < sig[i]:
                sig[i] = h
    return sig


def signature_similarity(a: list[int], b: list[int]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    same = sum(1 for x, y in zip(a, b) if x == y)
    return same / len(a)


def exact_content_hash(text: str) -> str:
    norm = normalize_text(text)
    return hashlib.sha256(norm.encode("utf-8", "ignore")).hexdigest()


def safe_filename(name: str) -> str:
    name = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", name)
    return name[:180] or "paper.pdf"


def split_sentences(text: str) -> list[str]:
    text = norm_ws(text)
    if not text:
        return []
    parts = SENT_END.split(text)
    return [p.strip() for p in parts if p.strip()]


def chunk_text(text: str, size: int = 900, overlap: int = 120,
               sentences: list[str] | None = None) -> list[str]:
    """按句子边界切块，块间保留 overlap 字符的上下文，避免切断论证。"""
    if sentences is None:
        sentences = split_sentences(text)
    chunks: list[str] = []
    buf = ""
    for sent in sentences:
        if len(buf) + len(sent) + 1 <= size:
            buf = f"{buf} {sent}".strip()
        else:
            if buf:
                chunks.append(buf)
            buf = sent
    if buf:
        chunks.append(buf)
    if overlap and len(chunks) > 1:
        padded = [chunks[0]]
        for prev, cur in zip(chunks, chunks[1:]):
            tail = prev[-overlap:] if len(prev) > overlap else prev
            padded.append((tail + " " + cur).strip())
        chunks = padded
    return [c for c in chunks if c.strip()]


def bbox_union(boxes: Iterable[list[float]] | None) -> list[float]:
    boxes = [b for b in (boxes or []) if b]
    if not boxes:
        return [0, 0, 0, 0]
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    return [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)]


def is_math_line(text: str) -> bool:
    if not text or len(text) < 4:
        return False
    symbols = len(MATH_HINT.findall(text))
    return symbols >= 3 and symbols / max(len(text), 1) > 0.08


def extract_year(text: str) -> int | None:
    found = [int(m) for m in re.findall(r"\b(19[5-9]\d|20[0-3]\d)\b", text or "")]
    return max(found) if found else None


def snippet(text: str, limit: int = 260) -> str:
    text = norm_ws(text)
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def keyword_overlap(a: str, b: str) -> float:
    ta = {t for t in tokenize(a) if len(t) > 3}
    tb = {t for t in tokenize(b) if len(t) > 3}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / math.sqrt(len(ta) * len(tb))
