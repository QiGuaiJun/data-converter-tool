"""清理本机生产库里的测试数据（只删测试产物，保留真实作业）。

用法：
    python scripts/cleanup_test_data.py            # 干跑，只打印将会删除什么
    python scripts/cleanup_test_data.py --apply    # 备份后真正执行

判定口径（刻意收紧，避免误删真数据）：
  · 作业 / 定时任务 / 连接：创建时间 >= 2026-09-30（这批测试数据都是 09-30 09:03~09:11 建的）
    且名字以测试前缀开头（p1g_ / qa_ / pc_ / mx_ / p2_ / p3_ / p4_）之一。
    两个条件同时满足才删 —— 单靠时间或单靠名字都可能误伤。
  · 关联历史：只删属于上述作业的运行记录与步骤、文件指纹守卫、预检记录。
  · 导入日志：只删 table_name 命中测试前缀的。

真实作业（小票导入 / 云购商城销售数据 / 中秋试饮系列）创建于 8/27~9/11，两条口径都不命中。
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "runtime" / "data" / "imports.db"

TEST_PREFIXES = ("p1g_", "qa_", "pc_", "mx_", "p2_", "p3_", "p4_")
# 这 5 张表名没有测试前缀，但逐个在 tests/ 里出现过（import_engine / import_feature_matrix /
# export_engine 用例建的），确认是测试产物才列进来 —— 不要扩成通配。
TEST_TABLE_NAMES = ("first", "second", "yg", "export_people", "export_large_people")
CUTOFF = "2026-09-30"


def _is_test_name(name: str) -> bool:
    return str(name or "").strip().lower().startswith(TEST_PREFIXES)


def _is_test_table(name: str) -> bool:
    text = str(name or "").strip()
    return text.lower() in TEST_TABLE_NAMES or text.lower().startswith(TEST_PREFIXES)


def _safe_ident(name: str) -> str:
    """只允许字母数字下划线才拿去拼 SQL —— 表名来自 sqlite_master，仍要守住这条线。"""
    if not name or not all(ch.isalnum() or ch == "_" for ch in name):
        raise ValueError(f"表名不安全，拒绝处理：{name!r}")
    return name


def plan(conn: sqlite3.Connection) -> dict:
    conn.row_factory = sqlite3.Row
    jobs = [
        dict(row)
        for row in conn.execute("select id, name, created_at from _jobs where created_at >= ? order by created_at", (CUTOFF,))
        if _is_test_name(row["name"])
    ]
    job_ids = [row["id"] for row in jobs]
    mark = ",".join("?" * len(job_ids)) if job_ids else "''"

    schedules = [
        dict(row)
        for row in conn.execute(
            "select id, name, job_id, created_at from _schedules where created_at >= ? order by created_at", (CUTOFF,)
        )
        if _is_test_name(row["name"]) or row["job_id"] in job_ids
    ]
    schedule_ids = [row["id"] for row in schedules]
    smark = ",".join("?" * len(schedule_ids)) if schedule_ids else "''"

    runs = conn.execute(f"select count(*) from _job_runs where job_id in ({mark})", job_ids).fetchone()[0]
    steps = conn.execute(
        f"select count(*) from _job_run_steps where run_id in (select id from _job_runs where job_id in ({mark}))",
        job_ids,
    ).fetchone()[0]
    guards = conn.execute(f"select count(*) from _job_file_guards where job_id in ({mark})", job_ids).fetchone()[0]
    prechecks = conn.execute(
        f"select count(*) from _schedule_prechecks where schedule_id in ({smark})", schedule_ids
    ).fetchone()[0] if schedule_ids else 0
    connections = [
        dict(row)
        for row in conn.execute("select id, name, created_at from _db_connections where created_at >= ?", (CUTOFF,))
        if _is_test_name(row["name"]) or row["name"].lower().startswith("qa ")
    ]
    conn_ids = [row["id"] for row in connections]

    log_rows = [
        dict(row)
        for row in conn.execute("select id, file_name, table_name from _import_logs")
        if _is_test_name(row["table_name"]) or _is_test_name(row["file_name"])
    ]
    fp_rows = [
        dict(row)
        for row in conn.execute("select file_key, table_name from _import_file_fingerprints")
        if _is_test_name(row["table_name"]) or _is_test_name(row["file_key"])
    ]

    keep_jobs = [
        dict(row)
        for row in conn.execute("select name, created_at from _jobs where created_at < ? order by created_at", (CUTOFF,))
    ]
    keep_schedules = [
        dict(row)
        for row in conn.execute("select name, created_at from _schedules where id not in ({})".format(
            ",".join("?" * len(schedule_ids)) if schedule_ids else "''"
        ), schedule_ids)
    ]
    # 孤儿历史：工具自带的 DELETE 只删作业、不删它的运行记录，
    # 于是更早被删掉的测试作业会留下一堆 job_id 已不存在的孤儿运行记录。
    orphan_runs = conn.execute(
        "select count(*) from _job_runs where job_id not in (select id from _jobs)"
    ).fetchone()[0]
    orphan_steps = conn.execute(
        "select count(*) from _job_run_steps where run_id not in (select id from _job_runs)"
    ).fetchone()[0]

    # 业务表：本机库里的业务表全部由测试创建（真实作业写的是 MySQL，见 steps_json 里的
    # targetDbType=mysql）。这里只挑测试前缀 + 逐个核对过的 5 个名字。
    tables = [
        r[0]
        for r in conn.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%' and name not like '\\_%' escape '\\' order by name"
        )
        if _is_test_table(r[0])
    ]
    all_tables = [
        r[0]
        for r in conn.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%' and name not like '\\_%' escape '\\' order by name"
        )
    ]
    return {
        "jobs": jobs, "job_ids": job_ids, "schedules": schedules, "schedule_ids": schedule_ids,
        "runs": runs, "steps": steps, "guards": guards, "prechecks": prechecks,
        "connections": connections, "conn_ids": conn_ids,
        "import_logs": log_rows, "fingerprints": fp_rows,
        "keep_jobs": keep_jobs, "keep_schedules": keep_schedules,
        "tables": tables, "all_tables": all_tables,
        "orphan_runs": orphan_runs, "orphan_steps": orphan_steps,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="真正执行（默认只干跑）")
    args = parser.parse_args()

    if not DB_PATH.is_file():
        print(f"找不到库文件：{DB_PATH}")
        return 2
    conn = sqlite3.connect(str(DB_PATH))
    p = plan(conn)

    print(f"库文件：{DB_PATH}（{DB_PATH.stat().st_size} 字节）")
    print()
    print(f"【将删除】测试作业 {len(p['jobs'])} 个：")
    for row in p["jobs"]:
        print(f"    - {row['name']}  ({row['created_at']})")
    print(f"【将删除】测试定时任务 {len(p['schedules'])} 个：")
    for row in p["schedules"]:
        print(f"    - {row['name']}  ({row['created_at']})")
    print(f"【将删除】测试连接 {len(p['connections'])} 个：")
    for row in p["connections"]:
        print(f"    - {row['name']}  ({row['created_at']})")
    print(f"【将删除】关联历史：运行 {p['runs']} 条、步骤 {p['steps']} 条、"
          f"文件守卫 {p['guards']} 条、预检 {p['prechecks']} 条")
    print(f"【将删除】导入日志 {len(p['import_logs'])} 条、文件指纹 {len(p['fingerprints'])} 条")
    print(f"【将删除】孤儿历史（作业已被删、记录还留着）：运行 {p['orphan_runs']} 条、步骤 {p['orphan_steps']} 条")
    print(f"【将删除】测试建的业务表 {len(p['tables'])} 个（本机库里的业务表全部来自测试；")
    print("           真实作业写的是 MySQL，见 steps_json 的 targetDbType=mysql）：")
    for name in p["tables"]:
        print(f"    - {name}")
    print(f"【保留】非测试命名的表 {len(p['all_tables']) - len(p['tables'])} 个："
          f"{[t for t in p['all_tables'] if t not in p['tables']]}")
    print()
    print(f"【保留】真实作业 {len(p['keep_jobs'])} 个：")
    for row in p["keep_jobs"]:
        print(f"    ✓ {row['name']}  ({row['created_at']})")
    print(f"【保留】真实定时任务 {len(p['keep_schedules'])} 个：")
    for row in p["keep_schedules"]:
        print(f"    ✓ {row['name']}  ({row['created_at']})")

    if not args.apply:
        print("\n（干跑结束，未做任何修改。加 --apply 才会执行）")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = DB_PATH.with_name(f"imports.db.bak-cleanup-{stamp}")
    shutil.copy2(DB_PATH, backup)
    print(f"\n已备份到：{backup}")

    job_ids, schedule_ids, conn_ids = p["job_ids"], p["schedule_ids"], p["conn_ids"]
    mark = ",".join("?" * len(job_ids))
    smark = ",".join("?" * len(schedule_ids))
    cmark = ",".join("?" * len(conn_ids))
    with conn:  # 一个事务里做完，失败全回滚
        if job_ids:
            conn.execute(f"delete from _job_run_steps where run_id in (select id from _job_runs where job_id in ({mark}))", job_ids)
            conn.execute(f"delete from _job_runs where job_id in ({mark})", job_ids)
            conn.execute(f"delete from _job_file_guards where job_id in ({mark})", job_ids)
            conn.execute(f"delete from _schedules where job_id in ({mark})", job_ids)
            conn.execute(f"delete from _jobs where id in ({mark})", job_ids)
        if schedule_ids:
            conn.execute(f"delete from _schedule_prechecks where schedule_id in ({smark})", schedule_ids)
            conn.execute(f"delete from _schedules where id in ({smark})", schedule_ids)
        if conn_ids:
            conn.execute(f"delete from _db_connections where id in ({cmark})", conn_ids)
        for row in p["import_logs"]:
            conn.execute("delete from _import_logs where id = ?", (row["id"],))
        for row in p["fingerprints"]:
            conn.execute("delete from _import_file_fingerprints where file_key = ?", (row["file_key"],))
        # 先删孤儿的步骤，再删孤儿运行（顺序不能反，否则步骤会找不到父记录）
        conn.execute("delete from _job_run_steps where run_id not in (select id from _job_runs)")
        conn.execute("delete from _job_runs where job_id not in (select id from _jobs)")
        for name in p["tables"]:
            conn.execute(f'drop table if exists "{_safe_ident(name)}"')
    conn.execute("vacuum")
    conn.close()

    verify = sqlite3.connect(str(DB_PATH))
    left_jobs = [r[0] for r in verify.execute("select name from _jobs order by created_at")]
    left_sched = [r[0] for r in verify.execute("select name from _schedules order by created_at")]
    left_conn = [r[0] for r in verify.execute("select name from _db_connections order by created_at")]
    left_runs = verify.execute("select count(*) from _job_runs").fetchone()[0]
    print("\n执行完毕，剩余：")
    print(f"  作业 {len(left_jobs)} 个：{left_jobs}")
    print(f"  定时任务 {len(left_sched)} 个：{left_sched}")
    print(f"  连接 {len(left_conn)} 个：{left_conn}")
    print(f"  运行记录 {left_runs} 条")
    left_tables = [r[0] for r in verify.execute(
        "select name from sqlite_master where type='table' and name not like 'sqlite_%' and name not like '\\_%' escape '\\' order by name")]
    print(f"  业务表 {len(left_tables)} 张：{left_tables}")
    verify.close()
    print(f"\n如需回退：copy \"{backup}\" \"{DB_PATH}\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
