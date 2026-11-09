"""测试环境隔离：把 DATA_DIR / DB_PATH 指到临时目录，避免污染演示库。

必须在导入 app.* 之前设置，因此放在 conftest 里（pytest 会先加载它）。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.gettempdir()) / "research_copilot_test_data"
_TMP.mkdir(parents=True, exist_ok=True)

os.environ["DATA_DIR"] = str(_TMP)
os.environ["DB_PATH"] = str(_TMP / "app.db")
os.environ["EMBEDDING_PROVIDER"] = "hash"
os.environ.setdefault("LLM_API_KEY", "")
