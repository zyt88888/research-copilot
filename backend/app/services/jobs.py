"""上传解析任务表（内存 + 后台线程 + 逐页进度）。

为什么需要：旧实现把 parse_pdf（扫描件要逐页 PaddleOCR）直接写在
`async def upload` 里，会让整个事件循环停住——论文列表、问答、健康检查
全部一起卡死，前端只能一直转圈。改成「上传即返回 job_id，后台线程解析，
前端轮询进度」后，大文件解析期间全站依然可用。

进程内存储即可：单 uvicorn worker + 本地部署，重启丢失任务不影响已入库数据。
"""
from __future__ import annotations

import threading
import time
import uuid

_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}
_TTL = 2 * 3600  # 2 小时后清掉已完成任务


def create(filename: str, save_path: str, paper_id: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _LOCK:
        _JOBS[job_id] = {
            "id": job_id,
            "filename": filename,
            "save_path": save_path,
            "paper_id": paper_id,
            "status": "queued",
            "stage": "排队中",
            "pages_done": 0,
            "pages_total": 0,
            "created_at": time.time(),
            "updated_at": time.time(),
            "message": "",
            "result": None,
            "dup": None,
            "version_diff": None,
        }
    _cleanup()
    return job_id


def get(job_id: str) -> dict | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def update(job_id: str, **fields) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        job.update(fields)
        job["updated_at"] = time.time()


def public(job: dict) -> dict:
    """给前端的视图：隐藏服务端路径与整篇解析结果。"""
    return {
        "job_id": job["id"],
        "filename": job["filename"],
        "status": job["status"],
        "stage": job["stage"],
        "pages_done": job["pages_done"],
        "pages_total": job["pages_total"],
        "message": job.get("message") or "",
        "result": job.get("result"),
        "duplicate": job.get("dup"),
        "version_diff": job.get("version_diff"),
        "elapsed": round(time.time() - job["created_at"], 1),
    }


def _cleanup() -> None:
    now = time.time()
    with _LOCK:
        for jid in [j for j, d in _JOBS.items()
                    if d["status"] in ("done", "duplicate", "error")
                    and now - d["updated_at"] > _TTL]:
            _JOBS.pop(jid, None)
