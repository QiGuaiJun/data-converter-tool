from __future__ import annotations

"""One-time / repeatable migration of stored connection secrets.

Rewrites legacy "b64:" values in data/imports.db to Fernet-encrypted
"fernet:" values produced by server.encode_secret(). This covers:

- _db_connections.password
- dbPasswordSecret snapshots embedded inside _jobs.steps_json

It is idempotent and safe to run multiple times: values that are already
"fernet:" (or plaintext without a prefix) are left untouched.

Usage:
    .venv/Scripts/python.exe scripts/migrate_password_encryption.py
"""

import os
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server  # noqa: E402


def main() -> int:
    try:
        # Force key resolution now so the reported location is accurate and a
        # missing/uncreatable key file surfaces as a clear error before writing.
        server._get_fernet()
    except Exception as exc:
        print(f"无法初始化加密密钥：{exc}", flush=True)
        print("提示：可设置 DC_MASTER_KEY 环境变量（Fernet key 的 base64 文本）后重试。", flush=True)
        return 1

    db_path = server.DB_PATH
    if not db_path.exists():
        print(f"未找到数据库文件 {db_path}，无需迁移。", flush=True)
        return 0

    print(f"数据库：{db_path}", flush=True)
    print(f"密钥文件：{server.SECRET_KEY_FILE}（存在：{server.SECRET_KEY_FILE.exists()}）", flush=True)
    if os.environ.get("DC_MASTER_KEY", "").strip():
        print("密钥来源：环境变量 DC_MASTER_KEY", flush=True)
    else:
        print("密钥来源：本地密钥文件", flush=True)

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        counts = server.migrate_legacy_secrets(conn)
    finally:
        conn.close()

    print(
        "迁移完成：连接密码 {connections} 条，作业快照 {job_snapshots} 条，错误 {errors} 条。".format(**counts),
        flush=True,
    )
    if counts["errors"]:
        print("存在迁移失败的条目，请检查数据库后再运行一次。", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
