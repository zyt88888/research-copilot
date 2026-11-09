"""代码执行沙箱接口：/api/code/run 与产物下载。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..services import codeexec

router = APIRouter(prefix="/api/code", tags=["code"])


class CodeReq(BaseModel):
    code: str
    timeout: int = 20


@router.post("/run")
def run(req: CodeReq):
    """执行一段 Python（pandas/numpy/scipy/matplotlib 可用），返回 stdout 与图片产物。"""
    res = codeexec.run_code(req.code, timeout=max(3, min(req.timeout, 30)))
    return res


@router.get("/artifact/{name}")
def artifact(name: str):
    p = codeexec.artifact_path(name)
    if not p:
        raise HTTPException(status_code=404, detail="产物不存在")
    return FileResponse(p)
