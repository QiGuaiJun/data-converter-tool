from __future__ import annotations

import csv
import base64
import datetime as dt
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterable, Iterator
from contextlib import nullcontext
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from itertools import chain
from io import BytesIO
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import msoffcrypto
import pymysql
import xlrd
from cryptography.fernet import Fernet
from dbfread import DBF
from openpyxl import Workbook, load_workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Border, Font, Protection, Side
from openpyxl.utils import get_column_letter
from pypinyin import Style, lazy_pinyin
from xml.sax.saxutils import escape as xml_escape


ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
DOCS = ROOT / "docs"

# Python 3.12 起 sqlite3 内置 datetime/date 默认适配器已弃用（每次写入都会告警），
# 按官方文档推荐显式注册替代：datetime→"YYYY-MM-DD HH:MM:SS"，date→"YYYY-MM-DD"。
# 这同时服务于 dateColumns（dt.date）与导入时间列（dt.datetime）两条写入路径。
sqlite3.register_adapter(dt.datetime, lambda value: value.isoformat(sep=" ", timespec="seconds"))
sqlite3.register_adapter(dt.date, lambda value: value.isoformat())


def env_path(name: str, fallback: Path) -> Path:
    raw_value = os.environ.get(name, "").strip()
    volume_mount = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
    if raw_value:
        path = Path(raw_value)
        if volume_mount and not path.is_absolute():
            return (Path(volume_mount) / path).resolve()
        return path.resolve()
    if volume_mount:
        return (Path(volume_mount) / fallback.name).resolve()
    return fallback.resolve()


# 源码与运行数据同处一个目录（2026-09-17 调整）：运行数据统一落在项目内的
# runtime/ 下，并整体 gitignore —— 整个项目就是**一个文件夹**，拷走即用；
# 同时源码区（server.py / public / tests）与运行数据区仍然泾渭分明，
# runtime/ 单独删掉不影响源码，重新起来会自动重建空目录。
RUNTIME_ROOT = ROOT / "runtime"


def runtime_path(name: str) -> Path:
    """运行数据子目录的默认落点（DATA_DIR / UPLOADS_DIR / EXPORTS_DIR 仍可覆盖）。"""
    return RUNTIME_ROOT / name


DATA = env_path("DATA_DIR", runtime_path("data"))
UPLOADS = env_path("UPLOADS_DIR", runtime_path("uploads"))
EXPORTS = env_path("EXPORTS_DIR", runtime_path("exports"))
# 应用版本号（P3-28）：/api/meta 与页面侧边栏底部展示，发版时改这一处。
APP_VERSION = "1.7.0"
TASK_SOURCES = DATA / "task_sources"
LINKED_SOURCES = DATA / "linked_sources"
DB_PATH = DATA / "imports.db"
# Stored connection passwords / snapshots are Fernet-encrypted and prefixed.
# SECRET_KEY_FILE is generated on first run and stored inside DATA so it stays
# with the database (incl. Railway volume) while remaining OUTSIDE the backup
# zip produced by scripts/create_data_backup.py (that archive only packs
# data/imports.db, never the whole data directory).
FERNET_PREFIX = "fernet:"
LEGACY_B64_PREFIX = "b64:"
PASSWORD_PREFIX = FERNET_PREFIX
SECRET_KEY_FILE = DATA / ".secret_key"

# Directories whose files may be served by /api/export/download (P2-14).
DOWNLOAD_ALLOWED_ROOTS = (EXPORTS, UPLOADS, TASK_SOURCES)

MAX_PREVIEW_ROWS = 20
EXPORT_FETCH_SIZE = 5000
SUPPORTED_EXTENSIONS = {".csv", ".txt", ".xlsx", ".xlsm", ".xls", ".json", ".xml", ".dbf"}


@dataclass
class UploadedFile:
    filename: str
    path: Path


@dataclass
class TabularData:
    columns: list[str]
    rows: list[list[str]]
    sheets: list[str]
    selected_sheet: str


def ensure_dirs() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    EXPORTS.mkdir(parents=True, exist_ok=True)
    TASK_SOURCES.mkdir(parents=True, exist_ok=True)
    LINKED_SOURCES.mkdir(parents=True, exist_ok=True)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    existing = [row["name"] for row in conn.execute(f"pragma table_info({table})").fetchall()]
    if column not in existing:
        conn.execute(f"alter table {table} add column {column} {ddl}")


def connect_db() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma busy_timeout = 30000")
    conn.execute(
        """
        create table if not exists _import_logs (
            id text primary key,
            created_at text not null,
            file_name text not null,
            table_name text not null,
            mode text not null,
            rows_read integer not null default 0,
            rows_written integer not null,
            rows_updated integer not null default 0,
            rows_skipped integer not null default 0,
            status text not null,
            message text not null
        )
        """
    )
    conn.execute(
        """
        create table if not exists _import_checkpoints (
            key text primary key,
            updated_at text not null,
            rows_done integer not null
        )
        """
    )
    # ---- 认证相关（P1，2026-09-22）：用户 / 会话 / 审计 ----
    conn.execute(
        """
        create table if not exists _users (
            id text primary key,
            username text not null unique,
            display_name text not null default '',
            password_hash text not null,
            role text not null default 'viewer',
            enabled integer not null default 1,
            created_at text not null,
            created_by text not null default '',
            last_login_at text,
            failed_count integer not null default 0,
            locked_until text
        )
        """
    )
    conn.execute(
        """
        create table if not exists _sessions (
            id text primary key,
            token_hash text not null unique,
            user_id text not null,
            created_at text not null,
            expires_at text not null,
            last_seen_at text,
            ip text not null default '',
            user_agent text not null default ''
        )
        """
    )
    conn.execute("create index if not exists idx_sessions_token on _sessions(token_hash)")
    conn.execute("create index if not exists idx_sessions_expires on _sessions(expires_at)")
    conn.execute(
        """
        create table if not exists _audit_logs (
            id text primary key,
            created_at text not null,
            user_id text not null default '',
            username text not null default '',
            action text not null,
            target text not null default '',
            detail text not null default '',
            ip text not null default '',
            status text not null default 'ok'
        )
        """
    )
    conn.execute("create index if not exists idx_audit_created on _audit_logs(created_at)")
    conn.execute(
        """
        create table if not exists _db_connections (
            id text primary key,
            name text not null,
            db_type text not null,
            host text not null,
            port integer not null,
            user_name text not null,
            password text not null default '',
            db_name text not null default '',
            charset text not null default 'utf8mb4',
            ssl_enabled integer not null default 0,
            ssl_ca text not null default '',
            ssl_cert text not null default '',
            ssl_key text not null default '',
            created_at text not null,
            updated_at text not null
        )
        """
    )
    conn.execute(
        """
        create table if not exists _jobs (
            id text primary key,
            name text not null,
            enabled integer not null default 1,
            steps_json text not null default '[]',
            guard_json text not null default '{}',
            created_at text not null,
            updated_at text not null
        )
        """
    )
    _ensure_column(conn, "_jobs", "guard_json", "text not null default '{}'")
    conn.execute(
        """
        create table if not exists _schedules (
            id text primary key,
            name text not null,
            job_id text not null,
            enabled integer not null default 0,
            rule_json text not null default '{}',
            start_at text not null default '',
            end_at text not null default '',
            next_run_at text not null default '',
            last_run_at text not null default '',
            last_status text not null default '',
            log_retention_days integer not null default 3,
            email_on_fail integer not null default 0,
            running integer not null default 0,
            created_at text not null,
            updated_at text not null
        )
        """
    )
    conn.execute(
        """
        create table if not exists _job_runs (
            id text primary key,
            job_id text not null,
            schedule_id text not null default '',
            job_name text not null,
            started_at text not null,
            ended_at text not null default '',
            elapsed_ms integer not null default 0,
            status text not null,
            message text not null default '',
            outputs_json text not null default ''
        )
        """
    )
    # P3-B：运行记录也要携带结构化产物清单（outputs_json），前端据此渲染「产出文件」块，
    # 不再靠正则从 message 文本里猜路径。历史记录该列为空 → 前端优雅降级（见 _decode_run_outputs）。
    _ensure_column(conn, "_job_runs", "outputs_json", "text not null default ''")
    conn.execute(
        """
        create table if not exists _job_run_steps (
            id text primary key,
            run_id text not null,
            step_index integer not null,
            step_name text not null,
            step_type text not null,
            started_at text not null,
            ended_at text not null default '',
            elapsed_ms integer not null default 0,
            status text not null,
            message text not null default ''
        )
        """
    )
    conn.execute(
        """
        create table if not exists _saved_queries (
            id text primary key,
            name text not null,
            connection_id text not null default '',
            sql_text text not null,
            created_at text not null,
            updated_at text not null
        )
        """
    )
    for column, ddl in {
        "rows_read": "integer not null default 0",
        "rows_updated": "integer not null default 0",
        "rows_skipped": "integer not null default 0",
    }.items():
        existing = [row["name"] for row in conn.execute("pragma table_info(_import_logs)").fetchall()]
        if column not in existing:
            conn.execute(f"alter table _import_logs add column {column} {ddl}")
    conn.execute(
        """
        create table if not exists _job_file_guards (
            job_id text not null,
            step_index integer not null,
            fingerprint text not null,
            source_text text not null,
            updated_at text not null,
            primary key (job_id, step_index)
        )
        """
    )
    # 执行前预检：定时任务在到期前 precheck_minutes 分钟先做一次「源文件有没有新增」检查，
    # 结论落在 _schedule_prechecks，执行期据此决定「执行 / 整个作业跳过」。
    # due_at 是本轮的 next_run_at —— 用它当"轮次"标识，主键 (schedule_id, due_at) 天然幂等。
    _ensure_column(conn, "_schedules", "precheck_minutes", "integer not null default 5")
    conn.execute(
        """
        create table if not exists _schedule_prechecks (
            schedule_id text not null,
            due_at text not null,
            checked_at text not null,
            has_new integer not null,
            detail_json text not null default '{}',
            primary key (schedule_id, due_at)
        )
        """
    )
    conn.execute(
        """
        create table if not exists _import_file_fingerprints (
            file_key text primary key,
            fingerprint text not null,
            table_name text not null default '',
            updated_at text not null
        )
        """
    )
    _migrate_legacy_secrets_once(conn)
    return conn


# ---------------------------------------------------------------------------
# Secret encryption (P1-4)
# Stored secrets are Fernet-encrypted with a master key that comes from either
# the DC_MASTER_KEY environment variable or an auto-generated local key file.
# ---------------------------------------------------------------------------

# RLock (reentrant): _get_fernet() acquires this lock and may call
# _load_or_create_secret_key(), which acquires the same lock again when the key
# file must be generated on first startup. A plain Lock() would deadlock there.
_SECRET_KEY_LOCK = threading.RLock()
_FERNET_INSTANCE: Fernet | None = None
_MIGRATION_LOCK = threading.Lock()
_MIGRATION_DONE = False


def _load_or_create_secret_key() -> bytes:
    """Return the Fernet master key (base64 text bytes).

    Precedence: DC_MASTER_KEY env var > local key file (auto-generated).
    Unattended startup is preserved: when no key file exists the server simply
    generates one next to the database.
    """
    env_key = os.environ.get("DC_MASTER_KEY", "").strip()
    if env_key:
        return env_key.encode("ascii")

    def _read_existing() -> bytes:
        try:
            data = SECRET_KEY_FILE.read_bytes().strip()
            if data:
                return data
        except OSError:
            pass
        return b""

    existing = _read_existing()
    if existing:
        return existing
    with _SECRET_KEY_LOCK:
        existing = _read_existing()
        if existing:
            return existing
        DATA.mkdir(parents=True, exist_ok=True)
        generated = Fernet.generate_key()
        try:
            SECRET_KEY_FILE.write_bytes(generated)
        except OSError:
            raise RuntimeError(f"无法创建密钥文件 {SECRET_KEY_FILE}，请设置 DC_MASTER_KEY 环境变量。")
        try:
            os.chmod(SECRET_KEY_FILE, 0o600)
        except OSError:
            pass  # Windows: chmod is best-effort.
        return generated


def _get_fernet() -> Fernet:
    global _FERNET_INSTANCE
    if _FERNET_INSTANCE is None:
        with _SECRET_KEY_LOCK:
            if _FERNET_INSTANCE is None:
                _FERNET_INSTANCE = Fernet(_load_or_create_secret_key())
    return _FERNET_INSTANCE


def encode_secret(value: str) -> str:
    if not value:
        return ""
    token = _get_fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return FERNET_PREFIX + token


def decode_secret(value: str) -> str:
    if not value:
        return ""
    if value.startswith(FERNET_PREFIX):
        try:
            return _get_fernet().decrypt(value[len(FERNET_PREFIX) :].encode("ascii")).decode("utf-8")
        except Exception:
            return ""
    if value.startswith(LEGACY_B64_PREFIX):
        # Legacy base64 secrets still decrypt fine (read compatibility). They are
        # upgraded to fernet: by the startup/script migration.
        try:
            return base64.b64decode(value[len(LEGACY_B64_PREFIX) :]).decode("utf-8")
        except Exception:
            return ""
    return value


def _is_legacy_b64(value: object) -> bool:
    return isinstance(value, str) and value.startswith(LEGACY_B64_PREFIX)


def _decode_legacy_b64(value: str) -> str:
    return base64.b64decode(value[len(LEGACY_B64_PREFIX) :]).decode("utf-8")


def migrate_legacy_secrets(conn: sqlite3.Connection) -> dict[str, int]:
    """Rewrite legacy 'b64:' secrets to Fernet. Idempotent - safe to re-run.

    Covers the _db_connections.password column and the dbPasswordSecret
    snapshots embedded in _jobs.steps_json. Returns {'connections', 'job_snapshots'}.
    """
    counts = {"connections": 0, "job_snapshots": 0, "errors": 0}
    try:
        rows = conn.execute("select id, password from _db_connections").fetchall()
    except sqlite3.OperationalError:
        rows = []
    for row in rows:
        stored = row[1] or ""
        if not _is_legacy_b64(stored):
            continue
        try:
            conn.execute(
                "update _db_connections set password = ? where id = ?",
                (encode_secret(_decode_legacy_b64(stored)), row[0]),
            )
            counts["connections"] += 1
        except Exception:
            counts["errors"] += 1
    try:
        job_rows = conn.execute("select id, steps_json from _jobs").fetchall()
    except sqlite3.OperationalError:
        job_rows = []
    for row in job_rows:
        try:
            steps = json.loads(row[1] or "[]")
        except Exception:
            continue
        if not isinstance(steps, list):
            continue
        changed = False
        for step in steps:
            config = step.get("config") if isinstance(step, dict) else None
            if not isinstance(config, dict):
                continue
            secret = config.get("dbPasswordSecret")
            if not _is_legacy_b64(secret):
                continue
            try:
                config["dbPasswordSecret"] = encode_secret(_decode_legacy_b64(secret))
                changed = True
            except Exception:
                counts["errors"] += 1
        if changed:
            conn.execute(
                "update _jobs set steps_json = ? where id = ?",
                (json.dumps(steps, ensure_ascii=False), row[0]),
            )
            counts["job_snapshots"] += 1
    conn.commit()
    return counts


def _migrate_legacy_secrets_once(conn: sqlite3.Connection) -> None:
    """Runs the lightweight startup migration at most once per process.

    Never raises so a migration failure cannot block serving requests.
    """
    global _MIGRATION_DONE
    if _MIGRATION_DONE:
        return
    with _MIGRATION_LOCK:
        if _MIGRATION_DONE:
            return
        try:
            migrate_legacy_secrets(conn)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"WARNING: password-encryption migration skipped: {exc}", flush=True)
            return
        _MIGRATION_DONE = True


def normalize_connection_payload(payload: dict[str, object]) -> dict[str, object]:
    db_type = str(payload.get("dbType") or payload.get("targetDbType") or "mysql").strip().lower()
    if db_type != "mysql":
        raise ValueError("当前连接模块先支持 MySQL，其他数据库会在后续模块开放。")
    host = str(payload.get("host") or payload.get("dbHost") or "").strip()
    user = str(payload.get("user") or payload.get("dbUser") or "").strip()
    if not host:
        raise ValueError("请填写主机。")
    if not user:
        raise ValueError("请填写用户名。")
    name = str(payload.get("name") or payload.get("connectionName") or "").strip()
    database = str(payload.get("database") or payload.get("dbName") or "").strip()
    if not name:
        name = f"MySQL - {host}{('/' + database) if database else ''}"
    return {
        "id": str(payload.get("id") or uuid.uuid4().hex),
        "name": name,
        "db_type": db_type,
        "host": host,
        "port": int(payload.get("port") or payload.get("dbPort") or 3306),
        "user_name": user,
        "password": str(payload.get("password") or payload.get("dbPassword") or ""),
        "db_name": database,
        "charset": str(payload.get("charset") or payload.get("dbCharset") or "utf8mb4").strip() or "utf8mb4",
        "ssl_enabled": 1 if payload.get("sslEnabled") in (True, "true", "1", 1, "on") else 0,
        "ssl_ca": str(payload.get("sslCa") or ""),
        "ssl_cert": str(payload.get("sslCert") or ""),
        "ssl_key": str(payload.get("sslKey") or ""),
    }


def connection_public(row: sqlite3.Row, include_password: bool = False) -> dict[str, object]:
    item = {
        "id": row["id"],
        "name": row["name"],
        "dbType": row["db_type"],
        "host": row["host"],
        "port": row["port"],
        "user": row["user_name"],
        "database": row["db_name"],
        "charset": row["charset"],
        "sslEnabled": bool(row["ssl_enabled"]),
        "sslCa": row["ssl_ca"],
        "sslCert": row["ssl_cert"],
        "sslKey": row["ssl_key"],
        "hasPassword": bool(row["password"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }
    if include_password:
        item["password"] = decode_secret(row["password"])
    return item


def load_saved_connection(connection_id: str) -> dict[str, object]:
    if not connection_id:
        return {}
    with connect_db() as conn:
        row = conn.execute("select * from _db_connections where id = ?", (connection_id,)).fetchone()
    if not row:
        raise ValueError("选择的数据库连接不存在，请重新选择。")
    item = connection_public(row, include_password=True)
    return {
        "targetDbType": item["dbType"],
        "dbHost": item["host"],
        "dbPort": str(item["port"]),
        "dbUser": item["user"],
        "dbPassword": item.get("password", ""),
        "dbName": item["database"],
        "dbCharset": item["charset"],
        "sslEnabled": "true" if item["sslEnabled"] else "false",
        "sslCa": item["sslCa"],
        "sslCert": item["sslCert"],
        "sslKey": item["sslKey"],
    }


def resolve_connection_fields(fields: dict[str, str]) -> dict[str, str]:
    if fields.get("targetDbType", "").strip().lower() == "sqlite":
        merged = dict(fields)
        merged["connectionId"] = ""
        return merged
    connection_id = fields.get("connectionId", "").strip()
    if fields.get("dbPasswordSecret") and not fields.get("dbPassword"):
        fields = dict(fields)
        fields["dbPassword"] = decode_secret(fields.get("dbPasswordSecret", ""))
    if not connection_id:
        return fields
    try:
        saved = load_saved_connection(connection_id)
    except ValueError:
        has_snapshot = any(fields.get(key, "").strip() for key in ["dbHost", "dbName", "dbUser"])
        if has_snapshot:
            merged = dict(fields)
            merged["connectionId"] = ""
            return merged
        raise
    merged = dict(fields)
    merged.update({key: str(value) for key, value in saved.items()})
    return merged


def has_direct_connection_fields(fields: dict[str, str]) -> bool:
    return bool(fields.get("dbHost", "").strip() and fields.get("dbName", "").strip())


def friendly_export_error(exc: Exception, fields: dict[str, str]) -> str:
    message = str(exc)
    if target_db_type(fields) == "sqlite" and "no such table" in message.lower():
        return f"{message}。当前导出任务使用的是本地 SQLite，未连接到业务数据库；请先在“新建连接”保存 MySQL 连接，并打开导出任务重新保存。"
    if "选择的数据库连接不存在" in message:
        return "导出任务保存的数据库连接不存在；请先在“新建连接”保存连接，然后打开导出任务重新保存。"
    return message


def attach_connection_snapshot(config: dict[str, object]) -> dict[str, object]:
    connection_id = str(config.get("connectionId") or "").strip()
    if not connection_id:
        return config
    try:
        saved = load_saved_connection(connection_id)
    except ValueError:
        return config
    snapshot = dict(config)
    password = str(saved.pop("dbPassword", "") or "")
    snapshot.update(saved)
    if password:
        snapshot["dbPasswordSecret"] = encode_secret(password)
        snapshot.pop("dbPassword", None)
    return snapshot


def target_db_type(fields: dict[str, str]) -> str:
    fields = resolve_connection_fields(fields)
    return fields.get("targetDbType", "sqlite").strip().lower() or "sqlite"


def connect_target_db(fields: dict[str, str]):
    fields = resolve_connection_fields(fields)
    if target_db_type(fields) == "mysql":
        ssl_config = None
        if parse_bool(fields, "sslEnabled", False):
            ssl_config = {}
            if fields.get("sslCa"):
                ssl_config["ca"] = fields["sslCa"]
            if fields.get("sslCert"):
                ssl_config["cert"] = fields["sslCert"]
            if fields.get("sslKey"):
                ssl_config["key"] = fields["sslKey"]
        try:
            return pymysql.connect(
                host=fields.get("dbHost", "127.0.0.1"),
                port=parse_int(fields, "dbPort", 3306),
                user=fields.get("dbUser", ""),
                password=fields.get("dbPassword", ""),
                database=fields.get("dbName", ""),
                charset=fields.get("dbCharset", "utf8mb4") or "utf8mb4",
                autocommit=fields.get("commitMode") == "auto",
                local_infile=fields.get("writeMode") == "load",
                ssl=ssl_config,
            )
        except Exception as exc:  # noqa: BLE001
            raise friendly_mysql_error(exc) from exc
    return connect_db()


def db_placeholder(fields: dict[str, str]) -> str:
    return "%s" if target_db_type(fields) == "mysql" else "?"


def db_quote(name: str, fields: dict[str, str]) -> str:
    if target_db_type(fields) == "mysql":
        return "`" + name.replace("`", "``") + "`"
    return '"' + name.replace('"', '""') + '"'


def db_now_sql(fields: dict[str, str]) -> str:
    return "now()" if target_db_type(fields) == "mysql" else "datetime('now', 'localtime')"


def fetch_all_dicts(cursor) -> list[dict[str, object]]:
    columns = [desc[0] for desc in cursor.description or []]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def mysql_connection_args(config: dict[str, object], include_database: bool = True) -> dict[str, object]:
    args: dict[str, object] = {
        "host": str(config.get("host") or config.get("dbHost") or "127.0.0.1"),
        "port": int(config.get("port") or config.get("dbPort") or 3306),
        "user": str(config.get("user") or config.get("user_name") or config.get("dbUser") or ""),
        "password": str(config.get("password") or config.get("dbPassword") or ""),
        "charset": str(config.get("charset") or config.get("dbCharset") or "utf8mb4") or "utf8mb4",
        "connect_timeout": 6,
        "read_timeout": 10,
        "write_timeout": 10,
        "local_infile": config.get("writeMode") == "load",
        "autocommit": True,
    }
    database = str(config.get("database") or config.get("dbName") or config.get("db_name") or "").strip()
    if include_database and database:
        args["database"] = database
    ssl_enabled = config.get("sslEnabled") in (True, "true", "1", 1, "on")
    if ssl_enabled:
        ssl_config = {}
        for source, target in (("sslCa", "ca"), ("sslCert", "cert"), ("sslKey", "key")):
            value = str(config.get(source) or "").strip()
            if value:
                ssl_config[target] = value
        args["ssl"] = ssl_config
    return args


# MySQL 系统库：出现在「show databases」结果里但对用户业务无意义，
# 连接表单的数据库下拉统一过滤掉（用户显式选中的库除外，见 test_mysql_connection）。
MYSQL_SYSTEM_DATABASES = {"information_schema", "performance_schema", "mysql", "sys"}


def test_mysql_connection(config: dict[str, object]) -> dict[str, object]:
    normalized = normalize_connection_payload(config)
    try:
        return _test_mysql_connection(normalized)
    except Exception as exc:  # noqa: BLE001
        raise friendly_mysql_error(exc, "测试数据库连接") from exc


def _test_mysql_connection(normalized: dict[str, object]) -> dict[str, object]:
    with pymysql.connect(**mysql_connection_args(normalized, include_database=False)) as conn:
        with conn.cursor() as cursor:
            cursor.execute("select version()")
            version = cursor.fetchone()[0]
            cursor.execute("show databases")
            databases = [row[0] for row in cursor.fetchall()]
    # P3-19：过滤系统库，避免下拉被 information_schema/performance_schema 等占满。
    databases = [name for name in databases if name.lower() not in MYSQL_SYSTEM_DATABASES]
    selected_db = str(normalized.get("db_name") or "")
    if selected_db:
        with pymysql.connect(**mysql_connection_args(normalized, include_database=True)) as conn:
            with conn.cursor() as cursor:
                cursor.execute("select database()")
                cursor.fetchone()
        if selected_db not in databases:
            databases.insert(0, selected_db)
    return {"version": version, "databases": databases}


def checkpoint_key(uploaded: UploadedFile, table_name: str, fields: dict[str, str]) -> str:
    stat = uploaded.path.stat()
    return f"{uploaded.filename}|{stat.st_size}|{int(stat.st_mtime)}|{target_db_type(fields)}|{table_name}|{fields.get('importMode', 'append')}"


def get_checkpoint(key: str) -> int:
    with connect_db() as conn:
        row = conn.execute("select rows_done from _import_checkpoints where key = ?", (key,)).fetchone()
        return int(row["rows_done"]) if row else 0


def set_checkpoint(key: str, rows_done: int) -> None:
    with connect_db() as conn:
        conn.execute(
            """
            insert into _import_checkpoints (key, updated_at, rows_done)
            values (?, datetime('now', 'localtime'), ?)
            on conflict(key) do update set updated_at = excluded.updated_at, rows_done = excluded.rows_done
            """,
            (key, rows_done),
        )


def clear_checkpoint(key: str) -> None:
    with connect_db() as conn:
        conn.execute("delete from _import_checkpoints where key = ?", (key,))


def json_response(
    handler: SimpleHTTPRequestHandler,
    payload: object,
    status: int = 200,
    extra_headers: list[tuple[str, str]] | None = None,
) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    for name, value in extra_headers or []:
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(body)


def error_response(handler: SimpleHTTPRequestHandler, message: str, status: int = 400) -> None:
    json_response(handler, {"ok": False, "error": message}, status)


# ---------------------------------------------------------------------------
# 操作手册（docs 帮助中心）
#
# Markdown 是内容层（docs/ 下 .md），docs.html 是展示层。服务端把 .md 渲染为
# HTML 后经 /api/docs 返回。这里同时维护可浏览的目录索引（首页任务卡片 + 侧栏树）。
# 新增文档：在 docs/ 放入 .md，并在 DOC_INDEX 里加一项即可。
# ---------------------------------------------------------------------------

DOC_INDEX: list[dict[str, object]] = [
    {"id": "user/quick-start", "title": "快速开始", "category": "user", "tag": "向导"},
    {"id": "user/auth", "title": "登录与账号", "category": "user", "tag": "权限"},
    {"id": "user/connections", "title": "数据库连接", "category": "user", "tag": "功能"},
    {"id": "user/import", "title": "数据导入", "category": "user", "tag": "功能"},
    {"id": "user/export", "title": "数据导出", "category": "user", "tag": "功能"},
    {"id": "user/query", "title": "数据查询", "category": "user", "tag": "功能"},
    {"id": "user/tables", "title": "数据表", "category": "user", "tag": "功能"},
    {"id": "user/jobs", "title": "作业", "category": "user", "tag": "功能"},
    {"id": "user/schedule", "title": "定时任务", "category": "user", "tag": "功能"},
    {"id": "user/faq", "title": "常见问题", "category": "user", "tag": "FAQ"},
]
DOC_CATEGORY_LABELS = {
    "user": "功能说明",
}


def _doc_path(doc_id: str) -> Path | None:
    rel = Path(doc_id + ".md")
    if rel.is_absolute() or ".." in rel.parts or "." in rel.parts:
        return None
    target = (DOCS / rel).resolve()
    try:
        target.relative_to(DOCS.resolve())
    except ValueError:
        return None
    return target if target.is_file() else None


def _md_inline(text: str) -> str:
    # 行内：`code`、**bold**、*italic*、[text](href)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a rel="noopener" href="\2">\1</a>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    return text


def _md_blockquote(lines: list[str]) -> str:
    body = " ".join(line.lstrip("> ").strip() for line in lines)
    return f"<blockquote>{_md_inline(body)}</blockquote>"


def _md_items(block: list[str], ordered: bool) -> str:
    tag = "ol" if ordered else "ul"
    items: list[str] = []
    collected: list[list[str]] = []
    for line in block:
        m = re.match(r"^(\s*)([-*+]|\d+\.)\s+(.*)$", line)
        if m:
            collected.append([m.group(3)])
        elif line.strip() == "":
            collected.append([""])
        else:
            if collected:
                collected[-1].append(line.strip())
    for it in collected:
        text = "<br>".join(_md_inline(x) for x in it if x != "")
        if text:
            items.append(f"<li>{text}</li>")
    return f"<{tag}>{''.join(items)}</{tag}>"


def _md_table(block: list[str]) -> str:
    rows: list[list[str]] = []
    for line in block:
        if re.match(r"^\s*\|\s*[-:]+\s*\|", line.strip(), flags=re.I):
            continue  # 分隔行
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append(cells)
    if not rows:
        return ""
    header = rows[0]
    thead = "".join(f"<th>{_md_inline(c)}</th>" for c in header)
    tbody_rows = "".join(
        "<tr>" + "".join(f"<td>{_md_inline(c)}</td>" for c in row) + "</tr>"
        for row in rows[1:]
    )
    return f"<table><thead><tr>{thead}</tr></thead><tbody>{tbody_rows}</tbody></table>"


def render_doc_markdown(raw: str) -> str:
    """极简 Markdown 渲染器，覆盖操作手册用到的语法（标题/列表/表格/引用/代码/粗体）。"""
    lines = raw.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if stripped == "":
            i += 1
            continue
        hr = re.match(r"^\s*---+\s*$", line)
        if hr:
            out.append("<hr />")
            i += 1
            continue
        # 标题
        hm = re.match(r"^(#{1,6})\s+(.*)$", line)
        if hm:
            level = len(hm.group(1))
            out.append(f"<h{level}>{_md_inline(hm.group(2))}</h{level}>")
            i += 1
            continue
        # 引用块
        if line.lstrip().startswith(">"):
            block: list[str] = []
            while i < n and lines[i].lstrip().startswith(">"):
                block.append(lines[i])
                i += 1
            out.append(_md_blockquote(block))
            continue
        # 有序/无序列表
        lm = re.match(r"^\s*([-*+]|\d+\.)\s+\S", line)
        if lm:
            ordered = lm.group(1) not in ("-", "*", "+")
            block = [line]
            i += 1
            while i < n:
                cur = lines[i]
                cm = re.match(r"^\s*([-*+]|\d+\.)\s+\S", cur)
                if cm:
                    cur_ordered = cm.group(1) not in ("-", "*", "+")
                    if cur_ordered != ordered:
                        break
                    block.append(cur)
                    i += 1
                    continue
                if cur.strip() == "":
                    break
                break
            out.append(_md_items(block, ordered))
            continue
        # 表格
        if stripped.startswith("|") and "|" in stripped[1:]:
            block = []
            while i < n and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            out.append(_md_table(block))
            continue
        # 普通段落（合并多行）
        para = []
        while i < n and lines[i].strip() != "":
            if lines[i].lstrip().startswith(">"):
                break
            para.append(lines[i].strip())
            i += 1
        out.append(f"<p>{_md_inline(' '.join(para))}</p>")
    return "".join(out)


def load_doc_content(doc_id: str) -> tuple[str, str]:
    """读取并渲染一篇 .md，返回 (标题, HTML)。"""
    path = _doc_path(doc_id)
    if path is None:
        return "", ""
    raw = path.read_text(encoding="utf-8")
    title = _md_inline((raw.strip("\n").split("\n")[0] or doc_id).lstrip("# "))
    return title, render_doc_markdown(raw)


def doc_index_payload() -> dict[str, object]:
    return {"ok": True, "index": DOC_INDEX, "categories": DOC_CATEGORY_LABELS}


def parse_bool(fields: dict[str, str], name: str, default: bool = False) -> bool:
    value = fields.get(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def parse_int(fields: dict[str, str], name: str, default: int = 0) -> int:
    value = fields.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数。") from exc


def split_values(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,，\n]", value or "") if item.strip()]


def decode_escaped(value: str) -> str:
    if not value:
        return ""
    return value.encode("utf-8").decode("unicode_escape")


def sanitize_identifier(value: str, fallback: str) -> str:
    text = (value or "").strip()
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "_", text, flags=re.UNICODE)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        text = fallback
    if text[0].isdigit():
        text = f"t_{text}"
    return text[:60]


def to_pinyin_initials(value: str) -> str:
    return "".join(lazy_pinyin(value, style=Style.FIRST_LETTER))


def unique_names(names: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    result: list[str] = []
    for index, name in enumerate(names, start=1):
        base = sanitize_identifier(name, f"column_{index}")
        count = counts.get(base, 0)
        counts[base] = count + 1
        result.append(base if count == 0 else f"{base}_{count + 1}")
    return result


def cell_to_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, float):
        # 整数形态的 float 转 int：避免 str() 用科学计数法（如 1.2345e+17）导致
        # 大整数被误判为 text 且呈现精度异常（P2-6）。仅在能安全装入 bigint 时转，
        # 更大的数保留 float 走 double（配合 infer_column_types 的科学计数法正则）。
        if value.is_integer() and abs(value) < 2**63:
            return str(int(value))
    return str(value)


def trim_trailing_blanks(row: list[str]) -> list[str]:
    end = len(row)
    while end > 0 and row[end - 1].strip() == "":
        end -= 1
    return row[:end]


def read_csv_rows(path: Path, encoding_option: str, delimiter: str, line_delimiter: str = "") -> list[list[str]]:
    encodings = ["utf-8-sig", "utf-8", "gbk", "gb18030"] if encoding_option == "auto" else [encoding_option]
    last_error: Exception | None = None
    for encoding in encodings:
        try:
            with path.open("r", encoding=encoding, newline="") as file:
                content = file.read()
                sample = content[:4096]
                source_lines = content.split(line_delimiter) if line_delimiter else content.splitlines()
                if delimiter:
                    dialect = csv.excel
                    dialect.delimiter = delimiter
                else:
                    try:
                        dialect = csv.Sniffer().sniff(sample)
                        # Sniffer 在单列/无分隔符数据下会把换行符(\r/\n)或普通字母数字
                        # 误判为分隔符，导致 csv.reader 报 bad delimiter value 或错误拆列。
                        # 这类分隔符不可靠，降级为默认逗号（单列数据按整行一列读取）。
                        sep = getattr(dialect, "delimiter", "")
                        if not sep or sep in ("\r", "\n") or sep.isalnum():
                            dialect = csv.excel
                    except (csv.Error, ValueError):
                        dialect = csv.excel
                return [[cell_to_text(cell) for cell in row] for row in csv.reader(source_lines, dialect)]
        except UnicodeDecodeError as exc:
            last_error = exc
    raise ValueError(f"无法识别 CSV/TXT 编码：{last_error}")


def read_excel_rows(path: Path, fields: dict[str, str]) -> tuple[list[list[str]], list[str], str]:
    source = path
    decrypted: BytesIO | None = None
    password = fields.get("excelPassword", "").strip()
    if password:
        decrypted = BytesIO()
        with path.open("rb") as file:
            office_file = msoffcrypto.OfficeFile(file)
            office_file.load_key(password=password)
            office_file.decrypt(decrypted)
        decrypted.seek(0)
        source = decrypted  # type: ignore[assignment]
    workbook = load_workbook(source, read_only=True, data_only=True)
    sheet_names = workbook.sheetnames
    if not sheet_names:
        workbook.close()
        raise ValueError("Excel 文件没有工作表。")

    mode = fields.get("sheetFilterMode", "name")
    requested = fields.get("sheetName", "").strip()
    selected = sheet_names[0]
    if requested:
        if mode == "index":
            index = int(requested) - 1
            if index < 0 or index >= len(sheet_names):
                workbook.close()
                raise ValueError("指定的 Sheet 序号不存在。")
            selected = sheet_names[index]
        elif requested in sheet_names:
            selected = requested
        else:
            workbook.close()
            raise ValueError("指定的 Sheet 名称不存在。")

    sheet = workbook[selected]
    rows = [[cell_to_text(cell) for cell in row] for row in sheet.iter_rows(values_only=True)]
    workbook.close()
    return rows, sheet_names, selected


def read_xls_rows(path: Path, fields: dict[str, str]) -> tuple[list[list[str]], list[str], str]:
    workbook = xlrd.open_workbook(path)
    sheet_names = workbook.sheet_names()
    if not sheet_names:
        raise ValueError("Excel 文件没有工作表。")
    mode = fields.get("sheetFilterMode", "name")
    requested = fields.get("sheetName", "").strip()
    selected = sheet_names[0]
    if requested:
        if mode == "index":
            index = int(requested) - 1
            if index < 0 or index >= len(sheet_names):
                raise ValueError("指定的 Sheet 序号不存在。")
            selected = sheet_names[index]
        elif requested in sheet_names:
            selected = requested
        else:
            raise ValueError("指定的 Sheet 名称不存在。")
    sheet = workbook.sheet_by_name(selected)
    rows = [[cell_to_text(sheet.cell_value(row, col)) for col in range(sheet.ncols)] for row in range(sheet.nrows)]
    return rows, sheet_names, selected


def read_dbf_rows(path: Path, fields: dict[str, str]) -> list[list[str]]:
    encoding = fields.get("encoding", "auto")
    kwargs = {} if encoding == "auto" else {"encoding": encoding}
    table = DBF(str(path), load=True, char_decode_errors="ignore", **kwargs)
    columns = list(table.field_names)
    rows = [columns]
    for record in table:
        rows.append([cell_to_text(record.get(column, "")) for column in columns])
    return rows


def flatten_object(data: dict[str, object], prefix: str = "") -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in data.items():
        name = f"{prefix}_{key}" if prefix else str(key)
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                result[f"{name}_{child_key}"] = child_value
        elif isinstance(value, list):
            result[name] = json.dumps(value, ensure_ascii=False)
        else:
            result[name] = value
    return result


def read_json_rows(path: Path, row_tag: str = "") -> list[list[str]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, dict):
        data = data.get(row_tag) if row_tag and row_tag in data else next((v for v in data.values() if isinstance(v, list)), [data])
    if not isinstance(data, list):
        raise ValueError("JSON 需要是对象数组，或包含对象数组的根对象。")
    flattened = [flatten_object(item) for item in data if isinstance(item, dict)]
    keys: list[str] = []
    for item in flattened:
        if isinstance(item, dict):
            for key in item.keys():
                if key not in keys:
                    keys.append(str(key))
    if not keys:
        raise ValueError("JSON 中没有可导入的对象行。")
    rows = [keys]
    for item in flattened:
        if isinstance(item, dict):
            rows.append([cell_to_text(item.get(key, "")) for key in keys])
    return rows


def read_xml_rows(path: Path, row_tag: str) -> list[list[str]]:
    root = ET.parse(path).getroot()
    elements = root.findall(f".//{row_tag}") if row_tag else list(root)
    if not elements:
        raise ValueError("XML 中没有可导入的行节点。")
    flattened_rows: list[dict[str, object]] = []
    for element in elements:
        row: dict[str, object] = {}
        for child in list(element):
            if list(child):
                for grandchild in list(child):
                    row[f"{child.tag}_{grandchild.tag}"] = grandchild.text or ""
            else:
                row[child.tag] = child.text or ""
        flattened_rows.append(row)
    keys: list[str] = []
    for row in flattened_rows:
        for key in row.keys():
            if key not in keys:
                keys.append(key)
    if not keys:
        raise ValueError("XML 行节点中没有字段。")
    rows = [keys]
    for row in flattened_rows:
        rows.append([cell_to_text(row.get(key, "")) for key in keys])
    return rows


def make_tabular(raw_rows: list[list[str]], fields: dict[str, str], sheets: list[str] | None = None, selected_sheet: str = "") -> TabularData:
    delete_empty_rows = parse_bool(fields, "deleteEmptyRows", True)
    rows = [trim_trailing_blanks(row) for row in raw_rows]
    if delete_empty_rows:
        rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError("文件中没有可导入的数据。")

    header_row = max(parse_int(fields, "headerRow", 1), 1)
    header_index = header_row - 1
    if header_index >= len(rows):
        raise ValueError("表头所在行号超出文件行数。")

    has_header = parse_bool(fields, "hasHeader", True)
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    if has_header:
        columns = unique_names(rows[header_index])
        default_data_start = header_row + 1
    else:
        columns = [f"column_{index}" for index in range(1, width + 1)]
        default_data_start = header_row

    data_start = parse_int(fields, "dataStartRow", default_data_start) or default_data_start
    data_rows = rows[max(data_start - 1, 0) :]
    skip_tail = max(parse_int(fields, "skipTailRows", 0), 0)
    if skip_tail:
        data_rows = data_rows[:-skip_tail] if skip_tail < len(data_rows) else []
    import_count = parse_int(fields, "importRowCount", 0)
    if import_count > 0:
        data_rows = data_rows[:import_count]

    columns, data_rows = apply_column_filter(columns, data_rows, fields.get("columnFilter", ""))
    return TabularData(columns=columns, rows=data_rows, sheets=sheets or [], selected_sheet=selected_sheet)


def apply_column_filter(columns: list[str], rows: list[list[str]], filter_value: str) -> tuple[list[str], list[list[str]]]:
    filters = split_values(filter_value)
    if not filters:
        return columns, rows
    indexes: list[int] = []
    for item in filters:
        if item.isdigit():
            index = int(item) - 1
            if 0 <= index < len(columns):
                indexes.append(index)
        elif item in columns:
            indexes.append(columns.index(item))
    if not indexes:
        raise ValueError("指定导入列没有匹配到任何字段。")
    return [columns[i] for i in indexes], [[row[i] if i < len(row) else "" for i in indexes] for row in rows]


# 读取阶段第三方库异常的「类型名 → 中文提示」映射。
# 用类型名而不是 isinstance，是为了避免为 xlrd/dbfread 的私有异常模块增加硬依赖。
_BAD_FILE_HINTS: tuple[tuple[str, str], ...] = (
    ("BadZipFile", "文件不是有效的 xlsx/xlsm（可能已损坏，或实际不是 Excel 文件）"),
    ("InvalidFileException", "文件不是有效的 Excel 文件（扩展名与实际内容不符）"),
    ("XLRDError", "文件不是有效的 xls（可能已损坏，或实际不是旧版 Excel 文件）"),
    ("JSONDecodeError", "文件不是有效的 JSON（格式错误或内容已损坏）"),
    ("ParseError", "文件不是有效的 XML（格式错误或内容已损坏）"),
    ("DbfError", "文件不是有效的 DBF（可能已损坏或版本不受支持）"),
    ("InvalidOperationError", "文件不是有效的 DBF（可能已损坏或版本不受支持）"),
    ("UnicodeDecodeError", "文件编码无法识别，请手动指定编码后重试"),
)


def friendly_read_error(exc: Exception, path: Path) -> Exception:
    """把读取阶段的第三方英文异常翻译成面向用户的中文提示。

    缺陷背景：坏文件曾被直接抛出 `File is not a zip file`、
    `Unsupported format, or corrupt file: Expected BOF record` 等驱动原文。
    这里统一转成「文件名 + 中文结论（原始错误：异常类名）」；
    无法识别且已是 ValueError（即我们自己抛出的中文提示）时原样透传。
    """
    for name, message in _BAD_FILE_HINTS:
        if type(exc).__name__ == name:
            return ValueError(f"{path.name} {message}。原始错误：{type(exc).__name__}")
    if isinstance(exc, ValueError):
        return exc
    return ValueError(f"读取 {path.name} 失败：{exc}")


def read_tabular_file(path: Path, fields: dict[str, str]) -> TabularData:
    try:
        return _read_tabular_file(path, fields)
    except Exception as exc:  # noqa: BLE001
        raise friendly_read_error(exc, path) from exc


def _read_tabular_file(path: Path, fields: dict[str, str]) -> TabularData:
    suffix = path.suffix.lower()
    if suffix in {".csv", ".txt"}:
        rows = read_csv_rows(path, fields.get("encoding", "auto"), fields.get("delimiter", ""), decode_escaped(fields.get("lineDelimiter", "")))
        return make_tabular(rows, fields)
    if suffix in {".xlsx", ".xlsm"}:
        rows, sheets, selected = read_excel_rows(path, fields)
        return make_tabular(rows, fields, sheets, selected)
    if suffix == ".xls":
        rows, sheets, selected = read_xls_rows(path, fields)
        return make_tabular(rows, fields, sheets, selected)
    if suffix == ".json":
        rows = read_json_rows(path, fields.get("rowTag", ""))
        with_header = dict(fields)
        with_header["hasHeader"] = "true"
        return make_tabular(rows, with_header)
    if suffix == ".xml":
        rows = read_xml_rows(path, fields.get("rowTag", ""))
        with_header = dict(fields)
        with_header["hasHeader"] = "true"
        return make_tabular(rows, with_header)
    if suffix == ".dbf":
        rows = read_dbf_rows(path, fields)
        with_header = dict(fields)
        with_header["hasHeader"] = "true"
        return make_tabular(rows, with_header)
    supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
    raise ValueError(f"暂不支持 {suffix or '未知'} 文件。当前支持：{supported}")


def read_tabular_tasks(path: Path, fields: dict[str, str]) -> list[TabularData]:
    try:
        return _read_tabular_tasks(path, fields)
    except Exception as exc:  # noqa: BLE001
        raise friendly_read_error(exc, path) from exc


def _read_tabular_tasks(path: Path, fields: dict[str, str]) -> list[TabularData]:
    suffix = path.suffix.lower()
    if fields.get("sheetMode", "specified") != "all" or suffix not in {".xlsx", ".xlsm", ".xls"}:
        return [read_tabular_file(path, fields)]
    if suffix == ".xls":
        names = xlrd.open_workbook(path).sheet_names()
    else:
        workbook = load_workbook(path, read_only=True, data_only=True)
        names = workbook.sheetnames
        workbook.close()
    tasks: list[TabularData] = []
    for name in names:
        per_sheet = dict(fields)
        per_sheet["sheetFilterMode"] = "name"
        per_sheet["sheetName"] = name
        tasks.append(read_tabular_file(path, per_sheet))
    return tasks

def transform_field_name(name: str, fields: dict[str, str], fallback: str) -> str:
    value = name.strip() or fallback
    if parse_bool(fields, "fieldPinyin", False):
        value = to_pinyin_initials(value)
    replace_from = fields.get("fieldReplaceFrom", "")
    replace_to = fields.get("fieldReplaceTo", "_")
    if replace_from == "symbol":
        value = re.sub(r"[^\w\u4e00-\u9fff]+", replace_to, value, flags=re.UNICODE)
    elif replace_from == "space":
        value = value.replace(" ", replace_to)
    field_case = fields.get("fieldCase", "lower")
    if field_case == "upper":
        value = value.upper()
    elif field_case == "lower":
        value = value.lower()
    return sanitize_identifier(value, fallback)


def parse_mapping(raw_value: str | None, source_columns: list[str]) -> list[dict[str, object]]:
    if not raw_value:
        return [
            {"sourceIndex": index, "source": column, "target": column, "enabled": True, "defaultValue": "", "matchKey": index == 0}
            for index, column in enumerate(source_columns)
        ]
    payload = json.loads(raw_value)
    if not isinstance(payload, list):
        raise ValueError("字段映射必须是数组。")
    if not payload:
        return [
            {"sourceIndex": index, "source": column, "target": column, "enabled": True, "defaultValue": "", "matchKey": index == 0}
            for index, column in enumerate(source_columns)
        ]
    mapping: list[dict[str, object]] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            continue
        source_index = int(item.get("sourceIndex", -1))
        if source_index < 0 or source_index >= len(source_columns):
            continue
        mapping.append(
            {
                "sourceIndex": source_index,
                "source": source_columns[source_index],
                "target": str(item.get("target") or source_columns[source_index]),
                "enabled": bool(item.get("enabled", True)),
                "defaultValue": str(item.get("defaultValue") or ""),
                "matchKey": bool(item.get("matchKey", index == 0)),
            }
        )
    return mapping


def column_indexes(columns: list[str], selector: str) -> list[int]:
    indexes: list[int] = []
    lower_map = {column.lower(): index for index, column in enumerate(columns)}
    for item in split_values(selector):
        if item.isdigit():
            index = int(item) - 1
            if 0 <= index < len(columns):
                indexes.append(index)
        elif item in columns:
            indexes.append(columns.index(item))
        elif item.lower() in lower_map:
            indexes.append(lower_map[item.lower()])
    return indexes


def parse_date_column_formats(columns: list[str], selector: str) -> dict[int, str]:
    result: dict[int, str] = {}
    lower_map = {column.lower(): index for index, column in enumerate(columns)}
    for item in split_values(selector):
        if ":" in item:
            key, fmt = item.split(":", 1)
        elif "=" in item:
            key, fmt = item.split("=", 1)
        else:
            continue
        key = key.strip()
        fmt = fmt.strip()
        index = None
        if key.isdigit():
            candidate = int(key) - 1
            if 0 <= candidate < len(columns):
                index = candidate
        elif key in columns:
            index = columns.index(key)
        elif key.lower() in lower_map:
            index = lower_map[key.lower()]
        if index is not None and fmt:
            result[index] = fmt
    return result


# 行级跳过明细最多回传多少条：够定位问题即可，避免大文件把响应体撑爆。
MAX_SKIP_DETAILS = 200


def apply_cleaning(
    columns: list[str],
    rows: list[list[str]],
    fields: dict[str, str],
    skip_details: list[dict[str, object]] | None = None,
) -> tuple[list[str], list[list[str]], int]:
    trim_values = parse_bool(fields, "trimValues", True)
    empty_as_null = parse_bool(fields, "emptyAsNull", False)
    zero_for_number = parse_bool(fields, "zeroForNumber", False)
    replace_blank_with = fields.get("replaceBlankWith", "")
    remove_text = fields.get("removeText", "")
    replace_text_from = fields.get("replaceTextFrom", "")
    replace_text_to = fields.get("replaceTextTo", "")
    blank_values = set(split_values(fields.get("blankCellValues", "")))
    fill_down_indexes = column_indexes(columns, fields.get("fillDownColumns", ""))
    dedupe_indexes = column_indexes(columns, fields.get("dedupeColumns", ""))
    date_formats = parse_date_column_formats(columns, fields.get("dateColumns", ""))

    cleaned: list[list[str | None]] = []
    previous: dict[int, str] = {}
    skipped = 0
    seen: set[tuple[str, ...]] = set()

    for row_number, row in enumerate(rows, start=1):
        values: list[str | None] = []
        for index, raw in enumerate(row):
            value = raw or ""
            if trim_values:
                value = value.strip()
            if value in blank_values:
                value = ""
            if remove_text:
                value = value.replace(remove_text, "")
            if replace_text_from:
                value = value.replace(replace_text_from, replace_text_to)
            if value == "" and index in fill_down_indexes and index in previous:
                value = previous[index]
            if value:
                previous[index] = value
            if value == "" and replace_blank_with:
                value = replace_blank_with
            if value == "" and zero_for_number:
                value = "0"
            if value and index in date_formats:
                try:
                    value = dt.datetime.strptime(value, date_formats[index]).isoformat(sep=" ", timespec="seconds")
                except ValueError as exc:
                    raise ValueError(f"日期列 {columns[index]} 的值 {value} 不符合格式 {date_formats[index]}") from exc
            values.append(None if empty_as_null and value == "" else value)

        if dedupe_indexes:
            key = tuple("" if values[i] is None else str(values[i]) for i in dedupe_indexes)
            if key in seen:
                skipped += 1
                # 改进C：行级跳过原先只回计数，用户不知道「哪一行、为什么被丢掉」。
                if skip_details is not None and len(skip_details) < MAX_SKIP_DETAILS:
                    skip_details.append(
                        {
                            "dataRow": row_number,
                            "reason": "按「去重列」判定为重复行",
                            "key": " | ".join(str(part) for part in key),
                        }
                    )
                continue
            seen.add(key)
        cleaned.append(values)

    return columns, cleaned, skipped


def describe_cleaning_rules(fields: dict[str, str]) -> list[dict[str, str]]:
    """列出本次导入**实际会生效**的数据转换规则（含默认开启项）。

    缺陷背景（P3-20）：`trimValues` 默认开启、列名默认转小写、类型默认自动识别，
    但预览页从不告知，用户看到值被改动会误判成数据出错。
    这里只描述会改变数据/列名的规则，纯开关类（如 disableLog）不列。
    """
    rules: list[dict[str, str]] = []

    def add(name: str, detail: str, source: str) -> None:
        rules.append({"rule": name, "detail": detail, "source": source})

    if parse_bool(fields, "trimValues", True):
        add("裁掉首尾空格", "每个单元格值的首尾空格会被去掉", "默认开启")
    if parse_bool(fields, "emptyAsNull", False):
        add("空值写为 NULL", "空字符串写入数据库时转为 NULL", "已开启")
    if parse_bool(fields, "zeroForNumber", False):
        add("空值写为 0", "空的数值单元格会补 0", "已开启")
    if fields.get("replaceBlankWith", ""):
        add("空值替换", f"空值统一替换为「{fields['replaceBlankWith']}」", "已开启")
    if fields.get("removeText", ""):
        add("移除指定文本", f"删除值中的「{fields['removeText']}」", "已开启")
    if fields.get("replaceTextFrom", ""):
        add(
            "文本替换",
            f"把「{fields['replaceTextFrom']}」替换为「{fields.get('replaceTextTo', '')}」",
            "已开启",
        )
    if fields.get("blankCellValues", "").strip():
        add("视为空白的值", f"以下值会被当作空：{fields['blankCellValues']}", "已开启")
    if fields.get("fillDownColumns", "").strip():
        add("向下填充", f"列 {fields['fillDownColumns']} 的空白单元格沿用上一行值", "已开启")
    if fields.get("dedupeColumns", "").strip():
        add("按列去重", f"按列 {fields['dedupeColumns']} 去重，重复行将被跳过", "已开启")
    if fields.get("dateColumns", "").strip():
        add("按指定格式解析日期", f"列 {fields['dateColumns']} 按配置格式转换为日期", "已开启")

    field_case = fields.get("fieldCase", "lower")
    if field_case == "upper":
        add("列名转大写", "字段名统一转为大写", "已开启")
    else:
        add("列名转小写", "字段名统一转为小写", "默认开启")
    if parse_bool(fields, "fieldPinyin", False):
        add("列名转拼音首字母", "字段名会替换为拼音首字母", "已开启")
    if fields.get("fieldReplaceFrom") == "symbol":
        add("列名符号替换", "字段名中的符号会被替换为下划线", "已开启")
    elif fields.get("fieldReplaceFrom") == "space":
        add("列名空格替换", "字段名中的空格会被替换为下划线", "已开启")

    type_mode = fields.get("typeMode", "auto")
    if type_mode == "text":
        add("全部按文本写入", "所有列都会以文本类型建表", "已开启")
    else:
        add(
            "自动识别列类型",
            "数值/日期列会自动识别为对应类型；超出 64 位整数范围的列按文本处理",
            "默认开启",
        )
    if fields.get("autoPkField", "").strip():
        add("自动主键", f"会自动生成主键列「{fields['autoPkField']}」", "已开启")
    if fields.get("defaultForEmpty", "") and parse_bool(fields, "defaultForEmpty", False):
        add("空值填默认值", "空单元格会使用字段映射里配置的默认值", "已开启")
    return rules


def build_target_data(
    tabular: TabularData,
    fields: dict[str, str],
    file_name: str,
    skip_details: list[dict[str, object]] | None = None,
) -> tuple[list[str], list[list[object]], list[str], int]:
    mapping = [item for item in parse_mapping(fields.get("mapping"), tabular.columns) if item.get("enabled")]
    if not mapping:
        raise ValueError("至少需要启用一个字段。")

    raw_columns = [str(item["target"]) for item in mapping]
    raw_rows = []
    for row in tabular.rows:
        values = []
        for item in mapping:
            source_index = int(item["sourceIndex"])
            value = row[source_index] if source_index < len(row) else ""
            if value == "" and (parse_bool(fields, "defaultForEmpty", False) or item.get("defaultValue")):
                value = str(item.get("defaultValue") or "")
            values.append(value)
        raw_rows.append(values)

    transformed_columns = unique_names([transform_field_name(name, fields, f"column_{i + 1}") for i, name in enumerate(raw_columns)])
    transformed_columns, cleaned_rows, skipped = apply_cleaning(transformed_columns, raw_rows, fields, skip_details)

    match_keys = [
        transformed_columns[index]
        for index, item in enumerate(mapping)
        if index < len(transformed_columns) and item.get("matchKey")
    ]
    if not match_keys and transformed_columns:
        match_keys = [transformed_columns[0]]

    final_columns = list(transformed_columns)
    final_rows: list[list[object]] = [list(row) for row in cleaned_rows]

    pk_column = ""
    pk_auto = False
    auto_pk_field = fields.get("autoPkField", "").strip()
    if auto_pk_field:
        column = transform_field_name(auto_pk_field, fields, "id")
        if column in transformed_columns:
            # 自动主键复用了源文件里已存在的列：不新增同名列，只把它标记为主键列。
            pk_column = column
        else:
            # 源文件里没有该列：新增自增主键列（值 = 行号 1,2,3...）。
            final_columns.insert(0, column)
            for index, row in enumerate(final_rows, start=1):
                row.insert(0, index)
            pk_column = column
            pk_auto = True
    # 通过内部键把主键信息传递到建表 / 写入流程（不影响对外的 fields 语义）。
    fields["_primaryKeyColumn"] = pk_column
    fields["_primaryKeyAuto"] = "true" if pk_auto else "false"

    extras: list[tuple[str, object]] = []
    if fields.get("importTimeField", "").strip():
        # 写入 datetime 对象（而非 ISO 字符串）：MySQL 端据此推断为 datetime 列类型，
        # 值由驱动原生绑定；SQLite 端经上方显式适配器落为 "YYYY-MM-DD HH:MM:SS" 文本。
        # 截掉微秒，保证 LOAD DATA 文本路径与 DATETIME(0) 列兼容。
        extras.append((transform_field_name(fields["importTimeField"], fields, "imported_at"), dt.datetime.now().replace(microsecond=0)))
    if fields.get("sheetNameField", "").strip():
        extras.append((transform_field_name(fields["sheetNameField"], fields, "sheet_name"), tabular.selected_sheet or Path(file_name).stem))
    if fields.get("fixedValueField", "").strip():
        extras.append((transform_field_name(fields["fixedValueField"], fields, "fixed_value"), fields.get("fixedValue", "")))

    for column, value in extras:
        final_columns.append(column)
        for row in final_rows:
            row.append(value)

    return final_columns, final_rows, match_keys, skipped


def normalize_target_name(uploaded: UploadedFile, tabular: TabularData, fields: dict[str, str]) -> str:
    if fields.get("tableName"):
        base = fields["tableName"]
    elif fields.get("tableNameRule") == "sheet" and tabular.selected_sheet:
        base = tabular.selected_sheet
    else:
        base = Path(uploaded.filename).stem

    regex = fields.get("tableRegex", "").strip()
    if regex:
        match = re.search(regex, base)
        if match:
            base = match.group(1) if match.groups() else match.group(0)

    if parse_bool(fields, "symbolToUnderscore", False):
        base = re.sub(r"[^\w\u4e00-\u9fff]+", "_", base, flags=re.UNICODE)
    if parse_bool(fields, "tablePinyin", False):
        base = to_pinyin_initials(base)

    value = f"{fields.get('tablePrefix', '')}{base}{fields.get('tableSuffix', '')}"
    target_case = fields.get("tableCase", "lower")
    if target_case == "upper":
        value = value.upper()
    elif target_case == "lower":
        value = value.lower()
    return sanitize_identifier(value, "import_table")


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def existing_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    rows = conn.execute(f"pragma table_info({quote_identifier(table_name)})").fetchall()
    return [row["name"] for row in rows]


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return (
        conn.execute(
            "select 1 from sqlite_master where type = 'table' and name = ? limit 1",
            (table_name,),
        ).fetchone()
        is not None
    )


def target_table_exists(conn, table_name: str, fields: dict[str, str]) -> bool:
    if target_db_type(fields) == "mysql":
        with conn.cursor() as cursor:
            cursor.execute(
                "select 1 from information_schema.tables where table_schema = database() and table_name = %s limit 1",
                (table_name,),
            )
            return cursor.fetchone() is not None
    return table_exists(conn, table_name)


def target_existing_columns(conn, table_name: str, fields: dict[str, str]) -> list[str]:
    """目标表已有列名，保留数据库报告的**原始大小写**（MySQL 取自 information_schema）。"""
    if target_db_type(fields) == "mysql":
        with conn.cursor() as cursor:
            cursor.execute(
                """
                select column_name from information_schema.columns
                where table_schema = database() and table_name = %s
                order by ordinal_position
                """,
                (table_name,),
            )
            return [row[0] for row in cursor.fetchall()]
    return existing_columns(conn, table_name)


def target_existing_column_keys(conn, table_name: str, fields: dict[str, str]) -> set[str]:
    """目标表已有列名的「比对键」集合（统一折叠为小写）。

    为什么需要单独的比对键：MySQL 的列名不区分大小写，但
    ``information_schema.columns.column_name`` 返回的是**建表时写的大小写**。
    导入侧启用 fieldCase=lower 后列名被归一成 ``name``，若用大小写敏感的方式比对，
    已有的 ``NAME`` 会被误判成「缺列」，于是 ADD COLUMN name 撞上既有列，
    报 1060 Duplicate column——目标表其实完全兼容却导入失败。
    SQLite 同样按大小写不敏感解析标识符，所以两者统一折叠。

    注意：这里只用于**存在性判断**；回写列定义时仍必须使用
    :func:`target_existing_columns` 返回的原始大小写，否则会把用户的大写列改名。
    """
    return {column.strip().lower() for column in target_existing_columns(conn, table_name, fields)}


def align_columns_by_position(
    conn,
    table_name: str,
    columns: list[str],
    match_keys: list[str],
    fields: dict[str, str],
) -> tuple[list[str], list[str]]:
    """字段匹配=「按顺序」：把源文件列**按列序号**对齐到既有目标表的列名。

    背景：前端「字段匹配」下拉（`#matchBy`）提供 按名称 / 按顺序 / 自定义 三项，
    其中 `matchBy=order` 长期只发送、服务端无消费方 —— 于是「按顺序」与「按名称」
    行为完全一致，选项形同虚设。本函数补上「按顺序」的真实语义：

    - 仅当 ``matchBy == "order"`` **且目标表已存在**时生效，此时把第 i 个源列
      映射到既有目标表的第 i 个列（沿用目标表的原始大小写）；
    - 目标表列数少于源列数时明确报错，不静默丢列；
    - 其余情况（未传 / ``name`` / ``custom`` / 目标表不存在）**原样返回**，
      行为与修复前逐字节一致 —— 既有作业与定时任务的配置里写的是 ``name``，
      因此不受影响。

    返回 ``(对齐后的列名, 按位置重映射后的匹配键)``；匹配键要一起换名，
    否则 update 模式会按旧列名找不到键列。
    """
    if str(fields.get("matchBy") or "name").strip().lower() != "order":
        return columns, match_keys
    if not target_table_exists(conn, table_name, fields):
        return columns, match_keys
    existing = target_existing_columns(conn, table_name, fields)
    if len(existing) < len(columns):
        raise ValueError(
            f"字段匹配设为「按顺序」，但目标表 {table_name} 只有 {len(existing)} 列"
            f"（{'、'.join(existing)}），少于源文件的 {len(columns)} 列"
            f"（{'、'.join(columns)}），无法按序号一一对应。"
            "请改为「按名称」，或先在目标表上补齐列。"
        )
    aligned = existing[: len(columns)]
    position = {name: index for index, name in enumerate(columns)}
    remapped_keys = [aligned[position[key]] for key in match_keys if key in position]
    return aligned, remapped_keys


def target_row_count(conn, table_name: str, fields: dict[str, str]) -> int:
    table = db_quote(table_name, fields)
    if target_db_type(fields) == "mysql":
        with conn.cursor() as cursor:
            cursor.execute(f"select count(*) from {table}")
            row = cursor.fetchone()
    else:
        row = conn.execute(f"select count(*) from {table}").fetchone()
    return int(row[0] if row else 0)


DATE_TEXT_PATTERN = re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?")
DATE_PARSE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
)


def parse_date_text(value: str) -> dt.datetime | None:
    text = value.strip()
    if len(text) < 8 or not DATE_TEXT_PATTERN.fullmatch(text):
        return None
    normalized = text.replace("T", " ")
    for fmt in DATE_PARSE_FORMATS:
        try:
            return dt.datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    return None


def convert_date_columns(columns: list[str], rows: list[list[object]], fields: dict[str, str]) -> list[list[object]]:
    """目标为 MySQL 且开启类型自动识别时，把整列均为日期形态的文本还原为 date/datetime 对象，
    使建表类型与入库值都是真正的日期类型，而不是 text 字符串。
    用户通过 columnTypeOverrides 覆盖为 date/datetime 的列也会被尽力转换为日期对象。"""
    if target_db_type(fields) == "sqlite" or not rows or not columns:
        return rows
    converted = [list(row) for row in rows]
    if fields.get("typeMode", "auto") != "text":
        for index in range(len(columns)):
            col_values = [row[index] for row in converted if index < len(row) and row[index] not in (None, "")]
            if not col_values:
                continue
            parsed = [parse_date_text(value) if isinstance(value, str) else None for value in col_values]
            if any(item is None for item in parsed):
                continue
            has_time = any(item.hour or item.minute or item.second for item in parsed)
            for row in converted:
                if index < len(row) and row[index] not in (None, ""):
                    value = parse_date_text(row[index])
                    row[index] = value if has_time else value.date()
    # 用户覆盖为 date/datetime 的列：把文本解析为日期对象，解析失败保持原值（交由数据库校验）。
    overrides = parse_column_type_overrides(fields)
    for column, override_type in overrides.items():
        if override_type not in {"date", "datetime"} or column not in columns:
            continue
        index = columns.index(column)
        for row in converted:
            if index >= len(row) or row[index] in (None, ""):
                continue
            value = row[index]
            if isinstance(value, str):
                parsed = parse_date_text(value)
                if parsed is not None:
                    row[index] = parsed if override_type == "datetime" else parsed.date()
    return converted


INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1


def fits_int64_literal(text: str) -> bool:
    """判断纯数字字面量能否被 64 位有符号整数精确容纳。

    SQLite 的 INTEGER 亲和性会把超范围整数静默转成 REAL（丢精度），
    MySQL 的 bigint 则直接报 1264 越界，因此这类列必须改用文本承载。
    """
    try:
        return INT64_MIN <= int(text) <= INT64_MAX
    except (TypeError, ValueError):
        return False


def infer_column_types(columns: list[str], rows: list[list[object]], fields: dict[str, str]) -> dict[str, str]:
    db_type = target_db_type(fields)
    text_type = "text" if db_type == "sqlite" else "text"
    int_type = "integer" if db_type == "sqlite" else "bigint"
    real_type = "real" if db_type == "sqlite" else "double"
    if fields.get("typeMode", "auto") == "text":
        types = {column: text_type for column in columns}
    else:
        types: dict[str, str] = {}
        for index, column in enumerate(columns):
            values = [row[index] for row in rows if index < len(row) and row[index] not in (None, "")]
            if values and all(isinstance(value, dt.date) for value in values):
                if db_type == "sqlite":
                    types[column] = text_type
                elif any(isinstance(value, dt.datetime) for value in values):
                    types[column] = "datetime"
                else:
                    types[column] = "date"
            elif values and all(re.fullmatch(r"[-+]?\d+", str(value)) for value in values):
                # 订单号/身份证号这类超长数字超出 int64 时不能声明成 integer/bigint：
                # SQLite 会转 REAL 丢精度、MySQL 会越界报错，因此降级为文本原样保留。
                if all(fits_int64_literal(str(value)) for value in values):
                    types[column] = int_type
                else:
                    types[column] = text_type
            elif values and all(re.fullmatch(r"[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?", str(value)) for value in values):
                types[column] = real_type
            else:
                types[column] = text_type
    # 用户手动指定的类型优先于自动识别结果（含 typeMode=text 的整表文本兜底）。
    overrides = parse_column_type_overrides(fields)
    for column, type_name in overrides.items():
        if column in types:
            normalized = normalize_type_override(type_name, db_type)
            if normalized:
                types[column] = normalized
    return types


VALID_TYPE_OVERRIDES = {"text", "integer", "bigint", "real", "double", "date", "datetime"}


def normalize_type_override(type_name: str, db_type: str) -> str:
    """把用户指定的类型名归一化为当前目标库可用的列类型；非法类型返回空字符串。"""
    value = (type_name or "").strip().lower()
    if value not in VALID_TYPE_OVERRIDES:
        return ""
    if db_type == "sqlite":
        return {"bigint": "integer", "double": "real", "date": "text", "datetime": "text"}.get(value, value)
    return {"integer": "bigint", "real": "double"}.get(value, value)


def parse_column_type_overrides(fields: dict[str, str]) -> dict[str, str]:
    """解析 columnTypeOverrides（JSON 字符串形如 {"编号":"bigint"}），非法值忽略。

    前端按“目标字段名”传键，这里用与 build_target_data 相同的 transform_field_name
    把键归一化为最终列名，保证大小写/符号替换/拼音转换后仍能匹配到目标列。"""
    raw_value = (fields.get("columnTypeOverrides") or "").strip()
    if not raw_value:
        return {}
    try:
        payload = json.loads(raw_value)
    except (ValueError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    entries: list[tuple[str, str]] = []
    for key, value in payload.items():
        if not isinstance(key, str) or not isinstance(value, str) or not value.strip():
            continue
        column = transform_field_name(key, fields, key)
        if column:
            entries.append((column, value.strip().lower()))
    # 与 build_target_data 的 unique_names 去重对齐：多个源列变换后同名时，
    # 覆盖键也按相同顺序去重为 xxx / xxx_2，避免第二个同名列的类型覆盖丢失。
    final_names = unique_names([name for name, _ in entries])
    return {name: type_name for (_, type_name), name in zip(entries, final_names)}


def primary_key_column(fields: dict[str, str]) -> str:
    """返回导入配置声明的主键列名；未配置自动主键时返回空字符串。"""
    return (fields.get("_primaryKeyColumn") or "").strip()


def auto_pk_column(fields: dict[str, str]) -> str:
    """返回自动生成的自增主键列名；仅当该主键为新增合成列时返回，否则返回空字符串。"""
    if parse_bool(fields, "_primaryKeyAuto", False):
        return (fields.get("_primaryKeyColumn") or "").strip()
    return ""


def detect_type_warnings(columns: list[str], rows: list[list[object]], column_types: dict[str, str]) -> list[dict[str, str]]:
    """对推断为 text、但列内同时混有可解析数字/日期与不可解析文本的列给出预警。"""
    warnings: list[dict[str, str]] = []
    for index, column in enumerate(columns):
        if column_types.get(column) != "text":
            continue
        values = [row[index] for row in rows if index < len(row) and row[index] not in (None, "")]
        if not values:
            continue
        digits = [str(value).strip() for value in values]
        if all(re.fullmatch(r"[-+]?\d+", text) for text in digits) and any(
            not fits_int64_literal(text) for text in digits
        ):
            warnings.append({"column": column, "reason": "该列数值超出 64 位整数范围，已按文本处理以保留精度"})
            continue
        numeric_like = 0
        date_like = 0
        unparseable = 0
        for value in values:
            text = str(value).strip()
            if re.fullmatch(r"[-+]?\d+", text) or re.fullmatch(r"[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?", text):
                numeric_like += 1
            elif parse_date_text(text):
                date_like += 1
            else:
                unparseable += 1
        if unparseable > 0 and (numeric_like > 0 or date_like > 0):
            warnings.append({"column": column, "reason": "该列含混合类型值，已按文本处理"})
    return warnings


# autoExpand 只拓宽这两类字符列；其余类型（bigint/double/date…）不做自动拓宽。
MYSQL_CHARACTER_TYPES = {"varchar", "char"}

# 被当作表达式而不是字符串字面量输出的默认值（MySQL 的 default 允许 CURRENT_TIMESTAMP 之类）。
_MYSQL_DEFAULT_EXPRESSION_RE = re.compile(
    r"^(?:current_timestamp(?:\(\d*\))?|current_date|current_time|now\(\)|null)$",
    re.I,
)

# pymysql 的 1406：Data too long for column 'xxx' at row N
_MYSQL_TOO_LONG_COLUMN_RE = re.compile(r"column '([^']+)'")


def mysql_character_column_profiles(conn, table_name: str) -> dict[str, dict[str, object]]:
    """读取 MySQL 目标表上的字符列长度与需要原样重述的属性。

    MySQL 的 MODIFY COLUMN 会**丢弃**未重述的属性，所以这里把 IS_NULLABLE /
    COLUMN_DEFAULT / EXTRA / COLUMN_COMMENT / 字符集 / 排序规则全部取回，
    重建列定义时逐项写回，避免拓宽长度时把主键自增、默认值、注释弄丢。
    """
    with conn.cursor() as cursor:
        cursor.execute(
            """
            select column_name, data_type, character_maximum_length, is_nullable,
                   column_default, extra, column_comment, character_set_name, collation_name
            from information_schema.columns
            where table_schema = database() and table_name = %s
            order by ordinal_position
            """,
            (table_name,),
        )
        profiles: dict[str, dict[str, object]] = {}
        for row in cursor.fetchall():
            data_type = str(row[1] or "").strip().lower()
            if data_type not in MYSQL_CHARACTER_TYPES or row[2] is None:
                continue
            profiles[str(row[0])] = {
                # 原样保留 information_schema 报告的列名大小写：ALTER TABLE ... MODIFY
                # COLUMN 必须使用实际列名，否则会顺带把用户的大写列改成小写。
                "name": str(row[0]),
                "data_type": data_type,
                "length": int(row[2]),
                "nullable": str(row[3] or "").strip().upper() == "YES",
                "default": row[4],
                "extra": str(row[5] or "").strip(),
                "comment": row[6],
                "charset": str(row[7] or "").strip(),
                "collation": str(row[8] or "").strip(),
            }
        return profiles


def mysql_sql_literal(conn, value: object) -> str:
    """把 Python 值渲染成 MySQL 字面量；CURRENT_TIMESTAMP 之类表达式原样输出。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value)
    if _MYSQL_DEFAULT_EXPRESSION_RE.match(text.strip()):
        return text.strip()
    escape = getattr(conn, "escape", None)
    if callable(escape):
        return str(escape(text))
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def mysql_max_text_length(rows: list[list[object]], index: int) -> int:
    """返回本批数据中第 index 列的最大字符长度（None 忽略，非字符串按 str() 长度计）。"""
    longest = 0
    for row in rows:
        if index >= len(row):
            continue
        value = row[index]
        if value is None:
            continue
        text = value if isinstance(value, str) else str(value)
        if len(text) > longest:
            longest = len(text)
    return longest


def expand_mysql_character_columns(
    conn, table_name: str, columns: list[str], rows: list[list[object]], fields: dict[str, str]
) -> None:
    """autoExpand：把既有字符列拓宽到能容纳本批数据（只加宽，绝不缩短）。

    仅处理 DATA_TYPE ∈ {varchar, char} 的列，且仅当本批实际最大长度 > 现有长度时才 ALTER；
    新长度取 max(现有长度, 本批实际最大长度)。其他类型不处理，长度不足时由插入阶段给出提示。
    """
    if not rows:
        return
    profiles = mysql_character_column_profiles(conn, table_name)
    if not profiles:
        return
    # 列名匹配同样要大小写不敏感：既有大写列 NAME 必须能被导入侧的小写 name 命中，
    # 否则该列永远不会被拓宽，长数据最终在插入阶段报 1406。
    profile_index = {str(profile["name"]).strip().lower(): profile for profile in profiles.values()}
    table = db_quote(table_name, fields)
    for index, column in enumerate(columns):
        profile = profile_index.get(column.strip().lower())
        if not profile:
            continue
        # 但重述列定义时必须用**实际列名**：MODIFY COLUMN `name` 会把大写列改名。
        actual_column = str(profile["name"])
        current = int(profile["length"])
        needed = mysql_max_text_length(rows, index)
        if needed <= current:
            continue
        definition = f"{db_quote(actual_column, fields)} {profile['data_type']}({needed})"
        if profile["charset"]:
            definition += f" character set {profile['charset']}"
        if profile["collation"]:
            definition += f" collate {profile['collation']}"
        definition += " null" if profile["nullable"] else " not null"
        if profile["default"] is not None:
            definition += f" default {mysql_sql_literal(conn, profile['default'])}"
        if profile["extra"]:
            definition += " " + str(profile["extra"])
        if profile["comment"] not in (None, ""):
            definition += f" comment {mysql_sql_literal(conn, profile['comment'])}"
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"alter table {table} modify column {definition}")
        except Exception as exc:
            # ALTER 失败（如参与主键/索引长度限制）必须暴露原因，不能吞掉。
            raise ValueError(
                f"自动扩展失败：无法把字段 {actual_column} 从 {current} 拓宽到 {needed} 字符，"
                f"请检查该字段是否参与主键/索引长度限制或手动调整表结构。原始错误：{exc}"
            ) from exc


def mysql_error_code(exc: Exception) -> int:
    """pymysql 异常的 args 通常是 (errno, errmsg)，取出 errno；取不到时返回 0。"""
    args = getattr(exc, "args", ())
    if args and isinstance(args[0], int):
        return int(args[0])
    return 0


# 常见 MySQL 错误码 → 可操作的中文提示。缺陷背景：断连时前端看到的是
# `(2003, "Can't connect to MySQL server on '127.0.0.1' ([WinError 10061] ...)")`
# 这类以驱动原文开头的消息，用户无法判断该改哪里。
_MYSQL_ERROR_HINTS: dict[int, str] = {
    2002: "无法连接到目标数据库服务器，请确认地址与端口是否正确、服务是否已启动",
    2003: "无法连接到目标数据库服务器，请确认地址与端口是否正确、服务是否已启动",
    2005: "无法解析目标数据库的主机名，请检查连接地址是否为 IP 或可解析的域名",
    1045: "目标数据库认证失败，请检查用户名与密码",
    1044: "当前数据库账号无权访问该数据库，请检查账号授权",
    1049: "目标数据库不存在，请确认数据库名",
    1130: "目标数据库服务器不允许本机连接，请在服务器端放开本机 IP 的访问权限",
    1146: "目标表不存在，请检查目标表名或改用「重建」模式新建",
    1054: "目标表中不存在该字段，请检查字段映射或开启自动扩展",
    1264: "数值超出目标列允许的范围，请调整字段类型后重试",
    1064: "SQL 语法错误，请检查所填写的自定义 SQL",
    1213: "目标数据库出现死锁，请稍后重试",
    1205: "等待目标数据库锁超时，请稍后重试",
}


def mysql_error_detail(exc: Exception) -> str:
    """取出 pymysql 异常的「消息」部分，剥掉 `(errno, ...)` 的元组外壳。"""
    args = getattr(exc, "args", ())
    if len(args) >= 2 and isinstance(args[0], int):
        return str(args[1])
    return str(exc)


def friendly_mysql_error(exc: Exception, action: str = "连接目标数据库") -> Exception:
    """把 pymysql 异常翻译成「中文主句 + 错误码」，不再以驱动原文开头。

    已是我们自己抛出的中文 ValueError 原样透传，避免二次包装。
    """
    if isinstance(exc, ValueError) and not getattr(exc, "args", ()):
        return exc
    code = mysql_error_code(exc)
    if code == 0:
        if isinstance(exc, ValueError):
            return exc
        return ValueError(f"{action}失败：{exc}")
    hint = _MYSQL_ERROR_HINTS.get(code)
    if hint:
        return ValueError(f"{action}失败：{hint}。（错误码 {code}）")
    return ValueError(f"{action}失败：数据库返回错误（错误码 {code}）：{mysql_error_detail(exc)}")


def wrap_mysql_insert_error(exc: Exception) -> Exception:
    """把 MySQL「数据过长」类驱动异常翻译成一句可操作的中文提示。

    非字符类型列（bigint / double / date …）不会被 autoExpand 拓宽，
    因此这里必须给出可落地的建议，而不是把 (1406, "Data too long for column ...")
    原样抛给用户。无法识别时返回原异常，保持其他错误原样暴露。
    """
    if mysql_error_code(exc) != 1406:
        return exc
    matched = _MYSQL_TOO_LONG_COLUMN_RE.search(str(exc))
    column = matched.group(1) if matched else ""
    target = f"字段 {column} " if column else "目标字段 "
    return ValueError(
        f"写入失败：{target}长度不足，数据库拒绝了本批数据。"
        "自动扩展（autoExpand）只会拓宽字符类型（varchar/char）列，"
        "其他类型或因索引限制无法拓宽时，请改用 typeMode=text，"
        "或通过 columnTypeOverrides 调整该字段类型后再导入。"
    )


def target_create_or_expand_table(conn, table_name: str, columns: list[str], rows: list[list[object]], rebuild: bool, allow_expand: bool, fields: dict[str, str]) -> None:
    table = db_quote(table_name, fields)
    column_types = infer_column_types(columns, rows, fields)
    pk_column = primary_key_column(fields)
    pk_auto = bool(auto_pk_column(fields))
    if rebuild:
        with conn.cursor() if target_db_type(fields) == "mysql" else nullcontext(conn) as cursor:
            cursor.execute(f"drop table if exists {table}")
    definitions_list: list[str] = []
    for column in columns:
        definition = f"{db_quote(column, fields)} {column_types[column]}"
        if column == pk_column:
            definition += " PRIMARY KEY"
            if target_db_type(fields) == "mysql" and pk_auto and column_types[column] in {"bigint", "integer"}:
                definition += " AUTO_INCREMENT"
        definitions_list.append(definition)
    definitions = ", ".join(definitions_list)
    sql = f"create table if not exists {table} ({definitions})"
    if target_db_type(fields) == "mysql":
        with conn.cursor() as cursor:
            cursor.execute(sql)
    else:
        conn.execute(sql)
    # 缺列判断必须大小写不敏感（MySQL/SQLite 的列名本身就不区分大小写），
    # 否则「目标表已有大写列 NAME、导入侧列名 name」会被误判成缺列并触发 1060。
    existing_keys = target_existing_column_keys(conn, table_name, fields)
    missing = [column for column in columns if column.strip().lower() not in existing_keys]
    if missing and not allow_expand:
        raise ValueError(f"目标表缺少字段：{', '.join(missing)}")
    for column in missing:
        sql = f"alter table {table} add column {db_quote(column, fields)} {column_types[column]}"
        if target_db_type(fields) == "mysql":
            with conn.cursor() as cursor:
                cursor.execute(sql)
        else:
            conn.execute(sql)
    # autoExpand：既补齐缺失列，也把已存在的**字符列**拓宽到能容纳本批数据。
    # 仅在 MySQL 目标生效（SQLite 弱类型，不做处理）；autoExpand=false 时行为完全不变。
    if allow_expand and target_db_type(fields) == "mysql":
        expand_mysql_character_columns(conn, table_name, columns, rows, fields)


def target_insert_rows(conn, table_name: str, columns: list[str], rows: list[list[object]], fields: dict[str, str], progress=None) -> int:
    if not rows:
        return 0
    table = db_quote(table_name, fields)
    # 合成自增主键由数据库自动生成，写入时排除该列，避免更新/追加时行号与已有主键冲突。
    auto_pk = auto_pk_column(fields)
    insert_columns = [column for column in columns if column != auto_pk]
    pk_index = columns.index(auto_pk) if auto_pk in columns else -1

    def project(row: list[object]) -> list[object]:
        values = [value for index, value in enumerate(row) if index != pk_index] if pk_index >= 0 else list(row)
        return values[: len(insert_columns)]

    quoted_columns = ", ".join(db_quote(column, fields) for column in insert_columns)
    placeholders = ", ".join(db_placeholder(fields) for _ in insert_columns)
    sql = f"insert into {table} ({quoted_columns}) values ({placeholders})"
    batch_size = max(parse_int(fields, "batchRows", 0), 0)
    batches = [rows] if batch_size <= 0 else [rows[index : index + batch_size] for index in range(0, len(rows), batch_size)]
    total = 0
    if target_db_type(fields) == "mysql":
        with conn.cursor() as cursor:
            for batch in batches:
                try:
                    cursor.executemany(sql, [project(row) for row in batch])
                except Exception as exc:
                    wrapped = wrap_mysql_insert_error(exc)
                    if wrapped is exc:
                        raise
                    raise wrapped from exc
                total += len(batch)
                if progress:
                    progress(total)
                if fields.get("commitMode") == "batch":
                    conn.commit()
    else:
        for batch in batches:
            conn.executemany(sql, [project(row) for row in batch])
            total += len(batch)
            if progress:
                progress(total)
            if fields.get("commitMode") == "batch":
                conn.commit()
    return total


def target_insert_rows_parallel(table_name: str, columns: list[str], rows: list[list[object]], fields: dict[str, str], progress=None) -> int:
    if not rows:
        return 0
    workers = max(parse_int(fields, "parallelWorkers", 4), 2)
    batch_size = max(parse_int(fields, "batchRows", 1000), 1)
    chunks = [rows[index : index + batch_size] for index in range(0, len(rows), batch_size)]
    completed = 0

    def write_chunk(chunk: list[list[object]]) -> int:
        conn = connect_target_db(fields)
        try:
            target_insert_rows(conn, table_name, columns, chunk, {**fields, "writeMode": "fast"}, None)
            if target_db_type(fields) != "mysql" or fields.get("commitMode") != "auto":
                conn.commit()
            return len(chunk)
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        for count in executor.map(write_chunk, chunks):
            completed += count
            if progress:
                progress(completed)
    return completed


def mysql_load_rows(conn, table_name: str, columns: list[str], rows: list[list[object]], fields: dict[str, str], progress=None) -> int:
    if target_db_type(fields) != "mysql" or not rows:
        return 0
    # 合成自增主键由数据库自动生成，LOAD DATA 时同样排除该列。
    auto_pk = auto_pk_column(fields)
    load_columns = [column for column in columns if column != auto_pk]
    pk_index = columns.index(auto_pk) if auto_pk in columns else -1
    fd, temp_name = tempfile.mkstemp(prefix="codex_load_", suffix=".tsv")
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with temp_path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.writer(file, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
            for row in rows:
                values = [value for index, value in enumerate(row) if index != pk_index] if pk_index >= 0 else list(row)
                writer.writerow(["\\N" if value is None else value for value in values[: len(load_columns)]])
        sql = (
            f"load data local infile {db_placeholder(fields)} into table {db_quote(table_name, fields)} "
            "character set utf8mb4 fields terminated by '\\t' optionally enclosed by '\"' "
            "lines terminated by '\\n' "
            f"({', '.join(db_quote(column, fields) for column in load_columns)})"
        )
        with conn.cursor() as cursor:
            cursor.execute(sql, (str(temp_path).replace("\\", "/"),))
        if progress:
            progress(len(rows))
        return len(rows)
    finally:
        temp_path.unlink(missing_ok=True)


def target_update_rows(conn, table_name: str, columns: list[str], rows: list[list[object]], match_keys: list[str], fields: dict[str, str]) -> tuple[int, int]:
    if not match_keys:
        raise ValueError("更新模式需要至少一个匹配键。")
    key_indexes = [columns.index(key) for key in match_keys if key in columns]
    if not key_indexes:
        raise ValueError("匹配键不在导入字段中。")
    auto_pk = auto_pk_column(fields)
    update_columns = [column for column in columns if column not in match_keys and column != auto_pk]
    inserted = 0
    updated = 0
    table = db_quote(table_name, fields)
    for row in rows:
        where = " and ".join(f"{db_quote(columns[index], fields)} <=> {db_placeholder(fields)}" if target_db_type(fields) == "mysql" else f"{db_quote(columns[index], fields)} is {db_placeholder(fields)}" for index in key_indexes)
        key_values = [row[index] for index in key_indexes]
        select_sql = f"select 1 from {table} where {where} limit 1"
        if target_db_type(fields) == "mysql":
            with conn.cursor() as cursor:
                cursor.execute(select_sql, key_values)
                exists = cursor.fetchone()
        else:
            exists = conn.execute(select_sql, key_values).fetchone()
        if exists:
            if update_columns:
                assignments = ", ".join(f"{db_quote(column, fields)} = {db_placeholder(fields)}" for column in update_columns)
                values = [row[columns.index(column)] for column in update_columns] + key_values
                update_sql = f"update {table} set {assignments} where {where}"
                if target_db_type(fields) == "mysql":
                    with conn.cursor() as cursor:
                        cursor.execute(update_sql, values)
                else:
                    conn.execute(update_sql, values)
            updated += 1
        else:
            target_insert_rows(conn, table_name, columns, [row], fields)
            inserted += 1
    return inserted, updated


def target_execute_sql_batch(conn, sql_text: str, label: str, fields: dict[str, str]) -> None:
    sql_text = (sql_text or "").strip()
    if not sql_text:
        return
    try:
        if target_db_type(fields) == "mysql":
            with conn.cursor() as cursor:
                for statement in [part.strip() for part in sql_text.split(";") if part.strip()]:
                    cursor.execute(statement)
        else:
            conn.executescript(sql_text)
    except Exception as exc:
        raise ValueError(f"{label} 执行失败：{exc}") from exc


def target_export_query_to_excel(conn, sql_text: str, output_name: str, fields: dict[str, str]) -> str:
    sql_text = (sql_text or "").strip()
    if not sql_text:
        return ""
    requested = Path(output_name.strip() or f"query_result_{int(time.time())}.xlsx").name
    output = export_target_path(Path(requested).stem, "xlsx", fields)
    workbook = Workbook()
    sheet = workbook.active
    if target_db_type(fields) == "mysql":
        with conn.cursor() as cursor:
            cursor.execute(sql_text)
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description or []]
        if columns:
            sheet.append(columns)
            for row in rows:
                sheet.append(list(row))
    else:
        rows = conn.execute(sql_text).fetchall()
        if rows:
            columns = rows[0].keys()
            sheet.append(list(columns))
            for row in rows:
                sheet.append([row[column] for column in columns])
    workbook.save(output)
    return str(output)


def create_or_expand_table(conn: sqlite3.Connection, table_name: str, columns: list[str], rebuild: bool, allow_expand: bool) -> None:
    table = quote_identifier(table_name)
    if rebuild:
        conn.execute(f"drop table if exists {table}")
    definitions = ", ".join(f"{quote_identifier(column)} text" for column in columns)
    conn.execute(f"create table if not exists {table} ({definitions})")
    existing = existing_columns(conn, table_name)
    missing = [column for column in columns if column not in existing]
    if missing and not allow_expand:
        raise ValueError(f"目标表缺少字段：{', '.join(missing)}")
    for column in missing:
        conn.execute(f"alter table {table} add column {quote_identifier(column)} text")


def insert_rows(conn: sqlite3.Connection, table_name: str, columns: list[str], rows: list[list[object]]) -> int:
    if not rows:
        return 0
    table = quote_identifier(table_name)
    quoted_columns = ", ".join(quote_identifier(column) for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    sql = f"insert into {table} ({quoted_columns}) values ({placeholders})"
    conn.executemany(sql, [row[: len(columns)] for row in rows])
    return len(rows)


def update_rows(conn: sqlite3.Connection, table_name: str, columns: list[str], rows: list[list[object]], match_keys: list[str]) -> tuple[int, int]:
    if not match_keys:
        raise ValueError("更新模式需要至少一个匹配键。")
    key_indexes = [columns.index(key) for key in match_keys if key in columns]
    if not key_indexes:
        raise ValueError("匹配键不在导入字段中。")

    update_columns = [column for column in columns if column not in match_keys]
    updated = 0
    inserted = 0
    table = quote_identifier(table_name)

    for row in rows:
        where = " and ".join(f"{quote_identifier(columns[index])} is ?" for index in key_indexes)
        key_values = [row[index] for index in key_indexes]
        exists = conn.execute(f"select 1 from {table} where {where} limit 1", key_values).fetchone()
        if exists:
            if update_columns:
                assignments = ", ".join(f"{quote_identifier(column)} = ?" for column in update_columns)
                values = [row[columns.index(column)] for column in update_columns] + key_values
                conn.execute(f"update {table} set {assignments} where {where}", values)
            updated += 1
        else:
            insert_rows(conn, table_name, columns, [row])
            inserted += 1
    return inserted, updated


def execute_sql_batch(conn: sqlite3.Connection, sql_text: str, label: str) -> None:
    sql_text = (sql_text or "").strip()
    if not sql_text:
        return
    try:
        conn.executescript(sql_text)
    except sqlite3.Error as exc:
        raise ValueError(f"{label} 执行失败：{exc}") from exc


def export_query_to_excel(conn: sqlite3.Connection, sql_text: str, output_name: str) -> str:
    sql_text = (sql_text or "").strip()
    if not sql_text:
        return ""
    output = EXPORTS / (output_name.strip() or f"query_result_{int(time.time())}.xlsx")
    if output.suffix.lower() != ".xlsx":
        output = output.with_suffix(".xlsx")
    rows = conn.execute(sql_text).fetchall()
    workbook = Workbook()
    sheet = workbook.active
    if rows:
        columns = rows[0].keys()
        sheet.append(list(columns))
        for row in rows:
            sheet.append([row[column] for column in columns])
    workbook.save(output)
    return str(output)


def safe_file_stem(value: str, fallback: str = "export") -> str:
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", (value or "").strip())
    stem = re.sub(r"\s+", " ", stem).strip(" ._")
    return (stem or fallback)[:120]


def safe_sheet_name(value: str, fallback: str = "Sheet1") -> str:
    name = re.sub(r"[:\\/?*\[\]]+", "_", (value or "").strip())
    return (name or fallback)[:31]


def export_sources(fields: dict[str, str]) -> list[dict[str, object]]:
    conn = connect_target_db(fields)
    try:
        if target_db_type(fields) == "mysql":
            result: list[dict[str, object]] = []
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    select table_name, table_type, table_comment, table_rows
                    from information_schema.tables
                    where table_schema = database()
                    order by table_name
                    """
                )
                for row in cursor.fetchall():
                    name = str(row[0])
                    table_type = str(row[1] or "")
                    comment = str(row[2] or "")
                    estimated = int(row[3] or 0)
                    count = estimated
                    approximate = False
                    # P2-7：information_schema 的行数是 InnoDB 估算值（如 2 万行显示
                    # 20,137）。估算 ≤ 10 万的表用 count(*) 取精确值；更大的表保留
                    # 估算值并标注 rowsApproximate，由前端显示"约 N 行"。
                    if table_type.upper() != "BASE TABLE":
                        result.append(
                            {"name": name, "type": table_type, "comment": comment, "rows": count, "rowsApproximate": False}
                        )
                        continue
                    if estimated <= 100_000:
                        try:
                            cursor.execute(f"select count(*) from {db_quote(name, fields)}")
                            count = int(list(cursor.fetchone())[0])
                        except Exception:
                            count = estimated
                            approximate = True
                    else:
                        approximate = True
                    result.append(
                        {"name": name, "type": table_type, "comment": comment, "rows": count, "rowsApproximate": approximate}
                    )
            return result
        rows = conn.execute(
            """
            select name, type
            from sqlite_master
            where type in ('table', 'view') and name not like 'sqlite_%' and name not like '\\_%' escape '\\'
            order by name
            """
        ).fetchall()
        result = []
        for row in rows:
            count = 0
            try:
                count = conn.execute(f"select count(*) from {db_quote(row['name'], fields)}").fetchone()[0]
            except Exception:
                count = 0
            result.append({"name": row["name"], "type": row["type"], "comment": "", "rows": count, "rowsApproximate": False})
        return result
    finally:
        conn.close()


def target_table_names(fields: dict[str, str]) -> list[str]:
    conn = connect_target_db(fields)
    try:
        if target_db_type(fields) == "mysql":
            with conn.cursor() as cursor:
                cursor.execute("show full tables")
                rows = cursor.fetchall()
            names: list[str] = []
            for row in rows:
                if len(row) > 1 and str(row[1]).upper() != "BASE TABLE":
                    continue
                names.append(str(row[0]))
            return names
        rows = conn.execute(
            """
            select name
            from sqlite_master
            where type = 'table' and name not like 'sqlite_%' and name not like '\\_%' escape '\\'
            order by name
            """
        ).fetchall()
        return [str(row["name"]) for row in rows]
    finally:
        conn.close()


def target_table_details(fields: dict[str, str], table_name: str) -> dict[str, object]:
    table_name = table_name.strip()
    if not table_name:
        raise ValueError("请选择要查看的表。")
    conn = connect_target_db(fields)
    try:
        columns: list[dict[str, object]] = []
        ddl = ""
        table_type = "TABLE"
        comment = ""
        if target_db_type(fields) == "mysql":
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    select table_type, table_comment
                    from information_schema.tables
                    where table_schema = database() and table_name = %s
                    """,
                    (table_name,),
                )
                table_row = cursor.fetchone()
                if not table_row:
                    raise ValueError("目标表不存在或当前账号无权访问。")
                table_type = str(table_row[0] or "TABLE")
                comment = str(table_row[1] or "")
                cursor.execute(
                    """
                    select ordinal_position, column_name, column_type, is_nullable,
                           column_default, column_key, extra, column_comment
                    from information_schema.columns
                    where table_schema = database() and table_name = %s
                    order by ordinal_position
                    """,
                    (table_name,),
                )
                columns = [
                    {
                        "position": row[0],
                        "name": row[1],
                        "type": row[2],
                        "nullable": row[3] == "YES",
                        "default": cell_to_text(row[4]),
                        "key": row[5] or "",
                        "extra": row[6] or "",
                        "comment": row[7] or "",
                    }
                    for row in cursor.fetchall()
                ]
                cursor.execute(f"show create {'view' if table_type == 'VIEW' else 'table'} {db_quote(table_name, fields)}")
                create_row = cursor.fetchone()
                ddl = str(create_row[1] if create_row and len(create_row) > 1 else "")
                cursor.execute(f"select * from {db_quote(table_name, fields)} limit 100")
                preview_columns = [str(item[0]) for item in cursor.description or []]
                preview_rows = [[cell_to_text(cell) for cell in row] for row in cursor.fetchall()]
        else:
            exists = conn.execute("select type, sql from sqlite_master where name = ? and type in ('table', 'view')", (table_name,)).fetchone()
            if not exists:
                raise ValueError("目标表不存在。")
            table_type = str(exists["type"] or "table").upper()
            ddl = str(exists["sql"] or "")
            for row in conn.execute(f"pragma table_info({db_quote(table_name, fields)})").fetchall():
                columns.append({"position": row["cid"] + 1, "name": row["name"], "type": row["type"], "nullable": not bool(row["notnull"]), "default": cell_to_text(row["dflt_value"]), "key": "PRI" if row["pk"] else "", "extra": "", "comment": ""})
            cursor = conn.execute(f"select * from {db_quote(table_name, fields)} limit 100")
            preview_columns = [str(item[0]) for item in cursor.description or []]
            preview_rows = [[cell_to_text(cell) for cell in row] for row in cursor.fetchall()]
        return {"name": table_name, "type": table_type, "comment": comment, "columns": columns, "ddl": ddl, "previewColumns": preview_columns, "previewRows": preview_rows}
    finally:
        conn.close()


def export_split_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return split_values(str(value or ""))


def export_field_list(value: object) -> list[str]:
    return export_split_list(value)


def split_sql_statements(sql: str) -> list[str]:
    """按分号切分 SQL 文本，跳过**引号内部**与**注释内部**的分号（引号 + 注释感知）。

    - 引号：单引号字符串、双引号标识符/字符串、反引号标识符；反斜杠转义（MySQL 风格）
      与引号双写（'' / "" / ``）都按"仍在字面量内"处理。
    - 注释：``/* ... */`` 块注释（不要求嵌套，未闭合时剩余全部按注释处理）；
      行注释 ``--`` **仅当其后跟空白或位于行尾**（MySQL 规则，因此 ``select 1--2``
      里的 ``--`` 不是注释）；``#`` 行注释。
    - 纯注释片段过滤：切分时维护 ``has_code`` 标记，**只有跳过注释与空白后仍有实际
      代码**的片段才算一条语句；纯注释 / 纯空白 / 纯注释+空白的片段直接被丢弃。因此
      ``select 1; -- tail`` 只有一条语句（``-- tail`` 属纯注释尾块），而
      ``/* c1 */; select 1`` 也归一为一条（``/* c1 */`` 属纯注释前块）。
    - 关键不变量：
        1. 含代码的片段，其原文**逐字节 append** 进当前语句缓冲，本函数只决定在哪里
           切分，绝不删改任何 SQL 文本。
        2. **剥掉注释与空白后，输出与输入的代码内容完全一致**（语义零改动）；
           被丢弃的只有"不含任何代码"的片段。
        3. **无纯注释片段**的输入，仍满足更强的旧不变量：只产出一条语句时，返回值与
           「去掉首尾空白 + 尾部分号后的原输入」逐字节一致（``select 1--2 as x`` 不会
           被截成 ``select 1``）。

    返回去掉首尾空白后的非空语句列表；分隔用的分号本身不属于任何语句。全为注释/空白/
    分号的输入返回空列表（交给调用方按"无语句"处理，不在这里抛错）。
    """
    statements: list[str] = []
    current: list[str] = []
    quote_char = ""
    has_code = False
    index = 0
    length = len(sql)
    while index < length:
        char = sql[index]
        if quote_char:
            current.append(char)
            if char == "\\" and quote_char != "`" and index + 1 < length:
                # 反斜杠转义：下一个字符无论是什么都不结束字面量
                current.append(sql[index + 1])
                index += 2
                continue
            if char == quote_char:
                if index + 1 < length and sql[index + 1] == quote_char:
                    # 引号双写（'' / "" / ``）表示字面量里的引号本身，字面量未结束
                    current.append(sql[index + 1])
                    index += 2
                    continue
                quote_char = ""
            index += 1
            continue
        if char in {"'", '"', "`"}:
            # 字面量本身即代码：开启引号即标记本片段含代码
            quote_char = char
            current.append(char)
            has_code = True
            index += 1
            continue
        if char == "/" and sql.startswith("/*", index):
            # 块注释：整段原样保留（含未闭合时的剩余全部），内部的分号不切分；
            # 注释不计入 has_code，故纯注释片段会被丢弃
            closing = sql.find("*/", index + 2)
            end = length if closing < 0 else closing + 2
            current.append(sql[index:end])
            index = end
            continue
        if char == "-" and sql.startswith("--", index) and (index + 2 >= length or sql[index + 2].isspace()):
            # MySQL 行注释：-- 后必须跟空白/换行或位于行尾；不跟空白的 -- 是运算符/双写负号
            newline = sql.find("\n", index)
            end = length if newline < 0 else newline
            current.append(sql[index:end])
            index = end
            continue
        if char == "#":
            # MySQL 行注释：独占到行尾
            newline = sql.find("\n", index)
            end = length if newline < 0 else newline
            current.append(sql[index:end])
            index = end
            continue
        if char == ";":
            # 仅当本片段含实际代码时才产出一条语句；纯注释/纯空白片段被丢弃
            if has_code:
                text = "".join(current).strip()
                if text:
                    statements.append(text)
            current = []
            has_code = False
        else:
            current.append(char)
            if not char.isspace():
                has_code = True
        index += 1
    if has_code:
        tail = "".join(current).strip()
        if tail:
            statements.append(tail)
    return statements


def sql_after_leading_comments(sql: str) -> str:
    """跳过 SQL 开头连续的空白与注释，返回从第一段实际代码开始的后缀。

    仅用于「取首个关键字」这一判断用途，不改变语义（真正执行的仍是原 SQL 文本）。
    注释规则与 ``split_sql_statements`` 保持一致：
    ``/* ... */`` 块注释、``--`` 后跟空白或行尾的行注释、``#`` 行注释。
    若整段都是注释 / 空白，返回空字符串。
    """
    index = 0
    length = len(sql)
    while index < length:
        char = sql[index]
        if char.isspace():
            index += 1
            continue
        if sql.startswith("/*", index):
            closing = sql.find("*/", index + 2)
            if closing < 0:
                return ""
            index = closing + 2
            continue
        if char == "-" and sql.startswith("--", index) and (index + 2 >= length or sql[index + 2].isspace()):
            newline = sql.find("\n", index)
            if newline < 0:
                return ""
            index = newline + 1
            continue
        if char == "#":
            newline = sql.find("\n", index)
            if newline < 0:
                return ""
            index = newline + 1
            continue
        break
    return sql[index:]


def export_query_from_item(item: dict[str, object], fields: dict[str, str]) -> tuple[str, str]:
    item_type = str(item.get("type") or "table")
    if item_type == "query":
        sql = str(item.get("sql") or "").strip()
        if not sql:
            raise ValueError("查询 SQL 不能为空。")
        # 归一化：去掉首尾空白与尾部分号（保持既有行为），同时给出可操作的多语句提示，
        # 不再把驱动原文（如 near ";" syntax error）直接抛给用户。
        statements = split_sql_statements(sql)
        if len(statements) > 1:
            raise ValueError("一次只能导出一条 SQL；检测到多条语句，请改用「多个查询」模式。")
        if not statements:
            # 整个输入只有空白/分号：保持原有行为，交给数据库报错。
            return sql.rstrip(";").strip(), str(item.get("name") or "query")
        return statements[0], str(item.get("name") or "query")
    table_name = str(item.get("table") or item.get("name") or "").strip()
    if not table_name:
        raise ValueError("请选择要导出的表。")
    selected_fields = export_field_list(fields.get("exportFields", ""))
    columns_sql = ", ".join(db_quote(column, fields) for column in selected_fields) if selected_fields else "*"
    sql = f"select {columns_sql} from {db_quote(table_name, fields)}"
    where = str(fields.get("whereClause") or "").strip()
    if where:
        sql += " where " + re.sub(r"^\s*where\s+", "", where, flags=re.I)
    return sql, table_name


def export_header_labels(item: dict[str, object], columns: list[str], fields: dict[str, str], conn) -> list[str]:
    """按 headerMode 解析导出表头标签（三条写出路径共用）。

    - headerMode != "comment"（field / none）：原样返回 columns，行为与修复前逐字节一致。
    - headerMode == "comment" 且目标是 MySQL 且 item 是表：取 information_schema 的字段注释，
      注释为空的列回退用列名。
    - headerMode == "comment" 但源头是查询（type=query）或目标是 SQLite：
      两者都没有字段注释可用，一律回退用列名（不报错）。
    """
    if str(fields.get("headerMode") or "field").lower() != "comment":
        return columns
    if target_db_type(fields) != "mysql" or str(item.get("type") or "table") != "table":
        return columns
    table_name = str(item.get("table") or item.get("name") or "").strip()
    if not table_name:
        return columns
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                select column_name, column_comment from information_schema.columns
                where table_schema = database() and table_name = %s
                """,
                (table_name,),
            )
            comments = {str(row[0]): str(row[1] or "").strip() for row in cursor.fetchall()}
    except Exception:
        # 读不到注释时不影响导出本身，回退列名。
        return columns
    return [comments.get(column) or column for column in columns]


EXPORT_SOURCE_KEYWORDS = {"select", "with", "show", "describe", "desc", "explain", "table", "values"}


def ensure_query_source(sql: str, action: str = "导出") -> str:
    """导出源 / 预览源必须是**查询语句**。

    查询模块已放开写权限，但导出链路会把 SQL 包成 ``select * from (<sql>) limit N``，
    写语句（INSERT/UPDATE/DELETE/DDL）塞进去只会得到一个看不懂的语法错误。
    这里提前拦下并给出可操作提示，而不是让用户在包装后报错里猜。
    """
    statements = split_sql_statements(sql)
    if not statements:
        raise ValueError(f"{action}源的 SQL 为空，请先填写查询语句。")
    if len(statements) > 1:
        raise ValueError(f"{action}源只能是一条查询语句，当前有 {len(statements)} 条（导出不支持多语句脚本）。")
    keyword = sql_first_keyword(statements[0])
    if keyword not in EXPORT_SOURCE_KEYWORDS:
        raise ValueError(
            f"{action}源只支持查询语句（SELECT / WITH / SHOW / DESCRIBE / EXPLAIN），"
            f"当前是 {keyword.upper() or '未知'}。如需先改数据，请在「查询」页面执行后再导出。"
        )
    return statements[0]


def fetch_export_rows(conn, sql: str, fields: dict[str, str], limit: int = 0) -> tuple[list[str], list[list[object]]]:
    ensure_query_source(sql)
    query = sql.strip().rstrip(";")
    if limit > 0:
        if target_db_type(fields) == "mysql":
            query = f"select * from ({query}) export_preview limit {limit}"
        else:
            query = f"select * from ({query}) limit {limit}"
    if target_db_type(fields) == "mysql":
        with conn.cursor() as cursor:
            cursor.execute(query)
            columns = [desc[0] for desc in cursor.description or []]
            rows = [list(row) for row in cursor.fetchall()]
        return columns, rows
    cursor = conn.execute(query)
    columns = [desc[0] for desc in cursor.description or []]
    return columns, [list(row) for row in cursor.fetchall()]


def export_row_batches(conn, sql: str, fields: dict[str, str], fetch_size: int = EXPORT_FETCH_SIZE) -> Iterator[tuple[list[str], list[list[object]]]]:
    ensure_query_source(sql)
    query = sql.strip().rstrip(";")
    if target_db_type(fields) == "mysql":
        cursor = conn.cursor(pymysql.cursors.SSCursor)
        try:
            cursor.execute(query)
            columns = [desc[0] for desc in cursor.description or []]
            while True:
                batch = cursor.fetchmany(fetch_size)
                if not batch:
                    break
                yield columns, [list(row) for row in batch]
        finally:
            cursor.close()
        return

    cursor = conn.execute(query)
    columns = [desc[0] for desc in cursor.description or []]
    while True:
        batch = cursor.fetchmany(fetch_size)
        if not batch:
            break
        yield columns, [list(row) for row in batch]


def add_export_time_column(columns: list[str], rows: list[list[object]], field_name: str) -> tuple[list[str], list[list[object]]]:
    field_name = (field_name or "").strip()
    if not field_name:
        return columns, rows
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return columns + [field_name], [row + [now] for row in rows]


def add_export_time_to_batch(rows: list[list[object]], field_name: str, now: str) -> list[list[object]]:
    if not field_name:
        return rows
    return [row + [now] for row in rows]


def split_rows_by_batch(rows: list[list[object]], fields: dict[str, str]) -> list[tuple[str, list[list[object]]]]:
    """按 batchRows 把行拆成多块，返回 [(文件名后缀, 行块)]。

    开启 splitByBatch 且 batchRows>0 时：拆成 _001/_002/...（仅一块时不加后缀），
    所有行都会导出，不再截断。未开启时返回单块、无后缀。
    """
    batch_rows = int(fields.get("batchRows") or 0)
    if not parse_bool(fields, "splitByBatch", False) or batch_rows <= 0:
        return [("", rows)]
    chunks = [rows[start:start + batch_rows] for start in range(0, len(rows), batch_rows)]
    if not chunks:
        chunks = [[]]
    if len(chunks) == 1:
        return [("", chunks[0])]
    return [(f"_{index + 1:03d}", chunk) for index, chunk in enumerate(chunks)]


def rows_to_dicts(columns: list[str], rows: list[list[object]]) -> list[dict[str, object]]:
    return [{column: row[index] if index < len(row) else None for index, column in enumerate(columns)} for row in rows]


def row_to_dict(columns: list[str], row: list[object]) -> dict[str, object]:
    return {column: row[index] if index < len(row) else None for index, column in enumerate(columns)}


def open_exported_files(fields: dict[str, str], files: list[str]) -> None:
    """导出完成后按界面选项在本机打开文件或所在文件夹。

    这是桌面工具的使用场景：服务跑在用户自己机器上，用系统默认程序打开。
    非 Windows、没有桌面会话、路径不存在时静默跳过，绝不影响导出结果本身。
    此前这两个选项只被前端采集进任务配置，后端从未读取，属于"假开关"。
    """
    if os.name != "nt":
        return
    want_file = parse_bool(fields, "openFileAfterExport", False)
    want_folder = parse_bool(fields, "openFolderAfterExport", False)
    if not (want_file or want_folder):
        return

    def launch(target: str, opened: set[str]) -> None:
        if not target or target in opened:
            return
        opened.add(target)
        try:
            os.startfile(target)  # type: ignore[attr-defined]
        except Exception:
            # 打开失败不影响导出结果，用户仍可手动去目录里取文件
            return

    opened: set[str] = set()
    if want_folder:
        for item in files:
            folder = Path(str(item)).parent
            if folder.is_dir():
                launch(str(folder), opened)
    if want_file:
        # 只打开前几个，避免一次导出几十个文件时弹满屏幕的窗口
        for item in files[:5]:
            path = Path(str(item))
            if path.is_file():
                launch(str(path), opened)


def export_target_path(base_name: str, extension: str, fields: dict[str, str]) -> Path:
    extension = extension.lower().lstrip(".") or "xlsx"
    if extension == "xls":
        raise ValueError("当前版本不支持 .xls 导出，请选择 .xlsx。")
    if extension == "dbf":
        raise ValueError("当前版本暂不支持 DBF 导出。")
    prefix = safe_file_stem(str(fields.get("filePrefix") or ""), "")
    suffix = safe_file_stem(str(fields.get("fileSuffix") or ""), "")
    name = safe_file_stem(f"{prefix}{base_name}{suffix}", "export")
    target_mode = str(fields.get("exportTargetMode") or "folder").strip().lower()
    if target_mode == "folder":
        folder_value = str(fields.get("exportFolder") or "").strip()
        if folder_value:
            folder = Path(folder_value).expanduser()
            if not folder.is_absolute():
                raise ValueError(f"目标文件夹必须是完整路径，当前保存的是：{folder_value}")
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise ValueError(f"无法创建目标文件夹：{folder}（{exc}）") from exc
            if not folder.is_dir():
                raise ValueError(f"目标文件夹不是目录：{folder}")
            return folder / f"{name}.{extension}"
    elif target_mode == "file":
        file_value = str(fields.get("outputName") or "").strip()
        if file_value:
            output = Path(file_value).expanduser()
            if output.is_absolute():
                if not output.parent.exists() or not output.parent.is_dir():
                    raise ValueError(f"目标文件所在文件夹不存在：{output.parent}")
                return output.with_suffix(f".{extension}")
    return EXPORTS / f"{name}.{extension}"


def native_pick_path(mode: str, initial: str = "", extension: str = "", suggest: str = "") -> str:
    """弹 Windows 原生对话框选目录/文件，返回绝对路径；用户取消返回空串。

    为什么要放在服务端：浏览器出于安全永远不把绝对路径交给页面
    （showDirectoryPicker 只给 handle.name，界面上那句"已选择文件夹：xxx"就是这么来的），
    而本工具的服务端与浏览器同机运行，只有服务端弹原生对话框才能拿到 D:\\导出 这样的完整路径。
    好处是路径能直接存进任务配置，由服务端落盘，定时任务（无浏览器）同样生效。

    只依赖标准库 ctypes —— 本项目 venv 不含 tkinter，也无法保证目标机装有 PowerShell 模块。
    """
    if os.name != "nt":
        raise NotImplementedError("服务端不是 Windows，无法打开系统选择框。")

    import ctypes
    from ctypes import wintypes

    mode = (mode or "folder").strip().lower()
    max_path = 260

    def resolve_initial_dir(value: str) -> str:
        if not value:
            return ""
        candidate = Path(value).expanduser()
        probe = candidate if candidate.is_dir() else candidate.parent
        return str(probe) if probe.is_dir() else ""

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    ole32.CoInitialize.argtypes = [ctypes.c_void_p]
    ole32.CoInitialize.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]

    # SHBrowseForFolderW / GetSaveFileNameW 要求调用线程处于 STA；HTTP 处理线程每次都是新建的，
    # 所以这里自行初始化。返回 0(S_OK) 或 1(S_FALSE，本线程此前已初始化) 都要配对 CoUninitialize。
    com_initialized = ole32.CoInitialize(None) in (0, 1)
    try:
        if mode == "folder":
            # 为什么不用 SHBrowseForFolderW：
            #   实测在 BIF_NEWDIALOGSTYLE 下 BFFM_SETSELECTIONW 完全不生效 —— 8 种组合
            #   （发给顶层 #32770 / 回调 hwnd / 回调 root，传 pidl / 传宽字符串，带 /
            #   不带 NEWDIALOGSTYLE）SendMessage 全返回 0，对话框里显示的还是默认目录，
            #   用户点确定只能拿到 C:\Users\<用户>。回调本身是通的（确实收到
            #   BFFM_INITIALIZED=1，且 hwnd 就是顶层对话框），所以不是接线问题。
            # 改用 Vista+ 的通用项对话框：IFileDialog + FOS_PICKFOLDERS + SetFolder，
            #   实测地址栏直接显示 initial 目录，点确定返回的正是该目录。
            SIGDN_FILESYSPATH = 0x80058000
            S_OK = 0
            ERROR_CANCELLED = 0x800704C7
            CLSCTX_INPROC_SERVER = 1
            FOS_PICKFOLDERS = 0x00000020
            FOS_FORCEFILESYSTEM = 0x00000040
            FOS_PATHMUSTEXIST = 0x00000800
            CLSID_FILE_OPEN_DIALOG = "DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7"
            IID_FILE_DIALOG = "42F85136-DB7E-439C-85F1-E4075D135FC8"
            IID_SHELL_ITEM = "43826D1E-E718-42EE-BC55-A1E261C37BFE"
            # COM vtable 下标：IUnknown(QueryInterface/AddRef/Release)=0/1/2，
            # IModalWindow::Show=3，IFileDialog::SetOptions=9、SetFolder=12、
            # SetTitle=17、GetResult=20，IShellItem::GetDisplayName=5。
            VT_RELEASE = 2
            VT_SHOW = 3
            VT_SET_OPTIONS = 9
            VT_SET_FOLDER = 12
            VT_SET_TITLE = 17
            VT_GET_RESULT = 20
            VT_GET_DISPLAY_NAME = 5

            class GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", ctypes.c_ulong),
                    ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

            ole32.CLSIDFromString.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(GUID)]
            ole32.CLSIDFromString.restype = ctypes.c_long
            ole32.CoCreateInstance.argtypes = [
                ctypes.POINTER(GUID),
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(GUID),
                ctypes.POINTER(ctypes.c_void_p),
            ]
            ole32.CoCreateInstance.restype = ctypes.c_long
            shell32.SHCreateItemFromParsingName.argtypes = [
                wintypes.LPCWSTR,
                ctypes.c_void_p,
                ctypes.POINTER(GUID),
                ctypes.POINTER(ctypes.c_void_p),
            ]
            shell32.SHCreateItemFromParsingName.restype = ctypes.c_long

            def make_guid(text: str) -> GUID:
                """字符串转 GUID；CLSIDFromString 只认带花括号的写法。"""
                value = GUID()
                if ole32.CLSIDFromString("{" + text + "}", ctypes.byref(value)) != 0:
                    raise RuntimeError(f"无效的 COM GUID：{text}")
                return value

            def com_method(pointer, index: int, restype, *arg_types):
                """按 vtable 下标取 COM 方法（this 指针作为第一个参数传入）。"""
                vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
                return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *arg_types)(vtable[index])

            dialog = ctypes.c_void_p()
            folder_item = ctypes.c_void_p()
            result_item = ctypes.c_void_p()
            try:
                created = ole32.CoCreateInstance(
                    ctypes.byref(make_guid(CLSID_FILE_OPEN_DIALOG)),
                    None,
                    CLSCTX_INPROC_SERVER,
                    ctypes.byref(make_guid(IID_FILE_DIALOG)),
                    ctypes.byref(dialog),
                )
                if created != S_OK or not dialog.value:
                    raise RuntimeError(f"无法创建系统文件夹选择对话框（HRESULT={created & 0xFFFFFFFF:#010x}）")

                set_options = com_method(dialog, VT_SET_OPTIONS, ctypes.c_long, wintypes.DWORD)
                set_options(dialog, FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST)
                set_title = com_method(dialog, VT_SET_TITLE, ctypes.c_long, wintypes.LPCWSTR)
                set_title(dialog, "选择导出文件夹（选择后会显示完整路径）")

                # initial 不存在时退到它的父目录；再不可用就不设初值，让系统用自己的默认位置。
                directory = resolve_initial_dir(initial)
                if directory:
                    item_hr = shell32.SHCreateItemFromParsingName(
                        directory,
                        None,
                        ctypes.byref(make_guid(IID_SHELL_ITEM)),
                        ctypes.byref(folder_item),
                    )
                    if item_hr == S_OK and folder_item.value:
                        set_folder = com_method(dialog, VT_SET_FOLDER, ctypes.c_long, ctypes.c_void_p)
                        set_folder(dialog, folder_item.value)

                shown = com_method(dialog, VT_SHOW, ctypes.c_long, wintypes.HWND)(dialog, None)
                if shown & 0xFFFFFFFF == ERROR_CANCELLED:
                    return ""
                if shown != S_OK:
                    raise RuntimeError(f"文件夹选择对话框返回失败（HRESULT={shown & 0xFFFFFFFF:#010x}）")

                get_result = com_method(
                    dialog, VT_GET_RESULT, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p)
                )
                if get_result(dialog, ctypes.byref(result_item)) != S_OK or not result_item.value:
                    return ""
                get_display_name = com_method(
                    result_item,
                    VT_GET_DISPLAY_NAME,
                    ctypes.c_long,
                    ctypes.c_uint,
                    ctypes.POINTER(ctypes.c_void_p),
                )
                path_pointer = ctypes.c_void_p()
                if get_display_name(result_item, SIGDN_FILESYSPATH, ctypes.byref(path_pointer)) != S_OK:
                    return ""
                if not path_pointer.value:
                    return ""
                try:
                    return ctypes.wstring_at(path_pointer.value)
                finally:
                    ole32.CoTaskMemFree(path_pointer)
            finally:
                # 三个 COM 对象按「后拿先放」顺序 Release，避免泄漏。
                for borrowed in (result_item, folder_item, dialog):
                    if borrowed.value:
                        com_method(borrowed, VT_RELEASE, ctypes.c_ulong)(borrowed)

        if mode == "file":

            class OPENFILENAMEW(ctypes.Structure):
                _fields_ = [
                    ("lStructSize", wintypes.DWORD),
                    ("hwndOwner", wintypes.HWND),
                    ("hInstance", wintypes.HINSTANCE),
                    ("lpstrFilter", wintypes.LPCWSTR),
                    ("lpstrCustomFilter", ctypes.c_void_p),
                    ("nMaxCustFilter", wintypes.DWORD),
                    ("nFilterIndex", wintypes.DWORD),
                    ("lpstrFile", ctypes.c_void_p),
                    ("nMaxFile", wintypes.DWORD),
                    ("lpstrFileTitle", ctypes.c_void_p),
                    ("nMaxFileTitle", wintypes.DWORD),
                    ("lpstrInitialDir", wintypes.LPCWSTR),
                    ("lpstrTitle", wintypes.LPCWSTR),
                    ("Flags", wintypes.DWORD),
                    ("nFileOffset", wintypes.WORD),
                    ("nFileExtension", wintypes.WORD),
                    ("lpstrDefExt", wintypes.LPCWSTR),
                    ("lCustData", ctypes.c_void_p),
                    ("lpfnHook", ctypes.c_void_p),
                    ("lpTemplateName", ctypes.c_void_p),
                    ("pvReserved", ctypes.c_void_p),
                    ("dwReserved", wintypes.DWORD),
                    ("FlagsEx", wintypes.DWORD),
                ]

            ext = (extension or "xlsx").lower().lstrip(".") or "xlsx"
            default_name = (suggest or "export").strip() or "export"
            if not default_name.lower().endswith(f".{ext}"):
                default_name = f"{default_name}.{ext}"
            # lpstrFile 由系统回写，必须给足缓冲：这里传 buffer 地址，不能传 Python 字符串。
            file_buffer = ctypes.create_unicode_buffer(default_name, max_path)
            info = OPENFILENAMEW()
            info.lStructSize = ctypes.sizeof(OPENFILENAMEW)
            info.hwndOwner = None
            info.hInstance = None
            # 过滤器串以双 NUL 结束（片段自带一个，ctypes 再补一个）。
            info.lpstrFilter = f"{ext.upper()} 文件 (*.{ext})\0*.{ext}\0所有文件 (*.*)\0*.*\0"
            info.lpstrCustomFilter = None
            info.nFilterIndex = 1
            info.lpstrFile = ctypes.addressof(file_buffer)
            info.nMaxFile = max_path
            info.lpstrFileTitle = None
            info.nMaxFileTitle = 0
            info.lpstrInitialDir = resolve_initial_dir(initial) or None
            info.lpstrTitle = "选择导出文件保存位置"
            # OVERWRITEPROMPT | HIDEREADONLY | NOCHANGEDIR | EXPLORER
            info.Flags = 0x00000002 | 0x00000004 | 0x00000008 | 0x00080000
            info.lpstrDefExt = ext
            info.lCustData = None
            info.lpfnHook = None
            info.lpTemplateName = None
            info.pvReserved = None
            info.dwReserved = 0
            info.FlagsEx = 0

            comdlg32 = ctypes.WinDLL("comdlg32", use_last_error=True)
            comdlg32.GetSaveFileNameW.argtypes = [ctypes.POINTER(OPENFILENAMEW)]
            comdlg32.GetSaveFileNameW.restype = wintypes.BOOL
            if not comdlg32.GetSaveFileNameW(ctypes.byref(info)):
                return ""
            return file_buffer.value

        raise ValueError(f"不支持的选择类型：{mode}")
    finally:
        if com_initialized:
            ole32.CoUninitialize()


# ---------------------------------------------------------------------------
# M12-011: 给原生选择框加「超时看门狗」。
# native_pick_path 内部的 Show()/GetSaveFileNameW 是同步阻塞的，会一直占用调用线程，
# 直到有人关掉窗口。若调用方超时/断开，窗口就会残留在桌面成为幽灵窗口。
# 这里保持同步接口不变（方案 B），另起一个 watchdog 线程：超时后通过 EnumWindows
# 找到本次请求新弹出的 #32770 窗口并 PostMessage(WM_CLOSE)，让 Show() 返回
# ERROR_CANCELLED，请求以 cancelled:true 正常结束，桌面不再残留窗口。
# 安全点：只按窗口类名 #32770 匹配 + 「本次请求前不存在」的基线排除，绝不按标题关键词
# 过滤（历史上曾因标题关键词匹配误关用户自己的 Chrome 窗口）。
# 注：本环境下 IFileDialog 会把 UI 代理到另一个进程（实测对话框 PID 与服务器不同），
# 按 PID 过滤反而永远匹配不到、无法清理；改用基线对比可无论是否代理都精准命中本请求的窗口。
# 超时时长可配：模块常量 NATIVE_DIALOG_TIMEOUT，或环境变量 NATIVE_DIALOG_TIMEOUT（秒）。
# ---------------------------------------------------------------------------
NATIVE_DIALOG_TIMEOUT = float(os.environ.get("NATIVE_DIALOG_TIMEOUT", "120"))


def _snapshot_dialogs() -> set[int]:
    """快照：当前系统里所有顶层 #32770 窗口（不限进程），用于基线对比。"""
    import ctypes
    from ctypes import wintypes
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    found: set[int] = set()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lparam):
        cls = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, cls, 256)
        if cls.value == "#32770":
            found.add(int(hwnd))
        return True

    u.EnumWindows(cb, 0)
    return found


def _close_new_dialogs(baseline: set[int]) -> list[int]:
    """关闭本次请求新出现的 #32770 窗口（基线之外的），强制 Show()/GetSaveFileNameW 取消。

    只按类名 #32770 匹配 + 基线排除（不碰请求前已存在的窗口，避免误伤用户其它对话框）；
    绝不按标题过滤。返回被关闭窗口的 HWND 列表。
    """
    import ctypes
    from ctypes import wintypes
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    closed: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lparam):
        cls = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, cls, 256)
        if cls.value != "#32770":
            return True
        if int(hwnd) in baseline:
            return True
        u.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        closed.append(int(hwnd))
        return True

    u.EnumWindows(cb, 0)
    return closed


def _dialog_watchdog(timeout: float, baseline: set[int], stop: threading.Event) -> list[int]:
    """看门狗：stop 触发前对话框仍开着（调用方超时/断开），则强制关闭本次新弹出的窗口。"""
    if stop.wait(timeout):
        return []
    return _close_new_dialogs(baseline)


def write_rows_to_sheet(
    sheet,
    columns: list[str],
    rows: list[list[object]],
    fields: dict[str, str],
    header_labels: list[str] | None = None,
) -> None:
    header_mode = str(fields.get("headerMode") or "field").lower()
    include_header = header_mode != "none"
    if include_header:
        sheet.append(header_labels or columns)
    for row in rows:
        sheet.append(row)

    row_height = float(fields.get("rowHeight") or 0)
    if row_height > 0:
        for row_idx in range(1, sheet.max_row + 1):
            sheet.row_dimensions[row_idx].height = row_height
    col_width = float(fields.get("columnWidth") or 0)
    if col_width > 0:
        for col_idx in range(1, sheet.max_column + 1):
            sheet.column_dimensions[get_column_letter(col_idx)].width = col_width

    font_name = str(fields.get("fontName") or "").strip()
    font_size = float(fields.get("fontSize") or 0)
    font = Font(name=font_name or None, size=font_size or None)
    border = Border(left=Side(style="thin"), right=Side(style="thin"), top=Side(style="thin"), bottom=Side(style="thin"))
    add_border = parse_bool(fields, "addBorder", False)
    for row in sheet.iter_rows():
        for cell in row:
            if font_name or font_size:
                cell.font = font
            if add_border:
                cell.border = border

    # 锁定：openpyxl 的单元格默认就是 locked=True，直接开工作表保护会把整张表都变成只读，
    # 而不是只锁表头行或指定列。所以先把整张表解锁，再只锁目标单元格，锁定范围才和界面上
    # 写的「锁定表头行 / 锁定指定列」一致。
    lock_header = parse_bool(fields, "lockHeader", False) and include_header
    locked_columns = export_split_list(fields.get("lockedColumns", ""))
    if lock_header or locked_columns:
        for row in sheet.iter_rows():
            for cell in row:
                cell.protection = Protection(locked=False)
        if lock_header:
            for cell in sheet[1]:
                cell.protection = Protection(locked=True)
        if locked_columns:
            column_indexes = {name: index + 1 for index, name in enumerate(columns)}
            for name in locked_columns:
                col_idx = column_indexes.get(name)
                if col_idx:
                    for row_idx in range(1, sheet.max_row + 1):
                        sheet.cell(row=row_idx, column=col_idx).protection = Protection(locked=True)
        sheet.protection.sheet = True


def write_export_file(
    path: Path,
    columns: list[str],
    rows: list[list[object]],
    fields: dict[str, str],
    sheet_name: str,
    header_labels: list[str] | None = None,
) -> None:
    EXPORTS.mkdir(parents=True, exist_ok=True)
    extension = path.suffix.lower()
    if extension == ".xlsx":
        mode = str(fields.get("exportMode") or "workbook")
        if path.exists() and mode in {"sheet", "data"}:
            workbook = load_workbook(path)
            if sheet_name in workbook.sheetnames:
                del workbook[sheet_name]
            sheet = workbook.create_sheet(sheet_name)
        else:
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = sheet_name
        write_rows_to_sheet(sheet, columns, rows, fields, header_labels)
        workbook.save(path)
        return
    if extension in {".csv", ".txt"}:
        delimiter = decode_escaped(fields.get("delimiter") or ("," if extension == ".csv" else "\t"))
        line_delimiter = decode_escaped(fields.get("lineDelimiter") or "\n")
        encoding = fields.get("encoding") or "utf-8"
        with path.open("w", encoding=encoding, newline="") as handle:
            writer = csv.writer(handle, delimiter=delimiter, lineterminator=line_delimiter)
            if str(fields.get("headerMode") or "field") != "none":
                writer.writerow(header_labels or columns)
            writer.writerows(rows)
        return
    if extension == ".json":
        path.write_text(json.dumps(rows_to_dicts(columns, rows), ensure_ascii=False, indent=2, default=cell_to_text), encoding="utf-8")
        return
    if extension == ".xml":
        root = ET.Element("rows")
        for row in rows_to_dicts(columns, rows):
            node = ET.SubElement(root, "row")
            for key, value in row.items():
                child = ET.SubElement(node, sanitize_identifier(key, "field"))
                child.text = cell_to_text(value)
        ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
        return
    raise ValueError(f"不支持的导出格式：{extension}")


def styled_write_only_row(sheet, values: list[object], fields: dict[str, str], header: bool = False) -> None:
    font_name = str(fields.get("fontName") or "").strip()
    font_size = float(fields.get("fontSize") or 0)
    add_border = parse_bool(fields, "addBorder", False)
    if not (font_name or font_size or add_border or header):
        sheet.append(values)
        return

    font = Font(name=font_name or None, size=font_size or None, bold=header)
    border = Border(left=Side(style="thin"), right=Side(style="thin"), top=Side(style="thin"), bottom=Side(style="thin"))
    cells = []
    for value in values:
        cell = WriteOnlyCell(sheet, value=value)
        if font_name or font_size or header:
            cell.font = font
        if add_border:
            cell.border = border
        cells.append(cell)
    sheet.append(cells)


def write_export_file_streaming(
    path: Path,
    columns: list[str],
    batches: Iterable[list[list[object]]],
    fields: dict[str, str],
    sheet_name: str,
    header_labels: list[str] | None = None,
) -> int:
    EXPORTS.mkdir(parents=True, exist_ok=True)
    extension = path.suffix.lower()
    header_mode = str(fields.get("headerMode") or "field").lower()
    include_header = header_mode != "none"
    rows_written = 0

    if extension == ".xlsx":
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet(sheet_name)
        row_height = float(fields.get("rowHeight") or 0)
        if row_height > 0:
            sheet.sheet_format.defaultRowHeight = row_height
        col_width = float(fields.get("columnWidth") or 0)
        if col_width > 0:
            for col_idx in range(1, len(columns) + 1):
                sheet.column_dimensions[get_column_letter(col_idx)].width = col_width
        if include_header:
            styled_write_only_row(sheet, header_labels or columns, fields, header=True)
        for batch in batches:
            for row in batch:
                styled_write_only_row(sheet, row, fields)
                rows_written += 1
        workbook.save(path)
        return rows_written

    if extension in {".csv", ".txt"}:
        delimiter = decode_escaped(fields.get("delimiter") or ("," if extension == ".csv" else "\t"))
        line_delimiter = decode_escaped(fields.get("lineDelimiter") or "\n")
        encoding = fields.get("encoding") or "utf-8"
        with path.open("w", encoding=encoding, newline="") as handle:
            writer = csv.writer(handle, delimiter=delimiter, lineterminator=line_delimiter)
            if include_header:
                writer.writerow(header_labels or columns)
            for batch in batches:
                writer.writerows(batch)
                rows_written += len(batch)
        return rows_written

    if extension == ".json":
        with path.open("w", encoding="utf-8") as handle:
            handle.write("[\n")
            first = True
            for batch in batches:
                for row in batch:
                    if not first:
                        handle.write(",\n")
                    handle.write(json.dumps(row_to_dict(columns, row), ensure_ascii=False, default=cell_to_text))
                    first = False
                    rows_written += 1
            handle.write("\n]\n")
        return rows_written

    if extension == ".xml":
        with path.open("w", encoding="utf-8") as handle:
            handle.write('<?xml version="1.0" encoding="utf-8"?>\n<rows>\n')
            safe_columns = [sanitize_identifier(column, "field") for column in columns]
            for batch in batches:
                for row in batch:
                    handle.write("  <row>\n")
                    for index, column in enumerate(safe_columns):
                        value = cell_to_text(row[index] if index < len(row) else "")
                        handle.write(f"    <{column}>{xml_escape(value)}</{column}>\n")
                    handle.write("  </row>\n")
                    rows_written += 1
            handle.write("</rows>\n")
        return rows_written

    raise ValueError(f"不支持的导出格式：{extension}")


def group_rows_by_field(columns: list[str], rows: list[list[object]], split_field: str) -> dict[str, list[list[object]]]:
    if not split_field:
        return {"": rows}
    if split_field not in columns:
        raise ValueError(f"拆分字段不存在：{split_field}")
    index = columns.index(split_field)
    groups: dict[str, list[list[object]]] = {}
    for row in rows:
        key = safe_file_stem(cell_to_text(row[index] if index < len(row) else ""), "empty")
        groups.setdefault(key, []).append(row)
    return groups


def run_export_job(payload: dict[str, object]) -> dict[str, object]:
    fields = {key: str(value) for key, value in payload.items() if not isinstance(value, (list, dict))}
    if fields.get("targetDbType") != "sqlite" and not fields.get("connectionId") and not has_direct_connection_fields(fields):
        raise ValueError("请选择数据库连接，或重新打开导出任务后保存一次连接配置。")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        single = {"type": payload.get("sourceType") or "table", "name": payload.get("table") or "query", "table": payload.get("table"), "sql": payload.get("sql")}
        items = [single]
    extension = str(payload.get("extension") or fields.get("extension") or "xlsx").lower().lstrip(".")
    if extension not in {"xlsx", "csv", "txt", "json", "xml"}:
        raise ValueError("当前仅支持 xlsx、csv、txt、json、xml 导出。")

    if parse_bool(fields, "clearLogBeforeExport", False):
        (EXPORTS / "export.log").write_text("", encoding="utf-8")

    conn = connect_target_db(fields)
    started = time.time()
    written_files: list[str] = []
    total_rows = 0

    # P2-13：勾选"表注释作为文件名"且未手动指定文件名时，读取各表注释，
    # 导出文件名优先使用表注释（经 safe_file_stem 清洗非法字符，前后缀仍生效）。
    comment_names: dict[str, str] = {}
    if (
        parse_bool(fields, "commentAsFileName", False)
        and target_db_type(fields) == "mysql"
        and not str(fields.get("exportFileName") or "").strip()
        and not str(fields.get("outputName") or "").strip()
    ):
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "select table_name, table_comment from information_schema.tables where table_schema = database()"
                )
                comment_names = {str(row[0]): str(row[1] or "").strip() for row in cursor.fetchall()}
        except Exception:
            comment_names = {}

    def resolve_base_name(item: dict[str, object], source_name: str) -> str:
        explicit = str(fields.get("exportFileName") or fields.get("outputName") or "").strip()
        if explicit:
            return explicit
        if str(item.get("type") or "") == "table":
            comment_base = comment_names.get(str(item.get("table") or item.get("name") or ""))
            if comment_base:
                return comment_base
        return source_name

    try:
        target_execute_sql_batch(conn, str(payload.get("beforeSql") or ""), "导出开始前 SQL", fields)
        split_field = str(fields.get("splitField") or "").strip()
        split_by_batch = parse_bool(fields, "splitByBatch", False) and int(fields.get("batchRows") or 0) > 0
        lock_requested = parse_bool(fields, "lockHeader", False) or bool(
            export_split_list(fields.get("lockedColumns", ""))
        )
        # 流式写入用的是 write_only 工作簿，没法逐格设置锁定；一旦要求锁定就改走普通写入，
        # 否则「锁定表头行 / 锁定指定列」会被静默忽略。
        can_stream = (
            not split_field
            and not split_by_batch
            and not lock_requested
            and str(fields.get("exportMode") or "workbook") == "workbook"
        )

        if can_stream and extension == "xlsx" and len(items) > 1:
            workbook = Workbook(write_only=True)
            for item in items:
                if not isinstance(item, dict):
                    continue
                sql, source_name = export_query_from_item(item, fields)
                sheet_name = safe_sheet_name(str(fields.get("sheetName") or source_name or "Sheet1"))
                sheet = workbook.create_sheet(sheet_name)
                row_count = 0
                header_labels: list[str] = []
                export_time_field = str(fields.get("exportTimeField") or "").strip()
                export_time_value = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                batch_limit = int(fields.get("batchRows") or 0) if parse_bool(fields, "splitByBatch", False) else 0
                for columns, batch in export_row_batches(conn, sql, fields):
                    if export_time_field and export_time_field not in columns:
                        columns = columns + [export_time_field]
                    if row_count == 0 and str(fields.get("headerMode") or "field") != "none":
                        # 按当前 item 的源表取注释（不能复用上一个 item 的映射）
                        header_labels = export_header_labels(item, columns, fields, conn)
                        styled_write_only_row(sheet, header_labels or columns, fields, header=True)
                    batch = add_export_time_to_batch(batch, export_time_field, export_time_value)
                    if batch_limit:
                        batch = batch[: max(0, batch_limit - row_count)]
                    for row in batch:
                        styled_write_only_row(sheet, row, fields)
                    row_count += len(batch)
                    if batch_limit and row_count >= batch_limit:
                        break
                if parse_bool(fields, "skipEmptyTable", False) and row_count == 0:
                    continue
                total_rows += row_count
            path = export_target_path(str(fields.get("exportFileName") or fields.get("outputName") or "export"), "xlsx", fields)
            workbook.save(path)
            written_files.append(str(path))
        else:
            for item in items:
                if not isinstance(item, dict):
                    continue
                sql, source_name = export_query_from_item(item, fields)
                sheet_name = safe_sheet_name(str(fields.get("sheetName") or source_name or "Sheet1"))
                base_name = resolve_base_name(item, source_name)
                path = export_target_path(base_name, extension, fields)

                if can_stream:
                    first_columns: list[str] = []
                    export_time_field = str(fields.get("exportTimeField") or "").strip()
                    export_time_value = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    batch_limit = int(fields.get("batchRows") or 0) if parse_bool(fields, "splitByBatch", False) else 0
                    rows_seen = 0

                    def streaming_batches() -> Iterator[list[list[object]]]:
                        nonlocal first_columns, rows_seen
                        for columns, batch in export_row_batches(conn, sql, fields):
                            if export_time_field and export_time_field not in columns:
                                columns = columns + [export_time_field]
                            if not first_columns:
                                first_columns = columns
                            batch = add_export_time_to_batch(batch, export_time_field, export_time_value)
                            if batch_limit:
                                batch = batch[: max(0, batch_limit - rows_seen)]
                            rows_seen += len(batch)
                            if batch:
                                yield batch
                            if batch_limit and rows_seen >= batch_limit:
                                break

                    buffered_batches = streaming_batches()
                    try:
                        first_batch = next(buffered_batches)
                    except StopIteration:
                        if parse_bool(fields, "skipEmptyTable", False):
                            continue
                        first_columns = []
                        first_batch = []
                    header_labels = export_header_labels(item, first_columns, fields, conn)
                    row_count = write_export_file_streaming(
                        path,
                        first_columns,
                        chain([first_batch], buffered_batches),
                        fields,
                        sheet_name,
                        header_labels,
                    )
                    written_files.append(str(path))
                    total_rows += row_count
                else:
                    columns, rows = fetch_export_rows(conn, sql, fields)
                    columns, rows = add_export_time_column(columns, rows, str(fields.get("exportTimeField") or ""))
                    if parse_bool(fields, "skipEmptyTable", False) and not rows:
                        continue
                    # P2-14：按字段分割时的两个附加选项（此前只被界面采集、后端从未读取）
                    #   splitIntoFolder    —— 每个分组值单独建一个文件夹
                    #   splitNameWithField —— 文件名固定用「字段名_表名」，靠文件夹区分分组；
                    #                         未分文件夹时仍拼上分组值，避免同名互相覆盖。
                    split_into_folder = parse_bool(fields, "splitIntoFolder", False)
                    split_name_with_field = parse_bool(fields, "splitNameWithField", False)
                    header_labels = export_header_labels(item, columns, fields, conn)
                    groups = group_rows_by_field(columns, rows, split_field)
                    for group_name, group_rows in groups.items():
                        group_base_name = base_name
                        if group_name:
                            if split_name_with_field:
                                group_base_name = f"{split_field}_{base_name}"
                                if not split_into_folder:
                                    group_base_name = f"{group_base_name}_{group_name}"
                            else:
                                group_base_name = f"{group_base_name}_{group_name}"
                        for chunk_suffix, chunk_rows in split_rows_by_batch(group_rows, fields):
                            chunk_path = export_target_path(f"{group_base_name}{chunk_suffix}", extension, fields)
                            if split_into_folder and group_name:
                                chunk_path = chunk_path.parent / safe_file_stem(group_name, "group") / chunk_path.name
                            chunk_path.parent.mkdir(parents=True, exist_ok=True)
                            write_export_file(chunk_path, columns, chunk_rows, fields, sheet_name, header_labels)
                            written_files.append(str(chunk_path))
                            total_rows += len(chunk_rows)
        target_execute_sql_batch(conn, str(payload.get("afterSql") or ""), "导出结束后 SQL", fields)
        conn.commit()
    finally:
        conn.close()

    return {
        "files": written_files,
        "rows": total_rows,
        "elapsedMs": int((time.time() - started) * 1000),
    }


def preview_export_job(payload: dict[str, object]) -> dict[str, object]:
    fields = {key: str(value) for key, value in payload.items() if not isinstance(value, (list, dict))}
    items = payload.get("items")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        item_list = [entry for entry in items if isinstance(entry, dict)]
    else:
        item_list = [
            {
                "type": payload.get("sourceType") or "table",
                "name": payload.get("table") or "query",
                "table": payload.get("table"),
                "sql": payload.get("sql"),
            }
        ]
    item_count = len(item_list)
    # 先解析每个 item 的 SQL 与来源名（解析阶段失败不挡整体，标记错误占位）。
    source_names: list[str] = []
    parsed_items: list[tuple[dict, "str | None", "str | None"]] = []
    for entry in item_list:
        try:
            sql, source_name = export_query_from_item(entry, fields)
            parsed_items.append((entry, sql, source_name))
            source_names.append(source_name)
        except ValueError:
            # 名称解析失败不应挡住预览本身，回退用 item 自带的名字。
            source_names.append(str(entry.get("name") or entry.get("table") or ""))
            parsed_items.append((entry, None, None))

    # 真实预览全部 item：每个 item 各自受 MAX_PREVIEW_ROWS 限制（互不抢占内存，
    # 不会让 N 个查询把内存打爆）；某个 item 执行失败（如语法错误）只影响该 item，
    # 返回错误占位、其余照常返回，避免整个预览请求 500。
    previews: list[dict[str, object]] = []
    conn = connect_target_db(fields)
    try:
        for entry, sql, source_name in parsed_items:
            if sql is None:
                previews.append({
                    "sourceName": str(entry.get("name") or entry.get("table") or ""),
                    "columns": [],
                    "rows": [],
                    "ok": False,
                    "error": "查询解析失败（请检查表名或 SQL）。",
                })
                continue
            try:
                columns, rows = fetch_export_rows(conn, sql, fields, MAX_PREVIEW_ROWS)
                previews.append({
                    "sourceName": source_name,
                    "columns": columns,
                    "rows": [[cell_to_text(cell) for cell in row] for row in rows],
                    "ok": True,
                    "error": None,
                })
            except Exception as exc:
                previews.append({
                    "sourceName": source_name,
                    "columns": [],
                    "rows": [],
                    "ok": False,
                    "error": friendly_export_error(exc, fields),
                })
    finally:
        conn.close()

    # 顶层兼容字段：取值 = 第 1 个 item 的预览结果（旧调用方依赖 sourceName/columns/rows）。
    first_preview = previews[0] if previews else None
    return {
        "sourceName": first_preview["sourceName"] if first_preview else "",
        "columns": first_preview["columns"] if first_preview else [],
        "rows": first_preview["rows"] if first_preview else [],
        "itemCount": item_count,
        "sourceNames": source_names,
        "previews": previews,
    }


# ---------------------------------------------------------------------------
# 查询模块：SQL 控制台
#
# 产品口径（2026-09-21 业主拍板）：查询页 = 完整 SQL 控制台，不限语句类型，
# 写权限完全交给连接账号（本机连接用的是 root，数据库层没有兜底），
# 因此**危险语句必须在服务端做一次性令牌确认**，不靠前端自觉。
#
# 与旧实现（只允许 SELECT/SHOW/DESCRIBE/EXPLAIN/WITH + 一次一条）的差异：
#   * 支持多语句脚本，按「引号/注释感知」切分后逐条执行（不是 MySQL 的 allowMultiQueries）
#   * 显式事务语义：默认逐条提交；显式 BEGIN/START TRANSACTION 开启后交给用户 COMMIT/ROLLBACK
#   * 每条语句都回报 kind（resultset/affected/ddl）+ affectedRows + warnings + elapsedMs
#   * MySQL 用 nextset() 收集多结果集（CALL 存储过程不再丢结果）
# ---------------------------------------------------------------------------

SQL_SAFE_KEYWORDS = {
    "select", "show", "desc", "describe", "explain", "with", "use",
    "begin", "commit", "rollback", "start", "savepoint", "release", "do", "help",
}
SQL_WRITE_KEYWORDS = {
    "insert", "replace", "update", "delete", "create", "alter", "drop", "truncate",
    "rename", "load", "call", "set", "grant", "revoke", "analyze", "optimize",
    "repair", "flush", "lock", "unlock", "prepare", "execute", "deallocate", "kill",
    "install", "uninstall", "reset", "purge", "change", "handler", "xa", "import",
}
SQL_DDL_KEYWORDS = {
    "create", "alter", "drop", "truncate", "rename", "grant", "revoke",
    "analyze", "optimize", "repair", "flush", "use", "set", "call",
}
SQL_TX_BEGIN_KEYWORDS = {"begin", "start"}
SQL_TX_END_KEYWORDS = {"commit", "rollback"}

QUERY_MAX_STATEMENTS = 200
QUERY_MAX_ROWS = 1000
_QUERY_CONFIRM_TTL = 300
_QUERY_CONFIRM_LOCK = threading.Lock()
_QUERY_CONFIRM_TOKENS: dict[str, dict[str, object]] = {}


def strip_sql_literals(sql: str) -> str:
    """把字符串字面量 / 引号标识符 / 注释替换成等长空白，只留可扫描的代码骨架。

    用于安全判定（例如「DELETE 有没有 WHERE」）——避免把 ``delete from t where a = 'drop'``
    里的字面量当成关键字。长度保持不变，便于按位置回溯原文。
    """
    out: list[str] = []
    index = 0
    length = len(sql)
    quote_char = ""
    while index < length:
        char = sql[index]
        if quote_char:
            if char == "\\" and quote_char != "`" and index + 1 < length:
                out.append("  ")
                index += 2
                continue
            if char == quote_char:
                if index + 1 < length and sql[index + 1] == quote_char:
                    out.append("  ")
                    index += 2
                    continue
                quote_char = ""
            out.append(" ")
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote_char = char
            out.append(" ")
            index += 1
            continue
        if char == "/" and sql.startswith("/*", index):
            closing = sql.find("*/", index + 2)
            end = length if closing < 0 else closing + 2
            out.append(" " * (end - index))
            index = end
            continue
        if char == "#" or (char == "-" and sql.startswith("--", index) and (index + 2 >= length or sql[index + 2].isspace())):
            newline = sql.find("\n", index)
            end = length if newline < 0 else newline
            out.append(" " * (end - index))
            index = end
            continue
        out.append(char)
        index += 1
    return "".join(out)


def sql_first_keyword(sql: str) -> str:
    head = sql_after_leading_comments(sql)
    match = re.match(r"^[\s(]*([a-zA-Z_]+)", head)
    return match.group(1).lower() if match else ""


def classify_sql_risk(sql: str) -> dict[str, object]:
    """给单条语句定危险级别。

    * ``safe``   —— 只读：SELECT / SHOW / DESC / EXPLAIN / 只读 WITH / 事务控制
    * ``write``  —— 会改数据但可预期：INSERT / UPDATE/DELETE 带 WHERE / CREATE ...
    * ``danger`` —— 需要二次确认：DROP / TRUNCATE / ALTER / GRANT / REVOKE /
                    无 WHERE 的 UPDATE/DELETE / SELECT ... INTO OUTFILE / 无法识别的语句
    """
    keyword = sql_first_keyword(sql)
    skeleton = strip_sql_literals(sql).lower()
    reasons: list[str] = []
    level = "safe"

    if keyword in {"drop", "truncate", "grant", "revoke", "rename"}:
        level = "danger"
        reasons.append(f"{keyword.upper()} 会不可逆地删除对象或改动权限")
    elif keyword == "alter":
        level = "danger"
        reasons.append("ALTER 会改动表结构，可能丢列或丢数据")
    elif keyword in {"update", "delete"}:
        if re.search(r"\bwhere\b", skeleton):
            level = "write"
        else:
            level = "danger"
            reasons.append(f"{keyword.upper()} 没有 WHERE 条件，会作用于整张表")
    elif keyword in {"with", "select"}:
        level = "safe"
        # WITH ... DELETE / INSERT 这类「CTE 打头、实际是写」的语句要提级
        if re.search(r"\)\s*(delete|update|insert|replace)\b", skeleton):
            level = "write"
    elif keyword == "set":
        if re.search(r"\bset\s+(global|persist|@@global)", skeleton):
            level = "danger"
            reasons.append("SET GLOBAL / PERSIST 会改动服务器级参数")
        else:
            level = "write"
    elif keyword in {"call", "load", "execute", "handler", "kill", "install", "uninstall", "purge", "change"}:
        level = "danger"
        reasons.append(f"{keyword.upper()} 属于管理类/过程类语句，可能产生副作用")
    elif keyword in SQL_SAFE_KEYWORDS:
        level = "safe"
    elif keyword in SQL_WRITE_KEYWORDS:
        level = "write"
    else:
        level = "danger"
        reasons.append("无法识别的语句类型，按高危处理")

    if re.search(r"\binto\s+(outfile|dumpfile)\b", skeleton):
        level = "danger"
        reasons.append("INTO OUTFILE / DUMPFILE 会写服务器文件系统")
    return {"level": level, "keyword": keyword, "reasons": reasons}


def _query_fingerprint(fields: dict[str, str], sql: str) -> str:
    target = "|".join([
        fields.get("connectionId", ""), fields.get("targetDbType", ""),
        fields.get("dbHost", ""), fields.get("dbName", ""), fields.get("dbUser", ""),
    ])
    return hashlib.sha256(f"{target}\n{sql}".encode("utf-8")).hexdigest()


def issue_query_confirm_token(fields: dict[str, str], sql: str) -> dict[str, object]:
    """为「含高危语句的脚本」签发一次性确认令牌（默认 5 分钟有效，用后作废）。"""
    fingerprint = _query_fingerprint(fields, sql)
    token = hashlib.sha256(f"{fingerprint}{time.time()}{uuid.uuid4().hex}".encode("utf-8")).hexdigest()[:32]
    now = time.time()
    with _QUERY_CONFIRM_LOCK:
        for key, item in list(_QUERY_CONFIRM_TOKENS.items()):
            if float(item.get("expiresAt") or 0) <= now:
                _QUERY_CONFIRM_TOKENS.pop(key, None)
        _QUERY_CONFIRM_TOKENS[token] = {"fingerprint": fingerprint, "expiresAt": now + _QUERY_CONFIRM_TTL}
    return {"token": token, "expiresIn": _QUERY_CONFIRM_TTL}


def consume_query_confirm_token(token: str, fields: dict[str, str], sql: str) -> bool:
    """校验并作废令牌。令牌只对「同一连接 + 同一 SQL 原文」有效。"""
    if not token:
        return False
    with _QUERY_CONFIRM_LOCK:
        item = _QUERY_CONFIRM_TOKENS.pop(token, None)
    if not item:
        return False
    if float(item.get("expiresAt") or 0) <= time.time():
        return False
    return str(item.get("fingerprint") or "") == _query_fingerprint(fields, sql)


def _mysql_warnings(conn) -> list[str]:
    """读连接级 warning 列表（如 1265 Data truncated），失败不影响主流程。"""
    try:
        with conn.cursor() as cursor:
            cursor.execute("show warnings")
            return [str(row[2]) for row in cursor.fetchall() if len(row) > 2][:20]
    except Exception:  # noqa: BLE001
        return []


def _collect_mysql_result_sets(cursor, max_rows: int) -> list[dict[str, object]]:
    """收集 MySQL 的全部结果集（CALL 存储过程会返回多个）。"""
    sets: list[dict[str, object]] = []
    while True:
        if cursor.description:
            raw = cursor.fetchmany(max_rows + 1)
            truncated = len(raw) > max_rows
            sets.append({
                "columns": [str(item[0]) for item in cursor.description],
                "rows": [[cell_to_text(cell) for cell in row] for row in raw[:max_rows]],
                "rowCount": min(len(raw), max_rows),
                "truncated": truncated,
            })
        try:
            if not cursor.nextset():
                break
        except Exception:  # noqa: BLE001
            break
    return sets


def execute_sql_script(
    fields: dict[str, str],
    sql_text: str,
    *,
    max_rows: int = QUERY_MAX_ROWS,
    max_statements: int = QUERY_MAX_STATEMENTS,
) -> dict[str, object]:
    """执行一段 SQL 脚本（可含多条语句），逐条上报结果。

    事务语义：默认**逐条提交**（等价于客户端的 autocommit 行为）；脚本里显式写
    ``BEGIN`` / ``START TRANSACTION`` 后进入用户事务，直到 ``COMMIT`` / ``ROLLBACK``
    才结束——此时中间的语句不会被自动提交。注意 MySQL 的 DDL 会隐式提交，
    这是服务端行为，工具无法回滚。
    """
    statements = split_sql_statements(sql_text)
    if not statements:
        raise ValueError("请输入要执行的 SQL。")
    if len(statements) > max_statements:
        raise ValueError(f"一次最多执行 {max_statements} 条语句（当前 {len(statements)} 条），请拆分后再执行。")

    is_mysql = target_db_type(fields) == "mysql"
    started = time.time()
    conn = connect_target_db(fields)
    results: list[dict[str, object]] = []
    failed_index = 0
    failed_error = ""
    in_transaction = False
    try:
        if is_mysql:
            try:
                conn.autocommit(False)
            except Exception:  # noqa: BLE001
                pass
        for index, statement in enumerate(statements, start=1):
            keyword = sql_first_keyword(statement)
            entry: dict[str, object] = {
                "index": index,
                "sql": statement.strip(),
                "keyword": keyword,
                "kind": "empty",
                "resultSets": [],
                "affectedRows": None,
                "warnings": [],
                "elapsedMs": 0,
                "error": None,
            }
            t0 = time.time()
            try:
                cursor = conn.cursor()
                try:
                    cursor.execute(statement)
                    if is_mysql:
                        result_sets = _collect_mysql_result_sets(cursor, max_rows)
                        warnings = _mysql_warnings(conn) if (getattr(getattr(cursor, "_result", None), "warning_count", 0) or 0) else []
                    else:
                        if cursor.description:
                            raw = cursor.fetchall()
                            truncated = len(raw) > max_rows
                            result_sets = [{
                                "columns": [str(item[0]) for item in cursor.description],
                                "rows": [[cell_to_text(cell) for cell in row] for row in raw[:max_rows]],
                                "rowCount": min(len(raw), max_rows),
                                "truncated": truncated,
                            }]
                        else:
                            result_sets = []
                        warnings = []
                    affected = None if result_sets else (int(cursor.rowcount) if (cursor.rowcount or 0) >= 0 else 0)
                finally:
                    cursor.close()
                entry["resultSets"] = result_sets
                entry["affectedRows"] = affected
                entry["warnings"] = warnings
                if result_sets:
                    entry["kind"] = "resultset"
                elif keyword in SQL_DDL_KEYWORDS:
                    entry["kind"] = "ddl"
                else:
                    entry["kind"] = "affected"
                # 事务控制
                if keyword in SQL_TX_BEGIN_KEYWORDS and not re.search(r"\bcommit\b", statement.lower()):
                    in_transaction = True
                elif keyword in SQL_TX_END_KEYWORDS:
                    in_transaction = False
                elif not in_transaction:
                    conn.commit()
            except Exception as exc:  # noqa: BLE001
                entry["elapsedMs"] = int((time.time() - t0) * 1000)
                entry["error"] = str(friendly_mysql_error(exc, f"执行第 {index} 条语句")) if is_mysql else f"第 {index} 条语句执行失败：{exc}"
                results.append(entry)
                failed_index = index
                failed_error = str(entry["error"])
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001
                    pass
                break
            entry["elapsedMs"] = int((time.time() - t0) * 1000)
            results.append(entry)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    flat_sets: list[dict[str, object]] = [item for row in results for item in row["resultSets"]]  # type: ignore[union-attr]
    first = flat_sets[0] if flat_sets else None
    succeeded = sum(1 for row in results if not row["error"])
    affected_total = sum(int(row["affectedRows"] or 0) for row in results)
    if failed_index:
        message = f"共 {len(statements)} 条语句，第 {failed_index} 条失败，已回滚该语句：{failed_error}"
    elif len(results) > 1:
        message = f"共执行 {len(results)} 条语句，全部成功。"
    else:
        single = results[0] if results else {}
        if single.get("kind") == "resultset":
            message = "查询执行成功。"
        elif single.get("kind") == "ddl":
            message = "语句执行成功（结构变更）。"
        else:
            message = f"语句执行成功，影响 {int(single.get('affectedRows') or 0)} 行。"
    return {
        "statements": results,
        "totals": {
            "statements": len(statements),
            "executed": len(results),
            "succeeded": succeeded,
            "failed": 1 if failed_index else 0,
            "affectedRows": affected_total,
            "resultSets": len(flat_sets),
        },
        "resultSetCount": len(flat_sets),
        "affectedRows": affected_total,
        "failedIndex": failed_index,
        # 兼容旧调用方（页面/复跑脚本依赖这几个顶层字段）
        "columns": first["columns"] if first else [],
        "rows": first["rows"] if first else [],
        "rowCount": first["rowCount"] if first else 0,
        "truncated": bool(first["truncated"]) if first else False,
        "elapsedMs": int((time.time() - started) * 1000),
        "message": message,
    }


def run_readonly_query(payload: dict[str, object]) -> dict[str, object]:
    """查询模块入口：支持任意 SQL（含 DDL / DML / 多语句）。

    函数名保留是为了兼容既有调用方与测试；语义已从「只读」放宽为「SQL 控制台」。
    危险语句（DROP / TRUNCATE / ALTER / 无 WHERE 的删改 …）必须携带一次性确认令牌，
    否则返回 ``needConfirm: true`` 而不是直接执行。
    """
    raw_sql = str(payload.get("sql") or "").strip()
    if not raw_sql:
        raise ValueError("请输入要执行的 SQL。")
    fields = {key: str(value) for key, value in payload.items() if not isinstance(value, (list, dict))}
    statements = split_sql_statements(raw_sql)
    if not statements:
        raise ValueError("请输入要执行的 SQL。")

    dangerous = []
    for index, statement in enumerate(statements, start=1):
        risk = classify_sql_risk(statement)
        if risk["level"] == "danger":
            first_line = statement.strip().splitlines()[0] if statement.strip() else ""
            dangerous.append({
                "index": index,
                "keyword": risk["keyword"],
                "reasons": risk["reasons"] or ["该语句被判定为高危"],
                "preview": first_line[:160],
            })
    if dangerous:
        token = str(payload.get("confirmToken") or "").strip()
        if not consume_query_confirm_token(token, fields, raw_sql):
            issued = issue_query_confirm_token(fields, raw_sql)
            return {
                "needConfirm": True,
                "dangerous": dangerous,
                "confirmToken": issued["token"],
                "expiresIn": issued["expiresIn"],
                "columns": [],
                "rows": [],
                "rowCount": 0,
                "elapsedMs": 0,
                "truncated": False,
                "message": "该脚本包含高危语句，需要确认后才会执行。",
            }
    return execute_sql_script(fields, raw_sql)


def saved_query_public(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "name": row["name"],
        "connectionId": row["connection_id"],
        "sql": row["sql_text"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


QUERY_BACKING_JOB_PREFIX = "查询："
"""Job name prefix for jobs that act as schedule-time proxies for a saved query.

Stored in _jobs so that the schedule module can pick them up via the existing
/api/jobs + jobPrimaryType === 'query' path. step.config.queryId is the
authoritative link to _saved_queries; the SQL is loaded at execution time so
that editing the saved query automatically reflects in any scheduled job.
"""


def _sync_query_to_job(conn: sqlite3.Connection, query_id: str, query_name: str, connection_id: str) -> None:
    """Upsert a proxy job into _jobs so the saved query shows up in the schedule UI.

    The job name is deterministic (``f"{QUERY_BACKING_JOB_PREFIX}{query_name}"``) so
    re-saving the same query updates the same job instead of creating a duplicate.
    The job's single query step carries config.queryId + config.connectionId; the
    SQL itself is *not* duplicated into steps_json (executor pulls from
    _saved_queries at run time, giving a single source of truth).

    Resave semantics for an existing job with the same name:
      * If a step with matching queryId exists → refresh its config in place.
      * Otherwise (same name, new queryId, e.g. user saved a fresh query under a
        name that already had a backing job) → treat as overwrite: drop the
        stale query step and append the new one, so the job never accumulates
        dead references.
    """
    job_name = f"{QUERY_BACKING_JOB_PREFIX}{query_name}"
    step = {
        "id": uuid.uuid4().hex,
        "type": "query",
        "name": "执行查询",
        "enabled": True,
        "continueOnError": False,
        "config": {
            "queryId": query_id,
            "connectionId": connection_id,
            "targetDbType": "mysql",
        },
    }
    now = now_text()
    existing = conn.execute("select id, steps_json, created_at from _jobs where name = ?", (job_name,)).fetchone()
    if existing:
        job_id = existing["id"]
        try:
            steps = json.loads(existing["steps_json"] or "[]")
        except (TypeError, ValueError):
            steps = []
        target = next(
            (item for item in steps if item.get("type") == "query" and (item.get("config") or {}).get("queryId") == query_id),
            None,
        )
        if target is not None:
            # Same queryId being re-saved: refresh config, keep the step id stable.
            target["config"] = dict(step["config"])
            target.setdefault("name", step["name"])
            target.setdefault("enabled", True)
            target.setdefault("continueOnError", False)
        else:
            # Name collides with an existing proxy job but no matching step:
            # the previous query must have been deleted or this is a deliberate
            # overwrite under the same name. Replace any existing query step to
            # avoid leaving dangling references.
            steps = [item for item in steps if not (item.get("type") == "query" and (item.get("config") or {}).get("queryId"))]
            steps.append(step)
        conn.execute(
            "update _jobs set steps_json = ?, updated_at = ? where id = ?",
            (json.dumps(steps, ensure_ascii=False), now, job_id),
        )
    else:
        conn.execute(
            "insert into _jobs (id, name, enabled, steps_json, created_at, updated_at) values (?, ?, 1, ?, ?, ?)",
            (uuid.uuid4().hex, job_name, json.dumps([step], ensure_ascii=False), now, now),
        )


def _delete_jobs_for_query(conn: sqlite3.Connection, query_id: str) -> int:
    """Remove proxy jobs whose only/binding reference is to this queryId.

    Returns the number of jobs deleted. A job is considered to "belong" to a query
    when one of its query-type steps has config.queryId == query_id.
    """
    deleted = 0
    rows = conn.execute("select id, steps_json from _jobs").fetchall()
    for row in rows:
        try:
            steps = json.loads(row["steps_json"] or "[]")
        except (TypeError, ValueError):
            continue
        if any(
            step.get("type") == "query" and (step.get("config") or {}).get("queryId") == query_id
            for step in steps
        ):
            conn.execute("delete from _jobs where id = ?", (row["id"],))
            deleted += 1
    return deleted


def app_now() -> dt.datetime:
    timezone_name = os.environ.get("APP_TIMEZONE", "Asia/Shanghai")
    try:
        return dt.datetime.now(ZoneInfo(timezone_name)).replace(tzinfo=None)
    except ZoneInfoNotFoundError:
        return dt.datetime.utcnow() + dt.timedelta(hours=8)


def now_text() -> str:
    return app_now().strftime("%Y-%m-%d %H:%M:%S")


def parse_datetime(value: str) -> dt.datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            parsed = dt.datetime.strptime(value, fmt)
            if fmt == "%Y-%m-%d":
                return parsed.replace(hour=0, minute=0, second=0)
            return parsed
        except ValueError:
            continue
    return None


def _row_int(row: sqlite3.Row, column: str, default: int) -> int:
    """安全读取行里的整数列：列缺失 / 值为空 / 非数字时返回 default。

    为什么要这层：precheck_minutes 是后来用 _ensure_column 补建的列，读行函数也可能
    被拿去读一个尚未跑过迁移的库的连接；直接 row[column] 在列缺失时抛 IndexError，
    会把整个「定时任务列表」接口打挂（历史库升级路径上必须能容错）。
    """
    try:
        value = row[column]
    except (IndexError, KeyError):
        return default
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def row_to_job(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "name": row["name"],
        "enabled": bool(row["enabled"]),
        "steps": json.loads(row["steps_json"] or "[]"),
        "guard": json.loads(row["guard_json"] or "{}"),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_schedule(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "name": row["name"],
        "jobId": row["job_id"],
        "enabled": bool(row["enabled"]),
        "rule": json.loads(row["rule_json"] or "{}"),
        "startAt": row["start_at"],
        "endAt": row["end_at"],
        "nextRunAt": row["next_run_at"],
        "lastRunAt": row["last_run_at"],
        "lastStatus": row["last_status"],
        "logRetentionDays": row["log_retention_days"],
        # 预检提前量（分钟）：0 = 关闭预检（行为与引入预检之前完全一致）
        "precheckMinutes": _row_int(row, "precheck_minutes", 5),
        "emailOnFail": bool(row["email_on_fail"]),
        "running": bool(row["running"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def normalize_steps(raw_steps: object) -> list[dict[str, object]]:
    if not isinstance(raw_steps, list):
        return []
    steps: list[dict[str, object]] = []
    for index, raw in enumerate(raw_steps):
        if not isinstance(raw, dict):
            continue
        step_type = str(raw.get("type") or "").strip().lower()
        if step_type not in {"import", "export", "query", "job", "sync"}:
            continue
        steps.append(
            {
                "id": str(raw.get("id") or uuid.uuid4().hex),
                "name": str(raw.get("name") or f"步骤 {index + 1}").strip() or f"步骤 {index + 1}",
                "type": step_type,
                "enabled": raw.get("enabled", True) not in (False, "false", "0", 0, "off"),
                "continueOnError": raw.get("continueOnError", False) in (True, "true", "1", 1, "on"),
                "config": attach_connection_snapshot(raw.get("config") if isinstance(raw.get("config"), dict) else {}),
            }
        )
    return steps


def save_job(payload: dict[str, object]) -> dict[str, object]:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("请填写作业名称。")
    steps = normalize_steps(payload.get("steps"))
    if not steps:
        raise ValueError("请至少添加一个子任务。")
    if any(step["type"] == "sync" for step in steps):
        raise ValueError("同步模块尚未开放，暂不能保存同步子任务。")
    job_id = str(payload.get("id") or uuid.uuid4().hex)
    now = now_text()
    guard = payload.get("guard") if isinstance(payload.get("guard"), dict) else {}
    with connect_db() as conn:
        old = conn.execute("select created_at from _jobs where id = ?", (job_id,)).fetchone()
        conn.execute(
            """
            insert into _jobs (id, name, enabled, steps_json, guard_json, created_at, updated_at)
            values (?, ?, ?, ?, ?, ?, ?)
            on conflict(id) do update set
                name = excluded.name,
                enabled = excluded.enabled,
                steps_json = excluded.steps_json,
                guard_json = excluded.guard_json,
                updated_at = excluded.updated_at
            """,
            (
                job_id,
                name,
                1 if payload.get("enabled", True) not in (False, "false", "0", 0, "off") else 0,
                json.dumps(steps, ensure_ascii=False),
                json.dumps(guard, ensure_ascii=False),
                old["created_at"] if old else now,
                now,
            ),
        )
        row = conn.execute("select * from _jobs where id = ?", (job_id,)).fetchone()
    return row_to_job(row)


def save_schedule(payload: dict[str, object]) -> dict[str, object]:
    name = str(payload.get("name") or "").strip()
    job_id = str(payload.get("jobId") or payload.get("job_id") or "").strip()
    if not name:
        raise ValueError("请填写任务名称。")
    if not job_id:
        raise ValueError("请选择作业。")
    with connect_db() as conn:
        if not conn.execute("select 1 from _jobs where id = ?", (job_id,)).fetchone():
            raise ValueError("选择的作业不存在。")
    rule = payload.get("rule") if isinstance(payload.get("rule"), dict) else {}
    schedule_id = str(payload.get("id") or uuid.uuid4().hex)
    start_at = str(payload.get("startAt") or payload.get("start_at") or "").replace("T", " ")
    end_at = str(payload.get("endAt") or payload.get("end_at") or "").replace("T", " ")
    retention = max(int(payload.get("logRetentionDays") or payload.get("log_retention_days") or 3), 1)
    # 执行前预检提前量（分钟）：缺省 5（= 到期前 5 分钟检查一次）；传负数归 0（关闭预检）。
    raw_precheck = payload.get("precheckMinutes", payload.get("precheck_minutes"))
    try:
        precheck_minutes = 5 if raw_precheck is None else int(raw_precheck)
    except (TypeError, ValueError):
        precheck_minutes = 5
    if precheck_minutes < 0:
        precheck_minutes = 0
    enabled = payload.get("enabled", False) in (True, "true", "1", 1, "on")
    next_run = compute_next_run(rule, start_at, end_at, None) if enabled else ""
    now = now_text()
    with connect_db() as conn:
        old = conn.execute("select created_at, last_run_at, last_status from _schedules where id = ?", (schedule_id,)).fetchone()
        conn.execute(
            """
            insert into _schedules (
                id, name, job_id, enabled, rule_json, start_at, end_at, next_run_at,
                last_run_at, last_status, log_retention_days, precheck_minutes, email_on_fail,
                running, created_at, updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            on conflict(id) do update set
                name = excluded.name,
                job_id = excluded.job_id,
                enabled = excluded.enabled,
                rule_json = excluded.rule_json,
                start_at = excluded.start_at,
                end_at = excluded.end_at,
                next_run_at = excluded.next_run_at,
                log_retention_days = excluded.log_retention_days,
                precheck_minutes = excluded.precheck_minutes,
                email_on_fail = excluded.email_on_fail,
                updated_at = excluded.updated_at
            """,
            (
                schedule_id,
                name,
                job_id,
                1 if enabled else 0,
                json.dumps(rule, ensure_ascii=False),
                start_at,
                end_at,
                next_run,
                old["last_run_at"] if old else "",
                old["last_status"] if old else "",
                retention,
                precheck_minutes,
                1 if payload.get("emailOnFail") in (True, "true", "1", 1, "on") else 0,
                old["created_at"] if old else now,
                now,
            ),
        )
        row = conn.execute("select * from _schedules where id = ?", (schedule_id,)).fetchone()
    return row_to_schedule(row)


def compute_next_run(rule: dict[str, object], start_at: str = "", end_at: str = "", last_run_at: str | None = None, from_time: dt.datetime | None = None) -> str:
    base = from_time or app_now()
    start = parse_datetime(start_at)
    end = parse_datetime(end_at)
    if start and base < start:
        base = start
    if end and base > end:
        return ""
    mode = str(rule.get("mode") or "once")
    last = parse_datetime(last_run_at or "")
    if mode == "once":
        candidate = start or base
        if last:
            return ""
    elif mode == "interval":
        amount = max(int(rule.get("amount") or 1), 1)
        unit = str(rule.get("unit") or "minutes")
        seconds = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86400}.get(unit, 60) * amount
        if last:
            candidate = last + dt.timedelta(seconds=seconds)
        elif start:
            candidate = start
        else:
            candidate = base + dt.timedelta(seconds=seconds)
        if candidate < base:
            steps = int((base - candidate).total_seconds() // seconds) + 1
            candidate += dt.timedelta(seconds=seconds * steps)
    else:
        time_text = str(rule.get("time") or "09:00:00")
        parts = [int(part) for part in re.findall(r"\d+", time_text)[:3]]
        while len(parts) < 3:
            parts.append(0)
        hour, minute, second = parts[:3]
        if mode == "daily":
            candidate = base.replace(hour=hour, minute=minute, second=second, microsecond=0)
            if candidate <= base:
                candidate += dt.timedelta(days=1)
        elif mode == "weekly":
            weekday = int(rule.get("weekday") or 1)
            weekday = max(1, min(7, weekday)) - 1
            candidate = base.replace(hour=hour, minute=minute, second=second, microsecond=0)
            days = (weekday - candidate.weekday()) % 7
            candidate += dt.timedelta(days=days)
            if candidate <= base:
                candidate += dt.timedelta(days=7)
        elif mode == "monthly":
            day = max(1, min(31, int(rule.get("day") or 1)))
            year, month = base.year, base.month
            while True:
                max_day = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
                candidate = dt.datetime(year, month, min(day, max_day), hour, minute, second)
                if candidate > base:
                    break
                month += 1
                if month > 12:
                    year += 1
                    month = 1
        elif mode == "yearly":
            month = max(1, min(12, int(rule.get("month") or 1)))
            day = max(1, min(31, int(rule.get("day") or 1)))
            year = base.year
            while True:
                max_day = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
                candidate = dt.datetime(year, month, min(day, max_day), hour, minute, second)
                if candidate > base:
                    break
                year += 1
        else:
            candidate = base
    if end and candidate > end:
        return ""
    return candidate.strftime("%Y-%m-%d %H:%M:%S")


def is_app_managed_file(path: Path) -> bool:
    """判断文件是否由本工具自己生成（上传副本 / 任务副本 / 链接快照）。

    这类文件删掉无风险。反之则是用户在「本地路径导入」里指定的**真实文件**，
    删除必须走系统回收站，否则就是不可恢复的数据丢失。
    """
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for root in (UPLOADS, TASK_SOURCES, LINKED_SOURCES):
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def move_to_recycle_bin(path: Path) -> bool:
    """Windows：把文件移入回收站（可恢复）。非 Windows 或失败返回 False。

    依据 win-recycle-delete 技能里已实测的 ctypes 实现：
    `shell32.SHFileOperationW` + `FOF_ALLOWUNDO(0x0040)`——
    **FOF_ALLOWUNDO 是「进回收站」的唯一开关**，不加就是永久删除。
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("wFunc", ctypes.c_uint),
                ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_uint16),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", wintypes.LPCWSTR),
            ]

        op = SHFILEOPSTRUCTW()
        op.hwnd = None
        op.wFunc = 0x0003  # FO_DELETE
        # pFrom 必须是「\0 分隔、\0\0 结尾」的多字符串
        op.pFrom = str(path) + "\0\0"
        op.pTo = None
        op.fFlags = 0x0040 | 0x0010 | 0x0004 | 0x0400 | 0x0200
        op.fAnyOperationsAborted = False
        op.hNameMappings = None
        op.lpszProgressTitle = None
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        return result == 0 and not op.fAnyOperationsAborted
    except Exception:  # noqa: BLE001
        return False


def safe_delete_source_file(path: Path) -> str:
    """按文件归属选择删除方式，返回给用户看的说明（永不抛错）。

    改进F：原先不论文件来源一律 `unlink`。用「本地路径导入」时上传对象直接
    指向用户的真实源文件，一次误勾选就永久删掉了原始数据。现在改为：
    工具自管副本直接删；用户真实文件进回收站；两者都不成立时**保留不删**。
    """
    try:
        if not path.exists():
            return ""
        if is_app_managed_file(path):
            path.unlink(missing_ok=True)
            return "上传副本已删除"
        if move_to_recycle_bin(path):
            return "源文件已移入回收站（可从回收站恢复）"
    except OSError as exc:
        return f"源文件删除失败，已原样保留：{exc}"
    return f"为防误删，源文件已保留（不在工具副本目录内且无法移入回收站）：{path}"


def collect_local_files(path_text: str) -> list[UploadedFile]:
    path_text = path_text.strip().strip('"')
    if not path_text:
        raise ValueError("请填写导入文件或目录路径。")
    path = Path(path_text)
    if not path.exists():
        raise ValueError(f"路径不存在：{path_text}")
    if path.is_file():
        return [UploadedFile(path.name, path)]
    files = [UploadedFile(item.name, item) for item in sorted(path.iterdir()) if item.is_file() and item.suffix.lower() in SUPPORTED_EXTENSIONS]
    if not files:
        raise ValueError("目录中没有可导入的文件。")
    return files


def execute_query_step(config: dict[str, object]) -> dict[str, object]:
    fields = {key: str(value) for key, value in config.items() if not isinstance(value, (list, dict))}
    query_id = str(config.get("queryId") or "").strip()
    sql = str(config.get("sql") or "").strip()
    # When the step carries a queryId, treat _saved_queries as the single source
    # of truth: pull the latest SQL at execution time so editing a saved query
    # automatically flows into scheduled runs. Falls back to config.sql when no
    # queryId is set (legacy jobs.html-created query steps with inline SQL).
    if query_id:
        with connect_db() as conn:
            row = conn.execute(
                "select sql_text, connection_id from _saved_queries where id = ?",
                (query_id,),
            ).fetchone()
        if not row:
            raise ValueError(f"查询任务不存在或已被删除（queryId={query_id}）。请在「查询」页面重新保存，或在「作业」中重新配置查询步骤。")
        sql = str(row["sql_text"] or "").strip()
        if not sql:
            raise ValueError("查询 SQL 不能为空。")
        if not fields.get("connectionId") and row["connection_id"]:
            fields["connectionId"] = str(row["connection_id"])
    if not sql:
        raise ValueError("查询 SQL 不能为空。")
    # 与查询页共用同一执行器：多语句按「引号/注释感知」切分（旧实现按裸分号切分，
    # 字符串里出现分号就会切错），DDL/DML 一律可用。
    # 作业是用户预先配置、可能定时自动跑的动作，配置本身即确认，因此不要求二次令牌。
    result = execute_sql_script(fields, sql)
    if result.get("failedIndex"):
        failed_index = int(result["failedIndex"])
        failed = next((item for item in result["statements"] if item.get("index") == failed_index), {})
        raise ValueError(str(failed.get("error") or f"第 {failed_index} 条语句执行失败。"))
    totals = result.get("totals") or {}
    affected = int(totals.get("affectedRows") or 0)
    # 纯查询步骤按结果行数计入（沿用旧语义），写语句按影响行数
    return {"rows": affected or int(result.get("rowCount") or 0), "message": result.get("message"), "statements": result.get("statements")}


def resolve_import_step_config(config: dict[str, object], visited: set[str] | None = None) -> dict[str, object]:
    """产品建议 B：作业导入步骤若引用已保存的导入任务（config.importJobId），
    加载该任务首个 import 步骤的完整配置执行，保证定时产出与手动导入逐格一致。

    支持多层引用但用 visited 防循环；引用目标不存在或没有可用导入步骤时明确报错。
    """
    job_id = str(config.get("importJobId") or "").strip()
    if not job_id:
        return config
    visited = visited or set()
    if job_id in visited:
        raise ValueError(f"导入任务引用存在循环，已停止：{job_id}")
    visited = visited | {job_id}
    with connect_db() as conn:
        row = conn.execute("select * from _jobs where id = ?", (job_id,)).fetchone()
    if not row:
        raise ValueError("引用的导入任务不存在或已删除，请重新编辑作业。")
    for step in json.loads(row["steps_json"] or "[]"):
        if step.get("type") != "import":
            continue
        cfg = step.get("config") if isinstance(step.get("config"), dict) else {}
        resolved = dict(cfg)
        if str(cfg.get("importJobId") or "").strip():
            resolved = resolve_import_step_config(cfg, visited)
        resolved.pop("importJobId", None)
        return resolved
    raise ValueError(f"导入任务“{row['name']}”中没有可用的导入步骤。")


def execute_import_step(config: dict[str, object]) -> dict[str, object]:
    fields = {key: str(value) for key, value in config.items() if not isinstance(value, (list, dict))}
    source_path = str(config.get("path") or config.get("sourcePath") or "")
    source = Path(source_path).resolve()
    managed_sources = TASK_SOURCES.resolve()
    if source == managed_sources or managed_sources in source.parents:
        raise ValueError(
            "该导入任务的源文件仍是软件一次性副本（task_sources），定时/作业执行不读取它。"
            "请打开这条导入任务，点击「关联本机原文件」选择电脑中的真实源文件，"
            "然后必须重新点击「保存为任务」——只关联不保存，任务路径不会更新。"
            "保存后引用该任务的作业会自动使用新路径，无需重新编辑作业。"
        )
    # 快照目录只提示、不阻断：linked_sources 是「关联本机原文件」上传后的服务器副本，
    # 它只在导入页面打开时才由前端 syncLinkedSources() 自动刷新。无人值守的定时执行
    # 页面没开 → 副本可能已陈旧，用户却以为"我每次都指定了路径"。阻断会破坏已有可用配置，
    # 因此这里只把风险写进步骤日志。
    linked_sources = LINKED_SOURCES.resolve()
    snapshot_hint = ""
    if source == linked_sources or linked_sources in source.parents:
        snapshot_hint = (
            "提示：本次读取的是服务器副本（data/linked_sources），它只在导入页面打开时才会"
            "自动同步本机更新，无人值守的定时执行不会刷新。若源文件由外部系统更新，"
            "请把该导入任务的路径直接指向源文件的真实路径。"
        )
    files = collect_local_files(source_path)
    pending_before_sql = str(fields.get("beforeAllSql") or "").strip()
    pending_after_sql = str(fields.get("afterAllSql") or "").strip()
    sql_status = "无待执行 SQL"
    if pending_before_sql:
        conn = connect_target_db(fields)
        try:
            target_execute_sql_batch(conn, pending_before_sql, "定时导入执行前 SQL", fields)
            conn.commit()
            sql_status = "执行前 SQL：已执行"
        finally:
            conn.close()
    results, _, skipped_files = run_import_batch(files, fields, fail_fast=True)
    if pending_after_sql:
        conn = connect_target_db(fields)
        try:
            target_execute_sql_batch(conn, pending_after_sql, "定时导入执行后 SQL", fields)
            conn.commit()
            sql_status = f"{sql_status}；执行后 SQL：已执行"
        finally:
            conn.close()
    return {
        "files": len(results),
        "sourcePath": source_path,
        "fileNames": [str(item["fileName"]) for item in results],
        "tableNames": sorted({str(item["tableName"]) for item in results if item.get("tableName")}),
        "rowsRead": sum(int(item["rowsRead"]) for item in results),
        "rowsWritten": sum(int(item["rowsWritten"]) for item in results),
        "rowsUpdated": sum(int(item["rowsUpdated"]) for item in results),
        "rowsSkipped": sum(int(item["rowsSkipped"]) for item in results),
        "verifiedRows": {str(item["tableName"]): int(item.get("verifiedRows", 0)) for item in results if item.get("tableName")},
        "skippedFiles": skipped_files,
        "sqlStatus": sql_status,
        "warning": snapshot_hint,
    }


def _file_fingerprint(path_text: str) -> str:
    """计算源文件/目录的指纹：文件数量 + 每个文件 (名称, 大小, mtime_ns) 拼接哈希。

    用于"文件无更新则跳过作业"守卫。不含内容级 hash（大文件性能考虑），
    以 大小 + 纳秒级 mtime 变化作为"文件有更新"的可靠近似。

    为什么用 st_mtime_ns 而不是 int(st_mtime)（P1-1）：
    秒级截断会把"同一秒内的两次修改"折叠成同一个整数。外部系统用批量脚本、
    下载即覆盖、或"导出后立刻回写"的形态更新源文件时，原始大小往往恰好不变
    （同样结构的 xlsx 只改了单元格值），于是 大小+秒 两个信号都没变
    → 新数据被漏判 → 该轮不执行导入。改用纳秒精度后，可分辨窗口从
    "最多 999 ms（秒级截断）"压到"约 15.6 ms（系统计时器 tick，见下方残留边界）"，
    生产链上"两个源文件被分别更新"的间隔以分钟/小时计，必然被区分出来。

    已知残留边界（真实取舍，不假装消除）：
      - Windows/NTFS 实测：两次写入若落在**同一个系统计时器 tick**（约 15.6 ms）内，
        文件系统可能完全不推进 last-write-time —— 紧邻的两次写入 st_mtime_ns 会一模一样。
        这种"同 tick 内改两次且大小不变"仍会漏判。
        为什么实务上可接受：守卫基线是在一次完整作业运行（sqlite 事务 + 文件解析，
        耗时远超 15.6 ms）的末尾读到的，两次外部写入之间必然夹着一次作业运行，
        因此必然跨 tick，撞不上这个窗口。这是本次改用 ns 之后剩下的唯一实测残留边界。
      - 比 tick 更长的间隔但 int(mtime) 相同（即本函数修复前漏判的那类）→ 已能可靠区分。
      - 改内容后外部工具刻意把 size 与 mtime 一起还原 → 漏判（需要内容 hash 才能识别）。
      - mtime 变但内容没变（复制/覆盖同名文件）→ 误判为"有更新"，多跑一次导入。
        方向是保守的（不会丢数据），且导入步骤本身是幂等的，可接受。
      - 要彻底消除前两类必须做内容 hash，代价是每次守卫评估都要读整个文件
        （生产源文件 xlsx 可达几十 MB，且守卫在每次定时触发前都要跑），
        与"守卫必须轻量"的设计目标冲突，因此保留 大小+时间 的近似。

    注意：本函数与 import_file_fingerprint()（skipSeenFile 用，大小+内容 sha256）
    是两个用途不同的函数，不能互相替代——前者要极轻量，后者要绝对可靠。
    """
    import hashlib
    files = collect_local_files(path_text)
    parts = [f"{len(files)}"]
    for f in files:
        st = f.path.stat()
        parts.append(f"{f.filename}|{st.st_size}|{st.st_mtime_ns}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _guard_summary(job: dict[str, object], _visited: set[str] | None = None) -> dict[tuple[str, int], dict[str, str]]:
    """收集作业中启用了文件守卫的导入步骤（含被引用的导入任务、嵌套子作业）。

    - 步骤 config.skipIfFileUnchanged 为真 → 该步骤启用文件守卫
    - 作业 guard.type == "file_has_new" → 本作业（及其递归子作业）全部启用的 import 步骤都启用文件守卫
    - 子作业自身 guard.type == "file_has_new" → 对父作业同样生效：父作业先跳过，
      子作业根本不会被执行到，用户看到的是"跳过"而不是"子作业执行失败"的假失败
    - type == "import" 且 config 含 importJobId → 复用 resolve_import_step_config() 展开真实路径。
      为什么必须展开：作业里的导入步骤常常只带 importJobId，真实文件路径存在被引用的导入任务里，
      只看 step.config 会得到空守卫 —— 这正是"明明没新数据却每次都执行"的根因。
      展开失败（引用不存在 / 循环 / 该任务没有可用导入步骤）直接抛出，不静默吞掉：
      静默返回空守卫等价于"条件没配"，问题会被藏到用户下次发现数据不对为止。

    返回 dict，键为 (job_id, step_index) —— 与 _job_file_guards 主键 (job_id, step_index) 一一对应。
    为什么要带 job_id：嵌套子作业的基线必须记在"子作业自己的 job_id"下。若统一用父作业 id 作键，
    父子作业相同的 step_index 会互相覆盖基线，指纹比对形同虚设。
    """
    job_guard = job.get("guard") if isinstance(job.get("guard"), dict) else {}
    force_all = str(job_guard.get("type") or "").strip().lower() == "file_has_new"
    job_id = str(job.get("id") or "")
    visited = set(_visited or ())
    if job_id:
        visited.add(job_id)
    guards: dict[tuple[str, int], dict[str, str]] = {}
    _collect_file_guards(job, job_id, force_all, visited, guards)
    return guards


def _collect_file_guards(
    job: dict[str, object],
    job_id: str,
    force_all: bool,
    visited: set[str],
    guards: dict[tuple[str, int], dict[str, str]],
) -> None:
    """``_guard_summary`` 的递归实现：把本作业（及嵌套子作业）里启用的导入步骤守卫收进 guards。

    visited 只用于阻断"祖先 → 后代"的环路，且每层分支各自复制一份，
    因此 A → C、B → C 这种菱形引用不会被误判成循环。
    """
    for index, step in enumerate(job.get("steps") or []):
        if not isinstance(step, dict) or not step.get("enabled", True):
            continue
        step_type = str(step.get("type") or "")
        cfg = step.get("config") if isinstance(step.get("config"), dict) else {}
        flag = str(cfg.get("skipIfFileUnchanged") or "").strip().lower()
        guarded = force_all or flag in {"true", "1", "yes", "on"}
        if step_type == "import":
            if not guarded:
                continue
            # 关键：路径可能不在 step.config 里，而在 config.importJobId 指向的导入任务里
            resolved = resolve_import_step_config(cfg)
            path_text = str(resolved.get("path") or resolved.get("sourcePath") or "").strip()
            if path_text:
                guards[(job_id, index)] = {
                    "source": path_text,
                    "jobId": job_id,
                    "stepIndex": str(index),
                    "stepName": str(step.get("name") or ""),
                }
            continue
        if step_type != "job":
            continue
        nested_id = str(cfg.get("jobId") or "").strip()
        if not nested_id:
            # 没有 jobId 的嵌套步骤本身是坏配置，执行到该步骤时会明确报错；
            # 这里拿不到任何守卫信息，跳过（不额外制造一个与守卫无关的失败）。
            continue
        if nested_id in visited:
            raise ValueError(f"检测到作业循环引用，无法评估文件更新条件：{nested_id}")
        with connect_db() as conn:
            nested_row = conn.execute("select * from _jobs where id = ?", (nested_id,)).fetchone()
        if not nested_row:
            # 不静默：这个分支既不能证明"源文件没有新内容"，也不能证明"有"，静默跳过
            # 等于让作业带着坏引用继续跑（或永久跳过）——引用被误删这种事会一直潜伏到
            # 用户发现数据不对为止。文案给出可执行动作，用户不必猜哪里坏了。
            raise ValueError(
                f"引用的子作业不存在或已删除（jobId={nested_id}），无法评估文件更新条件。"
                "请打开本作业，编辑这个子作业步骤重新选择目标作业，或删除该步骤后保存。"
            )
        nested_job = row_to_job(nested_row)
        nested_guard = nested_job.get("guard") if isinstance(nested_job.get("guard"), dict) else {}
        nested_force = force_all or str(nested_guard.get("type") or "").strip().lower() == "file_has_new"
        _collect_file_guards(nested_job, nested_id, nested_force, visited | {nested_id}, guards)


def evaluate_job_guard(job: dict[str, object], now: dt.datetime | None = None) -> tuple[bool, str]:
    """作业级执行条件（日期类守卫）判定。

    guard 结构（存 _jobs.guard_json）：
      {"type": "file_has_new"}                                    —— 自动文件守卫（由 run_saved_job 指纹机制处理）
      {"type": "date_match", "mode": "range", "start": "..", "end": ".."}   —— 该日期范围内每天执行
      {"type": "date_match", "mode": "dates", "values": ["2026-09-01"]}     —— 仅指定日期执行
      {"type": "date_match", "mode": "weekday", "values": [1,3,5]}          —— 仅周几(1=周一..7=周日)执行
      {"type": "date_match", "mode": "monthday", "values": [1,15]}          —— 仅每月几号执行
    返回 (ok, reason)：ok=False 表示本次不满足、应跳过整个作业。
    """
    guard = job.get("guard") if isinstance(job.get("guard"), dict) else {}
    gtype = str(guard.get("type") or "").strip().lower()
    if not gtype or gtype in {"", "none", "off"}:
        return True, ""
    if gtype == "file_has_new":
        return True, ""
    if gtype == "date_match":
        now = now or dt.datetime.now()
        mode = str(guard.get("mode") or "weekday").strip().lower()
        if mode not in {"range", "dates", "weekday", "monthday"}:
            # 模式拼错（如 weekdayy）此前会走到末尾静默返回 (True, "")，条件等于没配；
            # 这里显式失败，让用户在作业日志里看到真实原因。
            raise ValueError(f"作业执行条件：不支持的日期模式：{mode}。")
        today = now.strftime("%Y-%m-%d")
        if mode == "range":
            start = str(guard.get("start") or "").strip()[:10]
            end = str(guard.get("end") or "").strip()[:10]
            if not start or not end:
                raise ValueError("作业执行条件：日期范围需填写开始与结束日期。")
            if start > today or end < today:
                return False, f"作业仅在 {start} ~ {end} 期间执行，今天 {today} 不在范围内，作业已跳过"
            return True, "今天在作业执行日期范围内"
        if mode == "dates":
            values = [str(v).strip()[:10] for v in guard.get("values", []) if str(v or "").strip()]
            if not values:
                raise ValueError("作业执行条件：请至少选择一个执行日期。")
            if today not in values:
                return False, f"作业仅在 {values[0]} 等指定日期执行，今天 {today} 不在其中，作业已跳过"
            return True, "今天为作业指定执行日期"
        values = guard.get("values") if isinstance(guard.get("values"), list) else []
        try:
            # 宽松解析：合法数字项照常生效，空串 / 非数字垃圾项直接忽略。
            nums = sorted({int(v) for v in values if str(v).strip().isdigit()})
        except (TypeError, ValueError):
            nums = []
        if mode == "weekday":
            if not nums:
                # 空列表或全是垃圾值会让条件形同虚设（等于天天执行），与 dates/range 保持一致显式报错。
                raise ValueError("作业执行条件：请至少选择一个执行日。")
            today_wd = now.isoweekday()  # 1=周一 .. 7=周日
            if today_wd not in nums:
                names = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "日"}
                label = "、".join(f"周{names.get(n, n)}" for n in nums)
                return False, f"作业仅在 {label} 执行，今天不是执行日，作业已跳过"
            return True, "今天为作业执行日"
        # mode == "monthday"（mode 已在上面校验过，只剩这一种可能）
        if not nums:
            raise ValueError("作业执行条件：请至少选择一个执行日。")
        today_md = now.day
        if today_md not in nums:
            return False, f"作业仅在每月 {nums} 号执行，今天 {today_md} 号不是执行日，作业已跳过"
        return True, "今天为作业执行日"
    raise ValueError(f"不支持的作业执行条件类型：{gtype}")


def is_server_default_export(path_text: str) -> bool:
    """判断导出产物是否落在服务端默认导出目录。

    之所以要单独判定：export_target_path() 在 exportFolder 为空时会静默回退到
    EXPORTS 目录，作业照样报"成功"，用户只看到"没输出"。这里把它识别出来，
    好在运行日志里显式警告，让配置漏填一眼可见。
    """
    try:
        return Path(str(path_text)).expanduser().parent.resolve() == EXPORTS.resolve()
    except OSError:
        # 路径解析失败（如盘符临时不可用）时按"非默认目录"处理，避免误报。
        return False


def format_export_step_message(files: list[str], rows: int) -> str:
    """拼装导出步骤的运行日志文本，逐个列出产物的绝对路径。

    只写"导出 N 个文件，M 行"时用户无法知道文件落在哪，所以把绝对路径一并写入；
    若全部产物都落在服务端默认目录，则附加警告（说明该步骤没配目标文件夹）。
    """
    if not files:
        return f"导出 0 个文件，{rows} 行。"
    message = f"导出 {len(files)} 个文件（{rows} 行）：" + "；".join(files)
    if all(is_server_default_export(item) for item in files):
        message += "（警告：该导出步骤未配置目标文件夹，文件已写入服务端默认目录）"
    return message


def output_dedupe_key(path: str) -> str:
    """产物路径的去重键：按平台语义归一化后再比较。

    为什么需要：Windows 路径大小写不敏感、`\\` 与 `/` 是同一分隔符，`...\\out\\x.csv` 与
    `...\\OUT\\x.csv` 指向同一个真实文件。只用精确字符串比较会把同一个文件算成 2 个，
    于是「产出文件（2 个）」与实际落盘的 1 个文件对不上（P2-A）。
    `os.path.normcase` 在 Windows 上会统一分隔符并小写化，在 POSIX 上是恒等变换，
    所以这里对两个平台都安全；展示用的字符串仍是首次出现的原始写法。
    """
    return os.path.normcase(os.path.normpath(str(path).strip()))


def dedupe_export_outputs(paths: Iterable[str]) -> list[str]:
    """按出现顺序去重产物路径，保证「产出文件（N 个）」的 N 等于实际文件数。

    为什么需要：顶层作业会把子作业的产物冒泡上来（见 run_saved_job 里 type=job 分支），
    而子作业自己的 message 里也含同一批路径 —— 前端合并父子 run 一起展示时，同一条路径
    与表头就会被列两遍；同一个文件名被多个步骤导出时同样会重复。这里统一去重，
    只保留首次出现的位置与原始字符串，顺序不变。去重键见 output_dedupe_key（P2-A：
    大小写 / 分隔符变体视为同一个文件）。
    """
    seen: set[str] = set()
    unique: list[str] = []
    for item in paths:
        text = str(item).strip()
        key = output_dedupe_key(text)
        if not text or key in seen:
            continue
        seen.add(key)
        unique.append(text)
    return unique


def encode_run_outputs(outputs: Iterable[str]) -> str:
    """把一次运行的产物清单序列化成 _job_runs.outputs_json（前端据此渲染，零文本解析）。"""
    return json.dumps([str(item) for item in outputs], ensure_ascii=False)


def decode_run_outputs(raw: object) -> list[str]:
    """反解 _job_runs.outputs_json。

    容错是硬要求：历史记录（本次改动前落库的运行记录）该列为空串，任何解析失败都必须
    降级成空列表而不是抛异常，否则一条老日志就能把整个日志面板打崩。
    """
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


_OUTPUTS_BLOCK_HEADER = "产出文件（"
_OUTPUTS_WARNING_PREFIX = "（警告："


def strip_outputs_block(text: str, paths: Iterable[str] = ()) -> str:
    """剥离子作业 message 里的产物明细（P1-A）。

    父作业失败时会把子作业整段 message 内嵌进 last_error，子作业自己的「产出文件」块
    也随之进来；本层随后再追加一次自己的产物块，同一条 run message 就会出现 2 个表头、
    同一路径 2 次。这里只丢掉"清单行"：表头、默认目录警告，以及**确实属于该子作业产物列表**
    的路径行（按 output_dedupe_key 归一化比对，不是"像路径就删"），结论句与「最后错误」
    原样保留 —— 失败原因（如 `no such table: xxx`）必须仍然可见。
    用产物列表做白名单而不是正则判断，是为了不误删错误文本里出现的路径行（P2-B 同类问题）。
    """
    known = {output_dedupe_key(item) for item in paths if str(item).strip()}
    kept: list[str] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(_OUTPUTS_BLOCK_HEADER) or stripped.startswith(_OUTPUTS_WARNING_PREFIX):
            continue
        if stripped and output_dedupe_key(stripped) in known:
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def format_outputs_block(files: list[str], failed: bool = False) -> str:
    """把产物路径拼成运行日志里的一段文本块（表头一行 + 每个路径一行）。

    成功与失败两个出口共用同一格式：前端按行渲染时才不会出现两种样式；
    failed=True 时表头额外说明"失败前已落盘"，避免用户把半成品误当成整体成功。
    """
    if not files:
        return ""
    label = "产出文件（{} 个，失败前已落盘）：" if failed else "产出文件（{} 个）："
    return "\n" + label.format(len(files)) + "\n" + "\n".join(files)


def run_saved_job(job_id: str, schedule_id: str = "", visited: set[str] | None = None) -> dict[str, object]:
    visited = visited or set()
    if job_id in visited:
        raise ValueError("检测到作业循环引用，已停止执行。")
    visited.add(job_id)
    with connect_db() as conn:
        row = conn.execute("select * from _jobs where id = ?", (job_id,)).fetchone()
    if not row:
        raise ValueError("作业不存在。")
    job = row_to_job(row)

    # 运行记录先落库、再评估前置条件（P1-2）。
    # 过去 insert 排在守卫评估之后：守卫评估抛错时手动「立即运行」路径只回一条 HTTP 错误，
    # 作业日志里查不到这次失败（定时路径有 run_schedule_once 的兜底补写，手动路径没有）。
    # 先落"运行中"占位，任何提前退出都经 _abort_run_failed() 收口成"失败"，
    # 不会把"运行中"的僵尸记录留在库里。
    run_id = uuid.uuid4().hex
    started = dt.datetime.now()
    with connect_db() as conn:
        conn.execute(
            "insert into _job_runs (id, job_id, schedule_id, job_name, started_at, status) values (?, ?, ?, ?, ?, ?)",
            (run_id, job_id, schedule_id, str(job["name"]), started.strftime("%Y-%m-%d %H:%M:%S"), "运行中"),
        )

    def _abort_run_failed(exc: BaseException) -> None:
        """把本次运行收口成"失败"并落库，保证前置条件评估异常也留下可查的运行记录。"""
        ended_at = dt.datetime.now()
        with connect_db() as conn:
            conn.execute(
                "update _job_runs set ended_at = ?, elapsed_ms = ?, status = ?, message = ? where id = ?",
                (
                    ended_at.strftime("%Y-%m-%d %H:%M:%S"),
                    int((ended_at - started).total_seconds() * 1000),
                    "失败",
                    f"作业执行失败：{exc}",
                    run_id,
                ),
            )
        # 把 run_id 挂在异常上：run_schedule_once 的兜底补写据此判断"这次失败已经落过库了"，
        # 否则定时路径会同一次失败出现两条记录（一条带详细原因、一条只有异常文本）。
        try:
            setattr(exc, "run_id", run_id)
        except Exception:  # pragma: no cover - 异常对象不允许挂属性时退化为兜底补写
            pass

    # ---- 作业级执行条件（B/C 类守卫）：query_has_rows / date_match
    #      不满足 → 整个作业标记"跳过"，不执行任何步骤 ----
    guard = job.get("guard") if isinstance(job.get("guard"), dict) else {}
    guard_reason = ""
    guard_skip = False
    if guard and str(guard.get("type") or "").strip().lower() not in {"", "none", "off"}:
        try:
            ok, reason = evaluate_job_guard(job)
            if not ok:
                guard_skip = True
                guard_reason = reason
        except Exception as exc:
            # 条件评估异常（如查询连接失败）不静默——让作业失败以暴露配置问题
            failure = ValueError(f"作业执行条件评估失败：{exc}")
            _abort_run_failed(failure)
            raise failure from exc

    # ---- 文件守卫：作业中任一启用了 skipIfFileUnchanged 的导入步骤（含被引用的导入任务、
    #      嵌套子作业的导入步骤）----
    #      语义（P0-1）：用户诉求是「每次作业定时任务执行前，都要查询目标文件是否有新增的
    #      内容，如果有新增执行导入」→ any(changed) → 执行。
    #      所以只有当【全部】守卫的指纹都与基线一致（一份新增都没有）才跳过整个作业。
    #      上一轮的写法是"任意一条无变化就跳过"，等于"所有源文件同时变化才执行"，
    #      量词被取反；生产作业 6b990e83 的两个源文件（会员小票.xlsx 与
    #      中秋试饮权益核销.xlsx）由外部系统分别更新，"只变一个"是常态，
    #      被误跳过的那轮又不写基线 → 下一轮继续跳过，作业会静默卡死。
    try:
        guards = _guard_summary(job)
    except Exception as exc:
        # 守卫收集异常（引用的导入任务被删 / 引用成环 / 子作业不存在）不静默——
        # 静默等于条件没配，作业会无条件执行；让它在作业日志里明确失败。
        failure = ValueError(f"作业文件更新条件评估失败：{exc}")
        _abort_run_failed(failure)
        raise failure from exc

    guard_changed: list[str] = []    # 相对基线"有更新"的源文件（含无基线、无法评估）
    guard_unchanged: list[str] = []  # 相对基线"无更新"的源文件
    guard_notes: list[str] = []      # 单条守卫评估失败等原因，必须让用户在运行日志里看到
    # 指纹与基线完全一致的守卫 → 它对应的那个导入步骤本轮不必重跑（步骤级跳过）。
    guard_unchanged_keys: set[tuple[str, int]] = set()
    if guards and not guard_skip:
        for (guard_job_id, guard_step_index), guard_item in guards.items():
            source_text = str(guard_item.get("source") or "")
            try:
                current = _file_fingerprint(source_text)
                with connect_db() as conn:
                    prev = conn.execute(
                        "select fingerprint from _job_file_guards where job_id = ? and step_index = ?",
                        (guard_job_id, guard_step_index),
                    ).fetchone()
            except Exception as exc:
                # 按守卫逐条兜底（P2-1）：上一轮用 try 包住整个 for，第一条守卫的路径暂时
                # 不可达就会中断循环 —— 后面的守卫拿不到指纹、成功时也写不了基线，
                # 而且 guard_reason 被清空、静默无日志，与"不静默"的改法自相矛盾。
                # 现在：失败只影响这一条，其余守卫照常评估。
                # 判定方向：路径不可达 = 无法证明"没有新内容" → 按"有更新"处理并执行
                # （宁可多跑一次也不能把新数据静默漏掉）；真正的坏路径会在导入步骤里
                # 报出明确错误，原因写进 guard_notes 呈现给用户。
                guard_notes.append(f"{source_text}（{exc}）")
                guard_changed.append(source_text)
                continue
            if prev is not None and str(prev["fingerprint"]) == current:
                guard_unchanged.append(source_text)
                # 该源文件与基线一致 → 它对应的导入步骤本轮不重跑（步骤级跳过）
                guard_unchanged_keys.add((guard_job_id, guard_step_index))
            else:
                # prev is None：无基线（首次运行 / 新增守卫）必须视为"有更新"，
                # 否则第一次就跳过、作业永远不会执行。这层语义与上一轮一致，未改坏。
                guard_changed.append(source_text)
            # 只有成功算出指纹的守卫才写基线；评估失败的那条不写，也不阻断别人写。
            guard_item["fingerprint"] = current
        if not guard_changed:
            # 全部守卫都与基线一致 = 没有一个源文件新增内容 → 才跳过整个作业
            guard_skip = True
            guard_reason = f"源文件均无更新（{'、'.join(guard_unchanged)}），作业已跳过"

    # 运行日志里如实交代"这次为什么执行 / 哪些源文件驱动了执行"，
    # 以及"哪条守卫没评估成功、已被当作有更新处理"——都是用户排查配置的依据。
    guard_note_text = ""
    if guard_notes:
        guard_note_text = "\n（警告：以下源文件更新条件无法评估，本次已按「有更新」执行：" + "；".join(guard_notes) + "）"
    elif guard_changed:
        guard_note_text = "\n（本次触发执行：源文件有更新 — " + "、".join(dict.fromkeys(guard_changed)) + "）"

    # 步骤级跳过的明细（作业照常执行，但源文件没变的导入步骤不重跑）：逐个列在运行日志里，
    # 否则用户看到"本次触发执行"却不知道还有步骤被跳过了，会误以为数据被完整重导了一遍。
    guard_skipped_steps: list[str] = []

    if guard_skip:
        ended = dt.datetime.now()
        message = f"作业跳过：{guard_reason}。"
        with connect_db() as conn:
            conn.execute(
                "update _job_runs set ended_at = ?, elapsed_ms = ?, status = ?, message = ?, outputs_json = ? where id = ?",
                (
                    ended.strftime("%Y-%m-%d %H:%M:%S"),
                    int((ended - started).total_seconds() * 1000),
                    "跳过",
                    message,
                    encode_run_outputs([]),
                    run_id,
                ),
            )
        return {"id": run_id, "jobId": job_id, "status": "跳过", "message": message, "outputs": []}

    status = "成功"
    message = ""
    completed_steps = 0
    failed_steps = 0
    skipped_steps = sum(1 for step in job["steps"] if not step.get("enabled", True))
    last_error = ""
    # 本次运行产出的文件绝对路径。顶层作业要把嵌套子作业的产物合并上来，
    # 否则「立即运行」只回一句"作业执行成功"，用户根本看不到文件落在哪。
    outputs: list[str] = []
    for index, step in enumerate(job["steps"], start=1):
        if not step.get("enabled", True):
            continue
        step_id = uuid.uuid4().hex
        step_started = dt.datetime.now()
        step_status = "成功"
        step_message = ""
        should_stop = False
        with connect_db() as conn:
            conn.execute(
                "insert into _job_run_steps (id, run_id, step_index, step_name, step_type, started_at, status) values (?, ?, ?, ?, ?, ?, ?)",
                (step_id, run_id, index, str(step.get("name") or ""), str(step.get("type") or ""), step_started.strftime("%Y-%m-%d %H:%M:%S"), "运行中"),
            )
        # ---- 步骤级跳过：本步骤的源文件与基线一致 → 本轮不重跑这个导入 ----
        # 作业级已经判定"至少有一个源文件新增"所以本次要执行；但两源文件对应两步骤时，
        # 没变的那个步骤重跑一次对 append 模式（生产 6b990e83 的小票导入）等于把整份快照
        # 再追加一遍 → 目标表出现重复行。故只跳过"自己那条守卫未变化"的步骤。
        # 步骤索引换算：_collect_file_guards 用 0 基 enumerate，本循环从 1 开始计数。
        if (job_id, index - 1) in guard_unchanged_keys:
            guard_path = str((guards.get((job_id, index - 1)) or {}).get("source") or "")
            guard_skip_message = f"源文件无更新（{guard_path}），本步骤未重跑导入。"
            guard_skipped_steps.append(f"{step.get('name') or index}（{guard_path}）")
            step_ended = dt.datetime.now()
            with connect_db() as conn:
                conn.execute(
                    "update _job_run_steps set ended_at = ?, elapsed_ms = ?, status = ?, message = ? where id = ?",
                    (
                        step_ended.strftime("%Y-%m-%d %H:%M:%S"),
                        int((step_ended - step_started).total_seconds() * 1000),
                        "跳过",
                        guard_skip_message,
                        step_id,
                    ),
                )
            continue
        try:
            step_type = str(step.get("type") or "")
            config = step.get("config") if isinstance(step.get("config"), dict) else {}
            if step_type == "import":
                config = resolve_import_step_config(config)
                result = execute_import_step(config)
                step_message = (
                    f"导入完成；来源：{result['sourcePath']}；文件：{', '.join(result['fileNames'])}；"
                    f"目标表：{', '.join(result['tableNames'])}；读取 {result['rowsRead']} 行，"
                    f"成功写入 {result['rowsWritten']} 行，更新 {result['rowsUpdated']} 行，跳过 {result['rowsSkipped']} 行；"
                    f"数据库校验行数：{result['verifiedRows']}；{result['sqlStatus']}。"
                )
                if result.get("warning"):
                    step_message += f"\n{result['warning']}"
            elif step_type == "export":
                try:
                    result = run_export_job(config)
                except Exception as exc:
                    fields = {key: str(value) for key, value in config.items() if not isinstance(value, (list, dict))}
                    raise ValueError(friendly_export_error(exc, fields)) from exc
                # result["files"] 已是落盘后的绝对路径，直接进日志，用户才能去目录里取文件
                step_files = [str(item) for item in result["files"]]
                outputs.extend(step_files)
                step_message = format_export_step_message(step_files, int(result["rows"]))
            elif step_type == "query":
                result = execute_query_step(config)
                step_message = f"SQL 执行完成，影响/读取 {result['rows']} 行。"
            elif step_type == "job":
                nested = str(config.get("jobId") or "")
                nested_result = run_saved_job(nested, schedule_id, visited.copy())
                # 子作业的产物先冒泡到本层，成功失败都要：失败时子作业已落盘的半成品也属于
                # 本次运行的产物，否则父 outputs 为空、父 message 却列着路径，两者自相矛盾（P3-C）。
                nested_outputs = [str(item) for item in nested_result.get("outputs", [])]
                outputs.extend(nested_outputs)
                # 步骤日志只写子作业的结论句 + 失败原因：产物已经冒泡到本层、由顶层「产出文件」
                # 块统一列一次，若把子作业整条 message（含它自己的「产出文件」块）原样嵌进来，
                # 同一条 run 里就会出现 2 个表头、同一路径 2 次（P1-A）。
                nested_message = str(nested_result["message"])
                if nested_result["status"] == "跳过":
                    # 子作业因"源文件无更新"等执行条件跳过，这不是故障。若按失败抛出，
                    # 用户会在"确实没有新数据"时看到一条红色失败记录，误判成系统坏了。
                    step_message = f"子作业已跳过：{strip_outputs_block(nested_message, nested_outputs)}"
                elif nested_result["status"] != "成功":
                    raise ValueError(
                        f"子作业执行失败：{strip_outputs_block(nested_message, nested_outputs)}"
                    )
                else:
                    step_message = f"子作业执行成功：{nested_message.splitlines()[0] if nested_message else ''}"
            elif step_type == "sync":
                raise ValueError("同步模块尚未开放。")
            else:
                raise ValueError("不支持的子任务类型。")
        except Exception as exc:
            step_status = "失败"
            step_message = str(exc)
            failed_steps += 1
            status = "失败"
            last_error = step_message
            should_stop = not step.get("continueOnError", False)
        else:
            completed_steps += 1
        finally:
            ended = dt.datetime.now()
            with connect_db() as conn:
                conn.execute(
                    "update _job_run_steps set ended_at = ?, elapsed_ms = ?, status = ?, message = ? where id = ?",
                    (ended.strftime("%Y-%m-%d %H:%M:%S"), int((ended - step_started).total_seconds() * 1000), step_status, step_message, step_id),
                )
        if should_stop:
            break
    # 去重后再拼 message：同一文件可能被父子作业各收集一次（产物冒泡 + 子作业自身步骤），
    # 不去重会出现「产出文件（2 个）」但实际只有 1 个文件
    outputs = dedupe_export_outputs(outputs)
    ended = dt.datetime.now()
    if status == "成功":
        message = f"作业执行成功：{completed_steps} 个步骤成功，{skipped_steps} 个步骤未启用。"
        if outputs:
            # 逐行列出产物绝对路径（前端按行渲染），用户点「立即运行」后一眼能看到文件在哪
            message += format_outputs_block(outputs)
            if any(is_server_default_export(item) for item in outputs):
                # 顶层反馈也要提示，否则用户只看到一条服务端 exports 路径，意识不到是配置漏填
                message += "\n（警告：有文件写入服务端默认目录，说明对应导出步骤未配置目标文件夹）"
        # 作业整体成功后才更新文件守卫指纹（下次执行以此为基线比对）。
        # 基线写在"守卫所属的那个作业"的 job_id 下（嵌套子作业用自己的 id），
        # 与 _guard_summary 的复合键保持一致，否则父子作业会互相覆盖基线。
        if guards:
            for (guard_job_id, guard_step_index), guard_item in guards.items():
                fp = guard_item.get("fingerprint")
                if fp:
                    with connect_db() as conn:
                        conn.execute(
                            "insert or replace into _job_file_guards (job_id, step_index, fingerprint, source_text, updated_at) values (?, ?, ?, ?, ?)",
                            (guard_job_id, guard_step_index, fp, guard_item.get("source", ""), now_text()),
                        )
    else:
        message = f"作业执行失败：{completed_steps} 个步骤成功，{failed_steps} 个步骤失败，{skipped_steps} 个步骤未启用。最后错误：{last_error}"
        if outputs:
            # 失败前已落盘的产物也要报出来：2 步作业第 1 步导出成功、第 2 步失败时，
            # 只给"最后错误"用户不知道半成品在哪，只能重跑一遍
            message += format_outputs_block(outputs, failed=True)
    # 守卫结论统一追加在最后（成功/失败两个出口都要）：用户需要知道这次是被哪个源文件
    # 的新增内容驱动执行的，以及有没有哪条守卫没能评估成功。
    if guard_note_text:
        message += guard_note_text
    if guard_skipped_steps:
        message += "\n（以下步骤本轮未重跑：源文件无更新 — " + "、".join(guard_skipped_steps) + "）"
    with connect_db() as conn:
        conn.execute(
            "update _job_runs set ended_at = ?, elapsed_ms = ?, status = ?, message = ?, outputs_json = ? where id = ?",
            (
                ended.strftime("%Y-%m-%d %H:%M:%S"),
                int((ended - started).total_seconds() * 1000),
                status,
                message,
                encode_run_outputs(outputs),
                run_id,
            ),
        )
    return {"id": run_id, "jobId": job_id, "status": status, "message": message, "outputs": outputs}


def prune_job_logs(retention_days: int) -> None:
    cutoff = (dt.datetime.now() - dt.timedelta(days=max(retention_days, 1))).strftime("%Y-%m-%d %H:%M:%S")
    with connect_db() as conn:
        old_ids = [row["id"] for row in conn.execute("select id from _job_runs where started_at < ?", (cutoff,)).fetchall()]
        if old_ids:
            placeholders = ",".join("?" for _ in old_ids)
            conn.execute(f"delete from _job_run_steps where run_id in ({placeholders})", old_ids)
            conn.execute(f"delete from _job_runs where id in ({placeholders})", old_ids)


def recover_interrupted_runs() -> int:
    """P2-9: 恢复上次进程崩溃残留的「运行中」任务。

    进程在定时任务执行中被强制终止（断电 / OOM / kill -9）时，scheduler 已经把
    _schedules.running 置为 1，却没机会在 run_schedule_once 末尾重置。下次启动后
    dispatch 查询条件是 running = 0，会导致该计划被永久静默跳过。这里在启动时把
    这些僵尸计划重置为可调度，并把挂起的「运行中」执行/步骤记录标记为中断，
    保证历史记录不会永远停在「运行中」。幂等：正常启动时无残留，直接返回 0。
    """
    now = now_text()
    with connect_db() as conn:
        zombies = conn.execute("select id from _schedules where running = 1").fetchall()
        for row in zombies:
            conn.execute(
                "update _schedules set running = 0, updated_at = ? where id = ?",
                (now, row["id"]),
            )
        orphan_runs = conn.execute(
            "select id from _job_runs where status = ?", ("运行中",)
        ).fetchall()
        for row in orphan_runs:
            conn.execute(
                "update _job_runs set ended_at = ?, status = ?, message = ? where id = ?",
                (now, "失败", "进程异常中断，已自动恢复（上次执行未完成）。", row["id"]),
            )
        orphan_steps = conn.execute(
            "select id from _job_run_steps where status = ?", ("运行中",)
        ).fetchall()
        for row in orphan_steps:
            conn.execute(
                "update _job_run_steps set ended_at = ?, status = ?, message = ? where id = ?",
                (now, "失败", "进程异常中断，已自动恢复。", row["id"]),
            )
    n_z, n_r, n_s = len(zombies), len(orphan_runs), len(orphan_steps)
    recovered = n_z + n_r + n_s
    if recovered:
        print(
            f"[recover] 已恢复 {n_z} 个僵尸计划 / {n_r} 条运行记录 / {n_s} 条步骤记录"
            f"（上次进程异常中断）。",
            flush=True,
        )
    return recovered


# ---------------------------------------------------------------------------
# 执行前预检（precheck）
#
# 用户诉求原文：「每轮定时任务执行前 5 分钟做一次检查，有新增就执行，没有新增就跳过整个作业。」
# ——注意语义是**到期前做一次检查**，不是"执行时才检查"、也不是"持续轮询"。
# 所以流程是：到期前 N 分钟 → dispatch_prechecks 起线程跑 precheck_schedule → 结论落
# _schedule_prechecks(本轮 due_at) → 到期后 dispatch_due_schedules 触发 run_schedule_once
# → 读本轮预检结论 → 有新增执行 / 无新增整轮跳过。
# ---------------------------------------------------------------------------

# 正在跑预检的 (schedule_id, due_at)：防止 5 秒一次的调度循环在预检线程还没写完记录前
# 重复起线程（同一轮预检两次没有意义，反而会把文件 IO 翻倍）。
_PRECHECK_INFLIGHT_LOCK = threading.Lock()
_PRECHECK_INFLIGHT: set[str] = set()


def precheck_schedule(schedule_id: str) -> dict[str, object]:
    """定时任务「执行前预检」：判断本轮到期时，源文件相对基线【有没有新增】。

    只检查、不执行，且**严禁写回 _job_file_guards**：
      - _job_file_guards 是执行期「当前指纹 vs 基线」比对的另一端（run_saved_job 在
        作业整体成功后才更新它）。预检若顺手把它刷成当前指纹，执行期两边就变成同一个
        值 → 永远判定"无变化" → 作业从此静默卡死、新数据一条都进不来。
        这是"预检改写基线"类改法最典型的翻车方式，这里钉死：预检只读基线。
      - 同理，预检也不会写 _job_runs（跳过/执行的记录由执行期统一落）。

    量词：has_new = any(changed)。**绝不能写成 all**：
    生产作业通常有多个源文件（如会员小票.xlsx + 权益核销.xlsx），外部系统是分别更新的，
    "只变一个"才是常态。写成 all 就等于"所有源文件同时变化才执行" → 常态被判成"无新增"
    → 整轮跳过；而跳过又不写基线 → 下一轮继续跳过 → 作业静默卡死（P0-1 线上已踩过一次）。

    失败方向一律保守（宁可多跑一次，不能漏一轮数据）：
      - 单条守卫评估失败（路径不可达等）→ 该条按"有更新"并记入 notes（与执行期一致）；
      - 一条守卫都没有 → 按"有新增"；
      - 整个函数异常 → 不落记录，执行期找不到预检记录即按现状执行。
    """
    try:
        with connect_db() as conn:
            row = conn.execute("select * from _schedules where id = ?", (schedule_id,)).fetchone()
        if not row:
            raise ValueError(f"定时任务不存在：{schedule_id}")
        schedule = row_to_schedule(row)
        due_at = str(schedule["nextRunAt"] or "").strip()
        if not due_at:
            raise ValueError(f"定时任务 {schedule_id} 没有到期时间（next_run_at 为空），无法预检")
        job_id = str(schedule["jobId"] or "").strip()
        with connect_db() as conn:
            job_row = conn.execute("select * from _jobs where id = ?", (job_id,)).fetchone()
        if not job_row:
            raise ValueError(f"定时任务 {schedule_id} 引用的作业不存在：{job_id}")
        job = row_to_job(job_row)

        guards = _guard_summary(job)
        items: list[dict[str, object]] = []
        notes: list[str] = []
        changed_any = False
        for (guard_job_id, guard_step_index), guard_item in guards.items():
            source = str(guard_item.get("source") or "")
            try:
                current = _file_fingerprint(source)
                with connect_db() as conn:
                    prev = conn.execute(
                        "select fingerprint from _job_file_guards where job_id = ? and step_index = ?",
                        (guard_job_id, guard_step_index),
                    ).fetchone()
            except Exception as exc:
                # 与执行期同一条原则（P2-1）：单条守卫评估不出来 = 无法证明"没有新内容"
                # → 按"有更新"，并把原因写进 notes；不中断其他守卫的评估。
                notes.append(f"{source}（{exc}）")
                changed_any = True
                items.append({"source": source, "fingerprint": "", "changed": True})
                continue
            changed = prev is None or str(prev["fingerprint"]) != current
            items.append({"source": source, "fingerprint": current, "changed": changed})
            if changed:
                changed_any = True
        if not guards:
            has_new = 1
            reason = "未配置文件更新条件，按有新增处理"
        elif changed_any:
            has_new = 1
            reason = "源文件有更新"
        else:
            has_new = 0
            reason = "源文件均无更新"
        detail = {"items": items, "reason": reason, "notes": notes}
        checked_at = now_text()
        with connect_db() as conn:
            conn.execute(
                """
                insert or replace into _schedule_prechecks (schedule_id, due_at, checked_at, has_new, detail_json)
                values (?, ?, ?, ?, ?)
                """,
                (schedule_id, due_at, checked_at, has_new, json.dumps(detail, ensure_ascii=False)),
            )
        return {
            "scheduleId": schedule_id,
            "dueAt": due_at,
            "checkedAt": checked_at,
            "hasNew": has_new,
            "reason": reason,
            "items": items,
            "notes": notes,
        }
    except Exception as exc:
        # 预检失败绝不能阻止任务执行：不落记录 → 执行期按"无预检记录 = 保守执行"处理。
        print(f"[precheck] 定时任务 {schedule_id} 预检失败，本轮按「有新增」执行：{exc}", flush=True)
        return {
            "scheduleId": schedule_id,
            "dueAt": "",
            "checkedAt": now_text(),
            "hasNew": 1,
            "reason": f"预检失败，按有新增处理：{exc}",
            "items": [],
            "notes": [str(exc)],
            "failed": True,
        }


def _load_precheck(schedule_id: str, due_at: str) -> dict[str, object] | None:
    """读取本轮（schedule_id, due_at）的预检结论；没有记录返回 None（= 保守执行）。"""
    if not due_at:
        return None
    with connect_db() as conn:
        row = conn.execute(
            "select * from _schedule_prechecks where schedule_id = ? and due_at = ?",
            (schedule_id, due_at),
        ).fetchone()
    if not row:
        return None
    try:
        detail = json.loads(row["detail_json"] or "{}")
    except (TypeError, ValueError):
        detail = {}
    if not isinstance(detail, dict):
        detail = {}
    return {
        "scheduleId": str(row["schedule_id"]),
        "dueAt": str(row["due_at"]),
        "checkedAt": str(row["checked_at"]),
        "hasNew": int(row["has_new"] or 0),
        "detail": detail,
    }


def _verify_precheck_unchanged(detail: dict[str, object]) -> tuple[bool, str]:
    """预检结论复核：按 detail 里记录的指纹重算当前指纹，全部一致才确认"仍无新增"。

    为什么要复核：预检在到期前 5 分钟做，执行在 5 分钟后。这 5 分钟里源文件完全可能又被
    外部系统更新（预检判"无新增"之后来的新数据）。若只看预检结论就跳过，这一轮的新数据要
    等到**下一轮**才进库——用户以为是"5 分钟延迟"，实际是丢了一整轮。
    任一文件对不上就取消跳过、正常执行，并把原因报出来。
    """
    items = detail.get("items") if isinstance(detail.get("items"), list) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "")
        recorded = str(item.get("fingerprint") or "")
        if not source or not recorded:
            # 预检时就没算出指纹的那条（评估失败）已按"有更新"处理，不会走到跳过分支，
            # 这里即便出现也不该凭空认定"一致"。
            continue
        try:
            current = _file_fingerprint(source)
        except Exception as exc:
            return False, f"{source} 复核失败（{exc}）"
        if current != recorded:
            return False, f"{source} 在预检之后又发生变化"
    return True, ""


def _delete_precheck(schedule_id: str, due_at: str) -> None:
    """本轮预检记录用完即删。

    不删的话 5 分钟一轮的作业每天会在 _schedule_prechecks 堆 288 条垃圾记录，
    而且 (schedule_id, due_at) 主键永不复用，纯属空间泄漏。
    """
    if not due_at:
        return
    with connect_db() as conn:
        conn.execute("delete from _schedule_prechecks where schedule_id = ? and due_at = ?", (schedule_id, due_at))


def _insert_skipped_run(schedule: dict[str, object], precheck: dict[str, object]) -> None:
    """整轮跳过时补一条 _job_runs 记录（status="跳过"）。

    为什么要留痕：定时任务每几分钟一轮，跳过若不留记录，用户看到的会是"上次运行：3 小时前"，
    无法区分"作业挂了"和"确实没有新数据"，只能靠猜。
    """
    checked_at = str(precheck.get("checkedAt") or "")
    hhmm = checked_at[11:16] if len(checked_at) >= 16 else ""
    detail = precheck.get("detail") if isinstance(precheck.get("detail"), dict) else {}
    reason = str(detail.get("reason") or "源文件均无更新")
    message = f"预检（{hhmm or '预检'}）：{reason}，本轮跳过"
    now = now_text()
    with connect_db() as conn:
        conn.execute(
            """
            insert into _job_runs (id, job_id, schedule_id, job_name, started_at, ended_at, elapsed_ms, status, message)
            values (?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                str(schedule["jobId"]),
                str(schedule["id"]),
                str(schedule["name"]),
                now,
                now,
                "跳过",
                message,
            ),
        )


def _append_run_message(run_id: str, extra: str) -> None:
    """给已落库的运行记录追加一段说明（用于"取消跳过"这类执行期补充信息）。"""
    if not run_id or not extra:
        return
    with connect_db() as conn:
        conn.execute("update _job_runs set message = message || ? where id = ?", (extra, run_id))


def dispatch_prechecks() -> None:
    """扫描即将到期的计划，在到期前 precheck_minutes 分钟内触发一次预检（每轮只做一次）。

    与 dispatch_due_schedules 并列由 scheduler_loop 每 5 秒调用一次：
      - 已过点（now >= due）的不在这里处理，交给 dispatch_due_schedules 正常执行；
      - 本轮已有 (schedule_id, due_at) 预检记录 → 跳过（幂等，避免每 5 秒重复检查文件）；
      - 预检在 daemon 线程里跑，不阻塞调度循环。
    """
    now = app_now()
    with connect_db() as conn:
        rows = conn.execute(
            "select * from _schedules where enabled = 1 and running = 0 and next_run_at != '' and precheck_minutes > 0"
        ).fetchall()
    for row in rows:
        schedule = row_to_schedule(row)
        schedule_id = str(schedule["id"])
        due = parse_datetime(str(schedule["nextRunAt"]))
        if due is None or now >= due:
            continue
        minutes = _row_int(row, "precheck_minutes", 5)
        if minutes <= 0 or (due - now) > dt.timedelta(minutes=minutes):
            continue
        due_at = str(schedule["nextRunAt"])
        with connect_db() as conn:
            done = conn.execute(
                "select 1 from _schedule_prechecks where schedule_id = ? and due_at = ?",
                (schedule_id, due_at),
            ).fetchone()
        if done:
            continue
        key = f"{schedule_id}|{due_at}"
        with _PRECHECK_INFLIGHT_LOCK:
            if key in _PRECHECK_INFLIGHT:
                continue
            _PRECHECK_INFLIGHT.add(key)
        threading.Thread(target=_run_precheck_thread, args=(schedule_id, key), daemon=True).start()


def _run_precheck_thread(schedule_id: str, key: str) -> None:
    """预检线程体：跑完必须把 in-flight 标记摘掉，否则该轮之后再也预检不了。"""
    try:
        precheck_schedule(schedule_id)
    finally:
        with _PRECHECK_INFLIGHT_LOCK:
            _PRECHECK_INFLIGHT.discard(key)


def dispatch_due_schedules() -> None:
    now = now_text()
    with connect_db() as conn:
        rows = conn.execute(
            "select * from _schedules where enabled = 1 and running = 0 and next_run_at != '' and next_run_at <= ?",
            (now,),
        ).fetchall()
        for row in rows:
            conn.execute("update _schedules set running = 1 where id = ?", (row["id"],))
    for row in rows:
        threading.Thread(target=run_schedule_once, args=(row["id"],), daemon=True).start()


def run_schedule_once(schedule_id: str) -> None:
    with connect_db() as conn:
        row = conn.execute("select * from _schedules where id = ?", (schedule_id,)).fetchone()
    if not row:
        return
    schedule = row_to_schedule(row)
    # 本轮到期时间必须在算 next_run **之前**取：下面会把 _schedules.next_run_at 覆盖成
    # 下一轮时间，而预检记录以 (schedule_id, 本轮 next_run_at) 为主键，取晚了就查不到本轮结论。
    due_at = str(schedule["nextRunAt"] or "").strip()
    # 无预检记录（服务在这 5 分钟内才启动 / 预检异常 / precheck_minutes=0）→ 保守执行，
    # 行为与引入预检之前完全一致，不会因为"没检查过"就把作业跳掉。
    precheck = _load_precheck(schedule_id, due_at)
    status = "成功"
    cancel_note = ""
    try:
        prune_job_logs(int(schedule["logRetentionDays"]))
        should_skip = False
        skip_reason = ""
        if precheck is not None and int(precheck["hasNew"]) == 0:
            still_unchanged, note = _verify_precheck_unchanged(precheck["detail"])  # type: ignore[arg-type]
            if still_unchanged:
                should_skip = True
                detail = precheck["detail"] if isinstance(precheck["detail"], dict) else {}
                skip_reason = str(detail.get("reason") or "源文件均无更新")
            else:
                # 双校验未通过：预检之后、执行之前文件又变了 → 取消跳过，正常执行
                cancel_note = note
                print(
                    f"[precheck] 定时任务 {schedule_id} 本轮原判定跳过，但复核发现源文件已变化（{note}），取消跳过改为执行。",
                    flush=True,
                )
        if should_skip:
            status = "跳过"
            print(f"[precheck] 定时任务 {schedule_id} 本轮跳过（{skip_reason}）。", flush=True)
            _insert_skipped_run(schedule, precheck)  # type: ignore[arg-type]
        else:
            result = run_saved_job(str(schedule["jobId"]), schedule_id)
            status = str(result["status"])
            if cancel_note:
                _append_run_message(
                    str(result.get("id") or ""),
                    f"\n（预检判定无新增本可跳过，但复核发现源文件在预检后又发生变化（{cancel_note}），已取消跳过并执行本轮作业。）",
                )
    except Exception as exc:
        status = "失败"
        # run_saved_job 已经在自己的 run 记录里写过失败原因（P1-2：先落记录再评估前置条件，
        # 手动「立即运行」路径也才查得到）——那种情况下不再补写，避免同一次失败出现两条记录。
        # 其余异常（例如作业不存在、run 记录都还没建起来）仍走兜底，保证定时路径一定有记录。
        if not getattr(exc, "run_id", ""):
            with connect_db() as conn:
                conn.execute(
                    "insert into _job_runs (id, job_id, schedule_id, job_name, started_at, ended_at, status, message) values (?, ?, ?, ?, ?, ?, ?, ?)",
                    (uuid.uuid4().hex, str(schedule["jobId"]), schedule_id, str(schedule["name"]), now_text(), now_text(), "失败", str(exc)),
                )
    # 无论跳过还是执行，本轮预检结论都已消费完，删掉避免堆积
    _delete_precheck(schedule_id, due_at)
    rule = schedule["rule"] if isinstance(schedule["rule"], dict) else {}
    next_run = compute_next_run(rule, str(schedule["startAt"]), str(schedule["endAt"]), now_text())
    enabled = 1 if next_run else 0
    with connect_db() as conn:
        conn.execute(
            "update _schedules set running = 0, enabled = ?, last_run_at = ?, last_status = ?, next_run_at = ?, updated_at = ? where id = ?",
            (enabled, now_text(), status, next_run, now_text(), schedule_id),
        )


def scheduler_loop(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        # 先预检（到期前 N 分钟），再按点派发执行 —— 顺序不能反：
        # 反了会先执行、后预检，本轮的预检结论就永远差一轮。
        try:
            dispatch_prechecks()
        except Exception:
            pass
        try:
            dispatch_due_schedules()
        except Exception:
            pass
        stop_event.wait(5)


def log_import(
    conn: sqlite3.Connection,
    file_name: str,
    table_name: str,
    mode: str,
    rows_read: int,
    rows_written: int,
    rows_updated: int,
    rows_skipped: int,
    status: str,
    message: str,
) -> None:
    if parse_bool({"disableLog": "false"}, "disableLog", False):
        return
    conn.execute(
        """
        insert into _import_logs
        (id, created_at, file_name, table_name, mode, rows_read, rows_written, rows_updated, rows_skipped, status, message)
        values (?, datetime('now', 'localtime'), ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (str(uuid.uuid4()), file_name, table_name, mode, rows_read, rows_written, rows_updated, rows_skipped, status, message),
    )


def import_file_fingerprint(path: Path) -> str:
    """P2-12：源文件内容指纹（大小 + 内容 sha256 前 32 位）。

    浏览器上传产生的副本 mtime 每次都是新的，不能作为"文件是否更新"的依据；
    内容哈希是唯一可靠信号。按 1MB 分块读取，两万行 xlsx 约毫秒级。
    """
    import hashlib

    digest = hashlib.sha256()
    size = path.stat().st_size
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"{size}:{digest.hexdigest()[:32]}"


def import_file_key(file_name: str, fields: dict[str, str]) -> str:
    """P2-12：文件导入身份键 = 目标库 + 目标表配置 + 文件名。

    同一文件导入到不同目标（换连接或换表）不算"上次导入过"，
    避免同一模板文件导入第二张表时被误跳过。
    """
    dest = str(fields.get("connectionId") or "").strip()
    if not dest:
        dest = "|".join(
            [
                str(fields.get("dbHost") or ""),
                str(fields.get("dbPort") or ""),
                str(fields.get("dbName") or ""),
            ]
        )
    target = str(fields.get("targetDbType") or "mysql").strip().lower()
    return f"{target}|{dest}|{str(fields.get('tableName') or '').strip()}|{file_name}"


def import_file_fingerprint_seen(file_key: str, fingerprint: str) -> bool:
    with connect_db() as conn:
        row = conn.execute(
            "select fingerprint from _import_file_fingerprints where file_key = ?",
            (file_key,),
        ).fetchone()
    return bool(row and str(row["fingerprint"]) == fingerprint)


def record_import_file_fingerprint(file_key: str, fingerprint: str, table_name: str) -> None:
    with connect_db() as conn:
        conn.execute(
            """
            insert or replace into _import_file_fingerprints (file_key, fingerprint, table_name, updated_at)
            values (?, ?, ?, datetime('now', 'localtime'))
            """,
            (file_key, fingerprint, table_name),
        )


def run_import_batch(
    uploaded_files: list[UploadedFile],
    fields: dict[str, str],
    fail_fast: bool = False,
) -> tuple[list[dict[str, object]], list[dict[str, str]], int]:
    """按顺序导入一批文件（P2-12：skipSeenFile 命中时整文件跳过）。

    返回 (results, failures, skipped_files)：
    - 每次导入成功都记录内容指纹（无论是否勾选跳过），保证"上次导入"语义准确：
      先正常导入、后开启跳过选项也能正确命中；
    - 勾选"跳过自上次导入后未曾更新过的文件"时，按 内容指纹+目标身份 判断，
      命中则该文件不解析不写入，写一条"已跳过"日志，计入 skipped_files；
      仅导入成功后才记录指纹，失败的文件下次仍会重试。
    - fail_fast=True 时首个失败即抛出（作业/定时路径保持原有失败中止语义）。
    """
    skip_seen = parse_bool(fields, "skipSeenFile", False)
    results: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    skipped_files = 0
    for uploaded in uploaded_files:
        try:
            fingerprint = import_file_fingerprint(uploaded.path)
        except OSError:
            fingerprint = ""
        if skip_seen and fingerprint:
            file_key = import_file_key(uploaded.filename, fields)
            if import_file_fingerprint_seen(file_key, fingerprint):
                results.append(
                    {
                        "fileName": uploaded.filename,
                        "tableName": "",
                        "columns": [],
                        "rowsRead": 0,
                        "rowsWritten": 0,
                        "rowsUpdated": 0,
                        "rowsSkipped": 0,
                        "message": "文件自上次导入后未变更，已跳过",
                    }
                )
                skipped_files += 1
                if not parse_bool(fields, "disableLog", False):
                    with connect_db() as log_conn:
                        log_import(
                            log_conn,
                            uploaded.filename,
                            "",
                            fields.get("importMode", "append"),
                            0,
                            0,
                            0,
                            0,
                            "成功",
                            "文件自上次导入后未变更，已跳过",
                        )
                continue
        try:
            result = import_uploaded_file(uploaded, fields)
        except Exception as exc:
            if fail_fast:
                raise
            failures.append({"fileName": uploaded.filename, "error": str(exc)})
            continue
        results.append(result)
        if fingerprint:
            record_import_file_fingerprint(
                import_file_key(uploaded.filename, fields),
                fingerprint,
                str(result.get("tableName") or ""),
            )
    return results, failures, skipped_files


def import_uploaded_file(uploaded: UploadedFile, fields: dict[str, str]) -> dict[str, object]:
    if fields.get("sheetMode") == "all" and uploaded.path.suffix.lower() in {".xlsx", ".xlsm", ".xls"}:
        results = []
        original_table_name = fields.get("tableName", "")
        for tabular in read_tabular_tasks(uploaded.path, fields):
            per_sheet = dict(fields)
            per_sheet["sheetMode"] = "specified"
            per_sheet["sheetFilterMode"] = "name"
            per_sheet["sheetName"] = tabular.selected_sheet
            if original_table_name:
                per_sheet["tableName"] = f"{original_table_name}_{tabular.selected_sheet}"
            else:
                per_sheet["tableNameRule"] = "sheet"
            results.append(import_uploaded_file(uploaded, per_sheet))
        sheet_details = [
            detail for item in results for detail in (item.get("skipDetails") or [])
        ][:MAX_SKIP_DETAILS]
        return {
            "fileName": uploaded.filename,
            "tableName": results[0]["tableName"] if results else "",
            "columns": results[0]["columns"] if results else [],
            "rowsRead": sum(int(item["rowsRead"]) for item in results),
            "rowsWritten": sum(int(item["rowsWritten"]) for item in results),
            "rowsUpdated": sum(int(item["rowsUpdated"]) for item in results),
            "rowsSkipped": sum(int(item["rowsSkipped"]) for item in results),
            "skipDetails": sheet_details,
            "message": f"已导入 {len(results)} 个 Sheet",
            "sheetResults": results,
        }

    tabular = read_tabular_file(uploaded.path, fields)
    skip_details: list[dict[str, object]] = []
    columns, rows, match_keys, skipped = build_target_data(tabular, fields, uploaded.filename, skip_details)
    rows = convert_date_columns(columns, rows, fields)
    table_name = normalize_target_name(uploaded, tabular, fields)
    mode = fields.get("importMode", "append")
    if mode not in {"append", "update", "overwrite", "rebuild"}:
        raise ValueError("导入模式只能是追加、更新、覆盖或重建。")

    conn = connect_target_db(fields)
    written = 0
    updated = 0
    verified_rows = 0
    try:
        duplicate_mode = fields.get("duplicateTableMode", "same")
        if target_table_exists(conn, table_name, fields) and mode == "append" and duplicate_mode == "skip":
            if not parse_bool(fields, "disableLog", False):
                with connect_db() as log_conn:
                    log_import(log_conn, uploaded.filename, table_name, mode, len(tabular.rows), 0, 0, len(rows), "成功", "目标表重复，已按配置跳过")
            return {
                "fileName": uploaded.filename,
                "tableName": table_name,
                "columns": columns,
                "rowsRead": len(tabular.rows),
                "rowsWritten": 0,
                "rowsUpdated": 0,
                "rowsSkipped": len(rows),
                "skipDetails": [
                    {
                        "dataRow": 0,
                        "reason": f"目标表 {table_name} 已存在，重复表名策略为「跳过」，整个文件未导入",
                        "key": "",
                    }
                ],
                "message": "目标表重复，已跳过",
            }
        if target_table_exists(conn, table_name, fields) and mode == "append" and duplicate_mode == "suffix":
            base = table_name
            index = 2
            while target_table_exists(conn, table_name, fields):
                table_name = sanitize_identifier(f"{base}_{index}", "import_table")
                index += 1

        # 字段匹配「按顺序」：目标表已存在时按列序号对齐（默认 name 匹配不受影响）
        columns, match_keys = align_columns_by_position(conn, table_name, columns, match_keys, fields)

        cp_key = checkpoint_key(uploaded, table_name, fields)
        resume_offset = get_checkpoint(cp_key) if parse_bool(fields, "resumeImport", False) and mode in {"append", "rebuild", "overwrite"} else 0
        target_create_or_expand_table(conn, table_name, columns, rows, mode == "rebuild" and resume_offset == 0, parse_bool(fields, "autoExpand", True), fields)
        existing_keys = target_existing_column_keys(conn, table_name, fields)
        if any(column.strip().lower() not in existing_keys for column in columns):
            raise ValueError("目标表字段与当前导入字段不一致。")
        rows_before = target_row_count(conn, table_name, fields)

        if mode == "overwrite" and resume_offset == 0:
            sql = f"delete from {db_quote(table_name, fields)}"
            if target_db_type(fields) == "mysql":
                with conn.cursor() as cursor:
                    cursor.execute(sql)
            else:
                conn.execute(sql)

        active_rows = rows[resume_offset:] if resume_offset else rows

        def progress(done: int) -> None:
            if parse_bool(fields, "resumeImport", False):
                if target_db_type(fields) == "sqlite":
                    conn.commit()
                set_checkpoint(cp_key, resume_offset + done)

        if mode == "update":
            written, updated = target_update_rows(conn, table_name, columns, rows, match_keys, fields)
        else:
            if fields.get("writeMode") == "load" and target_db_type(fields) == "mysql":
                try:
                    written = mysql_load_rows(conn, table_name, columns, active_rows, fields, progress)
                except Exception:
                    written = target_insert_rows(conn, table_name, columns, active_rows, fields, progress)
            elif fields.get("writeMode") == "parallel":
                conn.commit()
                written = target_insert_rows_parallel(table_name, columns, active_rows, fields, progress)
            else:
                written = target_insert_rows(conn, table_name, columns, active_rows, fields, progress)
            updated = 0

        target_execute_sql_batch(conn, fields.get("customSql", ""), "自定义 SQL", fields)
        target_execute_sql_batch(conn, fields.get("afterEachSql", ""), "每次导入成功后 SQL", fields)
        if target_db_type(fields) != "mysql" or fields.get("commitMode") != "auto":
            conn.commit()
        verified_rows = target_row_count(conn, table_name, fields)
        if mode == "overwrite" and resume_offset == 0:
            expected_rows = written
        elif mode == "rebuild" and resume_offset == 0:
            expected_rows = written
        elif mode == "append":
            expected_rows = rows_before + written
        else:
            expected_rows = verified_rows
        if verified_rows != expected_rows:
            raise ValueError(
                f"导入后数据库行数校验失败：目标表当前 {verified_rows} 行，预期 {expected_rows} 行。任务已标记为失败。"
            )
        if not parse_bool(fields, "disableLog", False):
            with connect_db() as log_conn:
                log_import(
                    log_conn,
                    uploaded.filename,
                    table_name,
                    mode,
                    len(tabular.rows),
                    written,
                    updated,
                    skipped,
                    "成功",
                    f"导入完成；源文件 {len(tabular.rows)} 行，成功写入 {written} 行，数据库校验 {verified_rows} 行",
                )
        if parse_bool(fields, "resumeImport", False):
            clear_checkpoint(cp_key)
    except Exception as exc:
        conn.rollback()
        if not parse_bool(fields, "disableLog", False):
            with connect_db() as log_conn:
                log_import(
                    log_conn,
                    uploaded.filename,
                    table_name,
                    mode,
                    len(tabular.rows),
                    written,
                    updated,
                    skipped,
                    "失败",
                    f"导入失败：{exc}",
                )
        raise
    finally:
        conn.close()

    delete_note = ""
    if parse_bool(fields, "deleteAfterSuccess", False):
        delete_note = safe_delete_source_file(uploaded.path)

    return {
        "fileName": uploaded.filename,
        "tableName": table_name,
        "columns": columns,
        "rowsRead": len(tabular.rows),
        "rowsWritten": written,
        "rowsUpdated": updated,
        "rowsSkipped": skipped,
        "verifiedRows": verified_rows,
        "skipDetails": skip_details,
        "deleteNote": delete_note,
        "message": "导入完成",
    }

def parse_multipart(handler: SimpleHTTPRequestHandler) -> tuple[dict[str, str], list[UploadedFile]]:
    content_type = handler.headers.get("Content-Type", "")
    if "multipart/form-data" not in content_type:
        raise ValueError("请求格式错误，需要 multipart/form-data。")
    boundary_match = re.search(r"boundary=(.+)", content_type)
    if not boundary_match:
        raise ValueError("上传请求缺少 boundary。")
    boundary = boundary_match.group(1).strip('"')
    length = int(handler.headers.get("Content-Length", "0"))
    body = handler.rfile.read(length)
    parts = body.split(("--" + boundary).encode("utf-8"))
    fields: dict[str, str] = {}
    uploaded_files: list[UploadedFile] = []

    for part in parts:
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        header_blob, _, content = part.partition(b"\r\n\r\n")
        headers = header_blob.decode("utf-8", errors="replace")
        name_match = re.search(r'name="([^"]+)"', headers)
        if not name_match:
            continue
        name = name_match.group(1)
        filename_match = re.search(r'filename="([^"]*)"', headers)
        content = content.rstrip(b"\r\n")
        if filename_match and filename_match.group(1):
            original = Path(filename_match.group(1)).name
            target = UPLOADS / f"{int(time.time())}_{uuid.uuid4().hex}_{original}"
            target.write_bytes(content)
            uploaded_files.append(UploadedFile(filename=original, path=target))
        else:
            fields[name] = content.decode("utf-8", errors="replace")
    return fields, uploaded_files


def read_json_body(handler: SimpleHTTPRequestHandler) -> dict[str, object]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    payload = json.loads(raw) if raw.strip() else {}
    if not isinstance(payload, dict):
        raise ValueError("请求内容格式不正确。")
    return payload


# ---------------------------------------------------------------------------
# 认证内核（P1，2026-09-22）
#
# 双通道设计：
#   * Cookie 会话（浏览器）—— 登录页换取 dc_session；支持登出、超时、按角色
#   * HTTP Basic（脚本 / 健康检查）—— 沿用 ADMIN_USER / ADMIN_PASSWORD，
#     使既有 acceptance/ 复跑脚本与 /api/ping 探测**零改造**
#
# 安全约定：密码一律 scrypt 加盐哈希（不存明文）；会话 token 只在库里保存 sha256。
# ---------------------------------------------------------------------------

AUTH_COOKIE_NAME = "dc_session"
AUTH_SESSION_HOURS = 8
AUTH_REMEMBER_DAYS = 7
AUTH_MAX_FAILED = 5
AUTH_LOCK_MINUTES = 5
AUTH_ROLES = ("admin", "operator", "viewer")
AUTH_EXEMPT_PATHS = {"/api/ping", "/api/auth/login", "/api/auth/register", "/api/auth/signup-info"}
# 未登录也必须能直接打开的页面。登录页必须在这里，否则未登录访问 /login.html
# 会被 require_auth 再一次 302 到 /login.html 自己，浏览器报 ERR_TOO_MANY_REDIRECTS。
AUTH_PUBLIC_PAGES = {"/login.html"}
AUTH_STATIC_SUFFIXES = (
    ".css", ".js", ".svg", ".png", ".jpg", ".jpeg", ".ico", ".gif",
    ".woff", ".woff2", ".ttf", ".map", ".txt",
)


def signup_code() -> str:
    """开放注册的邀请码；未配置则注册不需要邀请码（见设计文档第十三节）。"""
    return os.environ.get("DC_SIGNUP_CODE", "").strip()


def signup_default_role() -> str:
    """自助注册的新账号拿什么角色。

    业主 2026-09-22：要求「任何人都能访问并自由创建账号」——「能注册」这件事
    默认就是开的（`DC_SIGNUP_CODE` 留空即可），但**新账号能不能写数据**是一个
    独立的安全决策，用 `DC_DEFAULT_ROLE` 控制：

    · viewer（默认）—— 只能看与只读查询，要导入/导出得管理员提权
    · operator      —— 注册完即可导入/导出/执行写语句

    取值非法时一律回落到最小权限 viewer：配置写错绝不能变成「悄悄给所有人写权限」。
    """
    value = os.environ.get("DC_DEFAULT_ROLE", "").strip().lower()
    return value if value in ROLE_ORDER else "viewer"


def hash_password(password: str) -> str:
    """scrypt 加盐哈希，格式：scrypt$n$r$p$salt_hex$hash_hex"""
    n, r, p = 2 ** 14, 8, 1
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """常量时间校验；任何解析异常一律视为不匹配。"""
    try:
        scheme, n_text, r_text, p_text, salt_hex, hash_hex = str(stored).split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n_text),
            r=int(r_text),
            p=int(p_text),
            dklen=len(hash_hex) // 2,
        )
    except Exception:  # noqa: BLE001
        return False
    return hmac.compare_digest(digest.hex(), hash_hex)


def parse_cookie_header(header_value: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in str(header_value or "").split(";"):
        name, separator, value = part.partition("=")
        if separator:
            cookies[name.strip()] = value.strip()
    return cookies


def request_is_https(handler) -> bool:
    return str(handler.headers.get("X-Forwarded-Proto", "")).strip().lower() == "https"


def _expiry_text(seconds: int) -> str:
    return (dt.datetime.now() + dt.timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")


def session_cookie_header(token: str, max_age: int, https: bool) -> str:
    cookie = f"{AUTH_COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"
    return cookie + "; Secure" if https else cookie


def expired_cookie_header(https: bool) -> str:
    cookie = f"{AUTH_COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
    return cookie + "; Secure" if https else cookie


def create_session(user_id: str, ip: str, user_agent: str, remember: bool) -> tuple[str, int]:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    seconds = AUTH_REMEMBER_DAYS * 86400 if remember else AUTH_SESSION_HOURS * 3600
    stamp = now_text()
    with connect_db() as conn:
        conn.execute(
            "insert into _sessions (id, token_hash, user_id, created_at, expires_at, last_seen_at, ip, user_agent)"
            " values (?, ?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, token_hash, user_id, stamp, _expiry_text(seconds), stamp, ip, str(user_agent)[:300]),
        )
    return token, seconds


def resolve_session(token: str) -> dict[str, object] | None:
    """校验会话并返回用户；过期 / 被停用 / 不存在都返回 None（顺带清理）。"""
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with connect_db() as conn:
        row = conn.execute(
            """
            select s.id as session_id, s.expires_at, u.id as user_id, u.username,
                   u.display_name, u.role, u.enabled
            from _sessions s join _users u on u.id = s.user_id
            where s.token_hash = ?
            """,
            (token_hash,),
        ).fetchone()
        if not row:
            return None
        if str(row["expires_at"]) <= now_text() or not int(row["enabled"] or 0):
            conn.execute("delete from _sessions where id = ?", (row["session_id"],))
            return None
        conn.execute("update _sessions set last_seen_at = ? where id = ?", (now_text(), row["session_id"]))
    return {
        "id": row["user_id"],
        "username": row["username"],
        "displayName": row["display_name"] or row["username"],
        "role": row["role"],
    }


def destroy_session(token: str) -> None:
    if not token:
        return
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with connect_db() as conn:
        conn.execute("delete from _sessions where token_hash = ?", (token_hash,))


def destroy_user_sessions(user_id: str) -> int:
    with connect_db() as conn:
        cursor = conn.execute("delete from _sessions where user_id = ?", (user_id,))
        return cursor.rowcount or 0


def prune_expired_sessions() -> int:
    with connect_db() as conn:
        cursor = conn.execute("delete from _sessions where expires_at <= ?", (now_text(),))
        return cursor.rowcount or 0


def current_user(handler) -> dict[str, object] | None:
    cookies = parse_cookie_header(handler.headers.get("Cookie", ""))
    return resolve_session(cookies.get(AUTH_COOKIE_NAME, ""))


def audit_log(
    user: dict[str, object] | None,
    action: str,
    target: str = "",
    detail: str = "",
    ip: str = "",
    status: str = "ok",
) -> None:
    """写审计；任何异常都不影响主流程（审计不是关键路径）。"""
    try:
        with connect_db() as conn:
            conn.execute(
                "insert into _audit_logs (id, created_at, user_id, username, action, target, detail, ip, status)"
                " values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    uuid.uuid4().hex,
                    now_text(),
                    str((user or {}).get("id") or ""),
                    str((user or {}).get("username") or ""),
                    action,
                    str(target)[:200],
                    str(detail)[:500],
                    str(ip),
                    status,
                ),
            )
    except Exception:  # noqa: BLE001
        pass


def find_user(username: str) -> sqlite3.Row | None:
    with connect_db() as conn:
        return conn.execute("select * from _users where username = ?", (username,)).fetchone()


def count_users() -> int:
    with connect_db() as conn:
        return int(conn.execute("select count(*) from _users").fetchone()[0])


def has_enabled_admin() -> bool:
    with connect_db() as conn:
        return bool(
            conn.execute("select 1 from _users where role = 'admin' and enabled = 1 limit 1").fetchone()
        )


def bootstrap_admin_from_env() -> None:
    """管理员引导：配置了 ADMIN_PASSWORD 且**没有任何启用中的 admin** 时，用它建一个。

    判定条件不是「用户表为空」——注册用户（viewer）可能先存在，但那不解决
    「没人能登录管理」的问题；只要不存在可用的管理员就补建，保证任何部署都不会被锁在门外。
    同名账号已存在时不再重复创建（幂等），也不会覆盖既有密码。
    """
    password = os.environ.get("ADMIN_PASSWORD", "").strip()
    if not password:
        return
    username = os.environ.get("ADMIN_USER", "admin").strip() or "admin"
    if find_user(username) or has_enabled_admin():
        return
    with connect_db() as conn:
        conn.execute(
            "insert into _users (id, username, display_name, password_hash, role, enabled, created_at, created_by)"
            " values (?, ?, ?, ?, 'admin', 1, ?, 'bootstrap')",
            (uuid.uuid4().hex, username, username, hash_password(password), now_text()),
        )
    print(f"[auth] 已按 ADMIN_USER/ADMIN_PASSWORD 创建首个管理员账号：{username}", flush=True)


def user_public(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "username": row["username"],
        "displayName": row["display_name"] or row["username"],
        "role": row["role"],
        "enabled": bool(row["enabled"]),
        "createdAt": row["created_at"],
        "lastLoginAt": row["last_login_at"] or "",
        "lockedUntil": row["locked_until"] or "",
    }


def public_auth_enabled() -> bool:
    enabled_value = os.environ.get("APP_AUTH_ENABLED", "").strip().lower()
    return enabled_value in {"1", "true", "yes", "on"} and bool(os.environ.get("ADMIN_PASSWORD", "").strip())


def check_basic_auth(header_value: str) -> bool:
    admin_user = os.environ.get("ADMIN_USER", "admin")
    admin_password = os.environ.get("ADMIN_PASSWORD", "")
    if not admin_password:
        return True
    if not header_value.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header_value[6:].strip()).decode("utf-8")
    except Exception:
        return False
    user, separator, password = decoded.partition(":")
    if not separator:
        return False
    return hmac.compare_digest(user, admin_user) and hmac.compare_digest(password, admin_password)


# ---------------------------------------------------------------------------
# 权限矩阵与请求防护（P3 / P4）
#
# 角色：viewer（只读） < operator（可写） < admin（管理员）
#
# · 路由鉴权按 (方法, 路径) 查 ROUTE_MIN_ROLE；查不到一律按 admin 处理（fail closed），
#   宁可漏放一个只读接口，也不让新接口默认对只读账号敞开。
# · 认证未启用（本机桌面模式）与 HTTP Basic 通道一律视为 admin —— 保持本机使用习惯，
#   并让 acceptance/ 下既有复跑脚本零改造。
# · CSRF 只针对 Cookie 会话通道：浏览器会自动带上 Cookie，而查询模块能执行 DROP，
#   所以写操作必须带自定义头且同源。Basic 通道是脚本，不受影响。
# ---------------------------------------------------------------------------

ROLE_ORDER = {"viewer": 1, "operator": 2, "admin": 3}
ROLE_LABELS = {"viewer": "只读", "operator": "可写", "admin": "管理员"}
ROLE_OPTIONS = [
    {"value": "viewer", "label": "只读", "description": "查看表/日志/查询，只能执行只读 SQL"},
    {"value": "operator", "label": "可写", "description": "导入、导出、执行写语句、管理作业与定时任务"},
    {"value": "admin", "label": "管理员", "description": "连接增删改、账号管理、审计查看，全部权限"},
]

ROUTE_MIN_ROLE: dict[tuple[str, str], str] = {
    # ---- 登录态与本人操作：任何已登录账号 ----
    ("GET", "/api/auth/me"): "viewer",
    ("POST", "/api/auth/logout"): "viewer",
    ("POST", "/api/auth/password"): "viewer",
    # ---- 只读 ----
    ("GET", "/api/ping"): "viewer",
    ("GET", "/api/meta"): "viewer",
    ("GET", "/api/docs"): "viewer",
    ("GET", "/api/tables"): "viewer",
    ("GET", "/api/table"): "viewer",
    ("GET", "/api/target-tables"): "viewer",
    ("GET", "/api/target-table-details"): "viewer",
    ("GET", "/api/logs"): "viewer",
    ("GET", "/api/storage/status"): "viewer",
    ("GET", "/api/connections"): "viewer",
    ("GET", "/api/jobs"): "viewer",
    ("GET", "/api/schedules"): "viewer",
    ("GET", "/api/job-runs"): "viewer",
    ("GET", "/api/queries"): "viewer",
    ("GET", "/api/export/sources"): "viewer",
    ("POST", "/api/tables"): "viewer",
    ("POST", "/api/table"): "viewer",
    ("POST", "/api/target-tables"): "viewer",
    ("POST", "/api/target-table-details"): "viewer",
    ("POST", "/api/export/sources"): "viewer",
    # 查询：只读账号只放行 safe 语句，写/危险语句在 handle_query_run 内二次判定
    ("POST", "/api/query/run"): "viewer",
    # ---- 可写 ----
    ("POST", "/api/preview"): "operator",
    ("POST", "/api/import"): "operator",
    ("POST", "/api/connections/test"): "operator",
    ("POST", "/api/export/preview"): "operator",
    ("POST", "/api/export/run"): "operator",
    ("GET", "/api/export/download"): "operator",
    ("POST", "/api/queries"): "operator",
    ("DELETE", "/api/queries"): "operator",
    ("POST", "/api/jobs"): "operator",
    ("POST", "/api/jobs/run"): "operator",
    ("DELETE", "/api/jobs"): "operator",
    ("POST", "/api/schedules"): "operator",
    ("POST", "/api/schedules/start"): "operator",
    ("POST", "/api/schedules/pause"): "operator",
    ("DELETE", "/api/schedules"): "operator",
    # ---- 管理员 ----
    # 连接是生产库入口（含库口令），只有管理员能增删改
    ("POST", "/api/connections"): "admin",
    ("DELETE", "/api/connections"): "admin",
    # 本机原生对话框：只在桌面模式有意义，且等同于读取宿主机文件系统
    ("POST", "/api/task-source"): "admin",
    ("POST", "/api/import/choose-source"): "admin",
    ("POST", "/api/export/choose-target"): "admin",
    # 账号与审计
    ("GET", "/api/users"): "admin",
    ("POST", "/api/users"): "admin",
    ("POST", "/api/users/update"): "admin",
    ("DELETE", "/api/users"): "admin",
    ("GET", "/api/audit-logs"): "admin",
}

CSRF_HEADER_NAME = "X-DC-Request"
CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# 不参与 CSRF 校验的路径，分两类：
#   1) 登录 / 注册 / 登录页探测 —— 发生在拿到会话之前，此时还没有可被利用的 Cookie；
#   2) 退出登录 / 本人改密 —— 前者不改变任何数据（最坏结果是被登出），
#      后者必须同时提供原密码，CSRF 单独无法完成。
# 真正有破坏性的写操作（导入、导出、执行 SQL、连接增删改、账号管理）一律强制校验头。
CSRF_EXEMPT_PATHS = {
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/signup-info",
    "/api/auth/logout",
    "/api/auth/password",
}

LOGIN_RATE_WINDOW_SECONDS = 60
LOGIN_RATE_MAX = 20
_login_rate_buckets: dict[str, list[float]] = {}


def role_at_least(role: str, minimum: str) -> bool:
    return ROLE_ORDER.get(str(role or ""), 0) >= ROLE_ORDER.get(str(minimum), 99)


def readonly_blocked_statements(sql: str) -> list[dict[str, object]]:
    """只读账号想执行、但被危险分级拦下的语句（空列表表示全部是 safe）。"""
    blocked: list[dict[str, object]] = []
    for statement in split_sql_statements(str(sql or "")):
        risk = classify_sql_risk(statement)
        if str(risk.get("level")) != "safe":
            blocked.append({
                "sql": statement[:200],
                "level": risk.get("level"),
                "reasons": risk.get("reasons") or [],
            })
    return blocked


def route_min_role(method: str, path: str) -> str:
    return ROUTE_MIN_ROLE.get((str(method).upper(), path), "admin")


def request_actor(handler) -> dict[str, object] | None:
    """把三种通道归一成一个「操作者」，供鉴权与审计共用。

    local  —— 未启用认证（本机桌面模式），等同 admin
    basic  —— HTTP Basic（脚本 / 健康检查），等同 admin
    session—— 浏览器 Cookie 会话，按 _users.role 受限
    """
    channel = str(getattr(handler, "auth_channel", "") or "")
    if channel == "local":
        return {"id": "", "username": "local", "displayName": "本机用户", "role": "admin", "channel": "local"}
    if channel == "basic":
        name = os.environ.get("ADMIN_USER", "admin").strip() or "admin"
        return {"id": "", "username": name, "displayName": "脚本账号", "role": "admin", "channel": "basic"}
    if channel == "session":
        user = getattr(handler, "auth_user", None) or current_user(handler)
        if user:
            actor = dict(user)
            actor["channel"] = "session"
            return actor
    return None


def same_origin_request(handler) -> bool:
    """Origin / Referer 与 Host 不一致时判为跨站请求。两者都缺省时放行（非浏览器客户端）。"""
    host = str(handler.headers.get("Host", "") or "").strip()
    if not host:
        return True
    for name in ("Origin", "Referer"):
        value = str(handler.headers.get(name, "") or "").strip()
        if not value:
            continue
        netloc = urlparse(value).netloc
        if netloc and netloc != host:
            return False
    return True


def require_csrf(handler, method: str, path: str) -> bool:
    if str(method).upper() in CSRF_SAFE_METHODS:
        return True
    if str(getattr(handler, "auth_channel", "") or "") != "session":
        return True
    if path in CSRF_EXEMPT_PATHS:
        return True
    token = str(handler.headers.get(CSRF_HEADER_NAME, "") or "").strip().lower()
    if token not in {"1", "true"}:
        json_response(
            handler,
            {"ok": False, "error": f"请求缺少安全校验头 {CSRF_HEADER_NAME}，已拒绝执行。"},
            HTTPStatus.FORBIDDEN,
        )
        return False
    if not same_origin_request(handler):
        json_response(handler, {"ok": False, "error": "请求来源与本站不一致，已拒绝执行。"}, HTTPStatus.FORBIDDEN)
        return False
    return True


def guard_request(handler, method: str, path: str) -> bool:
    """require_auth 之后的第二道门：角色鉴权 + CSRF。

    只作用于 /api/* —— 静态页面本身不做角色限制，无权限的入口由前端隐藏、
    后端在对应接口上拒绝，避免把页面路由也做成权限迷宫。
    """
    if not str(path).startswith("/api/"):
        return True
    actor = request_actor(handler)
    if actor:
        minimum = route_min_role(method, path)
        if not role_at_least(str(actor.get("role")), minimum):
            audit_log(
                actor,
                "denied",
                f"{method} {path}",
                f"权限不足，需要 {ROLE_LABELS.get(minimum, minimum)}",
                handler.request_ip(),
                "denied",
            )
            json_response(
                handler,
                {
                    "ok": False,
                    "error": f"当前账号权限不足：该操作需要「{ROLE_LABELS.get(minimum, minimum)}」角色。",
                    "needRole": minimum,
                },
                HTTPStatus.FORBIDDEN,
            )
            return False
    if not require_csrf(handler, method, path):
        if actor:
            audit_log(actor, "csrf", f"{method} {path}", "缺少或跨站的 CSRF 校验头", handler.request_ip(), "denied")
        return False
    return True


def login_rate_retry_after(ip: str) -> int:
    """同 IP 每分钟登录/注册上限；返回 0 表示放行，否则为需要等待的秒数。"""
    now = time.time()
    bucket = [stamp for stamp in _login_rate_buckets.get(str(ip), []) if now - stamp < LOGIN_RATE_WINDOW_SECONDS]
    if len(bucket) >= LOGIN_RATE_MAX:
        _login_rate_buckets[str(ip)] = bucket
        return max(1, int(LOGIN_RATE_WINDOW_SECONDS - (now - bucket[0])))
    bucket.append(now)
    _login_rate_buckets[str(ip)] = bucket
    return 0


def audit_retention_days() -> int:
    try:
        return max(1, int(os.environ.get("DC_AUDIT_RETENTION_DAYS", "90") or 90))
    except Exception:  # noqa: BLE001
        return 90


def prune_audit_logs() -> int:
    """审计只保留 N 天（默认 90，业主 2026-09-22 决策），随服务启动清理一次。"""
    cutoff = (dt.datetime.now() - dt.timedelta(days=audit_retention_days())).strftime("%Y-%m-%d %H:%M:%S")
    with connect_db() as conn:
        cursor = conn.execute("delete from _audit_logs where created_at < ?", (cutoff,))
        return cursor.rowcount or 0


def list_users() -> list[dict[str, object]]:
    with connect_db() as conn:
        rows = conn.execute("select * from _users order by created_at").fetchall()
        counts = {
            str(row[0]): int(row[1])
            for row in conn.execute(
                "select user_id, count(*) from _sessions where expires_at > ? group by user_id", (now_text(),)
            ).fetchall()
        }
    result: list[dict[str, object]] = []
    for row in rows:
        item = user_public(row)
        item["activeSessions"] = counts.get(str(row["id"]), 0)
        item["createdBy"] = row["created_by"] or ""
        result.append(item)
    return result


def validate_new_password(password: str) -> str:
    text = str(password or "")
    if len(text) < 8:
        raise ValueError("密码至少 8 位。")
    if len(text) > 200:
        raise ValueError("密码过长。")
    return text


def find_user_by_id(user_id: str) -> sqlite3.Row | None:
    with connect_db() as conn:
        return conn.execute("select * from _users where id = ?", (str(user_id),)).fetchone()


def enabled_admin_count(exclude_id: str = "") -> int:
    with connect_db() as conn:
        if exclude_id:
            row = conn.execute(
                "select count(*) from _users where role = 'admin' and enabled = 1 and id <> ?", (exclude_id,)
            ).fetchone()
        else:
            row = conn.execute("select count(*) from _users where role = 'admin' and enabled = 1").fetchone()
    return int(row[0])


def create_user(payload: dict[str, object]) -> dict[str, object]:
    username = str(payload.get("username") or "").strip()
    display_name = str(payload.get("displayName") or "").strip()
    role = str(payload.get("role") or "viewer").strip()
    password = validate_new_password(payload.get("password"))
    if len(username) < 3:
        raise ValueError("用户名至少 3 个字符。")
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{3,32}", username):
        raise ValueError("用户名只能包含字母、数字与 _ . @ - ，长度 3-32 位。")
    if role not in ROLE_ORDER:
        raise ValueError("角色不合法。")
    if find_user(username):
        raise ValueError("该用户名已存在。")
    user_id = uuid.uuid4().hex
    with connect_db() as conn:
        conn.execute(
            "insert into _users (id, username, display_name, password_hash, role, enabled, created_at, created_by, failed_count)"
            " values (?, ?, ?, ?, ?, 1, ?, ?, 0)",
            (user_id, username, display_name or username, hash_password(password), role, now_text(), "admin"),
        )
        row = conn.execute("select * from _users where id = ?", (user_id,)).fetchone()
    return user_public(row)


def update_user(payload: dict[str, object], actor: dict[str, object] | None) -> tuple[dict[str, object], list[str]]:
    """改角色 / 启停 / 重置密码 / 改显示名；返回（用户, 变更说明）。"""
    user_id = str(payload.get("id") or "").strip()
    if not user_id:
        raise ValueError("缺少用户 ID。")
    row = find_user_by_id(user_id)
    if not row:
        raise ValueError("用户不存在。")
    changes: list[str] = []
    role = str(payload.get("role") or "").strip()
    enabled_value = payload.get("enabled")
    display_name = payload.get("displayName")
    password = payload.get("password")

    if role and role != str(row["role"]):
        if role not in ROLE_ORDER:
            raise ValueError("角色不合法。")
        if str(row["role"]) == "admin" and role != "admin" and enabled_admin_count(str(row["id"])) == 0:
            raise ValueError("这是最后一名启用中的管理员，不能降级。请先指定另一名管理员。")
        changes.append(f"角色 {row['role']} → {role}")

    if enabled_value is not None:
        enabled = 1 if str(enabled_value).strip().lower() in {"1", "true", "yes", "on"} else 0
        if enabled != int(row["enabled"] or 0):
            if not enabled and str(row["role"]) == "admin" and enabled_admin_count(str(row["id"])) == 0:
                raise ValueError("这是最后一名启用中的管理员，不能停用。")
            if not enabled and actor and str(actor.get("id")) == str(row["id"]):
                raise ValueError("不能停用当前登录的账号。")
            changes.append("启用" if enabled else "停用")
    else:
        enabled = int(row["enabled"] or 0)

    new_display = str(display_name).strip() if display_name is not None else str(row["display_name"] or "")
    if display_name is not None and new_display != str(row["display_name"] or ""):
        changes.append("显示名变更")
    new_display = new_display or str(row["username"])

    with connect_db() as conn:
        if password not in (None, ""):
            conn.execute(
                "update _users set password_hash = ?, failed_count = 0, locked_until = NULL where id = ?",
                (hash_password(validate_new_password(password)), user_id),
            )
            changes.append("重置密码")
        conn.execute(
            "update _users set role = ?, enabled = ?, display_name = ? where id = ?",
            (role or row["role"], enabled, new_display, user_id),
        )
        updated = conn.execute("select * from _users where id = ?", (user_id,)).fetchone()
    # 改密 / 停用后立刻作废其全部会话，避免旧 Cookie 继续可用
    if password not in (None, "") or (enabled_value is not None and not int(updated["enabled"] or 0)):
        destroy_user_sessions(user_id)
    return user_public(updated), changes


def delete_user(user_id: str, actor: dict[str, object] | None) -> dict[str, object]:
    row = find_user_by_id(user_id)
    if not row:
        raise ValueError("用户不存在。")
    if actor and str(actor.get("id")) and str(actor.get("id")) == str(row["id"]):
        raise ValueError("不能删除当前登录的账号。")
    if str(row["role"]) == "admin" and int(row["enabled"] or 0) and enabled_admin_count(str(row["id"])) == 0:
        raise ValueError("这是最后一名启用中的管理员，不能删除。")
    removed = user_public(row)
    with connect_db() as conn:
        conn.execute("delete from _users where id = ?", (str(row["id"]),))
    destroy_user_sessions(str(row["id"]))
    return removed


def query_audit_logs(
    username: str = "",
    action: str = "",
    status: str = "",
    keyword: str = "",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, object]:
    clauses: list[str] = []
    args: list[object] = []
    if username:
        clauses.append("username = ?")
        args.append(username)
    if action:
        clauses.append("action = ?")
        args.append(action)
    if status:
        clauses.append("status = ?")
        args.append(status)
    if keyword:
        clauses.append("(target like ? or detail like ?)")
        args.extend([f"%{keyword}%", f"%{keyword}%"])
    where = (" where " + " and ".join(clauses)) if clauses else ""
    with connect_db() as conn:
        total = int(conn.execute(f"select count(*) from _audit_logs{where}", args).fetchone()[0])
        rows = conn.execute(
            f"select * from _audit_logs{where} order by created_at desc, rowid desc limit ? offset ?",
            args + [max(1, min(500, int(limit))), max(0, int(offset))],
        ).fetchall()
        actions = [str(item[0]) for item in conn.execute("select distinct action from _audit_logs order by action").fetchall()]
    return {"total": total, "logs": [dict(row) for row in rows], "actions": actions}


def bind_host() -> str:
    if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_ENVIRONMENT_NAME"):
        return "0.0.0.0"
    return os.environ.get("HOST", "127.0.0.1")


# Connection credential query parameters that must never reach the logs (P1-3).
SENSITIVE_QUERY_PARAMS = ("dbPassword", "dbPasswordSecret", "password")

# 脱敏必须把「整个敏感值」吃掉，同时又绝不能吞掉后面的合法参数（例如 &z=1）。
# 取值优先级（三选一，靠前的优先）：
#   1) 成对引号包裹（单/双）—— 引号内部允许空格、& 等任意字符，因此
#      ?dbPassword='a b'&z=1 会被整体抹成 ***，且 &z=1 原样保留。
#      （旧写法用 [^&#\s]* 取值，遇到引号内的空格就提前截断，把 " b'" 漏了出去）
#   2) 无引号值 —— 退化为「取到 & / # / 空白为止」，与既有行为完全一致；
#   3) 引号未闭合 —— 成对分支匹配失败后自动落回无引号分支，后续参数仍然保留。
_SENSITIVE_QUERY_VALUE_RE = re.compile(
    r"([?&](?:" + "|".join(re.escape(p) for p in SENSITIVE_QUERY_PARAMS) + r")=)"
    r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^&#\s]*)",
    re.IGNORECASE,
)


def redact_log_text(message: str) -> str:
    """Replace sensitive query parameter values (dbPassword etc.) with '***'."""
    return _SENSITIVE_QUERY_VALUE_RE.sub(r"\1***", message)


def is_allowed_download_path(path: Path) -> bool:
    """True only for regular files inside a download-whitelisted directory (P2-14)."""
    try:
        resolved = path.resolve()
    except OSError:
        return False
    if not resolved.is_file():
        return False
    for root in DOWNLOAD_ALLOWED_ROOTS:
        try:
            resolved.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


class ImportPrototypeHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        try:
            message = format % args
        except Exception:
            message = " ".join(str(arg) for arg in args)
        print(f"{self.address_string()} - {redact_log_text(message)}", flush=True)

    def request_ip(self) -> str:
        forwarded = str(self.headers.get("X-Forwarded-For", "")).split(",")[0].strip()
        return forwarded or self.address_string()

    def require_auth(self) -> bool:
        """双通道认证：HTTP Basic（脚本 / 健康检查）与 Cookie 会话（浏览器）。

        · 未启用认证（本机桌面模式）→ 直接放行，保持原有使用习惯
        · 页面请求且未登录 → 302 跳登录页并携带 next，登录后跳回原页面
        · 接口请求且未登录 → 401 JSON（带 needLogin 标记，前端统一处理）
        · 静态资源（css/js/图片）放行，否则登录页自身的样式脚本会被拦
        """
        if not public_auth_enabled():
            self.auth_channel = "local"  # type: ignore[attr-defined]
            return True
        parsed = urlparse(self.path)
        path = parsed.path
        if path in AUTH_EXEMPT_PATHS or path in AUTH_PUBLIC_PAGES:
            self.auth_channel = "anonymous"  # type: ignore[attr-defined]
            return True
        if check_basic_auth(self.headers.get("Authorization", "")):
            self.auth_channel = "basic"  # type: ignore[attr-defined]
            return True
        user = current_user(self)
        if user:
            self.auth_user = user  # type: ignore[attr-defined]
            self.auth_channel = "session"  # type: ignore[attr-defined]
            return True
        if path.startswith("/api/"):
            json_response(
                self,
                {"ok": False, "needLogin": True, "error": "未登录或会话已过期，请重新登录。"},
                HTTPStatus.UNAUTHORIZED,
            )
            return False
        if path == "/" or path.endswith(".html"):
            target = path + (f"?{parsed.query}" if parsed.query else "")
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/login.html?next=" + quote(target, safe=""))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False
        # 静态资源与其它路径交给静态文件处理器
        return True

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def translate_path(self, path: str) -> str:
        parsed = urlparse(path)
        request_path = parsed.path
        if request_path == "/":
            request_path = "/index.html"
        base = PUBLIC.resolve()
        target = (base / request_path.lstrip("/")).resolve()
        try:
            target.relative_to(base)
        except ValueError:
            return str(base / "__missing__")
        return str(target)

    def do_GET(self) -> None:
        if not self.require_auth():
            return
        parsed = urlparse(self.path)
        try:
            if not guard_request(self, "GET", parsed.path):
                return
            if parsed.path == "/api/auth/me":
                self.handle_auth_me()
                return
            if parsed.path == "/api/auth/signup-info":
                self.handle_auth_signup_info()
                return
            if parsed.path == "/api/tables":
                self.handle_tables(parsed.query)
                return
            if parsed.path == "/api/target-tables":
                self.handle_target_tables(parsed.query)
                return
            if parsed.path == "/api/target-table-details":
                self.handle_target_table_details(parsed.query)
                return
            if parsed.path == "/api/table":
                self.handle_table_preview(parsed.query)
                return
            if parsed.path == "/api/logs":
                self.handle_logs()
                return
            # 版本标识统一到唯一真值来源 APP_VERSION：两个探活/元信息接口都同时返回
            # appVersion（前端侧边栏读它）与 version（打包冒烟脚本读它），避免口径漂移。
            if parsed.path == "/api/ping":
                json_response(self, {"ok": True, "appVersion": APP_VERSION, "version": APP_VERSION})
                return
            if parsed.path == "/api/meta":
                json_response(self, {"ok": True, "appVersion": APP_VERSION, "version": APP_VERSION})
                return
            if parsed.path == "/api/docs":
                self.handle_docs(parsed.query)
                return
            if parsed.path == "/api/storage/status":
                self.handle_storage_status()
                return
            if parsed.path == "/api/connections":
                self.handle_connections()
                return
            if parsed.path == "/api/jobs":
                self.handle_jobs()
                return
            if parsed.path == "/api/schedules":
                self.handle_schedules()
                return
            if parsed.path == "/api/job-runs":
                self.handle_job_runs(parsed.query)
                return
            if parsed.path == "/api/queries":
                self.handle_queries()
                return
            if parsed.path == "/api/export/sources":
                self.handle_export_sources(parsed.query)
                return
            if parsed.path == "/api/export/download":
                self.handle_export_download(parsed.query)
                return
            if parsed.path == "/api/users":
                self.handle_users()
                return
            if parsed.path == "/api/audit-logs":
                self.handle_audit_logs(parsed.query)
                return
        except Exception as exc:
            error_response(self, str(exc), HTTPStatus.BAD_REQUEST)
            return
        return super().do_GET()

    def do_POST(self) -> None:
        if not self.require_auth():
            return
        post_path = urlparse(self.path).path
        try:
            if not guard_request(self, "POST", post_path):
                return
            if post_path == "/api/auth/login":
                self.handle_auth_login()
                return
            if post_path == "/api/auth/logout":
                self.handle_auth_logout()
                return
            if post_path == "/api/auth/register":
                self.handle_auth_register()
                return
            if post_path == "/api/auth/password":
                self.handle_auth_password()
                return
            if post_path == "/api/users":
                self.handle_user_create()
                return
            if post_path == "/api/users/update":
                self.handle_user_update()
                return
            # Read-only connection lookups accept POST + JSON body so connection
            # credentials are no longer required to travel in a GET query string.
            if post_path == "/api/tables":
                self.handle_tables()
                return
            if post_path == "/api/target-tables":
                self.handle_target_tables()
                return
            if post_path == "/api/target-table-details":
                self.handle_target_table_details()
                return
            if post_path == "/api/table":
                self.handle_table_preview()
                return
            if post_path == "/api/export/sources":
                self.handle_export_sources()
                return
            if self.path == "/api/preview":
                self.handle_preview()
                return
            if self.path == "/api/import":
                self.handle_import()
                return
            if self.path == "/api/task-source":
                self.handle_task_source()
                return
            if self.path == "/api/import/choose-source":
                self.handle_import_choose_source()
                return
            if self.path == "/api/connections/test":
                self.handle_connection_test()
                return
            if self.path == "/api/connections":
                self.handle_connection_save()
                return
            if self.path == "/api/export/preview":
                self.handle_export_preview()
                return
            if self.path == "/api/export/run":
                self.handle_export_run()
                return
            if self.path == "/api/export/choose-target":
                self.handle_export_choose_target()
                return
            if self.path == "/api/query/run":
                self.handle_query_run()
                return
            if self.path == "/api/queries":
                self.handle_query_save()
                return
            if self.path == "/api/jobs":
                self.handle_job_save()
                return
            if self.path == "/api/jobs/run":
                self.handle_job_run()
                return
            if self.path == "/api/schedules":
                self.handle_schedule_save()
                return
            if self.path == "/api/schedules/start":
                self.handle_schedule_state(True)
                return
            if self.path == "/api/schedules/pause":
                self.handle_schedule_state(False)
                return
            error_response(self, "未知接口。", HTTPStatus.NOT_FOUND)
        except Exception as exc:
            error_response(self, str(exc), HTTPStatus.BAD_REQUEST)

    def do_DELETE(self) -> None:
        if not self.require_auth():
            return
        parsed = urlparse(self.path)
        try:
            if not guard_request(self, "DELETE", parsed.path):
                return
            if parsed.path == "/api/connections":
                self.handle_connection_delete(parsed.query)
                return
            if parsed.path == "/api/jobs":
                self.handle_job_delete(parsed.query)
                return
            if parsed.path == "/api/schedules":
                self.handle_schedule_delete(parsed.query)
                return
            if parsed.path == "/api/queries":
                self.handle_query_delete(parsed.query)
                return
            error_response(self, "未知接口。", HTTPStatus.NOT_FOUND)
        except Exception as exc:
            error_response(self, str(exc), HTTPStatus.BAD_REQUEST)

    def guess_type(self, path: str) -> str:
        if path.endswith(".js"):
            return "text/javascript; charset=utf-8"
        if path.endswith(".css"):
            return "text/css; charset=utf-8"
        if path.endswith(".html"):
            return "text/html; charset=utf-8"
        return mimetypes.guess_type(path)[0] or "application/octet-stream"

    def handle_preview(self) -> None:
        fields, uploaded_files = parse_multipart(self)
        if not uploaded_files and fields.get("sourcePath", "").strip():
            uploaded_files = collect_local_files(fields["sourcePath"])
        if not uploaded_files:
            raise ValueError("请选择要预览的文件。")
        uploaded = uploaded_files[0]
        tabular = read_tabular_file(uploaded.path, fields)
        columns = tabular.columns
        rows = tabular.rows
        # 预览阶段的类型推断只依赖 targetDbType，避免解析/加载数据库连接（连接失效不应阻断预览）。
        type_fields = dict(fields)
        type_fields["connectionId"] = ""
        type_rows = convert_date_columns(columns, rows, type_fields) if target_db_type(type_fields) == "mysql" else rows
        column_types = infer_column_types(columns, type_rows, type_fields)
        type_warnings = detect_type_warnings(columns, rows, column_types)
        json_response(
            self,
            {
                "ok": True,
                "fileName": uploaded.filename,
                "suggestedTable": sanitize_identifier(Path(uploaded.filename).stem, "import_table"),
                "columns": columns,
                "preview": rows[:MAX_PREVIEW_ROWS],
                "totalRows": len(rows),
                "sheets": tabular.sheets,
                "selectedSheet": tabular.selected_sheet,
                "columnTypes": column_types,
                "typeWarnings": type_warnings,
                # P3-20：预览必须告知「本次会实际应用哪些清洗/重命名规则」，含默认开启项。
                "cleaningRules": describe_cleaning_rules(fields),
            },
        )

    def handle_import(self) -> None:
        fields, uploaded_files = parse_multipart(self)
        if not uploaded_files and fields.get("sourcePath", "").strip():
            uploaded_files = collect_local_files(fields["sourcePath"])
        if not uploaded_files:
            raise ValueError("请选择要导入的文件。")

        with connect_db() as log_conn:
            if parse_bool(fields, "clearLogBeforeImport", False):
                log_conn.execute("delete from _import_logs")

        target_conn = connect_target_db(fields)
        try:
            target_execute_sql_batch(target_conn, fields.get("beforeAllSql", ""), "全部导入开始前 SQL", fields)
            if target_db_type(fields) != "mysql" or fields.get("commitMode") != "auto":
                target_conn.commit()
        finally:
            target_conn.close()

        results, failures, skipped_files = run_import_batch(uploaded_files, fields)

        target_conn = connect_target_db(fields)
        try:
            target_execute_sql_batch(target_conn, fields.get("afterAllSql", ""), "全部导入结束后 SQL", fields)
            export_path = target_export_query_to_excel(target_conn, fields.get("afterQuerySql", ""), fields.get("afterQueryExport", ""), fields)
            if target_db_type(fields) != "mysql" or fields.get("commitMode") != "auto":
                target_conn.commit()
        finally:
            target_conn.close()

        if failures and not results:
            raise ValueError(failures[0]["error"])

        audit_log(
            request_actor(self),
            "import.run",
            str(results[0]["tableName"] if results else ""),
            f"{len(uploaded_files)} 个文件，成功 {len(results)} / 失败 {len(failures)}，"
            f"写入 {sum(int(item['rowsWritten']) for item in results)} 行",
            self.request_ip(),
            "ok",
        )
        json_response(
            self,
            {
                "ok": True,
                "tableName": results[0]["tableName"] if results else "",
                "columns": results[0]["columns"] if results else [],
                "summary": {
                    "totalFiles": len(uploaded_files),
                    "successFiles": len(results),
                    "failedFiles": len(failures),
                    "skippedFiles": skipped_files,
                    "rowsRead": sum(int(item["rowsRead"]) for item in results),
                    "rowsWritten": sum(int(item["rowsWritten"]) for item in results),
                    "rowsUpdated": sum(int(item["rowsUpdated"]) for item in results),
                    "rowsSkipped": sum(int(item["rowsSkipped"]) for item in results),
                },
                # 改进C：把行级跳过明细汇总到顶层，用户能看到「哪一行、为什么没进库」。
                "skipDetails": [
                    dict(detail, fileName=item.get("fileName", ""))
                    for item in results
                    for detail in (item.get("skipDetails") or [])
                ][:MAX_SKIP_DETAILS],
                "cleaningRules": describe_cleaning_rules(fields),
                "results": results,
                "failures": failures,
                "exportPath": export_path,
                "message": (
                    f"成功导入 {len(results)} 个文件，失败 {len(failures)} 个文件"
                    + (f"，跳过未变更文件 {skipped_files} 个。" if skipped_files else "。")
                ),
            },
        )

    def handle_task_source(self) -> None:
        _, uploaded_files = parse_multipart(self)
        if not uploaded_files:
            raise ValueError("请选择要关联到任务的源文件。")
        source_dir = TASK_SOURCES / uuid.uuid4().hex
        source_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for uploaded in uploaded_files:
            target = source_dir / Path(uploaded.filename).name
            uploaded.path.replace(target)
            paths.append(str(target.resolve()))
        source_path = paths[0] if len(paths) == 1 else str(source_dir.resolve())
        json_response(
            self,
            {
                "ok": True,
                "sourcePath": source_path,
                "files": paths,
                "message": f"已关联 {len(paths)} 个任务源文件。",
            },
        )

    def handle_import_choose_source(self) -> None:
        # 方案 A：浏览器选中的本机源文件上传后，落到服务器固定输入目录 linked_sources
        # （原名覆盖，非 task_sources 一次性副本）。该目录不在 execute_import_step 的
        # 拒绝名单内，定时/作业任务可稳定读取，保证"关联本机原文件"后的任务真正能定时跑。
        _, uploaded_files = parse_multipart(self)
        if not uploaded_files:
            raise ValueError("请选择要关联的源文件。")
        LINKED_SOURCES.mkdir(parents=True, exist_ok=True)
        paths = []
        for uploaded in uploaded_files:
            target = LINKED_SOURCES / Path(uploaded.filename).name
            uploaded.path.replace(target)
            paths.append(str(target.resolve()))
        source_path = paths[0] if len(paths) == 1 else str(LINKED_SOURCES.resolve())
        json_response(
            self,
            {
                "ok": True,
                "sourcePath": source_path,
                "files": paths,
                "message": f"已把源文件复制到服务器固定目录，定时任务可稳定读取；本机文件更新后请重新关联（会覆盖同名旧文件）。",
            },
        )

    def _request_fields(self, query: str = "") -> dict[str, str]:
        """Build a flat connection-field dict for the current request.

        - POST + JSON body is the modern, credential-safe transport.
        - Legacy GET query strings stay supported for backwards compatibility,
          but dbPassword / dbPasswordSecret are dropped so secrets never travel
          in a URL (P1-3). Saved connections still work over GET because
          resolve_connection_fields() loads the password from storage when a
          connectionId is present; direct-credential MySQL calls must use POST.
        """
        if self.command == "POST":
            payload = read_json_body(self)
            if not isinstance(payload, dict):
                raise ValueError("请求内容格式不正确。")
            return {
                str(key): ("" if value is None else str(value))
                for key, value in payload.items()
                if not isinstance(value, (list, dict))
            }
        raw = {key: values[-1] for key, values in parse_qs(query).items()}
        fields = {key: str(value) for key, value in raw.items()}
        for sensitive_key in SENSITIVE_QUERY_PARAMS:
            fields.pop(sensitive_key, None)
        return fields

    def handle_tables(self, query: str = "") -> None:
        fields = self._request_fields(query)
        if target_db_type(fields) == "mysql":
            try:
                tables = target_table_names(fields)
            except Exception as exc:
                raise friendly_mysql_error(exc, "读取目标数据库的表列表") from exc
            json_response(self, {"ok": True, "tables": tables, "targetDbType": "mysql"})
            return
        with connect_db() as conn:
            rows = conn.execute(
                """
                select name from sqlite_master
                where type = 'table' and name not like 'sqlite_%' and name not like '\\_%' escape '\\'
                order by name
                """
            ).fetchall()
        json_response(self, {"ok": True, "tables": [row["name"] for row in rows], "targetDbType": "sqlite"})

    def handle_target_tables(self, query: str = "") -> None:
        fields = self._request_fields(query)
        json_response(self, {"ok": True, "tables": target_table_names(fields)})

    def handle_target_table_details(self, query: str = "") -> None:
        fields = self._request_fields(query)
        table_name = fields.pop("name", "")
        json_response(self, {"ok": True, "table": target_table_details(fields, table_name)})

    def handle_table_preview(self, query: str = "") -> None:
        fields = self._request_fields(query)
        table_name = fields.pop("name", "").strip()
        if target_db_type(fields) == "mysql":
            if not table_name:
                raise ValueError("缺少表名。")
            self._preview_mysql_table(fields, table_name)
            return
        table_name = sanitize_identifier(table_name, "")
        if not table_name:
            raise ValueError("缺少表名。")
        with connect_db() as conn:
            columns = existing_columns(conn, table_name)
            if not columns:
                raise ValueError("表不存在或没有字段。")
            rows = conn.execute(f"select * from {quote_identifier(table_name)} limit 50").fetchall()
            count = conn.execute(f"select count(*) as total from {quote_identifier(table_name)}").fetchone()["total"]
        json_response(
            self,
            {
                "ok": True,
                "tableName": table_name,
                "columns": columns,
                "rows": [[cell_to_text(row[column]) for column in columns] for row in rows],
                "totalRows": count,
            },
        )

    def _preview_mysql_table(self, fields: dict[str, str], table_name: str) -> None:
        try:
            conn = connect_target_db(fields)
        except Exception as exc:
            raise friendly_mysql_error(exc) from exc
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"select count(*) as total from {db_quote(table_name, fields)}")
                count = int(list(cursor.fetchone())[0])
                cursor.execute(f"select * from {db_quote(table_name, fields)} limit 50")
                columns = [str(item[0]) for item in cursor.description or []]
                rows = cursor.fetchall()
        except Exception as exc:
            raise friendly_mysql_error(exc, "读取表数据")
        finally:
            conn.close()
        json_response(
            self,
            {
                "ok": True,
                "tableName": table_name,
                "columns": columns,
                "rows": [[cell_to_text(cell) for cell in row] for row in rows],
                "totalRows": count,
            },
        )

    def handle_logs(self) -> None:
        with connect_db() as conn:
            rows = conn.execute(
                """
                select created_at, file_name, table_name, mode, rows_read, rows_written, rows_updated, rows_skipped, status, message
                from _import_logs
                order by created_at desc
                limit 50
                """
            ).fetchall()
        json_response(self, {"ok": True, "logs": [dict(row) for row in rows]})

    def handle_storage_status(self) -> None:
        ensure_dirs()
        volume_mount = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
        write_test_path = DATA / ".storage-write-test"
        write_ok = False
        write_error = ""
        try:
            write_test_path.write_text(now_text(), encoding="utf-8")
            write_ok = write_test_path.exists()
        except Exception as exc:
            write_error = str(exc)
        def inside_volume(path: Path) -> bool:
            if not volume_mount:
                return False
            try:
                path.resolve().relative_to(Path(volume_mount).resolve())
                return True
            except ValueError:
                return False
        json_response(
            self,
            {
                "ok": True,
                "railwayVolumeMountPath": volume_mount,
                "persistentReady": bool(volume_mount) and inside_volume(DB_PATH) and write_ok,
                "dataDir": str(DATA),
                "uploadsDir": str(UPLOADS),
                "exportsDir": str(EXPORTS),
                "databasePath": str(DB_PATH),
                "databaseExists": DB_PATH.exists(),
                "writeTestOk": write_ok,
                "writeTestError": write_error,
            },
        )

    def handle_docs(self, query: str) -> None:
        """返回帮助中心目录索引或一篇文档渲染后的 HTML。

        /api/docs                   -> 目录索引（首页任务卡片 + 侧栏树）
        /api/docs?id=user/import    -> 该文档的渲染 HTML
        """
        params = parse_qs(query)
        doc_id = (params.get("id") or [""])[0].strip()
        if not doc_id:
            json_response(self, doc_index_payload())
            return
        title, html = load_doc_content(doc_id)
        if not html:
            error_response(self, "文档不存在。", HTTPStatus.NOT_FOUND)
            return
        json_response(self, {"ok": True, "id": doc_id, "title": title, "html": html})

    def handle_connections(self) -> None:
        with connect_db() as conn:
            rows = conn.execute("select * from _db_connections order by updated_at desc, name").fetchall()
        json_response(self, {"ok": True, "connections": [connection_public(row) for row in rows]})

    def handle_connection_test(self) -> None:
        payload = read_json_body(self)
        connection_id = str(payload.get("id") or "").strip()
        if connection_id and not str(payload.get("password") or ""):
            with connect_db() as conn:
                saved = conn.execute("select password from _db_connections where id = ?", (connection_id,)).fetchone()
            if saved:
                payload["password"] = decode_secret(saved["password"])
        result = test_mysql_connection(payload)
        json_response(
            self,
            {
                "ok": True,
                "message": "连接成功。",
                "version": result["version"],
                "databases": result["databases"],
            },
        )

    def handle_connection_save(self) -> None:
        payload = read_json_body(self)
        record = normalize_connection_payload(payload)
        now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with connect_db() as conn:
            old = conn.execute("select created_at, password from _db_connections where id = ?", (record["id"],)).fetchone()
            raw_password = str(payload.get("password") or "")
            stored_password = old["password"] if old and not raw_password else encode_secret(str(record["password"]))
            conn.execute(
                """
                insert into _db_connections (
                    id, name, db_type, host, port, user_name, password, db_name, charset,
                    ssl_enabled, ssl_ca, ssl_cert, ssl_key, created_at, updated_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(id) do update set
                    name = excluded.name,
                    db_type = excluded.db_type,
                    host = excluded.host,
                    port = excluded.port,
                    user_name = excluded.user_name,
                    password = excluded.password,
                    db_name = excluded.db_name,
                    charset = excluded.charset,
                    ssl_enabled = excluded.ssl_enabled,
                    ssl_ca = excluded.ssl_ca,
                    ssl_cert = excluded.ssl_cert,
                    ssl_key = excluded.ssl_key,
                    updated_at = excluded.updated_at
                """,
                (
                    record["id"],
                    record["name"],
                    record["db_type"],
                    record["host"],
                    record["port"],
                    record["user_name"],
                    stored_password,
                    record["db_name"],
                    record["charset"],
                    record["ssl_enabled"],
                    record["ssl_ca"],
                    record["ssl_cert"],
                    record["ssl_key"],
                    old["created_at"] if old else now,
                    now,
                ),
            )
            row = conn.execute("select * from _db_connections where id = ?", (record["id"],)).fetchone()
        # 连接变更是高价值审计点：谁在什么时候把工具指向了哪个库（不含任何口令）
        audit_log(
            request_actor(self),
            "connection.save",
            str(record["name"]),
            f"{record['db_type']} {record['host']}:{record['port']}/{record['db_name']} 用户 {record['user_name']}",
            self.request_ip(),
            "ok",
        )
        json_response(self, {"ok": True, "connection": connection_public(row)})

    def handle_connection_delete(self, query: str) -> None:
        params = parse_qs(query)
        connection_id = params.get("id", [""])[0]
        if not connection_id:
            raise ValueError("缺少连接编号。")
        with connect_db() as conn:
            existing = conn.execute("select name from _db_connections where id = ?", (connection_id,)).fetchone()
            conn.execute("delete from _db_connections where id = ?", (connection_id,))
        audit_log(
            request_actor(self),
            "connection.delete",
            str(existing["name"]) if existing else connection_id,
            "",
            self.request_ip(),
            "ok",
        )
        json_response(self, {"ok": True})

    def handle_jobs(self) -> None:
        with connect_db() as conn:
            rows = conn.execute("select * from _jobs order by updated_at desc, name").fetchall()
        json_response(self, {"ok": True, "jobs": [row_to_job(row) for row in rows]})

    def handle_job_save(self) -> None:
        job = save_job(read_json_body(self))
        json_response(self, {"ok": True, "job": job})

    def handle_job_run(self) -> None:
        payload = read_json_body(self)
        job_id = str(payload.get("id") or payload.get("jobId") or "").strip()
        schedule_id = str(payload.get("scheduleId") or "").strip()
        if not job_id:
            raise ValueError("缺少作业编号。")
        result = run_saved_job(job_id, schedule_id)
        if schedule_id:
            # 立即执行也要把结果同步回定时任务状态，否则列表 last_status 一直停在
            # 调度器上一次触发的结果，用户会看到"状态与最新日志不一致"。
            # 只回写 last_run_at/last_status；next_run_at/enabled 仍由调度器管理。
            status = str(result.get("status") or "失败")
            try:
                with connect_db() as conn:
                    conn.execute(
                        "update _schedules set last_run_at = ?, last_status = ?, updated_at = ? where id = ?",
                        (now_text(), status, now_text(), schedule_id),
                    )
            except Exception:
                pass  # 状态回写失败不影响本次执行结果的返回
        json_response(self, {"ok": True, "run": result})

    def handle_job_delete(self, query: str) -> None:
        params = parse_qs(query)
        job_id = params.get("id", [""])[0]
        if not job_id:
            raise ValueError("缺少作业编号。")
        with connect_db() as conn:
            conn.execute("delete from _jobs where id = ?", (job_id,))
            conn.execute("delete from _schedules where job_id = ?", (job_id,))
        json_response(self, {"ok": True})

    def handle_schedules(self) -> None:
        with connect_db() as conn:
            rows = conn.execute("select * from _schedules order by updated_at desc, name").fetchall()
        json_response(self, {"ok": True, "schedules": [row_to_schedule(row) for row in rows]})

    def handle_schedule_save(self) -> None:
        schedule = save_schedule(read_json_body(self))
        json_response(self, {"ok": True, "schedule": schedule})

    def handle_schedule_state(self, enabled: bool) -> None:
        payload = read_json_body(self)
        schedule_id = str(payload.get("id") or "").strip()
        if not schedule_id:
            raise ValueError("缺少定时任务编号。")
        with connect_db() as conn:
            row = conn.execute("select * from _schedules where id = ?", (schedule_id,)).fetchone()
            if not row:
                raise ValueError("定时任务不存在。")
            schedule = row_to_schedule(row)
            next_run = compute_next_run(schedule["rule"], str(schedule["startAt"]), str(schedule["endAt"]), None) if enabled else ""
            conn.execute(
                "update _schedules set enabled = ?, running = 0, next_run_at = ?, updated_at = ? where id = ?",
                (1 if enabled else 0, next_run, now_text(), schedule_id),
            )
            row = conn.execute("select * from _schedules where id = ?", (schedule_id,)).fetchone()
        json_response(self, {"ok": True, "schedule": row_to_schedule(row)})

    def handle_schedule_delete(self, query: str) -> None:
        params = parse_qs(query)
        schedule_id = params.get("id", [""])[0]
        if not schedule_id:
            raise ValueError("缺少定时任务编号。")
        with connect_db() as conn:
            row = conn.execute("select job_id from _schedules where id = ?", (schedule_id,)).fetchone()
            conn.execute("delete from _schedules where id = ?", (schedule_id,))
            # 若该定时任务指向的是"自动作业"载体，且不再被任何调度引用，则一并清理，避免孤儿残留
            if row:
                job_id = row["job_id"]
                job = conn.execute("select name from _jobs where id = ?", (job_id,)).fetchone()
                if job and str(job["name"] or "").endswith(" - 自动作业"):
                    still_used = conn.execute(
                        "select 1 from _schedules where job_id = ? limit 1", (job_id,)
                    ).fetchone()
                    if not still_used:
                        conn.execute("delete from _jobs where id = ?", (job_id,))
        json_response(self, {"ok": True})

    def handle_job_runs(self, query: str) -> None:
        params = parse_qs(query)
        job_id = params.get("jobId", [""])[0]
        schedule_id = params.get("scheduleId", [""])[0]
        where = []
        args: list[object] = []
        if job_id:
            where.append("job_id = ?")
            args.append(job_id)
        if schedule_id:
            where.append("schedule_id = ?")
            args.append(schedule_id)
        clause = (" where " + " and ".join(where)) if where else ""
        with connect_db() as conn:
            runs = conn.execute(f"select * from _job_runs{clause} order by started_at desc limit 80", args).fetchall()
            run_ids = [row["id"] for row in runs]
            steps_by_run: dict[str, list[dict[str, object]]] = {run_id: [] for run_id in run_ids}
            if run_ids:
                placeholders = ",".join("?" for _ in run_ids)
                steps = conn.execute(f"select * from _job_run_steps where run_id in ({placeholders}) order by step_index", run_ids).fetchall()
                for step in steps:
                    steps_by_run[step["run_id"]].append(dict(step))
        runs_payload: list[dict[str, object]] = []
        for row in runs:
            item = dict(row)
            # P3-B：把结构化产物清单直接交给前端，前端不再用正则从 message 文本里猜路径。
            # outputs_json 是内部存储列，替换成解析好的 outputs 列表后不外泄（历史记录为空 → []）。
            item["outputs"] = decode_run_outputs(item.pop("outputs_json", ""))
            item["steps"] = steps_by_run.get(row["id"], [])
            runs_payload.append(item)
        json_response(self, {"ok": True, "runs": runs_payload})

    # ------------------------------------------------------------ 认证接口（P1）

    def handle_auth_me(self) -> None:
        user = getattr(self, "auth_user", None) or current_user(self)
        if not user:
            json_response(self, {"ok": False, "needLogin": True, "error": "未登录。"}, HTTPStatus.UNAUTHORIZED)
            return
        json_response(self, {
            "ok": True,
            "user": user,
            "authEnabled": public_auth_enabled(),
            "signupCodeRequired": bool(signup_code()),
        })

    def handle_auth_signup_info(self) -> None:
        """登录页用它决定是否显示「注册」入口与邀请码输入框。"""
        default_role = signup_default_role()
        json_response(self, {
            "ok": True,
            "authEnabled": public_auth_enabled(),
            "signupCodeRequired": bool(signup_code()),
            "userCount": count_users(),
            "appVersion": APP_VERSION,
            # 注册前就把「新账号能做什么」告诉用户，避免注册完才发现看不到导入导出
            "defaultRole": default_role,
            "defaultRoleLabel": ROLE_LABELS.get(default_role, default_role),
            "defaultRoleHint": (
                "注册后即可导入 / 导出 / 执行写语句"
                if default_role != "viewer"
                else "注册后为只读账号，可查看与只读查询；需要导入导出请让管理员提升为「可写」"
            ),
        })

    def handle_auth_login(self) -> None:
        payload = read_json_body(self)
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        remember = bool(payload.get("remember"))
        ip = self.request_ip()
        if not username or not password:
            raise ValueError("请填写用户名与密码。")
        # 同 IP 每分钟登录次数上限（公网必然被扫，先挡在这里）
        retry_after = login_rate_retry_after(ip)
        if retry_after:
            audit_log({"username": username}, "login", "", "触发登录频率限制", ip, "denied")
            json_response(
                self,
                {"ok": False, "error": f"登录请求过于频繁，请 {retry_after} 秒后再试。"},
                HTTPStatus.TOO_MANY_REQUESTS,
            )
            return
        row = find_user(username)
        # 用户不存在与密码错误使用同一文案，避免暴露账号是否存在
        if not row:
            audit_log({"username": username}, "login", "", "用户不存在", ip, "denied")
            json_response(self, {"ok": False, "error": "用户名或密码不正确。"}, HTTPStatus.UNAUTHORIZED)
            return
        if row["locked_until"] and str(row["locked_until"]) > now_text():
            audit_log({"username": username}, "login", "", "账号锁定中", ip, "denied")
            json_response(self, {
                "ok": False,
                "error": f"连续失败次数过多，账号已锁定，请约 {AUTH_LOCK_MINUTES} 分钟后再试。",
            }, HTTPStatus.UNAUTHORIZED)
            return
        if not int(row["enabled"] or 0):
            audit_log({"username": username}, "login", "", "账号已停用", ip, "denied")
            json_response(self, {"ok": False, "error": "该账号已被停用，请联系管理员。"}, HTTPStatus.UNAUTHORIZED)
            return
        if not verify_password(password, str(row["password_hash"])):
            failed = int(row["failed_count"] or 0) + 1
            locked_until = _expiry_text(AUTH_LOCK_MINUTES * 60) if failed >= AUTH_MAX_FAILED else None
            with connect_db() as conn:
                conn.execute(
                    "update _users set failed_count = ?, locked_until = ? where id = ?",
                    (failed, locked_until, row["id"]),
                )
            audit_log({"username": username}, "login", "", f"密码错误（第 {failed} 次）", ip, "denied")
            message = "用户名或密码不正确。"
            if locked_until:
                message = f"连续失败 {failed} 次，账号已锁定 {AUTH_LOCK_MINUTES} 分钟。"
            json_response(self, {"ok": False, "error": message}, HTTPStatus.UNAUTHORIZED)
            return
        with connect_db() as conn:
            conn.execute(
                "update _users set failed_count = 0, locked_until = NULL, last_login_at = ? where id = ?",
                (now_text(), row["id"]),
            )
        token, seconds = create_session(row["id"], ip, str(self.headers.get("User-Agent", "")), remember)
        user = {
            "id": row["id"],
            "username": row["username"],
            "displayName": row["display_name"] or row["username"],
            "role": row["role"],
        }
        audit_log(user, "login", username, "登录成功", ip, "ok")
        json_response(
            self,
            {"ok": True, "user": user},
            extra_headers=[("Set-Cookie", session_cookie_header(token, seconds, request_is_https(self)))],
        )

    def handle_auth_logout(self) -> None:
        cookies = parse_cookie_header(self.headers.get("Cookie", ""))
        token = cookies.get(AUTH_COOKIE_NAME, "")
        user = getattr(self, "auth_user", None) or resolve_session(token)
        destroy_session(token)
        if user:
            audit_log(user, "logout", str(user.get("username") or ""), "", self.request_ip(), "ok")
        json_response(self, {"ok": True}, extra_headers=[("Set-Cookie", expired_cookie_header(request_is_https(self)))])

    def handle_auth_register(self) -> None:
        """开放注册（业主 2026-09-22 决策）：默认 viewer，可选邀请码。"""
        payload = read_json_body(self)
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        display_name = str(payload.get("displayName") or "").strip()
        code = str(payload.get("code") or "").strip()
        ip = self.request_ip()
        if not username or not password:
            raise ValueError("请填写用户名与密码。")
        if len(username) < 3:
            raise ValueError("用户名至少 3 个字符。")
        if len(password) < 8:
            raise ValueError("密码至少 8 位。")
        expected = signup_code()
        if expected and not hmac.compare_digest(code, expected):
            audit_log({"username": username}, "register", "", "邀请码不正确", ip, "denied")
            json_response(self, {"ok": False, "error": "邀请码不正确。"}, HTTPStatus.FORBIDDEN)
            return
        if find_user(username):
            raise ValueError("该用户名已被占用。")
        user_id = uuid.uuid4().hex
        role = signup_default_role()
        with connect_db() as conn:
            conn.execute(
                "insert into _users (id, username, display_name, password_hash, role, enabled, created_at, created_by, failed_count)"
                " values (?, ?, ?, ?, ?, 1, ?, 'self-register', 0)",
                (user_id, username, display_name or username, hash_password(password), role, now_text()),
            )
        user = {"id": user_id, "username": username, "displayName": display_name or username, "role": role}
        audit_log(user, "register", username, f"开放注册，默认角色 {role}", ip, "ok")
        token, seconds = create_session(user_id, ip, str(self.headers.get("User-Agent", "")), False)
        json_response(
            self,
            {"ok": True, "user": user},
            extra_headers=[("Set-Cookie", session_cookie_header(token, seconds, request_is_https(self)))],
        )

    # --------------------------------------------------- 账号管理与审计（P3，仅 admin）

    def handle_users(self) -> None:
        actor = request_actor(self)
        json_response(self, {
            "ok": True,
            "users": list_users(),
            "roles": ROLE_OPTIONS,
            "currentUserId": str((actor or {}).get("id") or ""),
        })

    def handle_user_create(self) -> None:
        actor = request_actor(self)
        user = create_user(read_json_body(self))
        audit_log(actor, "user.create", str(user["username"]), f"角色 {user['role']}", self.request_ip(), "ok")
        json_response(self, {"ok": True, "user": user, "message": f"账号 {user['username']} 已创建。"})

    def handle_user_update(self) -> None:
        actor = request_actor(self)
        user, changes = update_user(read_json_body(self), actor)
        detail = "；".join(changes) if changes else "无变化"
        audit_log(actor, "user.update", str(user["username"]), detail, self.request_ip(), "ok")
        json_response(self, {"ok": True, "user": user, "message": f"{user['username']}：{detail}"})

    def handle_user_delete(self, query: str) -> None:
        actor = request_actor(self)
        params = parse_qs(query)
        user_id = (params.get("id") or [""])[0].strip()
        if not user_id:
            raise ValueError("缺少用户 ID。")
        removed = delete_user(user_id, actor)
        audit_log(actor, "user.delete", str(removed["username"]), "", self.request_ip(), "ok")
        json_response(self, {"ok": True, "message": f"账号 {removed['username']} 已删除，其会话已全部作废。"})

    def handle_audit_logs(self, query: str) -> None:
        params = parse_qs(query)

        def pick(name: str, default: str = "") -> str:
            return (params.get(name) or [default])[0].strip()

        def number(name: str, default: int, low: int, high: int) -> int:
            try:
                value = int(pick(name, str(default)))
            except Exception:  # noqa: BLE001
                value = default
            return max(low, min(high, value))

        payload = query_audit_logs(
            username=pick("username"),
            action=pick("action"),
            status=pick("status"),
            keyword=pick("keyword"),
            limit=number("limit", 100, 1, 500),
            offset=number("offset", 0, 0, 10000000),
        )
        json_response(self, {
            "ok": True,
            **payload,
            "retentionDays": audit_retention_days(),
            "users": [str(item["username"]) for item in list_users()],
            "roleLabels": ROLE_LABELS,
        })

    def handle_auth_password(self) -> None:
        payload = read_json_body(self)
        user = getattr(self, "auth_user", None) or current_user(self)
        if not user:
            json_response(self, {"ok": False, "needLogin": True, "error": "未登录。"}, HTTPStatus.UNAUTHORIZED)
            return
        old_password = str(payload.get("oldPassword") or "")
        new_password = str(payload.get("newPassword") or "")
        if len(new_password) < 8:
            raise ValueError("新密码至少 8 位。")
        row = find_user(str(user["username"]))
        if not row or not verify_password(old_password, str(row["password_hash"])):
            audit_log(user, "password", str(user["username"]), "原密码不正确", self.request_ip(), "denied")
            json_response(self, {"ok": False, "error": "原密码不正确。"}, HTTPStatus.BAD_REQUEST)
            return
        with connect_db() as conn:
            conn.execute("update _users set password_hash = ? where id = ?", (hash_password(new_password), row["id"]))
        destroy_user_sessions(str(row["id"]))
        audit_log(user, "password", str(user["username"]), "修改密码成功", self.request_ip(), "ok")
        json_response(
            self,
            {"ok": True, "message": "密码已更新，请使用新密码重新登录。", "relogin": True},
            extra_headers=[("Set-Cookie", expired_cookie_header(request_is_https(self)))],
        )

    def handle_queries(self) -> None:
        with connect_db() as conn:
            rows = conn.execute("select * from _saved_queries order by updated_at desc, name").fetchall()
        json_response(self, {"ok": True, "queries": [saved_query_public(row) for row in rows]})

    def handle_query_run(self) -> None:
        payload = read_json_body(self)
        actor = request_actor(self)
        sql_text = str(payload.get("sql") or "")
        # 只读账号（viewer）只能执行 safe 语句：复用查询模块自己的危险分级，
        # 判定为 write / danger 直接 403 并落审计，不进入执行器。
        if actor and not role_at_least(str(actor.get("role")), "operator"):
            blocked = readonly_blocked_statements(sql_text)
            if blocked:
                audit_log(
                    actor,
                    "query.denied",
                    str(payload.get("connectionId") or ""),
                    f"只读账号执行写语句被拒（{blocked[0]['level']}）：{str(blocked[0]['sql'])[:120]}",
                    self.request_ip(),
                    "denied",
                )
                json_response(
                    self,
                    {
                        "ok": False,
                        "error": "只读账号只能执行查询语句（SELECT / SHOW / EXPLAIN 等）。需要写入请让管理员提升为「可写」。",
                        "blockedStatements": blocked,
                    },
                    HTTPStatus.FORBIDDEN,
                )
                return
        try:
            result = run_readonly_query(payload)
        except Exception as exc:
            raise ValueError(str(exc)) from exc
        # 写 / 危险语句落审计（成功的 DDL、DML 也要留痕）
        if actor:
            risks = [classify_sql_risk(item) for item in split_sql_statements(sql_text)]
            if any(str(risk.get("level")) != "safe" for risk in risks):
                worst = "danger" if any(str(risk.get("level")) == "danger" for risk in risks) else "write"
                failed_index = result.get("failedIndex")
                audit_log(
                    actor,
                    "query.write",
                    str(payload.get("connectionId") or ""),
                    f"{worst} 级语句共 {len(risks)} 条，失败序号：{failed_index if failed_index else '无'}",
                    self.request_ip(),
                    "failed" if failed_index else "ok",
                )
        json_response(self, {"ok": True, **result})

    def handle_query_save(self) -> None:
        payload = read_json_body(self)
        query_id = str(payload.get("id") or uuid.uuid4().hex)
        name = str(payload.get("name") or "").strip()
        sql = str(payload.get("sql") or "").strip()
        connection_id = str(payload.get("connectionId") or "").strip()
        if not name:
            raise ValueError("请填写查询名称。")
        if not sql:
            raise ValueError("请输入要保存的 SQL。")
        now = now_text()
        with connect_db() as conn:
            old = conn.execute("select created_at from _saved_queries where id = ?", (query_id,)).fetchone()
            conn.execute(
                "insert or replace into _saved_queries (id, name, connection_id, sql_text, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
                (query_id, name, connection_id, sql, old["created_at"] if old else now, now),
            )
            _sync_query_to_job(conn, query_id, name, connection_id)
            conn.commit()
            row = conn.execute("select * from _saved_queries where id = ?", (query_id,)).fetchone()
        json_response(self, {"ok": True, "query": saved_query_public(row)})

    def handle_query_delete(self, query: str) -> None:
        query_id = parse_qs(query).get("id", [""])[0]
        if not query_id:
            raise ValueError("缺少查询 ID。")
        with connect_db() as conn:
            conn.execute("delete from _saved_queries where id = ?", (query_id,))
            removed_jobs = _delete_jobs_for_query(conn, query_id)
            conn.commit()
        json_response(self, {"ok": True, "removedJobs": removed_jobs})

    def handle_export_sources(self, query: str = "") -> None:
        fields = self._request_fields(query)
        export_fields = {
            "connectionId": fields.get("connectionId", ""),
            "targetDbType": fields.get("targetDbType", "mysql"),
        }
        json_response(self, {"ok": True, "sources": export_sources(export_fields)})

    def handle_export_preview(self) -> None:
        payload = read_json_body(self)
        preview = preview_export_job(payload)
        json_response(self, {"ok": True, **preview})

    def handle_export_run(self) -> None:
        payload = read_json_body(self)
        try:
            result = run_export_job(payload)
        except Exception as exc:
            fields = {key: str(value) for key, value in payload.items() if not isinstance(value, (list, dict))}
            raise ValueError(friendly_export_error(exc, fields)) from exc
        fields = {key: str(value) for key, value in payload.items() if not isinstance(value, (list, dict))}
        # 「导出完成后打开文件 / 打开文件夹」——此前只采集不执行，这里补上真正的动作。
        open_exported_files(fields, [str(path) for path in result["files"]])
        audit_log(
            request_actor(self),
            "export.run",
            str(fields.get("connectionId") or fields.get("targetDbType") or ""),
            f"{len(result['files'])} 个文件 / {result['rows']} 行",
            self.request_ip(),
            "ok",
        )
        json_response(
            self,
            {
                "ok": True,
                "files": result["files"],
                "downloadUrls": ["/api/export/download?path=" + quote(str(path)) for path in result["files"]],
                "rows": result["rows"],
                "elapsedMs": result["elapsedMs"],
                "message": f"导出完成：{len(result['files'])} 个文件，{result['rows']} 行。",
            },
        )

    def handle_export_choose_target(self) -> None:
        # 由服务端弹 Windows 原生对话框，把绝对路径交还给页面。
        # 旧的实现直接抛错"请在浏览器中完成"，但浏览器拿不到绝对路径，界面上只能显示文件夹名，
        # 存进任务配置时也只能写空串，导致"选了文件夹但下次打开不生效"。
        payload = read_json_body(self)
        # 必须由前端显式声明选文件夹还是选文件，不能默认成 folder：
        # 否则一个缺字段的请求（空 body、老版本前端、随手 curl）就会在用户桌面弹出原生对话框，
        # 并且把该 HTTP 线程一直阻塞到有人去点掉窗口为止。
        mode = str(payload.get("mode") or "").strip().lower()
        if mode not in ("folder", "file"):
            raise ValueError("选择类型不支持，请指定 mode 为 folder 或 file。")
        if os.name != "nt":
            json_response(
                self,
                {"ok": False, "unsupported": True, "error": "服务端不是 Windows，无法打开系统选择框，请手动填写绝对路径。"},
                HTTPStatus.NOT_IMPLEMENTED,
            )
            return
        # M12-011: 同步弹框 + 超时看门狗，避免 HTTP 线程被无限占用、超时后残留幽灵窗口。
        # 先拍基线：本次请求前系统里已有的 #32770 窗口（含其它进程），看门狗只关新弹出的。
        baseline = _snapshot_dialogs()
        stop = threading.Event()
        threading.Thread(
            target=_dialog_watchdog,
            args=(NATIVE_DIALOG_TIMEOUT, baseline, stop),
            daemon=True,
        ).start()
        try:
            path = native_pick_path(
                mode,
                initial=str(payload.get("initial") or "").strip(),
                extension=str(payload.get("extension") or "xlsx").strip(),
                suggest=str(payload.get("suggest") or "").strip(),
            )
        except ValueError:
            raise
        except Exception as exc:  # 无桌面会话、对话框创建失败等 → 让前端回退到浏览器直选
            json_response(
                self,
                {"ok": False, "unsupported": True, "error": f"打开系统选择框失败：{exc}"},
                HTTPStatus.NOT_IMPLEMENTED,
            )
            return
        finally:
            stop.set()
        json_response(self, {"ok": True, "path": path, "cancelled": path == ""})

    def handle_export_download(self, query: str) -> None:
        params = parse_qs(query)
        raw_path = params.get("path", [""])[0]
        legacy_name = Path(params.get("name", [""])[0]).name
        if raw_path:
            path = Path(raw_path).resolve()
        elif legacy_name:
            path = (EXPORTS / legacy_name).resolve()
        else:
            raise ValueError("缺少文件名。")
        # P2-14: never serve files outside the export/upload product directories.
        if not is_allowed_download_path(path):
            if not path.exists():
                raise ValueError("导出文件不存在。")
            raise ValueError("该路径不在允许下载的目录内。")
        if not path.exists() or not path.is_file():
            raise ValueError("导出文件不存在。")
        name = path.name
        ascii_stem = re.sub(r"[^A-Za-z0-9_-]+", "_", path.stem, flags=re.ASCII).strip("_") or "export"
        ascii_suffix = re.sub(r"[^A-Za-z0-9.]+", "", path.suffix, flags=re.ASCII)
        ascii_name = f"{ascii_stem}{ascii_suffix}"
        encoded_name = quote(name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        self.send_header("Content-Disposition", f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}")
        self.send_header("Content-Length", str(path.stat().st_size))
        self.end_headers()
        with path.open("rb") as file:
            while True:
                chunk = file.read(1024 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)


def main() -> None:
    ensure_dirs()
    # 首个管理员引导（幂等：_users 非空时直接返回）。放在起服务之前，
    # 保证启用认证的部署不会出现"没有任何账号可登录"的死锁。
    try:
        bootstrap_admin_from_env()
        pruned = prune_expired_sessions()
        if pruned:
            print(f"[auth] 清理过期会话 {pruned} 条", flush=True)
        pruned_logs = prune_audit_logs()
        if pruned_logs:
            print(f"[auth] 清理超过 {audit_retention_days()} 天的审计记录 {pruned_logs} 条", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: 认证初始化跳过：{exc}", flush=True)
    # 启动即把三个运行数据落点打出来：定时任务无人值守跑，出问题时第一件事就是
    # 确认进程到底读的哪个 imports.db —— 数据目录一旦分裂成两份，症状是"数据没更新"
    # 而不是报错，极难排查。这里让路径一眼可见。
    print(f"数据目录 DATA_DIR      = {DATA}", flush=True)
    print(f"上传目录 UPLOADS_DIR   = {UPLOADS}", flush=True)
    print(f"导出目录 EXPORTS_DIR   = {EXPORTS}", flush=True)
    print(f"元数据库 DB_PATH       = {DB_PATH}", flush=True)
    try:
        recover_interrupted_runs()
    except Exception as exc:  # pragma: no cover - defensive
        print(f"WARNING: interrupted-run recovery skipped: {exc}", flush=True)
    port = int(os.environ.get("PORT", "8765"))
    host = bind_host()
    server = ThreadingHTTPServer((host, port), ImportPrototypeHandler)
    stop_event = threading.Event()
    scheduler = threading.Thread(target=scheduler_loop, args=(stop_event,), daemon=True)
    scheduler.start()
    print(f"Import prototype running at http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping import prototype...")
    finally:
        stop_event.set()
        server.server_close()


if __name__ == "__main__":
    main()
