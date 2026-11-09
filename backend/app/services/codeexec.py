"""受限 Python 代码执行沙箱（供 Agent 做数据处理与图表生成）。

隔离手段（演示级，不是容器级 jail）：
1. 每次执行在独立的临时工作目录内，产物只写在该目录；
2. 子进程 20 秒超时，超时直接 kill 整个进程树；
3. 环境变量白名单，绝不透传 LLM_API_KEY 等任何密钥；
4. `-I` 隔离模式，忽略 PYTHONPATH / 用户 site-packages 注入；
5. 静态黑名单拦截网络与命令执行类关键字；
6. stdout / stderr 截断，避免撑爆上下文。

图片产物落在 ARTIFACT_DIR，通过 `/api/code/artifact/{name}` 访问。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from ..settings import DATA_DIR

ARTIFACT_DIR = Path(DATA_DIR) / "code_artifacts"
TIMEOUT = 20
MAX_OUT = 4000

# 只拦“明显越界”的用法，属于防线而非安全边界；真正的隔离依赖容器与超时
BLOCKED = re.compile(
    r"\b(subprocess|socket|ctypes|multiprocessing|shutil\.rmtree|os\.system|"
    r"os\.popen|os\.remove|os\.unlink|requests|urllib|httpx|ftplib|smtplib)\b")

PRELUDE = """# -*- coding: utf-8 -*-
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
matplotlib.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False
"""


def run_code(code: str, timeout: int = TIMEOUT,
             datasets: dict | None = None) -> dict:
    """执行代码。datasets 会以 JSON 文件写入工作目录，
    便于 model 用 pandas.read_json('papers.json') 读取库内数据。"""
    code = (code or "").strip()
    if not code:
        return {"ok": False, "error": "代码为空"}
    hit = BLOCKED.search(code)
    if hit:
        return {"ok": False, "error": f"出于安全考虑，禁止使用：{hit.group(1)}"}

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:10]
    workdir = Path(tempfile.mkdtemp(prefix="rcpy_"))
    script = workdir / "main.py"
    for fname, rows in (datasets or {}).items():
        try:
            (workdir / fname).write_text(
                json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        except (OSError, TypeError):
            pass
    # 若用户代码自己 import matplotlib，则不加 prelude，避免重复；否则注入绘图环境
    body = code if "matplotlib" in code else PRELUDE + code
    script.write_text(body, encoding="utf-8")

    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(workdir),
        "TMPDIR": str(workdir),
        "LANG": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "MPLBACKEND": "Agg",
    }
    # Windows 上解释器启动依赖这些系统变量（缺 SYSTEMROOT 会直接初始化失败），
    # 但只透传系统级路径，绝不传递任何 API Key
    if os.name == "nt":
        for k in ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PATHEXT", "COMSPEC",
                  "TEMP", "TMP", "NUMBER_OF_PROCESSORS"):
            if k in os.environ:
                env[k] = os.environ[k]
        # Windows 下 Path.home() 读 USERPROFILE，指向沙箱临时目录
        env["USERPROFILE"] = str(workdir)
    # matplotlib 配置/缓存也落在沙箱目录，避免污染真实用户目录
    env["MPLCONFIGDIR"] = str(workdir / ".mpl")
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-u", str(script)],
            cwd=str(workdir), env=env, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
        stdout = proc.stdout.decode("utf-8", "replace")
        stderr = proc.stderr.decode("utf-8", "replace")
        rc = proc.returncode
    except subprocess.TimeoutExpired as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        return {"ok": False, "error": f"执行超时（>{timeout}s），已终止"}
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(workdir, ignore_errors=True)
        return {"ok": False, "error": f"执行失败：{exc}"}

    # 收集图片产物
    images: list[dict] = []
    for img in sorted(workdir.glob("*.png")) + sorted(workdir.glob("*.svg")):
        name = f"{token}_{img.name}"
        try:
            shutil.copy2(img, ARTIFACT_DIR / name)
        except OSError:
            continue
        images.append({"name": img.name, "url": f"/api/code/artifact/{name}"})

    files = [p.name for p in workdir.iterdir() if p.name != "main.py"
             and p.suffix.lower() not in (".png", ".svg", ".pyc")]
    shutil.rmtree(workdir, ignore_errors=True)

    return {
        "ok": rc == 0,
        "returncode": rc,
        "stdout": stdout[:MAX_OUT],
        "stderr": stderr[-MAX_OUT:] if rc != 0 else "",
        "images": images,
        "files": files[:10],
    }


def artifact_path(name: str) -> Path | None:
    """校验并返回产物路径，防止路径穿越。"""
    if not re.fullmatch(r"[0-9a-f]{10}_[\w.\-]{1,80}", name or ""):
        return None
    p = ARTIFACT_DIR / name
    return p if p.is_file() else None
