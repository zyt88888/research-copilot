"""论文封面缩略图：把 PDF 第一页（或首页）渲染成 PNG，前端用作卡片封面。

特征：
- PyMuPDF 3× 渲染（比 figures 图理解的 2× 更清晰，论文封面有大量文字）
- 按需生成 + 本地磁盘缓存（data/thumbnails/{pid}_cover.png）
- 自动裁剪页眉页脚（上 80px / 下 60px 安全区），更像学术期刊封面
- 扫描件（page_flags 非空）标注扫描态，封面灰度化降低误导
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

from ..settings import DATA_DIR
from .store import paper_meta, resolve_pdf_path

THUMB_DIR = Path(DATA_DIR) / "thumbnails"

# 白名单：只允许这些字符组合的文件名，防止目录穿越
IMG_NAME_RE = r"^[\w\-]+_cover\.png$"


def cover_path(paper_id: str) -> Path:
    """缩略图存储路径（paper_id 不含非法字符才被调用，由路由层白名单保证）。"""
    return THUMB_DIR / f"{paper_id}_cover.png"


def has_cover(paper_id: str) -> bool:
    return cover_path(paper_id).is_file()


def generate(paper_id: str, force: bool = False) -> Path | None:
    """生成封面缩略图（PDF 第一页）。返回路径，失败返回 None。"""
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
            # 安全区：裁掉顶部 ~80pt（页眉/页码）和底部 ~60pt（页脚）
            clip = pymupdf.Rect(
                24,                         # 左边距
                80,                         # 顶部安全区
                rect.x1 - 24,               # 右边距
                max(rect.y0 + 100, rect.y1 - 60)  # 底部安全区
            )
            # 3× 渲染（≈ 300dpi 等效），文本锐利清晰
            pix = page.get_pixmap(clip=clip, matrix=pymupdf.Matrix(3, 3), alpha=False)

            # 扫描件灰度化（降低误导，因为扫描封面看起来像黑白复印件）
            page_flags = (meta.get("page_flags") or {})
            is_scanned = bool(page_flags) and "0" in page_flags or len(page_flags) > 5
            if is_scanned:
                # 简单灰度：用 Pixmap 转灰度再存
                try:
                    from PIL import Image
                    import io
                    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
                    img.save(str(out), "PNG")
                except Exception:
                    pix.save(str(out))
            else:
                pix.save(str(out))

        return out if out.is_file() else None
    except Exception:
        return None
