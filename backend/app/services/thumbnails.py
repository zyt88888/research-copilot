"""论文封面缩略图（v3：彻底过滤页眉页脚 + 6× 渲染 + 亚像素优化）。

清晰度问题诊断（v2 后仍觉得不清晰的根因）：
- CSS `image-rendering: crisp-edges` 是像素艺术专用，对 PDF 文字反而让每个笔画边缘锯齿化，
  改用 `auto` 让浏览器双三次插值平滑缩放——这是最直接的修复。
- 页眉页脚过滤漏网："June 24, 2022" 这类日期行只有 1–2 行但含逗号，
  旧规则只靠行数判断。
- 5× 渲染下卡片 319px 宽已 ~8× Retina，但 PDF 矢量转位图有亚像素问题，
  升到 6× 让浏览器双三次时更从容。

v3 改进：
- 页脚过滤升级：正则匹配页码/日期/版权/arXiv 编号等页眉页脚特征模式
- 渲染倍率 6×（平均 ~3200px 宽 → 卡片内 10× Retina 密度）
"""
from __future__ import annotations

import re
from pathlib import Path

import pymupdf

from ..settings import DATA_DIR
from .store import paper_meta, resolve_pdf_path

THUMB_DIR = Path(DATA_DIR) / "thumbnails"

IMG_NAME_RE = r"^[\w\-]+_cover\.png$"

# 页脚特征正则（匹配一行的情况：页码、日期、版权、arXiv 编号、机构页眉）
_FOOTER_PATTERNS = [
    re.compile(r"^\s*(\d+|page\s+\d+|p\.\s*\d+)\s*$", re.I),   # 纯页码
    re.compile(r"^\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", re.I),  # 日期开头
    re.compile(r"^\s*copyright|©|\(c\)", re.I),
    re.compile(r"^\s*arxiv[:\-]\s*\d{4}\.\d{4,5}", re.I),
    re.compile(r"^\s*(http|https)://", re.I),
    re.compile(r"^\s*\d{4}\s*$"),                               # 纯年份（极少单独出现）
    re.compile(r"^\s*[\w\.\-\+]+@[\w\-]+\.[\w\.\-]+$"),        # 邮箱（页眉/机构行）
    re.compile(r"^\s*doi[:\-]\s*10\.\d{4,9}/[\S]+$", re.I),
]


def _is_footer_line(txt: str) -> bool:
    """判断单行文本是否是页眉/页脚（命中任一正则就认为是）。"""
    s = txt.strip()
    if not s or len(s) > 80:
        return False
    # 短单行（<= 50 字符）且位于页面顶/底部通常是页眉页脚
    if len(s) <= 50:
        for pat in _FOOTER_PATTERNS:
            if pat.search(s):
                return True
        # 数字或纯数字开头短行，大概率页码
        if re.match(r"^\d{1,3}$", s):
            return True
    return False


def cover_path(paper_id: str) -> Path:
    return THUMB_DIR / f"{paper_id}_cover.png"


def has_cover(paper_id: str) -> bool:
    return cover_path(paper_id).is_file()


