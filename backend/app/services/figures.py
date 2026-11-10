"""多模态图理解：定位论文中的「图 N」→ 裁剪渲染原图 → 结合图注/正文/OCR 给出有依据的解释。

链路：
1. 从用户问题解析图号（"图 3" / "Figure 2" / "Fig. 1"）；
2. 在解析产物中定位图块（kind=figure，bbox 即图像区域）或图注块（kind=caption）；
3. 用 PyMuPDF 按 bbox 裁剪渲染原图为 PNG（矢量图同样可渲染）；
4. 无文本层的图用 PaddleOCR 补识别图内文字；
5. 回答只基于图注 + 同页正文 + 图内 OCR 文本，不臆测图的内容。
"""
from __future__ import annotations

import re
from pathlib import Path

import pymupdf

from ..settings import DATA_DIR
from ..utils import snippet
from . import llm
from .store import chunks_of, paper_meta

FIG_DIR = Path(DATA_DIR) / "figures"

FIG_QUERY_RE = re.compile(
    r"(?:fig(?:ure)?s?\.?\s*(\d+))|(?:图\s*(\d+))", re.IGNORECASE)
FIG_HEAD_RE = re.compile(
    r"^\s*(?:fig(?:ure)?\.?\s*(\d+)[.:：\s]|图\s*(\d+)[.:：\s])", re.IGNORECASE)


def figure_numbers(query: str) -> list[int]:
    """从问题中抽取图号，如「图 3 展示了什么」「What does Figure 2 show」。"""
    out = []
    for m in FIG_QUERY_RE.finditer(query or ""):
        n = int(m.group(1) or m.group(2))
        if n not in out:
            out.append(n)
    return out


def _fig_no(text: str) -> int | None:
    m = FIG_HEAD_RE.match(text or "")
    return int(m.group(1) or m.group(2)) if m else None


def locate(paper_id: str, n: int) -> dict | None:
    """定位第 n 张图的图块（优先带图注文本的 figure 块）。"""
    figs = chunks_of(paper_id, kinds=("figure", "caption"))
    candidates = [c for c in figs if _fig_no(c.get("text", "")) == n]
    if candidates:
        # 优先 figure 块（bbox 是图像区域，可直接裁剪）
        candidates.sort(key=lambda c: 0 if c["kind"] == "figure" else 1)
        return candidates[0]
    # 退化：图块没有编号文本（OCR 或图注未关联），按出现顺序取第 n 个
    ordered = [c for c in figs if c["kind"] == "figure" and c.get("text") != "[Figure]"]
    if len(ordered) >= n:
        return ordered[n - 1]
    plain = [c for c in figs if c["kind"] == "figure"]
    return plain[n - 1] if len(plain) >= n else None


def render(paper_id: str, n: int) -> dict | None:
    """裁剪渲染第 n 张图，返回图注/页码/图片 URL/上下文/图内 OCR 文本。"""
    meta = paper_meta(paper_id)
    if not meta:
        return None
    chunk = locate(paper_id, n)
    if not chunk:
        return None
    from .store import resolve_pdf_path
    path = resolve_pdf_path(meta)
    if not path:
        return {"n": n, "page": chunk["page"], "caption": chunk.get("text") or "",
                    "image_url": None, "context": [], "ocr_text": "",
                    "error": "原始 PDF 不在本地，无法裁剪图像"}

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    out = FIG_DIR / f"{paper_id}_fig{n}.png"
    page_no = int(chunk["page"])
    caption = "" if chunk.get("text") == "[Figure]" else (chunk.get("text") or "")

    with pymupdf.open(path) as doc:
        page = doc[page_no - 1]
        rect = page.rect
        x0, y0, x1, y1 = chunk["bbox"]
        clip = pymupdf.Rect(max(0, x0 - 6), max(0, y0 - 6),
                            min(rect.x1, x1 + 6), min(rect.y1, y1 + 6))
        if chunk["kind"] == "caption":
            # 图注块：图通常在图注上方，向上扩展一个版面高度裁剪
            clip = pymupdf.Rect(36, max(0, y0 - 420), rect.x1 - 36, y1 + 6)
        if clip.height < 20 or clip.width < 20:
            clip = pymupdf.Rect(36, 36, rect.x1 - 36, rect.y1 * 0.6)
        pix = page.get_pixmap(clip=clip, matrix=pymupdf.Matrix(2, 2))
        pix.save(str(out))

    # 同页正文段落作为解释依据
    context = [snippet(c["text"], 220)
               for c in chunks_of(paper_id, kinds=("paragraph",))
               if int(c["page"]) == page_no and len(c.get("text", "")) >= 40][:3]

    # 图内文字 OCR（可选）：无文本层的扫描图也能读出坐标轴与标注
    ocr_text = ""
    try:
        from .pdf_parser import ocr_image  # 延迟导入，避免无 OCR 环境报错
        ocr_text = snippet(ocr_image(out) or "", 300)
    except Exception:
        ocr_text = ""

    return {"n": n, "page": page_no, "caption": snippet(caption, 400),
            "image_url": f"/api/figures/img/{out.name}",
            "context": context, "ocr_text": ocr_text}


def answer_figure(question: str, paper_id: str, n: int) -> dict | None:
    """回答「图 N 展示了什么」：只依据图注、同页正文与图内 OCR 文本。"""
    fig = render(paper_id, n)
    if not fig:
        return None
    if fig.get("error"):
        return fig
    parts = [f"图注：{fig['caption']}" if fig["caption"] else ""]
    parts += [f"同页正文：{t}" for t in fig["context"]]
    if fig["ocr_text"]:
        parts.append(f"图内识别文字：{fig['ocr_text']}")
    evidence = "\n".join(p for p in parts if p)
    if llm.is_available() and evidence.strip():
        try:
            text = llm.chat([
                {"role": "system", "content":
                 "你是论文图表解读助手。只能依据给出的图注、同页正文与图内识别文字回答，"
                 "不要臆测图的具体内容；依据不足就明说。用中文简洁回答，2-4 句。"},
                {"role": "user", "content": f"问题：{question}\n\n依据：\n{evidence}"},
            ], temperature=0, max_tokens=300)
            fig["explanation"] = text.strip()
        except Exception:
            fig["explanation"] = ""
    if not fig.get("explanation"):
        base = fig["caption"] or (fig["context"][0] if fig["context"] else "")
        fig["explanation"] = (f"图 {n}（第 {fig['page']} 页）：{base}" if base
                              else f"已定位图 {n}（第 {fig['page']} 页），但未提取到可用文字依据。")
    return fig
