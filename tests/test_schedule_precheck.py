"""定时任务「执行前预检」专项测试。

钉住的语义（用户诉求原文）：
    「每轮定时任务执行前 5 分钟做一次检查，有新增就执行，没有新增就跳过整个作业。」
    → 到期**之前**做一次检查；检查结论决定本轮"执行 / 整轮跳过"；不是执行时才检查。

本文件覆盖：
 1. 无新增 → 整轮跳过：作业一次都没被执行（探针计数=0），且 _job_runs 留有 status="跳过"
 2. 有新增 → 正常执行（探针计数=1，新数据真的进库）
 3. 两个源文件只变一个 → has_new=1（量词方向，写反会导致作业静默卡死）
 4. 预检后、执行前文件又变 → 双校验取消跳过，改为执行，并在运行日志写明原因
 5. 预检不写回 _job_file_guards（基线指纹必须逐字节不变）
 6. 无预检记录 / 预检抛异常 / precheck_minutes=0 → 三种情况都保守执行
 7. 同一个 due_at 重复 dispatch 只预检一次（幂等）
 8. 预检窗口外（due - now > precheck_minutes）不触发预检；已过点的也不预检
 9. precheck_minutes 的持久化与缺省/负数归一

隔离：数据/上传/导出落 %TEMP% 临时目录，绝不触碰真实 data/、uploads/、exports/，
目标库用 sqlite（= 隔离库自身），不连任何外部数据库。跑法：

    ./.venv/Scripts/python.exe -m pytest tests/test_schedule_precheck.py -q
"""

from __future__ import annotations

import atexit
import datetime as dt
import json
import os
import shutil
import sqlite3
import tempfile
import time
import uuid
from pathlib import Path

import pytest

# 隔离测试环境：数据/上传/导出落临时目录，绝不触碰真实 data/ uploads/ exports/
_tmp = tempfile.mkdtemp(prefix="dc_precheck_")
_SOURCES = Path(_tmp) / "sources"
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
os.environ["UPLOADS_DIR"] = str(Path(_tmp) / "uploads")
os.environ["EXPORTS_DIR"] = str(Path(_tmp) / "exports")

import server

# sqlite3 连接作为 with 语句使用时只提交事务、不 close，句柄会一直占着临时库文件；
# Windows 上这会挡住隔离目录的删除，把 %TEMP%/dc_precheck_* 越攒越多。这里记录句柄，
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
    """生成固定结构的两列 CSV：行数可控，且行数不同 → 字节长度不同（指纹必变）。"""
    return CSV_HEADER + "".join(f"n{i},{i + amount_offset}\n" for i in range(rows))


def _write_source(name: str, rows: int, amount_offset: int = 0) -> Path:
    """写源文件；用 write_bytes 避免 Windows 的 \\n→\\r\\n 转换，保证字节长度可控。"""
    _SOURCES.mkdir(parents=True, exist_ok=True)
    path = _SOURCES / name
    path.write_bytes(_csv_text(rows, amount_offset).encode("utf-8-sig"))
    return path


def _import_cfg(path: Path, table: str, guarded: bool = True) -> dict[str, str]:
    """导入步骤配置：目标库用 sqlite（= 隔离库自身），不碰任何真实库。"""
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


def _step(name: str, step_type: str, config: dict[str, object]) -> dict[str, object]:
    return {"name": name, "type": step_type, "enabled": True, "continueOnError": False, "config": config}


def _two_source_job(tag: str, src_a: Path, src_b: Path) -> dict[str, object]:
    """两条独立源文件守卫的作业：两个 import 步骤各自 skipIfFileUnchanged=true。"""
    return server.save_job(
        {
            "name": f"pc_job_{tag}",
            "guard": {},
            "steps": [
                _step("导入 A", "import", _import_cfg(src_a, f"pc_t_{tag}_a")),
                _step("导入 B", "import", _import_cfg(src_b, f"pc_t_{tag}_b")),
            ],
        }
    )


def _table_rows(table: str) -> int:
    with server.connect_db() as conn:
        row = conn.execute("select name from sqlite_master where type = 'table' and name = ?", (table,)).fetchone()
        if not row:
            return -1
        return int(conn.execute(f"select count(*) from {table}").fetchone()[0])


