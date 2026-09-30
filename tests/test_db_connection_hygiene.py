"""数据库连接的资源卫生：`with connect_db() as conn:` 必须真的关闭连接。

回归背景（2026-09-30 云端掉线 24 小时的根因）：
    `sqlite3.Connection` 的上下文管理器**只管事务**（提交/回滚），**不关闭连接**。
    项目里 82 处 `with connect_db() as conn:` 因此每个请求泄漏一个 SQLite 句柄。
    累计撞上 FD 上限（默认 1024）后：accept() 报 EMFILE → socketserver 静默吞掉该
    OSError → accept 循环 100% 空转、一个连接都不服务且一条错误都不打 → nginx 504。
    线上实测 PID 的 fd 分布：1019 个 `/opt/dc/runtime/data/imports.db`。

这组用例守住"连接会被关闭"这个不变量，防止有人把 `_ClosingConnection` 去掉。
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

# 隔离测试环境：数据/上传/导出落临时目录，不触碰真实 data/ uploads/ exports/
#
# ⚠️ 必须在 `import server` **之前**设置：server.DB_PATH 是模块导入时按 DATA_DIR
# 算出来并定死的，谁先 import server 谁就决定了它。本文件名以 `test_d` 开头，
# 字母序排在 `test_export_engine.py` **之前**，是实际上的第一个导入者 ——
# 这里若不设，DB_PATH 会被钉在生产路径 runtime/data/imports.db 上，
# 于是其他模块里所有"生产数据未被触碰 / 测试环境已隔离"的守卫用例集体失败
# （2026-09-30 加本文件时真实踩到过）。
_TMP = tempfile.mkdtemp(prefix="dc_test_")
os.environ["DATA_DIR"] = str(Path(_TMP) / "data")
os.environ["UPLOADS_DIR"] = str(Path(_TMP) / "uploads")
os.environ["EXPORTS_DIR"] = str(Path(_TMP) / "exports")

import server  # noqa: E402  —— 必须在上面几行之后导入


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """把 server.DB_PATH 指向一个全新的库（含全部元数据表）。"""
    db_path = tmp_path / "imports.db"
    monkeypatch.setattr(server, "DB_PATH", db_path)
    with server.connect_db():
        pass
    return db_path


def test_connect_db_returns_closing_connection(fresh_db):
    """connect_db 返回的必须是会在 with 退出时关闭连接的类型。"""
    conn = server.connect_db()
    try:
        assert isinstance(conn, server._ClosingConnection), (
            "有人把 connect_db 的 factory 去掉了 —— 那样 82 处 `with connect_db() as conn:` "
            "会重新开始泄漏文件描述符（见本文件顶部注释）"
        )
    finally:
        conn.close()


def test_connection_is_closed_after_with_block(fresh_db):
    """with 退出后连接应处于已关闭状态，再操作直接报错。"""
    with server.connect_db() as conn:
        assert conn.execute("select 1").fetchone()[0] == 1
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("select 1")


def test_with_block_still_commits(fresh_db):
    """补关闭不能破坏原有事务语义：退出时要提交。"""
    with server.connect_db() as conn:
        conn.execute("create table _t_probe (v integer)")
        conn.execute("insert into _t_probe values (7)")
    with server.connect_db() as conn:
        assert conn.execute("select v from _t_probe").fetchone()[0] == 7


def test_with_block_still_rolls_back_on_error(fresh_db):
    """异常退出时要回滚，且连接同样被关闭。"""
    with server.connect_db() as conn:
        conn.execute("create table _t_probe2 (v integer)")
    with pytest.raises(RuntimeError):
        with server.connect_db() as conn:
            conn.execute("insert into _t_probe2 values (1)")
            raise RuntimeError("boom")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("select 1")
    with server.connect_db() as conn:
        assert conn.execute("select count(*) from _t_probe2").fetchone()[0] == 0


@pytest.mark.skipif(
    not os.path.isdir("/proc/self/fd"),
    reason="需要 /proc/self/fd 统计文件描述符（仅 Linux；本机 Windows 会跳过）",
)
def test_repeated_db_access_does_not_leak_fds(fresh_db):
    """反复访问数据库不得持续累积文件描述符（这正是线上挂掉的那条不变量）。"""

    def open_fds() -> int:
        return len(os.listdir("/proc/self/fd"))

    with server.connect_db():
        pass
    before = open_fds()
    for _ in range(300):
        with server.connect_db() as conn:
            conn.execute("select 1").fetchone()
    grown = open_fds() - before
    assert grown <= 2, f"300 次连接后 fd 增加了 {grown} 个，说明连接没被关闭（泄漏）"


@pytest.mark.skipif(
    not hasattr(server, "ResilientHTTPServer"),
    reason="需要 ResilientHTTPServer（accept 失败不再静默空转）",
)
def test_accept_failure_is_logged_and_does_not_spin_silently(monkeypatch, capsys):
    """accept() 抛 OSError 时应打印 WARNING 并退避，而不是静默满速空转。

    标准库 `_handle_request_noblock` 会把这个 OSError 直接吞掉，导致线上
    "进程活着、CPU 100%、一个请求不服务、零日志" 的假死形态。
    """
    srv = server.ResilientHTTPServer.__new__(server.ResilientHTTPServer)  # 不起真正的 socket
    srv._accept_fail_streak = 0

    def boom():
        raise OSError(24, "Too many open files")

    monkeypatch.setattr(srv, "get_request", boom, raising=False)
    monkeypatch.setattr(server.time, "sleep", lambda _s: None)

    srv._handle_request_noblock()
    out = capsys.readouterr().out
    assert "accept() 失败" in out, "accept 失败必须留下痕迹，否则线上无从排查"
    assert srv._accept_fail_streak == 1


def test_accept_failure_exits_after_limit(monkeypatch):
    """连续失败到阈值应主动退出，交给 systemd 拉起，而不是永久空转。"""
    srv = server.ResilientHTTPServer.__new__(server.ResilientHTTPServer)
    srv._accept_fail_streak = srv.ACCEPT_FAIL_LIMIT - 1
    monkeypatch.setattr(
        srv, "get_request", lambda: (_ for _ in ()).throw(OSError(24, "Too many open files"))
    )
    monkeypatch.setattr(server.time, "sleep", lambda _s: None)
    called: dict[str, int] = {}
    monkeypatch.setattr(server.os, "_exit", lambda code: called.setdefault("code", code))
    srv._handle_request_noblock()
    assert called.get("code") == 1
