from __future__ import annotations

import datetime as dt
import json

import server


def reset() -> None:
    with server.connect_db() as conn:
        conn.execute("delete from _job_run_steps")
        conn.execute("delete from _job_runs")
        conn.execute("delete from _schedules where name like 'qa_%'")
        conn.execute("delete from _jobs where name like 'qa_%'")


def main() -> None:
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
    assert run["status"] == "成功"
    with server.connect_db() as conn:
        assert conn.execute("select count(*) as total from qa_job_table").fetchone()["total"] == 1
        assert conn.execute("select count(*) as total from _job_run_steps").fetchone()["total"] >= 2

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
    assert schedule["nextRunAt"]
    assert server.compute_next_run({"mode": "daily", "time": "09:00:00"}, "", "", None)
    assert server.compute_next_run({"mode": "weekly", "weekday": 1, "time": "09:00:00"}, "", "", None)
    assert server.compute_next_run({"mode": "monthly", "day": 1, "time": "09:00:00"}, "", "", None)
    assert server.compute_next_run({"mode": "yearly", "month": 1, "day": 1, "time": "09:00:00"}, "", "", None)
    print("jobs schedule checks passed")


if __name__ == "__main__":
    main()