def _guard_rows(job_id: str) -> dict[int, str]:
    """作业在 _job_file_guards 里的基线：{step_index: fingerprint}。"""
    with server.connect_db() as conn:
        return {
            int(row["step_index"]): str(row["fingerprint"])
            for row in conn.execute(
                "select step_index, fingerprint from _job_file_guards where job_id = ?", (job_id,)
            ).fetchall()
        }


def _precheck_rows(schedule_id: str = "") -> list[sqlite3.Row]:
    with server.connect_db() as conn:
        if schedule_id:
            return conn.execute(
                "select * from _schedule_prechecks where schedule_id = ? order by due_at", (schedule_id,)
            ).fetchall()
        return conn.execute("select * from _schedule_prechecks order by schedule_id, due_at").fetchall()


def _runs_of_schedule(schedule_id: str) -> list[sqlite3.Row]:
    with server.connect_db() as conn:
        return conn.execute(
            "select * from _job_runs where schedule_id = ? order by rowid", (schedule_id,)
        ).fetchall()


def _steps_of_runs(schedule_id: str) -> list[sqlite3.Row]:
    with server.connect_db() as conn:
        return conn.execute(
            "select s.* from _job_run_steps s join _job_runs r on r.id = s.run_id where r.schedule_id = ? order by s.rowid",
            (schedule_id,),
        ).fetchall()


def _schedule_status(schedule_id: str) -> tuple[str, str]:
    """(last_status, next_run_at)。"""
    with server.connect_db() as conn:
        row = conn.execute("select last_status, next_run_at from _schedules where id = ?", (schedule_id,)).fetchone()
    assert row is not None
    return str(row["last_status"]), str(row["next_run_at"])


