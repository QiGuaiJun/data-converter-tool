"""脚本共用的环境加载：像 systemd 的 EnvironmentFile 那样先读应用的 .env。

**为什么每个离线脚本都必须先调用它**：云端部署把 `DATA_DIR` 指到
`/opt/dc/runtime/data`（在应用目录**之外**）。直接在 shell 里跑脚本时这些变量不存在，
`server.DB_PATH` 会算成 `ROOT/runtime/data/imports.db` —— 那是另一个库（通常是空的），
表现为"数据不见了/账号不存在"，很容易误判成数据被删。

`reset_password.py` 与 `normalize_run_logs.py` 都从这里取，避免各写一份再漂移。
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env_file() -> str | None:
    """把 .env 里的键值补进 os.environ（已存在的以环境为准），返回实际用到的文件路径。"""
    for candidate in (os.environ.get("DC_ENV_FILE"), ROOT / ".env"):
        if not candidate:
            continue
        path = Path(candidate)
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
        return str(path)
    return None
