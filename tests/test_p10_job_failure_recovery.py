"""作业「执行失败后怎么恢复」的语义（对应验收报告 Q-004）。

报告原话是"失败恢复语义未澄清也未覆盖"。这里把口径**用测试固定下来**，共四条：

1. **默认（continueOnError=false）步骤失败即停**：后续步骤不再执行，
   连 ``_job_run_steps`` 行都不会建（步骤行是在循环体内插入的）。
2. **勾了 continueOnError=true 时后续步骤照跑，但整轮结论仍然是「失败」**：
   部分成功不等于成功，不能因为后面几步跑通了就把这次失败盖掉。
3. **失败后不留僵尸**：``_job_runs`` / ``_job_run_steps`` 里不能有 status='运行中'。
4. **进程被强杀留下的"运行中"，下次启动由 ``recover_interrupted_runs()`` 收口成「失败」**，
   不会永久卡住 —— 这是"整轮重跑"能成立的前提。

⚠️ 本工具**没有"单步重试"入口**（server.py 全文搜 retry 只有登录限流）。
失败后的恢复动作就是**整轮重跑**：重新点「立即执行」，所有步骤从头再跑一遍。
"第 1 步导入成功、第 2 步导出失败"这种半成品，重跑会把第 1 步也再做一次。

为什么选整轮重跑而不是断点续跑：步骤之间靠**产物路径**串联（导出产物被后续步骤消费），
断点续跑要先保证上一步的产物还在、且没被人工挪走或覆盖 —— 当前没有这层保证，
硬做出来就是"看着省事、实际跑错"。这条口径写在这里，免得以后被当成 bug 报。
"""

from __future__ import annotations

import os
import tempfile

_SANDBOX = tempfile.mkdtemp(prefix="p10_recover_")
os.environ.setdefault("DATA_DIR", os.path.join(_SANDBOX, "data"))
os.environ.setdefault("UPLOADS_DIR", os.path.join(_SANDBOX, "up"))
os.environ.setdefault("EXPORTS_DIR", os.path.join(_SANDBOX, "exp"))

import server  # noqa: E402


def _step(step_id: str, name: str, sql: str, continue_on_error: bool = False) -> dict:
    return {
        "id": step_id,
        "name": name,
        "type": "query",
        "enabled": True,
        "continueOnError": continue_on_error,
        # sqlite 目标库：既不需要 MySQL，又能让"表不存在"稳定报错
        "config": {"targetDbType": "sqlite", "sql": sql},
    }


def _two_step_job(name: str, continue_on_error: bool) -> dict:
    return server.save_job({
        "name": name,
        "steps": [
            _step("s1", "注定失败的一步", "select * from 这张表不存在", continue_on_error),
            _step("s2", "后面的一步", "select 1"),
        ],
    })


def _steps_of(run_id: str) -> list[dict]:
    with server.connect_db() as conn:
        rows = conn.execute(
            "select * from _job_run_steps where run_id = ? order by step_index", (run_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def _running_rows() -> tuple[int, int]:
    with server.connect_db() as conn:
        runs = conn.execute("select id from _job_runs where status = '运行中'").fetchall()
        steps = conn.execute("select id from _job_run_steps where status = '运行中'").fetchall()
    return len(runs), len(steps)


def test_default_stops_after_first_failure():
    """默认不继续：第 1 步失败后，第 2 步连步骤行都不该有。"""
    job = _two_step_job("失败恢复-默认停", False)
    run = server.run_saved_job(job["id"])

    assert run["status"] == "失败", run
    steps = _steps_of(run["id"])
    # 用步骤名断言，不依赖 step_index 的基准（实际是 1 起）
    assert [s["step_name"] for s in steps] == ["注定失败的一步"], f"第 2 步不该执行，实际步骤行={steps}"
    assert steps[0]["status"] == "失败"
    # 失败原因要给人话，不能只留一句"失败"
    assert "失败原因" in run["message"], run["message"]


def test_continue_on_error_runs_following_steps_but_run_still_fails():
    """勾了继续：第 2 步照跑且成功，但整轮结论**仍然是失败**。"""
    job = _two_step_job("失败恢复-继续跑", True)
    run = server.run_saved_job(job["id"])

    steps = _steps_of(run["id"])
    assert [s["step_name"] for s in steps] == ["注定失败的一步", "后面的一步"], f"第 2 步应当执行，实际={steps}"
    assert [s["step_index"] for s in steps] == sorted(s["step_index"] for s in steps), "步骤序号必须递增"
    assert steps[0]["status"] == "失败"
    assert steps[1]["status"] == "成功"
    # 关键：不因为后面跑通了就把整轮翻成"成功"
    assert run["status"] == "失败", run


def test_failed_run_leaves_no_running_zombie():
    """失败收口后，库里不能留 status='运行中' 的 run / step。"""
    job = _two_step_job("失败恢复-无僵尸", False)
    server.run_saved_job(job["id"])

    runs, steps = _running_rows()
    assert (runs, steps) == (0, 0), f"失败后不该留僵尸：runs={runs} steps={steps}"


def test_startup_recovery_closes_interrupted_running_records():
    """进程被强杀留下的"运行中"，启动恢复要收口成"失败"并注明原因。"""
    job = _two_step_job("失败恢复-强杀", False)
    run_id = "interrupted-run-1"
    with server.connect_db() as conn:
        conn.execute(
            "insert into _job_runs (id, job_id, schedule_id, job_name, started_at, status, message)"
            " values (?,?,?,?,?,?,?)",
            (run_id, job["id"], "", job["name"], "2026-10-15 09:00:00", "运行中", ""),
        )
        conn.execute(
            "insert into _job_run_steps (id, run_id, step_index, step_name, step_type, started_at, status)"
            " values (?,?,?,?,?,?,?)",
            ("interrupted-step-1", run_id, 0, "写一半", "query", "2026-10-15 09:00:00", "运行中"),
        )

    recovered = server.recover_interrupted_runs()
    assert recovered >= 2, f"应至少恢复 run + step 两条，实际 {recovered}"

    with server.connect_db() as conn:
        run = dict(conn.execute("select * from _job_runs where id = ?", (run_id,)).fetchone())
        step = dict(conn.execute("select * from _job_run_steps where id = ?", ("interrupted-step-1",)).fetchone())
    assert run["status"] == "失败", run
    assert "中断" in run["message"], run["message"]
    assert step["status"] == "失败", step
    assert run["ended_at"], "恢复时必须补结束时间，否则界面上时长算不出来"
