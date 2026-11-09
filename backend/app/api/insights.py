"""学术洞察接口：复现性评估与相关推荐。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..services import insights

router = APIRouter(prefix="/api/insights", tags=["insights"])


@router.get("/reproducibility")
def reproducibility(paper_id: str):
    """抽取论文中的 GitHub 仓库并评估可复现性。"""
    res = insights.reproducibility(paper_id)
    if res.get("error"):
        raise HTTPException(status_code=404, detail=res["error"])
    return res


@router.get("/recommend")
def recommend(paper_id: str, top_k: int = 5):
    """按当前论文的标题+摘要推荐库内相似论文。"""
    res = insights.recommend(paper_id, top_k=max(1, min(top_k, 10)))
    if res.get("error"):
        raise HTTPException(status_code=404, detail=res["error"])
    return res
