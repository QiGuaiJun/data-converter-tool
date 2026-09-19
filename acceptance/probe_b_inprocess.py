#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""B 层进程内探针：承接必须直接调用内部函数才能验证的验收条目。

由 acceptance/replay_b_layer.py 以子进程方式调用，**全部在隔离沙箱内**执行。

用法：
    probe_b_inprocess.py prune        <DATA_DIR>
    probe_b_inprocess.py recover      <DATA_DIR> [--seed-schedule <sid>]
    probe_b_inprocess.py legacy-b64   <DATA_DIR>
    probe_b_inprocess.py arm          <DATA_DIR> --schedule <sid> --next-run-at "<t>"
    probe_b_inprocess.py launcher     <SANDBOX_DIR>
    probe_b_inprocess.py docker-check <SANDBOX_DIR>
"""
from __future__ import annotations

import base64
import os
import sqlite3
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent


def _argv_value(flag: str) -> str:
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return ""


def _set_data_dir(path: str) -> None:
    root = Path(path).resolve()
    os.environ["DATA_DIR"] = str(root)
    os.environ["UPLOADS_DIR"] = str(root.parent / "uploads")
    os.environ["EXPORTS_DIR"] = str(root.parent / "exports")
    os.environ.setdefault("PYTHONUTF8", "1")
    if str(PROJECT) not in sys.path:
        sys.path.insert(0, str(PROJECT))


def _db_path(data_dir: str) -> Path:
    return Path(data_dir).resolve() / "imports.db"


# --------------------------------------------------------------------------- 探针


def probe_prune(data_dir: str) -> int:
    """造两条 5 天前的运行记录 + 步骤记录，调用 prune_job_logs(3) 验证超期清理。"""
    import server

    conn = sqlite3.connect(_db_path(data_dir), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        old = "2026-09-10 00:00:00"
        conn.execute(
            "insert or replace into _job_runs (id, job_id, schedule_id, job_name, started_at, ended_at,"
            " elapsed_ms, status) values ('bprune-run-old', 'bprune-job', '', '探针', ?, ?, 1, '成功')",
            (old, old),
        )
        conn.execute(
            "insert or replace into _job_run_steps (id, run_id, step_index, step_name, step_type, started_at,"
            " status) values ('bprune-step-old', 'bprune-run-old', 1, '探针步', 'query', ?, '成功')",
            (old,),
        )
        conn.commit()
        before_runs = conn.execute("select count(*) from _job_runs").fetchone()[0]
        before_steps = conn.execute("select count(*) from _job_run_steps").fetchone()[0]
        server.prune_job_logs(3)
        after_runs = conn.execute("select count(*) from _job_runs").fetchone()[0]
        after_steps = conn.execute("select count(*) from _job_run_steps").fetchone()[0]
        left_old = conn.execute(
            "select count(*) from _job_runs where id = 'bprune-run-old'").fetchone()[0]
        print(
            f"pruned: runs {before_runs}->{after_runs}, steps {before_steps}->{after_steps}, "
            f"超期记录残留={left_old}（期望 0）",
            flush=True,
        )
    finally:
        conn.close()
    return 0


def probe_recover(data_dir: str) -> int:
    """注入 running=1 与 status=运行中，调用 recover_interrupted_runs() 验证僵尸恢复。"""
    import server

    sid = _argv_value("--seed-schedule")
    conn = sqlite3.connect(_db_path(data_dir), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if sid:
            conn.execute("update _schedules set running = 1 where id = ?", (sid,))
        conn.execute(
            "insert or replace into _job_runs (id, job_id, schedule_id, job_name, started_at, status)"
            " values ('brecover-run', 'brecover-job', '', '探针', '2026-09-19 00:00:00', '运行中')"
        )
        conn.commit()
        zombie_runs = conn.execute(
            "select count(*) from _job_runs where status = '运行中'").fetchone()[0]
        recovered = server.recover_interrupted_runs()
        left_running_sched = conn.execute(
            "select count(*) from _schedules where running = 1").fetchone()[0]
        fixed = conn.execute(
            "select status, message from _job_runs where id = 'brecover-run'").fetchone()
        print(
            f"recovered={recovered} 注入前僵尸运行={zombie_runs} 残留 running 调度={left_running_sched}"
            f"（期望 0）；运行记录 status={fixed['status'] if fixed else 'NA'} "
            f"message={str(fixed['message'])[:40] if fixed else 'NA'}",
            flush=True,
        )
    finally:
        conn.close()
    return 0


def probe_legacy_b64(data_dir: str) -> int:
    """写入一条 `b64:` 旧格式密码，走 connect_db() 的自动迁移路径，验证变成 fernet: 且可解回明文。"""
    import server

    db = _db_path(data_dir)
    conn = sqlite3.connect(db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        token = "b64:" + base64.b64encode("LegacyPwd!9".encode("utf-8")).decode("ascii")
        conn.execute("delete from _db_connections where id = 'breplay-legacy'")
        conn.execute(
            "insert into _db_connections (id, name, db_type, host, port, user_name, password, db_name,"
            " charset, ssl_enabled, created_at, updated_at)"
            " values ('breplay-legacy', '复跑-旧格式', 'mysql', '127.0.0.1', 3306, 'root', ?,"
            " 'dc_p2_test', 'utf8mb4', 0, '2026-09-19 00:00:00', '2026-09-19 00:00:00')",
            (token,),
        )
        conn.commit()
    finally:
        conn.close()

    # 强制走一次「服务启动时的自动迁移」：复位进程级去重标记后调用 connect_db()
    server._MIGRATION_DONE = False
    with server.connect_db():
        pass

    conn = sqlite3.connect(db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "select password from _db_connections where id = 'breplay-legacy'").fetchone()
        stored = str(row["password"] or "") if row else ""
    finally:
        conn.close()
    decoded = ""
    try:
        decoded = server.decode_secret(stored)
    except Exception as exc:  # noqa: BLE001
        decoded = f"<decode-failed:{exc}>"
    ok = stored.startswith("fernet:") and decoded == "LegacyPwd!9"
    print(f"migrated={str(ok).lower()} 迁移后前缀={stored[:7]} 解回明文匹配={decoded == 'LegacyPwd!9'}",
          flush=True)
    return 0 if ok else 1


def probe_arm(data_dir: str) -> int:
    """把某个调度拨到「已到期」，让调度线程（5s 轮询）真正分发执行。"""
    sid = _argv_value("--schedule")
    next_run_at = _argv_value("--next-run-at")
    conn = sqlite3.connect(_db_path(data_dir), timeout=30)
    try:
        conn.execute(
            "update _schedules set enabled = 1, running = 0, next_run_at = ? where id = ?",
            (next_run_at, sid),
        )
        conn.commit()
    finally:
        conn.close()
    print(f"armed schedule={sid[:12]} next_run_at={next_run_at}", flush=True)
    return 0


def probe_launcher(sandbox: str) -> int:
    """桌面启动器：屏蔽浏览器后真起服务，验证 /api/ping 可通。"""
    import threading
    import time
    import urllib.request
    import webbrowser

    opened: list[str] = []
    webbrowser.open = lambda url, *a, **k: opened.append(str(url)) or True  # noqa: ARG005

    box = Path(sandbox).resolve()
    data_root = box / "launcher"
    os.environ.pop("PORT", None)          # 让启动器自己找端口
    os.environ["DATA_DIR"] = str(data_root / "data")
    os.environ["UPLOADS_DIR"] = str(data_root / "uploads")
    os.environ["EXPORTS_DIR"] = str(data_root / "exports")
    os.environ.setdefault("APP_AUTH_ENABLED", "false")
    if str(PROJECT) not in sys.path:
        sys.path.insert(0, str(PROJECT))

    import desktop_launcher

    t = threading.Thread(target=desktop_launcher.main, daemon=True)
    t.start()

    port = 0
    for _ in range(60):
        port = int(os.environ.get("PORT") or 0)
        if port:
            break
        time.sleep(0.5)
    ok = False
    t0 = time.time()
    while time.time() - t0 < 40 and port:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=3) as rp:
                if rp.status == 200:
                    ok = True
                    break
        except Exception:  # noqa: BLE001
            time.sleep(1)
    print(f"launcher-ok={str(ok).lower()} port={port} 浏览器调用被拦截={len(opened)}次 "
          f"数据目录={data_root}", flush=True)
    return 0 if ok else 1


def probe_docker(sandbox: str) -> int:
    import shutil

    docker = shutil.which("docker")
    if not docker:
        print("docker-unavailable", flush=True)
        return 1
    print(f"docker-found:{docker}", flush=True)
    return 0


PROBES = {
    "prune": probe_prune,
    "recover": probe_recover,
    "legacy-b64": probe_legacy_b64,
    "arm": probe_arm,
    "launcher": probe_launcher,
    "docker-check": probe_docker,
}


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: probe_b_inprocess.py <probe> <dir> [flags]", flush=True)
        return 2
    name, target = sys.argv[1], sys.argv[2]
    fn = PROBES.get(name)
    if not fn:
        print(f"unknown probe: {name}", flush=True)
        return 2
    if name in {"launcher", "docker-check"}:
        return fn(target)
    _set_data_dir(target)
    return fn(target)


if __name__ == "__main__":
    raise SystemExit(main())
