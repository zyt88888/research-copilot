"""图表理解接口：定位「图 N」、裁剪渲染原图、给出有依据的图意解释。"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ..services import figures

router = APIRouter(prefix="/api/figures", tags=["figures"])

IMG_NAME_RE = re.compile(r"^[\w\-]+_fig\d+\.png$")


# 注意：/img/{name} 必须声明在 /{paper_id}/{n} 之前，
# 否则 "img" 会被当作 paper_id、"xxx.png" 被当作 int 图号而 422
@router.get("/img/{name}")
def figure_image(name: str):
    """读取裁剪后的图（文件名白名单校验，防路径穿越）。"""
    if not IMG_NAME_RE.fullmatch(name or ""):
        raise HTTPException(status_code=400, detail="非法文件名")
    p = figures.FIG_DIR / name
    if not p.is_file():
        raise HTTPException(status_code=404, detail="图不存在")
    return FileResponse(str(p), media_type="image/png")


@router.get("/{paper_id}/{n}")
def figure_detail(paper_id: str, n: int, q: str = Query("")):
    """返回第 n 张图的图注、页码、裁剪图 URL、同页正文与图内 OCR 文本；
    q 非空时额外给出基于这些依据的图意解释。"""
    if n < 1 or n > 99:
        raise HTTPException(status_code=400, detail="图号超出范围")
    if q.strip():
        out = figures.answer_figure(q, paper_id, n)
    else:
        out = figures.render(paper_id, n)
    if not out:
        raise HTTPException(status_code=404, detail=f"未定位到图 {n}")
    return out
