"""论文封面缩略图 API：/api/thumbnails/{paper_id} 触发生成并返回图片路径，/img/{name} 白名单直出。"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..services import thumbnails
from ..settings import DATA_DIR

router = APIRouter(prefix="/api/thumbnails", tags=["thumbnails"])

IMG_NAME_RE = re.compile(r"^[\w\-]+_cover\.png$")


@router.get("/{paper_id}")
def get_cover(paper_id: str, force: bool = False):
    """触发封面生成（如已生成直接返回路径）。"""
    if not re.match(r"^[\w\-]{1,40}$", paper_id):
        raise HTTPException(400, "paper_id 格式不合法")
    out = thumbnails.generate(paper_id, force=force)
    if not out:
        raise HTTPException(404, "封面生成失败（原始 PDF 不在本地或解析异常）")
    return {"ok": True, "image_url": f"/api/thumbnails/img/{out.name}"}


@router.get("/img/{name}")
def serve_cover(name: str):
    """直出封面图片（文件名白名单，防止目录穿越）。"""
    if not IMG_NAME_RE.match(name):
        raise HTTPException(400, "文件名不合法")
    p = Path(DATA_DIR) / "thumbnails" / name
    if not p.is_file():
        raise HTTPException(404, "封面不存在，请先 GET /api/thumbnails/{paper_id}")
    return FileResponse(p, media_type="image/png")
