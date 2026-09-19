"""任务与定时调度检查。

两种跑法完全等价：
    python test_jobs_schedule.py        # 脚本入口，全部通过时打印 "jobs schedule checks passed"
    pytest -q test_jobs_schedule.py     # 每个检查项一个用例

为什么改成这样：原文件只有一个 ``main()``，模块里没有任何 ``def test_*``，
pytest 收集 0 个用例却显示通过（假绿）。现在每个检查项拆成独立的 ``def test_xxx``，
断言只有一处定义（``run_checks()`` 缓存一次执行结果）。

隔离：所有数据/上传/导出落临时目录，绝不触碰真实 data/、uploads/、exports/。
"""
from __future__ import annotations

import atexit
import datetime as dt
import os
import shutil
import tempfile
from pathlib import Path

# 隔离测试环境：所有数据/上传/导出落临时目录，绝不触碰真实 data/、uploads/、exports/
_tmp = tempfile.mkdtemp(prefix="dc_test_")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
os.environ["UPLOADS_DIR"] = str(Path(_tmp) / "uploads")
os.environ["EXPORTS_DIR"] = str(Path(_tmp) / "exports")

import server

# sqlite3 连接作为 with 语句使用时只提交事务、不 close，句柄会一直占着临时库文件；
# Windows 上这会挡住隔离目录的删除，把 %TEMP%/dc_test_* 越攒越多。这里记录句柄，
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



def reset() -> None:
    with server.connect_db() as conn:
        conn.execute("delete from _job_run_steps")
        conn.execute("delete from _job_runs")
        conn.execute("delete from _schedules where name like 'qa_%'")
        conn.execute("delete from _jobs where name like 'qa_%'")


_CHECKS: dict[str, object] | None = None


def run_checks() -> dict[str, object]:
    """建任务、跑一次任务、建一个秒级调度，并缓存结果（首次调用执行）。"""
    global _CHECKS
    if _CHECKS is not None:
        return _CHECKS

    reset()
    with server.connect_db() as conn:
        conn.execute("drop table if exists qa_job_table")
        conn.execute("create table qa_job_table (id integer, name text)")

    job = server.save_job(
        {
            "name": "qa_query_job",
            "steps": [
                {
                    "name": "insert row",
                    "type": "query",
                    "enabled": True,
                    "continueOnError": False,
                    "config": {"targetDbType": "sqlite", "sql": "insert into qa_job_table values (1, 'Alice')"},
                },
                {
                    "name": "export row",
                    "type": "export",
                    "enabled": True,
                    "continueOnError": False,
                    "config": {
                        "targetDbType": "sqlite",
                        "items": [{"type": "query", "name": "qa_job_export", "sql": "select * from qa_job_table"}],
                        "extension": "csv",
                        "outputName": "qa_job_export",
                    },
                },
            ],
        }
    )
    run = server.run_saved_job(str(job["id"]))
    with server.connect_db() as conn:
        rows_in_target = conn.execute("select count(*) as total from qa_job_table").fetchone()["total"]
        step_count = conn.execute("select count(*) as total from _job_run_steps").fetchone()["total"]

    start = (dt.datetime.now() - dt.timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
    schedule = server.save_schedule(
        {
            "name": "qa_schedule",
            "jobId": job["id"],
            "enabled": True,
            "startAt": start,
            "endAt": "2099-12-31 23:59:59",
            "rule": {"mode": "interval", "amount": 10, "unit": "seconds"},
            "logRetentionDays": 3,
        }
    )

    _CHECKS = {
        "run": run,
        "rows_in_target": rows_in_target,
        "step_count": step_count,
        "schedule": schedule,
        "next_daily": server.compute_next_run({"mode": "daily", "time": "09:00:00"}, "", "", None),
        "next_weekly": server.compute_next_run({"mode": "weekly", "weekday": 1, "time": "09:00:00"}, "", "", None),
        "next_monthly": server.compute_next_run({"mode": "monthly", "day": 1, "time": "09:00:00"}, "", "", None),
        "next_yearly": server.compute_next_run({"mode": "yearly", "month": 1, "day": 1, "time": "09:00:00"}, "", "", None),
    }
    return _CHECKS


# ---------------------------------------------------------------------------
# 检查项
# ---------------------------------------------------------------------------


def test_saved_job_runs_successfully() -> None:
    assert run_checks()["run"]["status"] == "成功"


def test_job_insert_step_wrote_row() -> None:
    assert run_checks()["rows_in_target"] == 1


def test_job_logged_at_least_two_steps() -> None:
    assert run_checks()["step_count"] >= 2


def test_schedule_has_next_run_at() -> None:
    assert run_checks()["schedule"]["nextRunAt"]


def test_compute_next_run_daily() -> None:
    assert run_checks()["next_daily"]


def test_compute_next_run_weekly() -> None:
    assert run_checks()["next_weekly"]


def test_compute_next_run_monthly() -> None:
    assert run_checks()["next_monthly"]


def test_compute_next_run_yearly() -> None:
    assert run_checks()["next_yearly"]


TESTS = (
    test_saved_job_runs_successfully,
    test_job_insert_step_wrote_row,
    test_job_logged_at_least_two_steps,
    test_schedule_has_next_run_at,
    test_compute_next_run_daily,
    test_compute_next_run_weekly,
    test_compute_next_run_monthly,
    test_compute_next_run_yearly,
)


def main() -> None:
    """脚本入口：逐个跑上面的检查项，任一断言失败即中断。

    注意：pytest 的 ``pytest.skip.Exception`` 继承自 ``BaseException``，
    ``except Exception`` 抓不到它；本模块不使用 skip，因此这里不做包装，
    让异常直接冒泡、以非零码退出。
    """
    checks = run_checks()
    for check in TESTS:
        check()
    print(f"job status: {checks['run']['status']}")
    print(f"schedule nextRunAt: {checks['schedule']['nextRunAt']}")
    print("jobs schedule checks passed")


if __name__ == "__main__":
    main()