def _in_minutes(minutes: float) -> str:
    """相对 app_now() 的到期时间文本（与 dispatch_prechecks / now_text 同一时钟）。"""
    return (server.app_now() + dt.timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


def _set_next_run(schedule_id: str, due_at: str) -> None:
    with server.connect_db() as conn:
        conn.execute("update _schedules set next_run_at = ?, running = 0 where id = ?", (due_at, schedule_id))


def _make_schedule(job_id: str, minutes: int = 5, due_at: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": f"pc_{uuid.uuid4().hex[:8]}",
        "jobId": job_id,
        "enabled": True,
        "startAt": (server.app_now() - dt.timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S"),
        "endAt": "2099-12-31 23:59:59",
        "rule": {"mode": "interval", "amount": 10, "unit": "minutes"},
        "logRetentionDays": 3,
        "precheckMinutes": minutes,
    }
    schedule = server.save_schedule(payload)
    if due_at is not None:
        _set_next_run(str(schedule["id"]), due_at)
        schedule["nextRunAt"] = due_at
    return schedule


def _wait_until(predicate, timeout: float = 10.0, interval: float = 0.02) -> bool:
    """轮询等待条件成立（预检/后台线程是异步的，不能靠 sleep 猜）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class _Probe:
    """统计 run_saved_job 的调用：跳过路径必须 0 次，执行路径必须 1 次。

    为什么不用"目标表行数"判断作业跑没跑：rebuild 模式下重跑一遍行数也不变，
    看不出差别；直接数调用最硬。
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[str] = []
        original = server.run_saved_job

        def fake(job_id: str, schedule_id: str = "", visited: set[str] | None = None):
            self.calls.append(str(job_id))
            return original(job_id, schedule_id, visited)

        monkeypatch.setattr(server, "run_saved_job", fake)


class _PrecheckCounter:
    """统计 precheck_schedule 的真实执行次数（dispatch_prechecks 走线程，必须能数）。

    为什么断言要用 calls_for(自己)：dispatch_prechecks 是**全表扫描**，同库里还有
    test_jobs_schedule.py 造的 qa_schedule（10 秒一轮、enabled=1）也会进预检窗口；
    全量跑 pytest 时它会一起被预检，只数总数会假红。
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[str] = []
        original = server.precheck_schedule

        def fake(schedule_id: str):
            self.calls.append(str(schedule_id))
            return original(schedule_id)

        monkeypatch.setattr(server, "precheck_schedule", fake)

    def calls_for(self, schedule_id: str) -> list[str]:
        return [item for item in self.calls if item == schedule_id]


@pytest.fixture(autouse=True)
def _clean_slates() -> None:
    """每个用例前清掉本文件造的作业/计划/预检记录，保证用例之间互不污染。

    只删 name 以 pc_ 开头的自造数据：同库里还有 test_jobs_schedule.py 的 qa_* 数据，
    误删会让别人的缓存断言变成假红/假绿。
    """
    with server.connect_db() as conn:
        sched_ids = [r["id"] for r in conn.execute("select id from _schedules where name like 'pc_%'").fetchall()]
        job_ids = [r["id"] for r in conn.execute("select id from _jobs where name like 'pc_job_%'").fetchall()]
        conn.execute("delete from _schedule_prechecks")
        for schedule_id in sched_ids:
            conn.execute("delete from _job_runs where schedule_id = ?", (schedule_id,))
            conn.execute("delete from _schedules where id = ?", (schedule_id,))
        for job_id in job_ids:
            conn.execute("delete from _job_runs where job_id = ?", (job_id,))
            conn.execute("delete from _job_file_guards where job_id = ?", (job_id,))
            conn.execute("delete from _jobs where id = ?", (job_id,))
        tables = [
            r["name"]
            for r in conn.execute("select name from sqlite_master where type = 'table' and name like 'pc_t_%'").fetchall()
        ]
        for table in tables:
            conn.execute(f"drop table if exists {table}")


# ---------------------------------------------------------------------------
# 1. 无新增 → 整轮跳过
# ---------------------------------------------------------------------------


def test_no_update_skips_whole_round_and_logs_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    src_a = _write_source("pc_skip_a.csv", 2)
    src_b = _write_source("pc_skip_b.csv", 2)
    job = _two_source_job("skip", src_a, src_b)
    job_id = str(job["id"])

    # 首次运行：无基线 → 执行并写入基线
    first = server.run_saved_job(job_id)
    assert first["status"] == "成功"
    assert _guard_rows(job_id), "首次运行必须写入守卫基线"

    schedule = _make_schedule(job_id, minutes=5, due_at=_in_minutes(3))
    schedule_id = str(schedule["id"])
    precheck = server.precheck_schedule(schedule_id)
    assert precheck["hasNew"] == 0, f"源文件都没动，预检必须判无新增：{precheck}"

    probe = _Probe(monkeypatch)
    server.run_schedule_once(schedule_id)

    assert probe.calls == [], f"无新增时整轮跳过，作业一次都不该被执行：{probe.calls}"
    runs = _runs_of_schedule(schedule_id)
    assert len(runs) == 1, f"跳过必须留一条可查的运行记录：{[dict(r) for r in runs]}"
    assert runs[0]["status"] == "跳过"
    assert "预检（" in str(runs[0]["message"]) and "本轮跳过" in str(runs[0]["message"]), runs[0]["message"]
    assert _steps_of_runs(schedule_id) == [], "跳过的轮次不该有任何步骤记录"
    assert _precheck_rows(schedule_id) == [], "本轮预检记录用完后必须删除，避免堆积"
    last_status, next_run = _schedule_status(schedule_id)
    assert last_status == "跳过"
    assert next_run, "跳过后仍要算出下一轮时间，否则任务会停摆"


def test_no_update_skip_does_not_touch_target_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    """跳过的轮次里源文件完全没被读进目标表：目标表行数保持基线那一轮的结果。"""
    src_a = _write_source("pc_keep_a.csv", 2)
    src_b = _write_source("pc_keep_b.csv", 2)
    job = _two_source_job("keep", src_a, src_b)
    job_id = str(job["id"])
    server.run_saved_job(job_id)
    baseline_rows = _table_rows("pc_t_keep_a")

    schedule = _make_schedule(job_id, minutes=5, due_at=_in_minutes(3))
    assert server.precheck_schedule(str(schedule["id"]))["hasNew"] == 0
    probe = _Probe(monkeypatch)
    server.run_schedule_once(str(schedule["id"]))

    assert probe.calls == []
    assert _table_rows("pc_t_keep_a") == baseline_rows


# ---------------------------------------------------------------------------
# 2. 有新增 → 正常执行
# ---------------------------------------------------------------------------


def test_has_update_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    src_a = _write_source("pc_exec_a.csv", 2)
    src_b = _write_source("pc_exec_b.csv", 2)
    job = _two_source_job("exec", src_a, src_b)
    job_id = str(job["id"])
    server.run_saved_job(job_id)

    _write_source("pc_exec_a.csv", 5)  # 行数变化 → 大小变化 → 指纹必变
    schedule = _make_schedule(job_id, minutes=5, due_at=_in_minutes(3))
    schedule_id = str(schedule["id"])
    precheck = server.precheck_schedule(schedule_id)
    assert precheck["hasNew"] == 1, f"A 已更新，预检必须判有新增：{precheck}"

    probe = _Probe(monkeypatch)
    server.run_schedule_once(schedule_id)

    assert probe.calls == [job_id], f"有新增必须正常执行：{probe.calls}"
    assert _table_rows("pc_t_exec_a") == 5, "新数据必须真的进库"
    runs = _runs_of_schedule(schedule_id)
    assert runs and runs[-1]["status"] == "成功"
    assert _precheck_rows(schedule_id) == [], "执行完也要清掉本轮预检记录"


# ---------------------------------------------------------------------------
# 3. 两个源文件只变一个 → has_new=1（量词方向）
# ---------------------------------------------------------------------------


def test_only_one_of_two_sources_changed_means_has_new() -> None:
    src_a = _write_source("pc_one_a.csv", 2)
    src_b = _write_source("pc_one_b.csv", 2)
    job = _two_source_job("one", src_a, src_b)
    job_id = str(job["id"])
    server.run_saved_job(job_id)

    _write_source("pc_one_a.csv", 4)  # 只改 A，B 保持不动
    schedule = _make_schedule(job_id, minutes=5, due_at=_in_minutes(3))
    result = server.precheck_schedule(str(schedule["id"]))

    assert result["hasNew"] == 1, "两个源只更新一个也必须判【有新增】（量词是 any 不是 all）"
    items = {str(item["source"]): bool(item["changed"]) for item in result["items"]}
    assert items == {str(src_a): True, str(src_b): False}, f"逐条守卫的判定也要正确：{result['items']}"


def test_only_the_other_source_changed_also_means_has_new() -> None:
    """反向：只改 B 同样必须有新增（防止只对第一条守卫生效的实现蒙混过关）。"""
    src_a = _write_source("pc_two_a.csv", 2)
    src_b = _write_source("pc_two_b.csv", 2)
    job = _two_source_job("two", src_a, src_b)
    job_id = str(job["id"])
    server.run_saved_job(job_id)

    _write_source("pc_two_b.csv", 4)
    schedule = _make_schedule(job_id, minutes=5, due_at=_in_minutes(3))
    result = server.precheck_schedule(str(schedule["id"]))

    assert result["hasNew"] == 1
    items = {str(item["source"]): bool(item["changed"]) for item in result["items"]}
    assert items == {str(src_a): False, str(src_b): True}


# ---------------------------------------------------------------------------
# 4. 预检后、执行前文件又变 → 双校验取消跳过
# ---------------------------------------------------------------------------


def test_file_changed_after_precheck_cancels_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    src_a = _write_source("pc_late_a.csv", 2)
    src_b = _write_source("pc_late_b.csv", 2)
    job = _two_source_job("late", src_a, src_b)
    job_id = str(job["id"])
    server.run_saved_job(job_id)

    schedule = _make_schedule(job_id, minutes=5, due_at=_in_minutes(3))
    schedule_id = str(schedule["id"])
    assert server.precheck_schedule(schedule_id)["hasNew"] == 0

    # 预检之后、执行之前：外部系统又把 A 更新了
    _write_source("pc_late_a.csv", 7)

    probe = _Probe(monkeypatch)
    server.run_schedule_once(schedule_id)

    assert probe.calls == [job_id], f"预检后又变了必须取消跳过、正常执行：{probe.calls}"
    assert _table_rows("pc_t_late_a") == 7, "这一轮的新数据不能等到下一轮才进库"
    runs = _runs_of_schedule(schedule_id)
    assert runs and runs[-1]["status"] == "成功"
    assert "取消跳过" in str(runs[-1]["message"]), f"取消跳过的原因必须写在运行日志里：{runs[-1]['message']}"


def test_detail_fingerprint_of_precheck_is_recorded() -> None:
    """detail_json 必须存得住逐条指纹，双校验才有依据（顺带钉住落库字段）。"""
    src_a = _write_source("pc_detail_a.csv", 2)
    src_b = _write_source("pc_detail_b.csv", 2)
    job = _two_source_job("detail", src_a, src_b)
    job_id = str(job["id"])
    server.run_saved_job(job_id)

    schedule = _make_schedule(job_id, minutes=5, due_at=_in_minutes(3))
    schedule_id = str(schedule["id"])
    server.precheck_schedule(schedule_id)

    rows = _precheck_rows(schedule_id)
    assert len(rows) == 1
    detail = json.loads(str(rows[0]["detail_json"]))
    assert rows[0]["has_new"] == 0
    assert str(rows[0]["due_at"]) == str(schedule["nextRunAt"])
    assert detail["reason"] == "源文件均无更新"
    assert {str(item["source"]) for item in detail["items"]} == {str(src_a), str(src_b)}
    assert all(item["fingerprint"] for item in detail["items"]), "每条守卫都要留下指纹供执行期复核"


# ---------------------------------------------------------------------------
# 5. 预检不写回 _job_file_guards
# ---------------------------------------------------------------------------


def test_precheck_does_not_write_guard_baselines() -> None:
    src_a = _write_source("pc_base_a.csv", 2)
    src_b = _write_source("pc_base_b.csv", 2)
    job = _two_source_job("base", src_a, src_b)
    job_id = str(job["id"])
    server.run_saved_job(job_id)

    schedule = _make_schedule(job_id, minutes=5, due_at=_in_minutes(3))
    schedule_id = str(schedule["id"])
    before = _guard_rows(job_id)
    assert before, "基线应已由首次运行写入"

    server.precheck_schedule(schedule_id)  # 无新增
    assert _guard_rows(job_id) == before, "预检写回基线会让执行期的「当前 vs 基线」比对永远判定无变化"

    _write_source("pc_base_a.csv", 6)  # 有新增
    server.precheck_schedule(schedule_id)
    assert _guard_rows(job_id) == before, "有新增时同样不许写回基线"


# ---------------------------------------------------------------------------
# 6. 保守执行：无记录 / 预检异常 / precheck_minutes=0
# ---------------------------------------------------------------------------


def test_missing_precheck_record_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    """服务在这 5 分钟内才启动、压根没预检过 → 按现状执行，绝不能因为"没检查"就跳过。"""
    src_a = _write_source("pc_norec_a.csv", 2)
    src_b = _write_source("pc_norec_b.csv", 2)
    job = _two_source_job("norec", src_a, src_b)
    job_id = str(job["id"])
    server.run_saved_job(job_id)

    schedule = _make_schedule(job_id, minutes=5, due_at=_in_minutes(3))
    schedule_id = str(schedule["id"])
    assert _precheck_rows(schedule_id) == []

    probe = _Probe(monkeypatch)
    server.run_schedule_once(schedule_id)

    assert probe.calls == [job_id], f"没有预检记录必须保守执行：{probe.calls}"


def test_precheck_exception_executes_conservatively(monkeypatch: pytest.MonkeyPatch) -> None:
    """预检抛异常 → 不落记录 → 执行期保守执行（预检失败绝不能阻止任务执行）。"""
    src_a = _write_source("pc_boom_a.csv", 2)
    src_b = _write_source("pc_boom_b.csv", 2)
    job = _two_source_job("boom", src_a, src_b)
    job_id = str(job["id"])
    server.run_saved_job(job_id)

    schedule = _make_schedule(job_id, minutes=5, due_at=_in_minutes(3))
    schedule_id = str(schedule["id"])

    original = server._guard_summary
    state = {"fail": True}

    def flaky_guard_summary(job: dict[str, object], _visited: set[str] | None = None):
        if state["fail"]:
            raise RuntimeError("注入的预检失败")
        return original(job, _visited)

    monkeypatch.setattr(server, "_guard_summary", flaky_guard_summary)
    result = server.precheck_schedule(schedule_id)
    assert result["hasNew"] == 1 and result.get("failed") is True
    assert _precheck_rows(schedule_id) == [], "预检失败时不能落记录（落了会让执行期误判无新增）"

    state["fail"] = False
    probe = _Probe(monkeypatch)
    server.run_schedule_once(schedule_id)
    assert probe.calls == [job_id], f"预检失败必须按有新增执行：{probe.calls}"


def test_precheck_minutes_zero_executes_and_dispatches_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """precheck_minutes=0（关闭预检）→ 不预检、执行期照现状执行。"""
    src_a = _write_source("pc_zero_a.csv", 2)
    src_b = _write_source("pc_zero_b.csv", 2)
    job = _two_source_job("zero", src_a, src_b)
    job_id = str(job["id"])
    server.run_saved_job(job_id)

    schedule = _make_schedule(job_id, minutes=0, due_at=_in_minutes(2))
    schedule_id = str(schedule["id"])
    assert int(schedule["precheckMinutes"]) == 0

    counter = _PrecheckCounter(monkeypatch)
    server.dispatch_prechecks()
    server.dispatch_prechecks()
    assert counter.calls_for(schedule_id) == [], "precheck_minutes=0 的计划不该进入预检窗口"
    assert _precheck_rows(schedule_id) == []

    probe = _Probe(monkeypatch)
    server.run_schedule_once(schedule_id)
    assert probe.calls == [job_id], f"关闭预检时必须保持改造前的行为（正常执行）：{probe.calls}"


def test_job_without_guard_config_has_new_is_one() -> None:
    """没配任何文件更新条件 → 按有新增处理（条件没配不等于"没有新数据"）。"""
    src_a = _write_source("pc_noguard_a.csv", 2)
    job = server.save_job(
        {
            "name": "pc_job_noguard",
            "guard": {},
            "steps": [_step("导入 A", "import", _import_cfg(src_a, "pc_t_noguard_a", guarded=False))],
        }
    )
    server.run_saved_job(str(job["id"]))
    schedule = _make_schedule(str(job["id"]), minutes=5, due_at=_in_minutes(3))
    result = server.precheck_schedule(str(schedule["id"]))

    assert result["items"] == []
    assert result["hasNew"] == 1
    assert result["reason"] == "未配置文件更新条件，按有新增处理"


# ---------------------------------------------------------------------------
# 7. 幂等：同一个 due_at 只预检一次
# ---------------------------------------------------------------------------


def test_dispatch_precheck_is_idempotent_for_same_due(monkeypatch: pytest.MonkeyPatch) -> None:
    src_a = _write_source("pc_idem_a.csv", 2)
    src_b = _write_source("pc_idem_b.csv", 2)
    job = _two_source_job("idem", src_a, src_b)
    job_id = str(job["id"])
    server.run_saved_job(job_id)

    due_at = _in_minutes(2)  # 在 5 分钟预检窗口内
    schedule = _make_schedule(job_id, minutes=5, due_at=due_at)
    schedule_id = str(schedule["id"])

    counter = _PrecheckCounter(monkeypatch)
    server.dispatch_prechecks()
    assert _wait_until(lambda: len(counter.calls_for(schedule_id)) >= 1), "预检线程应被触发一次"
    server.dispatch_prechecks()
    server.dispatch_prechecks()
    assert _wait_until(lambda: not server._PRECHECK_INFLIGHT), "预检线程必须收尾"

    assert counter.calls_for(schedule_id) == [schedule_id], f"同一轮只应预检一次，实际 {counter.calls_for(schedule_id)}"
    rows = _precheck_rows(schedule_id)
    assert len(rows) == 1, f"预检记录按 (schedule_id, due_at) 幂等：{[dict(r) for r in rows]}"
    assert str(rows[0]["due_at"]) == due_at


def test_dispatch_precheck_repeats_for_new_due(monkeypatch: pytest.MonkeyPatch) -> None:
    """换一轮（due_at 变了）必须重新预检：幂等是按轮次，不是按计划。"""
    src_a = _write_source("pc_newdue_a.csv", 2)
    src_b = _write_source("pc_newdue_b.csv", 2)
    job = _two_source_job("newdue", src_a, src_b)
    job_id = str(job["id"])
    server.run_saved_job(job_id)

    first_due = _in_minutes(2)
    schedule = _make_schedule(job_id, minutes=5, due_at=first_due)
    schedule_id = str(schedule["id"])

    counter = _PrecheckCounter(monkeypatch)
    server.dispatch_prechecks()
    assert _wait_until(lambda: len(counter.calls_for(schedule_id)) >= 1)
    assert _wait_until(lambda: not server._PRECHECK_INFLIGHT)

    second_due = _in_minutes(3)
    _set_next_run(schedule_id, second_due)
    server.dispatch_prechecks()
    assert _wait_until(lambda: len(counter.calls_for(schedule_id)) >= 2), "新的一轮必须重新预检"
    assert _wait_until(lambda: not server._PRECHECK_INFLIGHT)

    assert len(_precheck_rows(schedule_id)) == 2, "两轮的预检记录都要在"


# ---------------------------------------------------------------------------
# 8. 预检窗口外不触发
# ---------------------------------------------------------------------------


def test_outside_precheck_window_not_triggered(monkeypatch: pytest.MonkeyPatch) -> None:
    """到期前 30 分钟、预检窗口只有 5 分钟 → 不该预检（不是持续轮询）。"""
    src_a = _write_source("pc_out_a.csv", 2)
    src_b = _write_source("pc_out_b.csv", 2)
    job = _two_source_job("out", src_a, src_b)
    job_id = str(job["id"])
    server.run_saved_job(job_id)

    schedule = _make_schedule(job_id, minutes=5, due_at=_in_minutes(30))
    schedule_id = str(schedule["id"])

    counter = _PrecheckCounter(monkeypatch)
    server.dispatch_prechecks()
    server.dispatch_prechecks()
    _wait_until(lambda: not server._PRECHECK_INFLIGHT)
    time.sleep(0.2)

    assert counter.calls_for(schedule_id) == [], f"窗口外不该预检：{counter.calls_for(schedule_id)}"
    assert _precheck_rows(schedule_id) == []


def test_past_due_is_not_prechecked(monkeypatch: pytest.MonkeyPatch) -> None:
    """已过点（now >= due）的由 dispatch_due_schedules 处理，预检不插手。"""
    src_a = _write_source("pc_past_a.csv", 2)
    src_b = _write_source("pc_past_b.csv", 2)
    job = _two_source_job("past", src_a, src_b)
    job_id = str(job["id"])
    server.run_saved_job(job_id)

    schedule = _make_schedule(job_id, minutes=5, due_at=_in_minutes(-1))
    schedule_id = str(schedule["id"])

    counter = _PrecheckCounter(monkeypatch)
    server.dispatch_prechecks()
    _wait_until(lambda: not server._PRECHECK_INFLIGHT)
    time.sleep(0.2)

    assert counter.calls_for(schedule_id) == [], f"已过点的计划不该再预检：{counter.calls_for(schedule_id)}"
    assert _precheck_rows(schedule_id) == []


# ---------------------------------------------------------------------------
# 9. precheck_minutes 的持久化与归一
# ---------------------------------------------------------------------------


def test_precheck_minutes_persisted_and_normalized() -> None:
    src_a = _write_source("pc_min_a.csv", 2)
    job = _two_source_job("min", src_a, src_a)
    job_id = str(job["id"])

    default_schedule = server.save_schedule(
        {
            "name": f"pc_{uuid.uuid4().hex[:8]}",
            "jobId": job_id,
            "enabled": True,
            "startAt": (server.app_now() - dt.timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S"),
            "rule": {"mode": "interval", "amount": 10, "unit": "minutes"},
        }
    )
    assert int(default_schedule["precheckMinutes"]) == 5, "缺省 5 分钟"

    custom = _make_schedule(job_id, minutes=15)
    assert int(custom["precheckMinutes"]) == 15

    negative = _make_schedule(job_id, minutes=-3)
    assert int(negative["precheckMinutes"]) == 0, "负数归一为 0（关闭预检）"

    updated = server.save_schedule({**{"id": str(custom["id"])}, "name": str(custom["name"]), "jobId": job_id, "enabled": True, "precheckMinutes": 0})
    assert int(updated["precheckMinutes"]) == 0, "编辑保存也要能改预检提前量"

    with server.connect_db() as conn:
        stored = conn.execute("select precheck_minutes from _schedules where id = ?", (str(custom["id"]),)).fetchone()
    assert int(stored["precheck_minutes"]) == 0, "必须真的落库，而不是只在返回体里改"
