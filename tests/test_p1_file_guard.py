"""P1 文件守卫（skipIfFileUnchanged / guard.type=file_has_new）专项回归测试。

为什么单独建这个文件：上一轮交付的守卫机制是**零测试覆盖**——
``_guard_summary`` / ``_collect_file_guards`` / ``skipIfFileUnchanged`` /
``file_has_new`` 在全部既有测试里命中 0 次（``_file_fingerprint`` 的 3 次命中实为
``import_file_fingerprint`` 的子串误命中），所以"量词取反"这种直接违背用户诉求的缺陷
才能一路漏到交付。注意 ``_file_fingerprint``（守卫用：大小 + mtime_ns，要极轻量）
与 ``import_file_fingerprint``（skipSeenFile 用：大小 + 内容 sha256）是两个不同函数，
后者有覆盖，前者在本次之前没有。

本文件按用户诉求原文逐条钉住语义：
    「每次作业定时任务执行前，都要查询目标文件是否有新增的内容，如果有新增执行导入」
    → any(changed) → 执行

覆盖清单：
 1. 两条守卫、只改其一（**双向**）→ 必须【执行】，且新数据真的进库
 2. 两条守卫、都不变 → 【跳过】
 3. 无基线首次运行 → 【执行】并写入基线
 4. 守卫挂父作业 / 守卫挂子作业 两种形状 → 第二次运行【跳过】
 5. importJobId 引用不存在 → 抛错，且**留有 _job_runs 失败记录**（P1-2）
 6. importJobId 两层引用穿透 → 守卫拿到最终路径
 7. 同一秒内改两次且大小不变 → 识别为【有更新】（P1-1，st_mtime_ns）
 8. _file_fingerprint 边界：单文件 / 目录 / 文件→目录 / 空目录 / 服务端默认目录与用户目录
 9. 交替更新不会静默饥饿（只改一个 → 执行；都不变 → 跳过；改另一个 → 执行）
10. 单条守卫路径不可达不阻断其他守卫的评估与基线写入（P2-1）
11. 步骤级跳过：作业按 any(changed) 执行，但**没变的那个导入步骤不重跑**
    （append 模式下重跑未变源文件 = 目标表出现重复行）；跳过动作留步骤日志；
    跳过判定按 (job_id, step_index) 作用域，父子作业同 step_index 不串味

隔离：数据/上传/导出落 %TEMP% 临时目录，绝不触碰真实 data/、uploads/、exports/，
也不连接任何外部数据库（目标库用 sqlite，即隔离库自身）。

跑法（两种等价）：
    ./.venv/Scripts/python.exe -m pytest tests/test_p1_file_guard.py -q
    ./.venv/Scripts/python.exe tests/test_p1_file_guard.py -v
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest

# 隔离测试环境：数据/上传/导出落临时目录，绝不触碰真实 data/ uploads/ exports/
_tmp = tempfile.mkdtemp(prefix="dc_p1guard_")
_SOURCES = Path(_tmp) / "sources"
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
os.environ["UPLOADS_DIR"] = str(Path(_tmp) / "uploads")
os.environ["EXPORTS_DIR"] = str(Path(_tmp) / "exports")

import server

# sqlite3 连接作为 with 语句使用时只提交事务、不 close，句柄会一直占着临时库文件；
# Windows 上这会挡住隔离目录的删除，把 %TEMP%/dc_p1guard_* 越攒越多。这里记录句柄，
# 进程退出时统一关闭再删目录，保证跑完 %TEMP% 零残留。
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


# ---------------------------------------------------------------------------
# 夹具工具
# ---------------------------------------------------------------------------

CSV_HEADER = "name,amount\n"


def _csv_text(rows: int, amount_offset: int = 0) -> str:
    """生成固定结构的两列 CSV：行数可控，且同样的行数 → 同样的字节长度。"""
    return CSV_HEADER + "".join(f"n{i},{i + amount_offset}\n" for i in range(rows))


def _write_source(name: str, rows: int, amount_offset: int = 0) -> Path:
    """写一个源文件；用 write_bytes 避免 Windows 的 \\n→\\r\\n 转换，保证字节长度可控。"""
    _SOURCES.mkdir(parents=True, exist_ok=True)
    path = _SOURCES / name
    path.write_bytes(_csv_text(rows, amount_offset).encode("utf-8-sig"))
    return path


def _import_cfg(path: Path | str, table: str, guarded: bool = False) -> dict[str, str]:
    """作业里一个导入步骤的配置：目标库用 sqlite（= 隔离库自身），不碰任何真实库。"""
    cfg = {
        "sourcePath": str(path),
        "targetDbType": "sqlite",
        "importMode": "rebuild",
        "tableName": table,
        "hasHeader": "true",
        "mapping": json.dumps(
            [
                {"sourceIndex": 0, "target": "name", "enabled": True, "defaultValue": "", "matchKey": False},
                {"sourceIndex": 1, "target": "amount", "enabled": True, "defaultValue": "", "matchKey": False},
            ]
        ),
        "tableCase": "lower",
        "fieldCase": "lower",
        "commitMode": "once",
    }
    if guarded:
        cfg["skipIfFileUnchanged"] = "true"
    return cfg


def _step(name: str, step_type: str, config: dict[str, object], continue_on_error: bool = False) -> dict[str, object]:
    return {
        "name": name,
        "type": step_type,
        "enabled": True,
        "continueOnError": continue_on_error,
        "config": config,
    }


def _two_guard_job(name: str, src_a: Path, src_b: Path, table_a: str, table_b: str) -> dict[str, object]:
    """两条独立源文件守卫的作业：两个 import 步骤各自 skipIfFileUnchanged=true。"""
    return server.save_job(
        {
            "name": name,
            "guard": {},
            "steps": [
                _step("导入 A", "import", _import_cfg(src_a, table_a, guarded=True)),
                _step("导入 B", "import", _import_cfg(src_b, table_b, guarded=True)),
            ],
        }
    )


def _load_job(job_id: str) -> dict[str, object]:
    with server.connect_db() as conn:
        row = conn.execute("select * from _jobs where id = ?", (job_id,)).fetchone()
    assert row is not None, f"作业 {job_id} 不存在"
    return server.row_to_job(row)


def _table_rows(table: str) -> int:
    with server.connect_db() as conn:
        row = conn.execute(
            "select name from sqlite_master where type = 'table' and name = ?", (table,)
        ).fetchone()
        if not row:
            return -1
        return int(conn.execute(f"select count(*) from {table}").fetchone()[0])


def _table_amounts(table: str) -> list[int]:
    with server.connect_db() as conn:
        return sorted(int(row[0]) for row in conn.execute(f"select amount from {table}").fetchall())


def _guard_rows(job_id: str) -> dict[int, str]:
    """读取某作业在 _job_file_guards 里的基线：{step_index: fingerprint}。"""
    with server.connect_db() as conn:
        return {
            int(row["step_index"]): str(row["fingerprint"])
            for row in conn.execute(
                "select step_index, fingerprint from _job_file_guards where job_id = ?", (job_id,)
            ).fetchall()
        }


def _runs_of(job_id: str) -> list[sqlite3.Row]:
    with server.connect_db() as conn:
        return conn.execute(
            "select * from _job_runs where job_id = ? order by rowid", (job_id,)
        ).fetchall()


# ---------------------------------------------------------------------------
# 1 / 2 / 9：量词语义（any(changed) → 执行）——P0-1
# ---------------------------------------------------------------------------


def test_two_guards_change_first_only_must_execute() -> None:
    """方向 A：两条守卫只改第 1 条 → 必须执行，且新数据真的进库（P0-1）。"""
    src_a = _write_source("p0a_ticket.csv", 2)
    src_b = _write_source("p0a_benefit.csv", 2)
    job = _two_guard_job("p1g_dir_a", src_a, src_b, "p1g_dir_a_ticket", "p1g_dir_a_benefit")

    first = server.run_saved_job(str(job["id"]))
    assert first["status"] == "成功", first["message"]
    assert _table_rows("p1g_dir_a_ticket") == 2
    assert _table_rows("p1g_dir_a_benefit") == 2

    # 只改【第 1 条】源文件（另一个文件保持字节、mtime 完全不动）
    src_a.write_bytes(_csv_text(4).encode("utf-8-sig"))
    second = server.run_saved_job(str(job["id"]))

    print(f"[方向A] status={second['status']!r}")
    print(f"[方向A] ticket 行数 2 -> {_table_rows('p1g_dir_a_ticket')}（源文件改为 4 行）")
    print(f"[方向A] benefit 行数 = {_table_rows('p1g_dir_a_benefit')}（源文件未动）")
    print(f"[方向A] message={second['message'].splitlines()[0]}")

    assert second["status"] == "成功", f"只改了一个源文件却未执行：{second['message']}"
    assert _table_rows("p1g_dir_a_ticket") == 4, "新数据必须真的进库"
    assert _table_rows("p1g_dir_a_benefit") == 2
    assert "p0a_ticket.csv" in second["message"], "要能看出是哪个源文件驱动了执行"


def test_two_guards_change_second_only_must_execute() -> None:
    """方向 B：两条守卫只改第 2 条 → 必须执行，且新数据真的进库（P0-1）。"""
    src_a = _write_source("p0b_ticket.csv", 2)
    src_b = _write_source("p0b_benefit.csv", 2)
    job = _two_guard_job("p1g_dir_b", src_a, src_b, "p1g_dir_b_ticket", "p1g_dir_b_benefit")

    first = server.run_saved_job(str(job["id"]))
    assert first["status"] == "成功", first["message"]

    src_b.write_bytes(_csv_text(5).encode("utf-8-sig"))
    second = server.run_saved_job(str(job["id"]))

    print(f"[方向B] status={second['status']!r}")
    print(f"[方向B] benefit 行数 2 -> {_table_rows('p1g_dir_b_benefit')}（源文件改为 5 行）")
    print(f"[方向B] ticket 行数 = {_table_rows('p1g_dir_b_ticket')}（源文件未动）")
    print(f"[方向B] message={second['message'].splitlines()[0]}")

    assert second["status"] == "成功", f"只改了一个源文件却未执行：{second['message']}"
    assert _table_rows("p1g_dir_b_benefit") == 5, "新数据必须真的进库"
    assert _table_rows("p1g_dir_b_ticket") == 2
    assert "p0b_benefit.csv" in second["message"], "要能看出是哪个源文件驱动了执行"


def test_two_guards_both_unchanged_skips() -> None:
    """两条守卫都不变 → 跳过，且文案如实说明"均无更新"。"""
    src_a = _write_source("p0c_ticket.csv", 3)
    src_b = _write_source("p0c_benefit.csv", 3)
    job = _two_guard_job("p1g_both_same", src_a, src_b, "p1g_same_ticket", "p1g_same_benefit")

    assert server.run_saved_job(str(job["id"]))["status"] == "成功"
    second = server.run_saved_job(str(job["id"]))

    print(f"[都不变] status={second['status']!r}")
    print(f"[都不变] message={second['message']}")

    assert second["status"] == "跳过", second["message"]
    assert "源文件均无更新" in second["message"]
    assert "p0c_ticket.csv" in second["message"] and "p0c_benefit.csv" in second["message"]
    assert _table_rows("p1g_same_ticket") == 3
    assert _table_rows("p1g_same_benefit") == 3


def test_alternating_updates_never_starve() -> None:
    """不静默饥饿：只改一个 → 执行；都不变 → 跳过；改另一个 → 执行（逐轮证明）。"""
    src_a = _write_source("p0d_ticket.csv", 2)
    src_b = _write_source("p0d_benefit.csv", 2)
    job = _two_guard_job("p1g_alt", src_a, src_b, "p1g_alt_ticket", "p1g_alt_benefit")
    job_id = str(job["id"])

    r1 = server.run_saved_job(job_id)  # 无基线 → 执行
    src_a.write_bytes(_csv_text(3).encode("utf-8-sig"))
    r2 = server.run_saved_job(job_id)  # 只改 A → 执行
    r3 = server.run_saved_job(job_id)  # 都不变 → 跳过
    src_b.write_bytes(_csv_text(6).encode("utf-8-sig"))
    r4 = server.run_saved_job(job_id)  # 只改 B → 执行

    print(f"[不饥饿] 轮1(无基线)={r1['status']!r} 轮2(只改A)={r2['status']!r} "
          f"轮3(都不变)={r3['status']!r} 轮4(只改B)={r4['status']!r}")
    print(f"[不饥饿] ticket 行数={_table_rows('p1g_alt_ticket')} benefit 行数={_table_rows('p1g_alt_benefit')}")

    assert [r1["status"], r2["status"], r3["status"], r4["status"]] == ["成功", "成功", "跳过", "成功"]
    assert _table_rows("p1g_alt_ticket") == 3
    assert _table_rows("p1g_alt_benefit") == 6


# ---------------------------------------------------------------------------
# 3：无基线首次运行
# ---------------------------------------------------------------------------


def test_no_baseline_first_run_executes_and_records_baseline() -> None:
    """首次运行（_job_file_guards 无记录）必须视为"有更新"→ 执行，并写入基线。"""
    src_a = _write_source("p0e_ticket.csv", 2)
    src_b = _write_source("p0e_benefit.csv", 2)
    job = _two_guard_job("p1g_first", src_a, src_b, "p1g_first_ticket", "p1g_first_benefit")
    job_id = str(job["id"])

    assert _guard_rows(job_id) == {}, "前置条件：这条作业必须还没有基线"
    run = server.run_saved_job(job_id)
    rows = _guard_rows(job_id)

    print(f"[无基线] status={run['status']!r} 基线行数={len(rows)} keys={sorted(rows)}")

    assert run["status"] == "成功", run["message"]
    assert set(rows) == {0, 1}, "两条守卫都要写入基线"
    assert rows[0] != rows[1], "不同源文件的指纹不应相同"
    assert rows[0] == server._file_fingerprint(str(src_a))
    assert rows[1] == server._file_fingerprint(str(src_b))


# ---------------------------------------------------------------------------
# 4：守卫挂父作业 / 守卫挂子作业
# ---------------------------------------------------------------------------


def test_parent_level_guard_second_run_skips() -> None:
    """形状一：guard.type=file_has_new 挂在父作业上（步骤自身没写 skipIfFileUnchanged）。"""
    src_a = _write_source("p0f_ticket.csv", 2)
    src_b = _write_source("p0f_benefit.csv", 2)
    job = server.save_job(
        {
            "name": "p1g_parent_guard",
            "guard": {"type": "file_has_new"},
            "steps": [
                _step("导入 A", "import", _import_cfg(src_a, "p1g_pg_ticket")),
                _step("导入 B", "import", _import_cfg(src_b, "p1g_pg_benefit")),
            ],
        }
    )
    job_id = str(job["id"])
    first = server.run_saved_job(job_id)
    second = server.run_saved_job(job_id)

    print(f"[父作业守卫] 第一次={first['status']!r} 第二次={second['status']!r}")
    print(f"[父作业守卫] 基线={sorted(_guard_rows(job_id))}")

    assert first["status"] == "成功", first["message"]
    assert second["status"] == "跳过", second["message"]
    assert sorted(_guard_rows(job_id)) == [0, 1]


def test_child_level_guard_second_run_skips() -> None:
    """形状二：守卫挂在被引用的子作业上，父作业通过 type=job 调用它。

    子作业的基线必须记在**子作业自己的 job_id** 下（复合键 (job_id, step_index)），
    否则父子作业同 step_index 会互相覆盖基线。
    """
    src_a = _write_source("p0g_ticket.csv", 2)
    child = server.save_job(
        {
            "name": "p1g_child_guard",
            "guard": {"type": "file_has_new"},
            "steps": [_step("子作业导入", "import", _import_cfg(src_a, "p1g_cg_ticket"))],
        }
    )
    parent = server.save_job(
        {
            "name": "p1g_parent_of_child",
            "guard": {},
            "steps": [_step("调用子作业", "job", {"jobId": child["id"]})],
        }
    )
    parent_id, child_id = str(parent["id"]), str(child["id"])

    first = server.run_saved_job(parent_id)
    second = server.run_saved_job(parent_id)

    print(f"[子作业守卫] 第一次={first['status']!r} 第二次={second['status']!r}")
    print(f"[子作业守卫] 子作业基线 step={sorted(_guard_rows(child_id))} 父作业基线={sorted(_guard_rows(parent_id))}")

    assert first["status"] == "成功", first["message"]
    assert second["status"] == "跳过", second["message"]
    assert 0 in _guard_rows(child_id), "子作业的基线必须记在子作业 id 下"
    assert _guard_rows(parent_id) == {}, "父作业 step0 是 job 类型，不产生守卫"


# ---------------------------------------------------------------------------
# 5：坏引用 → 抛错 + 留下 _job_runs 记录（P1-2）
# ---------------------------------------------------------------------------


def _bad_reference_job(name: str, continue_on_error: bool) -> dict[str, object]:
    """guard.type=file_has_new + import 步骤引用不存在的导入任务 → 守卫收集必失败。"""
    return server.save_job(
        {
            "name": name,
            "guard": {"type": "file_has_new"},
            "steps": [
                _step(
                    "坏引用导入",
                    "import",
                    {"importJobId": "ffffffffffffffffffffffffffffffff"},
                    continue_on_error=continue_on_error,
                )
            ],
        }
    )


def test_unknown_import_job_id_raises_and_leaves_failed_run() -> None:
    """守卫评估失败必须留下 _job_runs 失败记录，不能只有一条"运行中"僵尸。"""
    job = _bad_reference_job("p1g_bad_ref", continue_on_error=False)
    job_id = str(job["id"])

    with pytest.raises(ValueError) as excinfo:
        server.run_saved_job(job_id)

    runs = _runs_of(job_id)
    print(f"[坏引用] 异常={excinfo.value}")
    for row in runs:
        print(f"[坏引用] _job_runs row: status={row['status']!r} ended_at={row['ended_at']!r} message={row['message']!r}")

    assert "作业文件更新条件评估失败" in str(excinfo.value)
    assert len(runs) == 1, "守卫评估失败也必须产生一条运行记录"
    assert runs[0]["status"] == "失败"
    assert runs[0]["ended_at"] != "", "不能留下'运行中'的僵尸记录"
    assert "作业文件更新条件评估失败" in runs[0]["message"]


def test_unknown_nested_job_reference_with_continue_on_error_records_run() -> None:
    """P1-2 的 continueOnError 取舍：坏引用仍是**整作业失败**，但一定有运行记录。

    记录这条用例的目的：把这个"代价"钉成可回归的事实，而不是靠口头约定。
    取舍理由见交付报告——守卫评估是作业级前置条件（与日期条件同级），
    发生在任何步骤执行之前，不适用步骤级 continueOnError；
    静默容忍会让"引用被误删"长期潜伏。
    """
    parent = server.save_job(
        {
            "name": "p1g_bad_nested",
            "guard": {},
            "steps": [
                _step("坏子作业引用", "job", {"jobId": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"}, continue_on_error=True),
                _step("后续步骤", "query", {"targetDbType": "sqlite", "sql": "select 1"}),
            ],
        }
    )
    job_id = str(parent["id"])

    with pytest.raises(ValueError) as excinfo:
        server.run_saved_job(job_id)

    runs = _runs_of(job_id)
    print(f"[坏子作业引用+continueOnError] 异常={excinfo.value}")
    print(f"[坏子作业引用+continueOnError] _job_runs 条数={len(runs)} status={[r['status'] for r in runs]}")

    assert "子作业不存在或已删除" in str(excinfo.value)
    assert len(runs) == 1 and runs[0]["status"] == "失败"
    assert runs[0]["ended_at"] != ""


def test_scheduled_path_records_exactly_one_failure() -> None:
    """定时路径不得因 run_schedule_once 的兜底补写而同一次失败出现两条记录（P1-2 收口）。"""
    job = _bad_reference_job("p1g_bad_ref_sched", continue_on_error=False)
    job_id = str(job["id"])
    schedule = server.save_schedule(
        {
            "name": "p1g_sched_bad_ref",
            "jobId": job_id,
            "enabled": True,
            "startAt": "2020-01-01 00:00:00",
            "endAt": "",
            "rule": {"mode": "interval", "amount": 10, "unit": "minutes"},
            "logRetentionDays": 3,
        }
    )

    server.run_schedule_once(str(schedule["id"]))
    runs = _runs_of(job_id)

    print(f"[定时兜底] _job_runs 条数={len(runs)} status={[r['status'] for r in runs]}")
    for row in runs:
        print(f"[定时兜底] message={row['message']!r} ended_at={row['ended_at']!r}")

    assert len(runs) == 1, "同一次定时失败只能有一条运行记录"
    assert runs[0]["status"] == "失败"
    assert "作业文件更新条件评估失败" in runs[0]["message"]
    assert runs[0]["ended_at"] != ""


# ---------------------------------------------------------------------------
# 6：importJobId 两层引用穿透
# ---------------------------------------------------------------------------


def test_import_job_id_two_layer_reference_resolves_final_path() -> None:
    """importJobId 两层穿透：守卫必须拿到最内层导入任务的真实路径。"""
    src = _write_source("p0h_chain.csv", 2)
    inner = server.save_job(
        {"name": "p1g_chain_inner", "guard": {}, "steps": [_step("inner", "import", _import_cfg(src, "p1g_chain"))]}
    )
    mid = server.save_job(
        {"name": "p1g_chain_mid", "guard": {}, "steps": [_step("mid", "import", {"importJobId": inner["id"]})]}
    )
    outer = server.save_job(
        {
            "name": "p1g_chain_outer",
            "guard": {"type": "file_has_new"},
            "steps": [_step("outer", "import", {"importJobId": mid["id"]})],
        }
    )
    outer_id = str(outer["id"])

    guards = server._guard_summary(_load_job(outer_id))
    print(f"[两层引用] guards={ {k: v['source'] for k, v in guards.items()} }")

    assert list(guards) == [(outer_id, 0)]
    assert guards[(outer_id, 0)]["source"] == str(src), "守卫必须穿透两层引用拿到最终路径"

    first = server.run_saved_job(outer_id)
    second = server.run_saved_job(outer_id)
    print(f"[两层引用] 第一次={first['status']!r} 第二次={second['status']!r} 表行数={_table_rows('p1g_chain')}")

    assert first["status"] == "成功" and _table_rows("p1g_chain") == 2
    assert second["status"] == "跳过", second["message"]


# ---------------------------------------------------------------------------
# 7：同一秒内改两次且大小不变（P1-1）
# ---------------------------------------------------------------------------


def test_same_second_same_size_update_is_detected() -> None:
    """P1-1：基线所在的那一秒里又改了一次、大小还不变 → 必须识别为"有更新"。

    确定性构造（不是"碰运气撞同一秒"）：用 os.utime 把三次写入固定在**同一个整数秒**内、
    只让纳秒部分不同——这正是外部系统"批量脚本 / 下载即覆盖"产生的形态，
    也是 QA 复现 int(st_mtime) 漏判的形态：
        基线   int(mtime)=T   size=53
        第二次 int(mtime)=T   size=53   ← 秒级算法看不出任何变化

    这里同时打印"秒级会看到的"和"纳秒级实际看到的"，用于对照。
    """
    src = _SOURCES / "p0i_same_second.csv"
    _SOURCES.mkdir(parents=True, exist_ok=True)
    table = "p1g_same_second"
    job = server.save_job(
        {
            "name": "p1g_same_second",
            "guard": {},
            "steps": [_step("导入", "import", _import_cfg(src, table, guarded=True))],
        }
    )
    job_id = str(job["id"])

    t_second = 1_760_000_000  # 固定整数秒，便于把三次写入压在"同一秒"里
    ns_base = t_second * 10**9 + 900_000_000
    bom = CSV_HEADER.encode("utf-8-sig")
    payload_base = bom + b"n0,111\nn1,1\n"
    payload_first = bom + b"n0,900\nn1,1\n"
    payload_second = bom + b"n0,777\nn1,1\n"

    # 基线：2 行
    src.write_bytes(payload_base)
    os.utime(src, ns=(ns_base, ns_base))
    st_base = src.stat()
    assert server.run_saved_job(job_id)["status"] == "成功"

    # 同一整数秒内改两次，且字节长度不变（只换了一个数字）
    src.write_bytes(payload_first)
    os.utime(src, ns=(ns_base + 10_000_000, ns_base + 10_000_000))  # T + 0.910s
    st_first = src.stat()
    src.write_bytes(payload_second)
    os.utime(src, ns=(ns_base + 90_000_000, ns_base + 90_000_000))  # T + 0.990s
    st_second = src.stat()

    print(f"[同秒改两次] size: 基线={st_base.st_size} 第一次={st_first.st_size} 第二次={st_second.st_size}")
    print(f"[同秒改两次] int(st_mtime): 基线={int(st_base.st_mtime)} 第一次={int(st_first.st_mtime)} 第二次={int(st_second.st_mtime)}")
    print(f"[同秒改两次] st_mtime_ns:   基线={st_base.st_mtime_ns} 第一次={st_first.st_mtime_ns} 第二次={st_second.st_mtime_ns}")

    run = server.run_saved_job(job_id)
    print(f"[同秒改两次] status={run['status']!r} amounts={_table_amounts(table)}")

    # 前置条件：这正是"秒级算法必然漏判"的形态
    assert st_base.st_size == st_second.st_size, "构造必须保持大小不变"
    assert int(st_base.st_mtime) == int(st_second.st_mtime), "构造必须落在同一个整数秒内"
    # 真实结论
    assert run["status"] == "成功", f"同一秒内的更新被漏判：{run['message']}"
    assert _table_amounts(table) == [1, 777], "第二次写入的数据必须真的进库"
    assert st_second.st_mtime_ns > st_base.st_mtime_ns, "纳秒级 mtime 必须给出可分辨的变化"


def test_p1_1_natural_writes_and_residual_tick_boundary() -> None:
    """P1-1 补充：钉住"纳秒级"到底保证什么，以及实测剩下的残留边界。

    实测（本机 Windows/NTFS）：两次紧邻写入**可能拿到完全相同的 st_mtime_ns**——
    文件系统在同一个系统计时器 tick（约 15.6 ms）内不推进 last-write-time。
    这是文件系统行为，不是算法能修的；本用例把它如实记录下来，
    避免以后有人误以为"纳秒级 = 绝对可靠"。

    修复的真实价值：把可分辨窗口从"最多 999 ms（秒级截断）"压到"约 15.6 ms"。
    真实作业流程里基线是在一次完整作业运行末尾读到的（sqlite 事务 + 文件解析，
    远超 15.6 ms），两次外部写入之间必然夹着一次作业运行 → 必然跨 tick。
    """
    bom = CSV_HEADER.encode("utf-8-sig")
    first_payload = bom + b"n0,111\nn1,1\n"
    second_payload = bom + b"n0,222\nn1,1\n"
    third_payload = bom + b"n0,333\nn1,1\n"
    assert len(first_payload) == len(second_payload) == len(third_payload), "构造前提：等长"

    src = _SOURCES / "p0j_natural.csv"
    _SOURCES.mkdir(parents=True, exist_ok=True)
    src.write_bytes(first_payload)
    before = src.stat()
    src.write_bytes(second_payload)
    after = src.stat()

    same_tick = after.st_mtime_ns == before.st_mtime_ns
    print(f"[残留边界] size {before.st_size} -> {after.st_size}；"
          f"int(st_mtime) {int(before.st_mtime)} -> {int(after.st_mtime)}；"
          f"st_mtime_ns {before.st_mtime_ns} -> {after.st_mtime_ns}；同一个 tick={same_tick}")

    # 两种算法在这组数据下的可分辨性对照（前置事实）
    assert before.st_size == after.st_size
    assert int(before.st_mtime) == int(after.st_mtime), "秒级算法在这组数据下必然漏判"
    assert after.st_mtime_ns >= before.st_mtime_ns, "mtime_ns 只会前进或不变"

    # 纳秒级算法的确定性保证：时间戳一旦推进（跨 tick 的真实形态），必须被区分
    os.utime(src, ns=(after.st_mtime_ns + 1, after.st_mtime_ns + 1))
    fp_ns_plus_1 = server._file_fingerprint(str(src))
    os.utime(src, ns=(after.st_mtime_ns + 1_000_000, after.st_mtime_ns + 1_000_000))
    fp_ns_plus_1ms = server._file_fingerprint(str(src))
    print(f"[残留边界] +1ns 指纹={fp_ns_plus_1[:12]} +1ms 指纹={fp_ns_plus_1ms[:12]}")
    assert fp_ns_plus_1ms != fp_ns_plus_1, "纳秒级差异必须被区分（这不是秒级算法能做到的）"

    # 同 tick 时漏判是文件系统不推进时间戳导致的，如实记录而不是假装没有
    if same_tick:
        print("[残留边界] 本次观测到同一个 tick 内 st_mtime_ns 未推进 → 残留窗口 ≈ 15.6 ms")
    src.write_bytes(third_payload)  # 让文件回到"内容已更新"的确定状态，不留给后续用例


# ---------------------------------------------------------------------------
# 8：_file_fingerprint 边界
# ---------------------------------------------------------------------------


def test_file_fingerprint_boundaries_file_dir_empty_and_move() -> None:
    """单文件 / 目录 / 目录内增删文件 / 文件→目录 / 空目录 的行为边界。"""
    single = _write_source("p0k_single.csv", 2)
    fp_file_a = server._file_fingerprint(str(single))
    assert fp_file_a == server._file_fingerprint(str(single)), "同一文件两次指纹必须一致"

    folder = Path(_tmp) / "boundary_dir"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "a.csv").write_bytes(_csv_text(1).encode("utf-8-sig"))
    fp_dir_1 = server._file_fingerprint(str(folder))
    assert fp_dir_1 == server._file_fingerprint(str(folder)), "同一目录两次指纹必须一致"

    (folder / "b.csv").write_bytes(_csv_text(1).encode("utf-8-sig"))
    fp_dir_2 = server._file_fingerprint(str(folder))
    assert fp_dir_2 != fp_dir_1, "目录内新增文件必须改变指纹"

    # 文件 → 目录（同名）：不崩、且指纹必须变化
    moved = Path(_tmp) / "boundary_moved"
    moved.write_bytes(_csv_text(2).encode("utf-8-sig"))
    fp_moved_file = server._file_fingerprint(str(moved))
    moved.unlink()
    moved.mkdir()
    (moved / "x.csv").write_bytes(_csv_text(2).encode("utf-8-sig"))
    fp_moved_dir = server._file_fingerprint(str(moved))

    empty = Path(_tmp) / "boundary_empty"
    empty.mkdir(exist_ok=True)

    print(f"[边界] 文件={fp_file_a[:12]} 目录(1文件)={fp_dir_1[:12]} 目录(2文件)={fp_dir_2[:12]}")
    print(f"[边界] 文件→目录 {fp_moved_file[:12]} -> {fp_moved_dir[:12]}")

    assert fp_moved_dir != fp_moved_file, "同名路径从文件变成目录后指纹必须变化"
    with pytest.raises(ValueError, match="目录中没有可导入的文件"):
        server._file_fingerprint(str(empty))
    with pytest.raises(ValueError, match="路径不存在"):
        server._file_fingerprint(str(Path(_tmp) / "boundary_never_exists" / "x.csv"))


def test_fingerprint_server_default_and_user_directory() -> None:
    """服务端默认目录（EXPORTS_DIR）与用户自选目录（含中文路径）都能正常取指纹。

    同时钉住一条容易被误当成 bug 的行为：``collect_local_files`` 对"单文件"与
    "只含这一个文件的目录"返回的是同一个文件集合，因此二者指纹**相同**（按设计如此，
    守卫关心的是"这批文件变没变"，不是"用户填的是文件还是目录"）。

    注意（本用例曾经不稳）：指纹是 大小 + mtime_ns，两个目录里若写**同名同大小**的文件，
    而两次写入又落在同一个系统计时器 tick（约 15.6 ms）内，mtime_ns 会完全一致 →
    两个"不同目录"的指纹相同。那种情况下断言``fp_server != fp_user``会随机失败，
    而这是文件系统的行为、不是缺陷。所以这里让两个目录的源文件**行数不同（大小不同）**，
    把"不同目录指纹不同"这条断言变成由字节数决定的确定性事实，不依赖计时器精度。
    """
    server_dir = Path(server.EXPORTS) / "p1g_boundary_server"
    user_dir = Path(_tmp) / "boundary_user_home" / "数据导入"
    for folder, rows in ((server_dir, 2), (user_dir, 3)):
        folder.mkdir(parents=True, exist_ok=True)
        # 行数不同 → 字节数不同 → 指纹必然不同（不依赖 mtime 精度）
        (folder / "同源.csv").write_bytes(_csv_text(rows).encode("utf-8-sig"))

    fp_server = server._file_fingerprint(str(server_dir))
    fp_user = server._file_fingerprint(str(user_dir))
    fp_server_file = server._file_fingerprint(str(server_dir / "同源.csv"))
    print(f"[边界] 服务端默认目录={fp_server[:12]} 其内文件={fp_server_file[:12]} 用户目录={fp_user[:12]}")

    assert fp_server == server._file_fingerprint(str(server_dir)), "同一目录两次指纹必须一致"
    assert fp_user == server._file_fingerprint(str(user_dir)), "同一目录两次指纹必须一致"
    assert fp_server != fp_user, "不同目录（内容大小不同）指纹不应相同"
    assert fp_server_file == fp_server, "单文件与只含该文件的目录是同一个文件集合 → 指纹相同（设计如此）"

    # 目录里再加一个文件 → 必须变
    (server_dir / "第二个.csv").write_bytes(_csv_text(1).encode("utf-8-sig"))
    fp_server_two = server._file_fingerprint(str(server_dir))
    assert fp_server_two != fp_server
    assert fp_server_two != fp_user, "目录里 2 个文件时不能退化成与单文件集合相同"


# ---------------------------------------------------------------------------
# 10：单条守卫不可达不阻断其他守卫（P2-1）
# ---------------------------------------------------------------------------


def test_unreachable_guard_does_not_block_other_guards() -> None:
    """第 1 条守卫的路径不可达时，第 2 条守卫仍要被评估、且基线写入不受阻断（P2-1）。

    构造：父作业 step0 调用一个**被 date_match 永久跳过**的子作业，子作业内部那个
    导入步骤的源路径不存在 → 该守卫必然评估失败；step1 是路径正常的守卫。
    上一轮 try 包住整个 for，第一条抛错就 break 掉循环，step1 拿不到指纹、写不了基线，
    而且 guard_reason 被清空（静默）——正是本用例要钉住的反例。
    """
    good_src = _write_source("p0l_good.csv", 2)
    missing = Path(_tmp) / "boundary_never_exists" / "nope.csv"

    child = server.save_job(
        {
            "name": "p1g_child_unreachable",
            "guard": {"type": "date_match", "mode": "dates", "values": ["1999-01-01"]},
            "steps": [_step("不可达导入", "import", _import_cfg(missing, "p1g_never", guarded=True))],
        }
    )
    parent = server.save_job(
        {
            "name": "p1g_unreachable_parent",
            "guard": {},
            "steps": [
                _step("调用（永久跳过）", "job", {"jobId": child["id"]}),
                _step("正常导入", "import", _import_cfg(good_src, "p1g_unreach_good", guarded=True)),
            ],
        }
    )
    parent_id, child_id = str(parent["id"]), str(child["id"])

    run = server.run_saved_job(parent_id)
    parent_rows = _guard_rows(parent_id)
    child_rows = _guard_rows(child_id)

    print(f"[守卫逐条] status={run['status']!r}")
    print(f"[守卫逐条] 父作业基线 keys={sorted(parent_rows)}（step1 必须被写入）")
    print(f"[守卫逐条] 子作业基线 keys={sorted(child_rows)}（不可达的那条不写）")
    print(f"[守卫逐条] message={run['message']}")

    assert run["status"] == "成功", run["message"]
    assert "无法评估" in run["message"], "评估失败必须如实写进运行日志，不能静默"
    assert str(missing) in run["message"]
    assert 1 in parent_rows, "第 1 条守卫失败不得阻断第 2 条守卫的基线写入"
    assert parent_rows[1] == server._file_fingerprint(str(good_src))
    assert child_rows == {}, "评估失败的守卫不写基线，也不影响别人"
    assert _table_rows("p1g_unreach_good") == 2


# ---------------------------------------------------------------------------
# 11：步骤级跳过（P0-1 粒度取舍）——作业执行 ≠ 每个步骤都重跑
# ---------------------------------------------------------------------------


def _append_cfg(path: Path, table: str) -> dict[str, str]:
    """append 模式的导入步骤配置（复刻生产 6b990e83 → 小票导入：append 且无 matchKey）。"""
    cfg = _import_cfg(path, table, guarded=True)
    cfg["importMode"] = "append"
    return cfg


def _steps_of(run_id: str) -> list[sqlite3.Row]:
    with server.connect_db() as conn:
        return conn.execute(
            "select * from _job_run_steps where run_id = ? order by step_index", (run_id,)
        ).fetchall()


def test_step_level_skip_avoids_duplicate_append_for_unchanged_source() -> None:
    """没变的源文件不得被重复 append —— 整作业粒度会把整份快照再追加一遍。

    为什么必须有这条：生产作业 6b990e83 的两个导入步骤各自对应一个独立源文件，
    "小票数据源导入"实测 importMode=append、mapping 里 matchKey 全为空（源文件是整份快照，
    append 即"再插入一遍全部行"）。整作业粒度下，只改了权益文件也会把小票再追加一次 →
    目标表 会员小票表 每轮多一份重复行。用户诉求是"有新增就执行导入"，不是"把所有步骤重跑一遍"。
    """
    src_a = _write_source("p0m_ticket.csv", 3)
    src_b = _write_source("p0m_benefit.csv", 3)
    job = server.save_job(
        {
            "name": "p1g_step_skip_append",
            "guard": {},
            "steps": [
                _step("小票导入", "import", _append_cfg(src_a, "p1g_app_ticket")),
                _step("权益导入", "import", _append_cfg(src_b, "p1g_app_benefit")),
            ],
        }
    )
    job_id = str(job["id"])

    first = server.run_saved_job(job_id)
    assert first["status"] == "成功", first["message"]
    assert _table_rows("p1g_app_ticket") == 3
    assert _table_rows("p1g_app_benefit") == 3

    # 只改【权益】文件：作业必须执行（any(changed)→执行），但小票步骤不得重跑
    src_b.write_bytes(_csv_text(4).encode("utf-8-sig"))
    second = server.run_saved_job(job_id)

    ticket_rows = _table_rows("p1g_app_ticket")
    benefit_rows = _table_rows("p1g_app_benefit")
    print(f"[步骤级跳过] status={second['status']!r}")
    print(f"[步骤级跳过] 小票表（append，源文件未变）3 -> {ticket_rows}；"
          f"权益表（append，源文件改为 4 行）3 -> {benefit_rows}（3 + 4 = 7）")
    print(f"[步骤级跳过] message={second['message']}")

    assert second["status"] == "成功", second["message"]
    assert benefit_rows == 3 + 4, "变化的源文件必须真的进库（append = 原 3 行 + 新导入 4 行）"
    assert ticket_rows == 3, "源文件未变的步骤被重跑 → append 出重复行（整作业粒度的代价）"
    assert "未重跑" in second["message"], "运行日志要如实交代哪些步骤没重跑"


def test_step_level_skip_visible_in_step_log() -> None:
    """步骤级跳过必须留下 _job_run_steps 记录（status=跳过 + 原因），不能静默消失。"""
    src_a = _write_source("p0o_ticket.csv", 2)
    src_b = _write_source("p0o_benefit.csv", 2)
    job = server.save_job(
        {
            "name": "p1g_step_log",
            "guard": {},
            "steps": [
                _step("小票导入", "import", _import_cfg(src_a, "p1g_log_ticket", guarded=True)),
                _step("权益导入", "import", _import_cfg(src_b, "p1g_log_benefit", guarded=True)),
            ],
        }
    )
    job_id = str(job["id"])
    server.run_saved_job(job_id)

    src_b.write_bytes(_csv_text(5).encode("utf-8-sig"))
    second = server.run_saved_job(job_id)
    steps = _steps_of(str(second["id"]))

    for row in steps:
        print(f"[步骤日志] step{row['step_index']} {row['step_name']!r} "
              f"status={row['status']!r} ended={row['ended_at']!r} message={row['message']!r}")

    skipped = [row for row in steps if str(row["status"]) == "跳过"]
    assert len(skipped) == 1, "只有那条源文件未变的步骤应被标记为跳过"
    assert str(skipped[0]["step_name"]) == "小票导入"
    assert "源文件无更新" in str(skipped[0]["message"])
    assert str(skipped[0]["ended_at"]) != "", "跳过也要落 ended_at，不能留'运行中'僵尸步骤"
    assert all(str(row["status"]) != "运行中" for row in steps)
    assert _table_rows("p1g_log_ticket") == 2
    assert _table_rows("p1g_log_benefit") == 5


def test_step_level_skip_respects_job_id_scope() -> None:
    """步骤级跳过按 (job_id, step_index) 作用域：子作业变化不得影响父作业自己的步骤判定。

    为什么：嵌套子作业与父作业可能有相同的 step_index（父 step0、子 step0）。
    若只用 step_index 做键，父子会互相覆盖 —— 基线比对与步骤级跳过都会串味。
    """
    parent_src = _write_source("p0p_parent.csv", 2)
    child_src = _write_source("p0p_child.csv", 2)
    child = server.save_job(
        {
            "name": "p1g_scope_child",
            "guard": {},
            "steps": [_step("子导入", "import", _import_cfg(child_src, "p1g_scope_child_t", guarded=True))],
        }
    )
    parent = server.save_job(
        {
            "name": "p1g_scope_parent",
            "guard": {},
            "steps": [
                _step("父导入", "import", _import_cfg(parent_src, "p1g_scope_parent_t", guarded=True)),
                _step("调用子作业", "job", {"jobId": child["id"]}),
            ],
        }
    )
    parent_id, child_id = str(parent["id"]), str(child["id"])

    first = server.run_saved_job(parent_id)
    assert first["status"] == "成功", first["message"]
    assert _table_rows("p1g_scope_parent_t") == 2
    assert _table_rows("p1g_scope_child_t") == 2

    # 只改子作业的源文件
    child_src.write_bytes(_csv_text(6).encode("utf-8-sig"))
    second = server.run_saved_job(parent_id)

    print(f"[作用域] status={second['status']!r} 父导入步骤={second['message'].count('父导入')}")
    print(f"[作用域] 父表 2 -> {_table_rows('p1g_scope_parent_t')}（源文件未变，步骤应跳过）")
    print(f"[作用域] 子表 2 -> {_table_rows('p1g_scope_child_t')}（源文件变了，子作业必须执行）")

    assert second["status"] == "成功", second["message"]
    assert _table_rows("p1g_scope_child_t") == 6, "子作业的源文件变了，必须真的执行"
    assert _table_rows("p1g_scope_parent_t") == 2, "父作业自己的源文件没变，不得被重跑"
    assert 0 in _guard_rows(child_id), "子作业基线写在子作业 id 下（子作业只有 1 个步骤 → step_index=0）"
    assert 0 in _guard_rows(parent_id), "父作业基线写在父作业 id 下"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "-s"]))
