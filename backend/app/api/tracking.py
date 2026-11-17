"""arXiv 动态追踪与个性化推荐接口（赛题模块四）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import tracking

router = APIRouter(prefix="/api/tracking", tags=["tracking"])


class RefreshBody(BaseModel):
    queries: list[str] | None = None
    limit: int = 12


class FeedbackBody(BaseModel):
    track_id: str
    vote: int
    user_id: str = "default"


@router.get("")
def list_(user_id: str = "default", limit: int = 30):
    """按相关度 + 代码可用性 + 个人反馈排序的推荐列表。"""
    return tracking.list_tracks(user_id=user_id, limit=max(1, min(limit, 100)))


@router.get("/status")
def status(user_id: str = "default"):
    return tracking.status(user_id=user_id)


@router.post("/refresh")
def refresh(body: RefreshBody | None = None):
    """立即抓取 arXiv 最新论文；queries 为空时按本地文献库自动提炼主题。"""
    body = body or RefreshBody()
    res = tracking.refresh(queries=body.queries,
                           limit_per_query=max(1, min(body.limit, 30)))
    if res.get("status") == "error":
        raise HTTPException(status_code=502, detail=res.get("message"))
    return res


@router.post("/feedback")
def feedback(body: FeedbackBody):
    """点赞 / 踩 / 取消（vote=1 / -1 / 0）。"""
    return tracking.set_feedback(body.track_id, body.vote, user_id=body.user_id.strip()
                                 or "default")


@router.post("/{track_id}/add")
def add(track_id: str):
    """把追踪到的 arXiv 论文下载并入库。"""
    res = tracking.add_to_library(track_id)
    if res.get("error"):
        raise HTTPException(status_code=404, detail=res["error"])
    if res.get("status") == "duplicate":
        raise HTTPException(status_code=409, detail=res.get("suggestion") or "内容重复")
    return res
