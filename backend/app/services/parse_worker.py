"""在独立子进程里解析 PDF。

为什么不在 API 进程里解析：PyMuPDF 逐页版式解析 + PaddleOCR 推理是重 CPU 任务，
放在主进程里会长时间占住 GIL（实测上传一本 541 页扫描书期间，论文列表接口
从 0.2s 退化到 10s，前端表现为「一直在检索」）。用子进程隔离后，API 进程
始终可以正常响应，只有 CPU 资源竞争。

进度：子进程把 {done,total,kind} 写进一个进度文件（先写临时文件再原子重命名，
父进程不会读到半截 JSON），父进程轮询该文件更新任务状态。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import pdf_parser

_THROTTLE = 0.5  # 进度写盘最小间隔（秒）


def parse_to_progress_file(save_path: str, filename: str, progress_file: str) -> dict:
    last_write = [0.0]

    def on_progress(done: int, total: int, kind: str) -> None:
        now = time.time()
        if now - last_write[0] < _THROTTLE:
            return
        last_write[0] = now
        _write(progress_file, {"done": done, "total": total, "kind": kind})

    _write(progress_file, {"done": 0, "total": 0, "kind": "layout"})
    parsed = pdf_parser.parse_pdf(Path(save_path), filename=filename,
                                  on_progress=on_progress)
    _write(progress_file, {"done": parsed.get("num_pages") or 0,
                           "total": parsed.get("num_pages") or 0,
                           "kind": "text"})
    return parsed


def _write(path: str, data: dict) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception:
        pass