def _content_bbox(page: pymupdf.Page) -> pymupdf.Rect | None:
    """返回第一页真正有意义内容（标题+作者+摘要+正文首段）的最小包围盒。

    v4 关键改进（解决 v3 遗留问题）：
    过滤后的 KEEP 块之间若有大 gap（比如标题区 ↔ 摘要之间夹了日期行），
    直接算包围盒会把 gap 里被过滤的内容（日期/arXiv 头部）也包进来。
    修复：按 gap 把 KEEP 块分成密集簇，取最大簇的包围盒——这样日期行所在的
    gap 被排除在 clip 之外。
    """
    rect = page.rect
    blocks = page.get_text("blocks")

    # 第一遍：标记每个块是否 KEEP
    all_blocks = []
    for b in blocks:
        x0,y0,x1,y1,txt,*_ = b
        s = (txt or "").strip()
        single_line = ("\n" not in s); hit = False
        if not s: hit = True
        elif single_line and len(s.strip()) <= 8 and re.match(r"^[-\d\s·•]+$", s.strip()): hit = True
        elif re.search(r"arxiv[:\-]\s*\d{4}\.\d{4,5}", s, re.I): hit = True
        elif single_line and re.match(r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+\d{1,2},\s+\d{4}$", s, re.I): hit = True
        elif single_line and _is_footer_line(s): hit = True
        elif y0 < 50 and len(s) <= 40 and single_line: hit = True
        elif y1 > rect.y1 - 70 and len(s) <= 50 and single_line: hit = True
        elif (x1-x0) < rect.width * 0.08 and len(s) <= 20 and single_line and hit: hit = True
        all_blocks.append((x0,y0,x1,y1, not hit))

    keep = [b for b in all_blocks if b[4]]
    if not keep:
        return None

    # 按 y0 排序后，用 gap > 40pt 分割成密集簇
    keep.sort(key=lambda b: b[1])
    clusters, cur = [[keep[0]]], [keep[0]]
    for b in keep[1:]:
        gap = b[1] - cur[-1][3]   # 当前块 y0 - 上一块 y1
        if gap > 40:
            clusters.append(cur); cur = [b]
        else:
            cur.append(b)
    clusters.append(cur)

    # 选最大簇（元素数优先，范围其次）
    clusters.sort(key=lambda c: (-len(c), -(c[-1][3] - c[0][1])))
    main = clusters[0]
    if len(main) < 2:
        main = keep

    valid_x0 = min(b[0] for b in main)
    valid_y0 = min(b[1] for b in main)
    valid_x1 = max(b[2] for b in main)
    valid_y1 = max(b[3] for b in main)

    # 8pt 内边距
    pad = 8
    bbox = pymupdf.Rect(
        max(rect.x0 + 10, valid_x0 - pad),
        max(rect.y0 + 10, valid_y0 - pad),
        min(rect.x1 - 10, valid_x1 + pad),
        min(rect.y1 - 10, valid_y1 + pad),
    )

    # ===== 3:4 比例对齐 =====
    target_ratio = 0.75
    bw, bh = bbox.x1 - bbox.x0, bbox.y1 - bbox.y0
    cur_ratio = bw / bh
    if cur_ratio > target_ratio * 1.08:
        want_h = bw / target_ratio; delta = (want_h - bh) / 2
        bbox.y0 = max(rect.y0 + 6, bbox.y0 - delta)
        bbox.y1 = min(rect.y1 - 4, bbox.y1 + delta)
    elif cur_ratio < target_ratio / 1.08:
        want_w = bh * target_ratio; delta = (want_w - bw) / 2
        bbox.x0 = max(rect.x0 + 6, bbox.x0 - delta)
        bbox.x1 = min(rect.x1 - 6, bbox.x1 + delta)

    return bbox


def generate(paper_id: str, force: bool = False) -> Path | None:
    """v3：6× 渲染 + 彻底的页眉页脚过滤 + PNG 无损压缩。"""
    out = cover_path(paper_id)
    if out.is_file() and not force:
        return out

    meta = paper_meta(paper_id)
    if not meta:
        return None
    path = resolve_pdf_path(meta)
    if not path:
        return None

    THUMB_DIR.mkdir(parents=True, exist_ok=True)

    try:
        with pymupdf.open(path) as doc:
            if doc.page_count == 0:
                return None
            page = doc[0]
            rect = page.rect

            # 内容驱动裁剪（v3：彻底页眉页脚过滤）
            clip = _content_bbox(page)
            if clip is None:
                clip = pymupdf.Rect(24, 60, rect.x1 - 24, rect.y1 - 40)

            # 6× 渲染：PDF 矢量转位图后 ~3200px 宽，浏览器双三次插值到 319px 更平滑
            pix = page.get_pixmap(clip=clip, matrix=pymupdf.Matrix(6, 6), alpha=False)

            # PIL optimize=True PNG 无损压缩
            try:
                from PIL import Image
                import io
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                page_flags = (meta.get("page_flags") or {})
                is_scanned = len(page_flags) > 5 or bool(page_flags.get("0"))
                if is_scanned:
                    img = img.convert("L")
                    img = img.point(lambda v: int(v * 0.92 + 20))
                img.save(str(out), "PNG", optimize=True)
            except Exception:
                pix.save(str(out))

        return out if out.is_file() else None
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return None
