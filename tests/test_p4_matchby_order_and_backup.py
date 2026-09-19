"""P4 修复专项回归测试（2026-09-19）。

覆盖两条本轮修复的缺陷：

1. **M2-016「字段匹配 → 按顺序」死控件** —— 前端 `#matchBy` 长期只发送、服务端无消费方，
   于是「按顺序」与「按名称」行为完全一致。现由 `server.align_columns_by_position()`
   实现：`matchBy=order` 且目标表已存在时，第 i 个源列对齐到既有目标表第 i 列。
   **默认（不传 / `name` / `custom`）必须与修复前逐字节一致。**

2. **M10-001/M10-002 备份脚本路径失效** —— `scripts/create_data_backup.py` 曾写死
   `ROOT/data/imports.db`；2026-09-17 迁移后运行数据在 `runtime/data/`，脚本必然失败。
   现改为按 `DATA_DIR / UPLOADS_DIR / EXPORTS_DIR` 取数（默认 `ROOT/runtime/*`），
   与 `server.py` 的 `runtime_path()`、`restore_data_backup.py` 统一口径。

隔离：与其它测试文件一致 —— 导入 `server` 前把 DATA_DIR/UPLOADS_DIR/EXPORTS_DIR
指向临时目录，**绝不触碰真实 runtime/**。备份脚本用例用 `subprocess` + 自定义 env 跑，
同样只落在临时目录。
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

# 隔离测试环境：数据/上传/导出落临时目录，不触碰真实 runtime/
_tmp = tempfile.mkdtemp(prefix="dc_p4_")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
os.environ["UPLOADS_DIR"] = str(Path(_tmp) / "uploads")
os.environ["EXPORTS_DIR"] = str(Path(_tmp) / "exports")

import server  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# sqlite3 连接作为 with 语句使用时只提交事务、不 close，句柄会一直占着临时库文件；
# Windows 上这会挡住隔离目录的删除。记录句柄，进程退出时统一关闭再删目录。
_TRACKED_CONNECTIONS: list[object] = []
_ORIGINAL_CONNECT_DB = server.connect_db


def _tracking_connect_db():
    conn = _ORIGINAL_CONNECT_DB()
    _TRACKED_CONNECTIONS.append(conn)
    return conn


server.connect_db = _tracking_connect_db


@atexit.register
def _remove_isolated_tmp() -> None:
    for conn in _TRACKED_CONNECTIONS:
        try:
            conn.close()
        except Exception:  # pragma: no cover - 关闭失败不影响测试结论
            pass
    shutil.rmtree(_tmp, ignore_errors=True)


# --------------------------------------------------------------------------- 夹具


def _write_csv(name: str, text: str) -> Path:
    path = Path(_tmp) / "uploads" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig")
    return path


def _import(name: str, text: str, **fields):
    """用产品自身的落库路径导入一份 CSV，返回 import_uploaded_file() 的结果。"""
    path = _write_csv(name, text)
    payload = {"tableCase": "lower", "fieldCase": "lower", **fields}
    return server.import_uploaded_file(server.UploadedFile(name, path), payload)


def _rows(table: str):
    with server.connect_db() as conn:
        return [tuple(row) for row in conn.execute(f"select * from {table} order by rowid")]


def _columns(table: str) -> list[str]:
    with server.connect_db() as conn:
        return [row[1] for row in conn.execute(f"pragma table_info({table})")]


# --------------------------------------------------------------- M2-016 按顺序匹配


def test_matchby_order_aligns_source_columns_to_existing_table() -> None:
    """按顺序：既有表 (x, y) + 表头为 a,b 的文件 → 值按列序号落进 x、y。"""
    _import("p4_o1.csv", "x,y\n1,2\n", tableName="p4_order", importMode="rebuild")
    assert _columns("p4_order") == ["x", "y"]

    result = _import("p4_o2.csv", "a,b\n7,8\n", tableName="p4_order", importMode="append",
                     autoExpand="false", matchBy="order")

    assert result["rowsWritten"] == 1
    assert _rows("p4_order") == [(1, 2), (7, 8)]
    # 列名不得被源文件表头改写
    assert _columns("p4_order") == ["x", "y"]


def test_matchby_name_rejects_unmatched_columns() -> None:
    """按名称（含不传 matchBy）：既有列名与源表头不匹配 → 明确报错，不静默写坏数据。"""
    _import("p4_n1.csv", "x,y\n1,2\n", tableName="p4_name", importMode="rebuild")

    with pytest.raises(ValueError, match="目标表缺少字段"):
        _import("p4_n2.csv", "a,b\n7,8\n", tableName="p4_name", importMode="append",
                autoExpand="false", matchBy="name")

    # 不传 matchBy 时行为必须与 matchBy=name 完全一致（既有作业配置就是这种形态）
    with pytest.raises(ValueError, match="目标表缺少字段"):
        _import("p4_n3.csv", "c,d\n9,9\n", tableName="p4_name", importMode="append",
                autoExpand="false")

    assert _rows("p4_name") == [(1, 2)]


def test_matchby_order_reports_when_target_has_fewer_columns() -> None:
    """按顺序：目标表列数少于源列数时必须报错，而不是静默丢列。"""
    _import("p4_s1.csv", "only\n1\n", tableName="p4_short", importMode="rebuild")
    assert _columns("p4_short") == ["only"]

    with pytest.raises(ValueError, match="按顺序"):
        _import("p4_s2.csv", "p,q\n1,2\n", tableName="p4_short", importMode="append",
                autoExpand="false", matchBy="order")

    assert _rows("p4_short") == [(1,)]


def test_matchby_order_is_noop_for_new_table() -> None:
    """按顺序：目标表不存在时无可对齐对象，按源表头建表（与按名称同结果）。"""
    result = _import("p4_new.csv", "m,n\n3,4\n", tableName="p4_new", importMode="rebuild",
                     matchBy="order")

    assert result["rowsWritten"] == 1
    assert _columns("p4_new") == ["m", "n"]
    assert _rows("p4_new") == [(3, 4)]


def test_align_columns_by_position_remaps_match_keys() -> None:
    """辅助函数的单元级断言：列名按位置对齐，且匹配键一起换成目标表列名。

    匹配键不换名会让 update 模式按旧列名找不到键列 —— 这是该修复最容易漏的一环。
    """
    with server.connect_db() as conn:
        conn.execute("drop table if exists p4_helper")
        conn.execute("create table p4_helper (alpha text, beta text)")
        aligned, keys = server.align_columns_by_position(
            conn, "p4_helper", ["first", "second"], ["first"], {"matchBy": "order"}
        )
        assert aligned == ["alpha", "beta"]
        assert keys == ["alpha"]           # 键按位置一起重映射

        # 默认（name）与无 matchBy 时原样返回
        assert server.align_columns_by_position(
            conn, "p4_helper", ["first", "second"], ["first"], {}) == (["first", "second"], ["first"])
        assert server.align_columns_by_position(
            conn, "p4_helper", ["first", "second"], ["first"], {"matchBy": "name"}
        ) == (["first", "second"], ["first"])


# ------------------------------------------------------- M10-001/002 备份脚本口径


def _run_backup(tmp_path: Path, extra_args: list[str], env_extra: dict[str, str]):
    env = {**os.environ, **env_extra}
    return subprocess.run(
        [sys.executable, "scripts/create_data_backup.py", "--output-dir", str(tmp_path / "out"),
         *extra_args],
        cwd=PROJECT_ROOT, env=env, capture_output=True,
    )


def test_backup_script_reads_data_dir_env(tmp_path: Path) -> None:
    """备份脚本必须按 DATA_DIR 取数（而不是写死的 data/imports.db）。"""
    data = tmp_path / "data"
    data.mkdir(parents=True)
    (data / "imports.db").write_bytes(b"SQLITE-PROBE-BYTES")
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "u.csv").write_text("a\n1\n", encoding="utf-8")

    proc = _run_backup(tmp_path, ["--include-uploads"],
                       {"DATA_DIR": str(data), "UPLOADS_DIR": str(uploads),
                        "EXPORTS_DIR": str(tmp_path / "exports")})
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")

    zips = sorted((tmp_path / "out").glob("data-converter-backup-*.zip"))
    assert len(zips) == 1
    with zipfile.ZipFile(zips[0]) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    assert "data/imports.db" in names
    assert any(name.startswith("uploads/") for name in names)
    # manifest 必须记录实际取数目录，便于排查「备份的是哪份数据」
    assert manifest["source"]["dataDir"] == str(data.resolve())


def test_backup_script_reports_full_path_when_db_missing(tmp_path: Path) -> None:
    """库文件不存在时要报出**完整路径**，而不是含糊的相对路径。"""
    empty = tmp_path / "empty-data"
    empty.mkdir()
    proc = _run_backup(tmp_path, [], {"DATA_DIR": str(empty),
                                      "UPLOADS_DIR": str(tmp_path / "uploads"),
                                      "EXPORTS_DIR": str(tmp_path / "exports")})
    assert proc.returncode != 0
    message = (proc.stdout + proc.stderr).decode("utf-8", "replace")
    assert "Nothing to back up" in message
    assert str(empty / "imports.db") in message


def test_restore_script_default_target_matches_runtime_layout() -> None:
    """restore 的三个默认落点必须指向 runtime/（与 server.py 的 runtime_path() 一致）。

    只查 `target_path(...)` 调用的那三行 —— 直接全文搜 `ROOT / "data"` 会被
    `RUNTIME_ROOT = ROOT / "runtime"` 这类定义行误伤。
    """
    source = (PROJECT_ROOT / "scripts" / "restore_data_backup.py").read_text(encoding="utf-8")
    # 用 `target_path("` 过滤，避开 `def target_path(name: str, ...)` 那行定义
    calls = [line.strip() for line in source.splitlines() if 'target_path("' in line]
    assert len(calls) == 3, calls
    for line in calls:
        assert "RUNTIME_ROOT /" in line, line
    assert 'RUNTIME_ROOT = ROOT / "runtime"' in source
