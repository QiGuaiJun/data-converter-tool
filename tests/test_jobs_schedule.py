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


# --------------------------------------------------------------- 运行日志文案整理
# 2026-10-08 业主反馈"任务的运行日志特别乱"。乱在：父子嵌套套娃、括号段落重复、
# 英文 Errno 直出。下面是纯字符串函数的用例（不碰数据库），守住这三条。

_NESTED_SAMPLE = (
    "作业执行失败：0 个步骤成功，1 个步骤失败，0 个步骤未启用。"
    "最后错误：子作业执行失败：作业执行失败：2 个步骤成功，1 个步骤失败，0 个步骤未启用。"
    "最后错误：[Errno 13] Permission denied: 'C://data//中秋试饮活动.xlsx'\n"
    "（本次触发执行：源文件有更新 — C://data//云购商城销售数据源.xlsx）/n"
    "（本次触发执行：源文件有更新 — C://data//云购商城销售数据源.xlsx）/n"
    "（以下步骤本轮未重跑：源文件无更新 — 中秋权益导入）"
)


def test_compact_failure_reason_peels_nested_prefixes_and_translates():
    """嵌套三层要能剥到最内层，并把 Errno 13 翻译成人话。"""
    reason = server.compact_failure_reason(_NESTED_SAMPLE)
    assert "子作业执行失败" not in reason, "嵌套前缀没剥干净"
    assert "作业执行失败" not in reason
    assert "最后错误" not in reason
    assert "Permission denied" not in reason, "英文原始错误应被翻译掉"
    assert "文件被占用" in reason or "没有权限" in reason
    assert "中秋试饮活动.xlsx" in reason, "真实错误里的文件名必须保留"


def test_compact_failure_reason_keeps_unknown_error_text():
    """认不出来的错误不能吞掉 —— 原样保留，方便排查。"""
    assert server.compact_failure_reason("no such table: 会员小票表") == "no such table: 会员小票表"
    assert server.compact_failure_reason("") == "（无错误详情）"


def test_translate_system_error_covers_common_cases():
    assert "找不到文件" in server.translate_system_error("[Errno 2] No such file or directory: 'C://a.xlsx'")
    assert "磁盘空间不足" in server.translate_system_error("[Errno 28] No space left on device")
    assert "文件正被其他程序占用" in server.translate_system_error("[WinError 32] 另一个程序正在使用此文件")
    # 认不出来的原样返回
    assert server.translate_system_error("自定义错误") == "自定义错误"


def test_dedupe_log_paragraphs_removes_exact_duplicates_only():
    """只按整行精确去重：内容不同的两段（不同触发文件）不能被误合并。"""
    text = "结论句\n（本次触发执行：A）\n（本次触发执行：A）\n（本次触发执行：B）"
    out = server.dedupe_log_paragraphs(text)
    assert out.count("（本次触发执行：A）") == 1
    assert out.count("（本次触发执行：B）") == 1
    assert out.startswith("结论句")


def test_dedupe_log_paragraphs_keeps_normal_lines():
    text = "第一行\n第二行\n第一行"
    assert server.dedupe_log_paragraphs(text) == text, "非括号行不该被动"
