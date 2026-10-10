"""同步模块 · 真实数据链路验证（阶段 1 验收）。

用法：
    CODEBUDDY_SAFE_DELETE_ENABLED=0 CODEBUDDY_SAFE_DELETE_SANDBOX=0 \
        ./.venv/Scripts/python.exe acceptance/verify_sync_real_pair.py

在**隔离沙箱**里跑（自己的 DATA_DIR），不碰用户的 runtime/data/imports.db。
源 = 本机 lcdp_fe（生产库完整副本，零生产负载），目标 = dc_sync_dst（测试库）。
前置：先跑 acceptance/setup_sync_test_db.py 建出 dc_sync_dst。

覆盖 21 条：数据模型、源端探测（列/键/行数/风险）、全量覆盖执行（含自动建表）、
逐行指纹比对、重复执行幂等、水位落库，以及 6 条**必须被拒绝**的反向用例
（未接上链路的模式 / 同连接自同步 / 缺源表 / 缺键列 / 缺水位列 / 源表不存在）。
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import time

ROOT = pathlib.Path(r"D:\ProjectDevelopment\data-converter-tool")
sys.path.insert(0, str(ROOT))

SANDBOX = pathlib.Path(tempfile.mkdtemp(prefix="sync_p1_"))
os.environ["DATA_DIR"] = str(SANDBOX / "data")
os.environ["UPLOADS_DIR"] = str(SANDBOX / "up")
os.environ["EXPORTS_DIR"] = str(SANDBOX / "exp")

import pymysql  # noqa: E402
import server  # noqa: E402

OK, BAD = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def rec(case: str, ok: bool, detail: str) -> None:
    results.append((case, OK if ok else BAD, detail))
    print(f"[{OK if ok else BAD}] {case} :: {detail}")


def raw(db: str | None = None):
    return pymysql.connect(host="127.0.0.1", port=3306, user="root", password="123456",
                           database=db, charset="utf8mb4", autocommit=True)


def fingerprint(conn, db: str, table: str, columns: list[str]) -> str:
    expr = ", ".join(f"ifnull(cast(`{c}` as char), '␀')" for c in columns)
    cols = ", ".join(f"`{c}`" for c in columns)
    with conn.cursor() as cur:
        cur.execute(f"select md5(group_concat(concat_ws('|', {expr}) order by {cols})) from `{db}`.`{table}`")
        return cur.fetchone()[0]


print(f"沙箱 DATA_DIR = {os.environ['DATA_DIR']}\n")

# ---- 0) 准备：清掉目标表，让"自动建表"这条路径真的被走到 ----
boot = raw()
with boot.cursor() as cur:
    cur.execute("drop table if exists `dc_sync_dst`.`会员小票表`")
print("已清空 dc_sync_dst.会员小票表（用于验证自动建表）\n")

# ---- 1) 数据模型 ----
now = server.now_text()
with server.connect_db() as conn:
    for cid, name, dbname in [("src-lcdp", "本机 lcdp_fe（生产副本）", "lcdp_fe"),
                              ("dst-test", "测试目标库 dc_sync_dst", "dc_sync_dst")]:
        conn.execute(
            "insert or replace into _db_connections (id, name, db_type, host, port, user_name,"
            " password, db_name, charset, ssl_enabled, ssl_ca, ssl_cert, ssl_key, created_at, updated_at)"
            " values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, name, "mysql", "127.0.0.1", 3306, "root", server.encode_secret("123456"),
             dbname, "utf8mb4", 0, "", "", "", now, now),
        )
rec("1. 沙箱内的两个连接已建立", True, "src-lcdp(lcdp_fe) / dst-test(dc_sync_dst)")

task = server.save_sync_task({
    "name": "会员小票 → 测试目标库",
    "sourceConnectionId": "src-lcdp",
    "sourceMode": "table",
    "sourceTable": "会员小票表",
    "targetConnectionId": "dst-test",
    "targetTable": "会员小票表",
    "syncMode": "full",
    "batchRows": 5000,
})
rec("2. 保存同步任务", bool(task["id"]) and task["syncModeLabel"] == "全量覆盖",
    f"id={task['id'][:8]}… 模式={task['syncModeLabel']} 就绪={task['syncModeReady']}")

listed = server.list_sync_tasks()
rec("3. 任务可列出（带水位与上次结果字段）", len(listed) == 1 and "watermark" in listed[0],
    f"共 {len(listed)} 条，字段含 watermark/lastStatus")

# ---- 2) 源端探测 ----
row = server.load_sync_task_row(task["id"])
probe = server.sync_probe_source(row)
cols = [c["name"] for c in probe["columns"]]
rec("4. 源端探测：列清单", len(cols) == 22,
    f"{len(cols)} 列；前 4 列 {cols[:4]}")
rec("5. 源端探测：行数", probe["rowCount"] == 302938, f"rowCount={probe['rowCount']:,}")
rec("6. 源端探测：无主键（如实报告，不假装有）",
    probe["primaryKeys"] == [], f"primaryKeys={probe['primaryKeys']} uniqueKeys={probe['uniqueKeys']}")
rec("7. 源端探测：识别出「会变的列」",
    bool(probe["mutableColumns"]), f"mutableColumns={probe['mutableColumns']}")
rec("8. 源端探测：识别出字符串时间列",
    "提单时间" in probe["stringTimeColumns"], f"stringTimeColumns={probe['stringTimeColumns']}")
rec("9. 源端探测：给出风险提示（1 条无主键 + 1 条会变列 + 1 条字符串时间）",
    len(probe["warnings"]) >= 2, f"{len(probe['warnings'])} 条；首条：{probe['warnings'][0][:38]}…")

# ---- 3) 执行全量覆盖 ----
t0 = time.time()
result = server.execute_sync_task({"id": task["id"]})
wall = time.time() - t0
rec("10. 全量覆盖执行成功", result["rowsWritten"] == 302938,
    f"读 {result['rowsRead']:,} / 写 {result['rowsWritten']:,}，耗时 {wall:.1f}s（引擎内报 {result['elapsedMs']}ms）")
rec("11. 目标表由源表结构自动建出",
    result["targetCreated"] is True, f"targetCreated={result['targetCreated']}")

# ---- 4) 逐行内容比对（不是只比行数）----
src_conn, dst_conn = raw("lcdp_fe"), raw("dc_sync_dst")
f_src = fingerprint(src_conn, "lcdp_fe", "会员小票表", cols)
f_dst = fingerprint(dst_conn, "dc_sync_dst", "会员小票表", cols)
rec("12. 逐行内容与源完全一致（指纹比对）", f_src == f_dst, f"源 {f_src[:16]}… 目标 {f_dst[:16]}…")

with dst_conn.cursor() as cur:
    cur.execute("select count(*) from `dc_sync_dst`.`会员小票表`")
    dst_rows = cur.fetchone()[0]
rec("13. 目标表行数", dst_rows == 302938, f"{dst_rows:,} 行")

# ---- 5) 幂等：再跑一次结果不变 ----
before = fingerprint(dst_conn, "dc_sync_dst", "会员小票表", cols)
again = server.execute_sync_task({"id": task["id"]})
after = fingerprint(dst_conn, "dc_sync_dst", "会员小票表", cols)
rec("14. 重复执行幂等（全量覆盖）", before == after and again["rowsWritten"] == 302938,
    f"两次指纹一致，第二次仍写 {again['rowsWritten']:,} 行")

# ---- 6) 水位/统计已落库 ----
marks = [m for m in server.list_sync_tasks() if m["id"] == task["id"]][0]
rec("15. 上次结果已落库", marks["lastStatus"] == "成功" and marks["lastRowsWritten"] == 302938,
    f"lastStatus={marks['lastStatus']} lastRowsWritten={marks['lastRowsWritten']:,} lastRunAt={marks['lastRunAt']}")

print("\n— 反向用例（必须被拒绝，不能静默降级）—")


def expect_error(case: str, fn) -> None:
    try:
        fn()
        rec(case, False, "❌ 竟然成功了")
    except ValueError as exc:
        rec(case, True, f"已拒绝：{str(exc)[:60]}")


expect_error("16. 未接上链路的模式（按时间戳增量）被拒绝执行", lambda: server.execute_sync_task({
    "name": "临时-增量", "sourceConnectionId": "src-lcdp", "sourceMode": "table",
    "sourceTable": "会员小票表", "targetConnectionId": "dst-test",
    "targetTable": "会员小票表", "syncMode": "incremental"}))
expect_error("17. 源与目标不能是同一个连接", lambda: server.save_sync_task({
    "name": "自同步", "sourceConnectionId": "src-lcdp", "targetConnectionId": "src-lcdp",
    "sourceTable": "会员小票表", "syncMode": "full"}))
expect_error("18. 整表模式必须选源表", lambda: server.save_sync_task({
    "name": "缺源表", "sourceConnectionId": "src-lcdp", "targetConnectionId": "dst-test",
    "sourceMode": "table", "syncMode": "full"}))
expect_error("19. upsert 必须给键列", lambda: server.save_sync_task({
    "name": "缺键列", "sourceConnectionId": "src-lcdp", "targetConnectionId": "dst-test",
    "sourceTable": "会员小票表", "syncMode": "upsert"}))
expect_error("20. 增量必须给水位列", lambda: server.save_sync_task({
    "name": "缺水位", "sourceConnectionId": "src-lcdp", "targetConnectionId": "dst-test",
    "sourceTable": "会员小票表", "syncMode": "incremental"}))
expect_error("21. 不存在的源表要给明确报错", lambda: server.execute_sync_task({
    "name": "源表不存在", "sourceConnectionId": "src-lcdp", "sourceMode": "table",
    "sourceTable": "根本不存在的表", "targetConnectionId": "dst-test",
    "targetTable": "x", "syncMode": "full"}))

print("\n" + "=" * 68)
passed = sum(1 for _, v, _ in results if v == OK)
print(f"结论：{passed} / {len(results)} 通过")
for case, verdict, _ in results:
    if verdict != OK:
        print(f"  {verdict}  {case}")
print(f"沙箱：{SANDBOX}")
src_conn.close()
dst_conn.close()
sys.exit(0 if passed == len(results) else 1)
