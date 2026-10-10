"""同步模块 · 四档模式验证（阶段 2 验收）。

用法：
    CODEBUDDY_SAFE_DELETE_ENABLED=0 CODEBUDDY_SAFE_DELETE_SANDBOX=0 \
        ./.venv/Scripts/python.exe acceptance/verify_sync_modes.py

在**隔离沙箱**里跑（自己的 DATA_DIR），不碰用户的 runtime/data/imports.db。
源 = dc_sync_src，目标 = dc_sync_dst（都来自 acceptance/setup_sync_test_db.py，先跑它）。

覆盖：
  · 全量覆盖：行数与逐行指纹都要与源一致
  · 仅追加：跑两次行数翻倍（不去重是它的定义，不是 bug）
  · 按业务键 upsert：目标已有陈旧行被替换、新行被插入，updated/inserted 计数精确
  · upsert + 键值含 NULL：NULL 安全比较（`<=>`），重复跑不得插出重复行
  · 按时间戳增量：首次全量建水位 → 只取新行 → 无新数据时读取 0 行且水位不倒退
  · 增量失败时**水位不得前进**（否则下一轮会跳过没同步成功的行）
  · 运行历史 _sync_runs 要留下成功与失败两种记录
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(r"D:\ProjectDevelopment\data-converter-tool")
sys.path.insert(0, str(ROOT))

SANDBOX = pathlib.Path(tempfile.mkdtemp(prefix="sync_p2_"))
os.environ["DATA_DIR"] = str(SANDBOX / "data")
os.environ["UPLOADS_DIR"] = str(SANDBOX / "up")
os.environ["EXPORTS_DIR"] = str(SANDBOX / "exp")

import pymysql  # noqa: E402
import server  # noqa: E402

SRC_DB = "dc_sync_src"
DST_DB = "dc_sync_dst"
OK, BAD = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def rec(case: str, ok: bool, detail: str) -> None:
    results.append((case, OK if ok else BAD, detail))
    print(f"[{OK if ok else BAD}] {case} :: {detail}")


def raw(db: str | None = None):
    return pymysql.connect(host="127.0.0.1", port=3306, user="root", password="123456",
                           database=db, charset="utf8mb4", autocommit=True)


def one(conn, sql: str, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else None


def exec_(conn, sql: str, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)


def fingerprint(conn, db: str, table: str, columns: list[str]) -> str:
    expr = ", ".join(f"ifnull(cast(`{c}` as char), '␀')" for c in columns)
    cols = ", ".join(f"`{c}`" for c in columns)
    return one(conn, f"select md5(group_concat(concat_ws('|', {expr}) order by {cols}))"
                     f" from `{db}`.`{table}`")


def table_columns(conn, db: str, table: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "select column_name from information_schema.columns"
            " where table_schema=%s and table_name=%s order by ordinal_position",
            (db, table),
        )
        return [row[0] for row in cur.fetchall()]


print(f"沙箱 DATA_DIR = {os.environ['DATA_DIR']}\n")

boot = raw()
for db in (SRC_DB, DST_DB):
    if one(boot, "select count(*) from information_schema.schemata where schema_name=%s", (db,)) == 0:
        print(f"缺少测试库 {db}，请先跑 acceptance/setup_sync_test_db.py")
        sys.exit(2)

# ---- 准备连接（沙箱内的元数据库） ----
now = server.now_text()
with server.connect_db() as conn:
    for cid, name, dbname in [("src", "源库 dc_sync_src", SRC_DB), ("dst", "目标库 dc_sync_dst", DST_DB)]:
        conn.execute(
            "insert or replace into _db_connections (id, name, db_type, host, port, user_name,"
            " password, db_name, charset, ssl_enabled, ssl_ca, ssl_cert, ssl_key, created_at, updated_at)"
            " values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, name, "mysql", "127.0.0.1", 3306, "root", server.encode_secret("123456"),
             dbname, "utf8mb4", 0, "", "", "", now, now),
        )
rec("0. 沙箱内两个连接已建立", True, f"{SRC_DB} / {DST_DB}")


def make_task(**kw) -> str:
    payload = {"name": kw.pop("name"), "sourceConnectionId": "src", "targetConnectionId": "dst"}
    payload.update(kw)
    return str(server.save_sync_task(payload)["id"])


def watermark_of(task_id: str):
    with server.connect_db() as conn:
        row = conn.execute("select watermark_value from _sync_watermarks where task_id = ?",
                           (task_id,)).fetchone()
    return row["watermark_value"] if row else None


# ================================================================ 1) 全量覆盖
print("\n===== 1) 全量覆盖 =====")
exec_(boot, f"delete from `{DST_DB}`.`小票表`")
src_rows = one(boot, f"select count(*) from `{SRC_DB}`.`小票表`")
t_full = make_task(name="全量覆盖·小票表", sourceTable="小票表", targetTable="小票表", syncMode="full")
r1 = server.execute_sync_task({"taskId": t_full})
ticket_cols = table_columns(boot, SRC_DB, "小票表")
same = fingerprint(boot, SRC_DB, "小票表", ticket_cols) == fingerprint(boot, DST_DB, "小票表", ticket_cols)
dst_rows = one(boot, f"select count(*) from `{DST_DB}`.`小票表`")
rec("1.1 全量覆盖后目标行数 == 源行数", dst_rows == src_rows, f"源 {src_rows} / 目标 {dst_rows}")
rec("1.2 全量覆盖后逐行指纹一致", same, "md5 指纹一致" if same else "指纹不一致")
rec("1.3 执行结果自报口径正确", r1["rowsRead"] == src_rows and r1["rowsWritten"] == src_rows
    and r1["status"] == "成功", f"读 {r1['rowsRead']} 写 {r1['rowsWritten']} 状态 {r1['status']}")

# ================================================================ 2) 仅追加
print("\n===== 2) 仅追加 =====")
# 用小票表（无主键）——「仅追加」的定义就是不去重，落在有主键的表上第二次必然唯一键冲突，
# 那是模式与表不匹配，不是引擎缺陷。
exec_(boot, f"delete from `{DST_DB}`.`小票表`")
t_append = make_task(name="仅追加·小票表", sourceTable="小票表", targetTable="小票表", syncMode="append")
server.execute_sync_task({"taskId": t_append})
after1 = one(boot, f"select count(*) from `{DST_DB}`.`小票表`")
server.execute_sync_task({"taskId": t_append})
after2 = one(boot, f"select count(*) from `{DST_DB}`.`小票表`")
rec("2.1 追加不清空目标（第二次 = 第一次 × 2）",
    after1 == src_rows and after2 == src_rows * 2,
    f"源 {src_rows} → 第一次 {after1} → 第二次 {after2}")

# 追加到有主键的表 → 必须明确失败并给出人话原因（而不是静默丢行）
t_append_pk = make_task(name="仅追加·有主键的表", sourceTable="边界表", targetTable="边界表",
                        syncMode="append")
exec_(boot, f"delete from `{DST_DB}`.`边界表`")
exec_(boot, f"insert into `{DST_DB}`.`边界表` (`id`,`中文列`) values (1,'占位')")
dup_failed, dup_msg = False, ""
try:
    server.execute_sync_task({"taskId": t_append_pk})
except Exception as exc:  # noqa: BLE001
    dup_failed, dup_msg = True, str(exc)
rec("2.2 追加撞唯一键 → 明确失败且翻译成人话（不静默丢行）",
    dup_failed and "唯一约束冲突" in dup_msg and "按业务键 upsert" in dup_msg,
    dup_msg.replace("\n", " ⏎ ")[:130])

# ================================================================ 3) 按业务键 upsert
print("\n===== 3) 按业务键 upsert =====")
exec_(boot, f"delete from `{DST_DB}`.`边界表`")
src_boundary = one(boot, f"select count(*) from `{SRC_DB}`.`边界表`")
boundary_cols = table_columns(boot, SRC_DB, "边界表")
# 目标里预置 3 行「陈旧且内容错误」的行，id 与源重合
exec_(boot, f"insert into `{DST_DB}`.`边界表` (`id`,`中文列`,`空值列`) values (1,'陈旧A','旧'),(2,'陈旧B','旧'),(3,'陈旧C','旧')")
t_up = make_task(name="upsert·边界表", sourceTable="边界表", targetTable="边界表",
                 syncMode="upsert", keyColumns=["id"])
r3 = server.execute_sync_task({"taskId": t_up})
dst_rows = one(boot, f"select count(*) from `{DST_DB}`.`边界表`")
stale_left = one(boot, f"select count(*) from `{DST_DB}`.`边界表` where `中文列` like '陈旧%'")
same3 = fingerprint(boot, SRC_DB, "边界表", boundary_cols) == fingerprint(boot, DST_DB, "边界表", boundary_cols)
rec("3.1 upsert 后目标行数 == 源行数", dst_rows == src_boundary, f"源 {src_boundary} / 目标 {dst_rows}")
rec("3.2 upsert 把陈旧行换掉了（0 行陈旧残留）", stale_left == 0, f"残留 {stale_left} 行")
rec("3.3 upsert 后逐行指纹一致", same3, "md5 指纹一致" if same3 else "指纹不一致")
rec("3.4 updated + inserted == 读取行数",
    r3["rowsUpdated"] + r3["rowsWritten"] == r3["rowsRead"] and r3["rowsUpdated"] == 3,
    f"updated={r3['rowsUpdated']} inserted={r3['rowsWritten']} read={r3['rowsRead']}")
# 再跑一次必须幂等
r3b = server.execute_sync_task({"taskId": t_up})
dst_rows2 = one(boot, f"select count(*) from `{DST_DB}`.`边界表`")
rec("3.5 upsert 幂等（重复跑不增行，全部记为更新）",
    dst_rows2 == src_boundary and r3b["rowsUpdated"] == src_boundary and r3b["rowsWritten"] == 0,
    f"目标 {dst_rows2} 行，updated={r3b['rowsUpdated']} inserted={r3b['rowsWritten']}")

# ================================================================ 4) 键值含 NULL
print("\n===== 4) upsert · 键值含 NULL =====")
exec_(boot, f"delete from `{SRC_DB}`.`小票表` where `提单编号` is null")
exec_(boot, f"delete from `{DST_DB}`.`小票表`")
exec_(boot, f"insert into `{SRC_DB}`.`小票表` (`提单时间`,`提单编号`,`渠道`,`商品名称`)"
            " values ('2026-01-01', null, '空键渠道', '空键商品')")
t_null = make_task(name="upsert·空键", sourceTable="小票表", targetTable="小票表",
                   syncMode="upsert", keyColumns=["提单编号"])
r4a = server.execute_sync_task({"taskId": t_null})
r4b = server.execute_sync_task({"taskId": t_null})
null_rows = one(boot, f"select count(*) from `{DST_DB}`.`小票表` where `提单编号` is null")
rec("4.1 NULL 键不会被静默丢弃", r4a["rowsRead"] >= 1 and null_rows == 1,
    f"读取 {r4a['rowsRead']} 行，目标里 NULL 键行 {null_rows} 行")
rec("4.2 NULL 键重复跑不插重复行", r4b["rowsRead"] > 0 and null_rows == 1,
    f"第二次读取 {r4b['rowsRead']} 行后仍是 {null_rows} 行（走的是 <=> 路径）")

# ================================================================ 5) 按时间戳增量
print("\n===== 5) 按时间戳增量 =====")
exec_(boot, f"delete from `{SRC_DB}`.`小票表` where `提单编号` is null")
# 清理上一次运行留在这张**共享夹具表**里的标记行：不删的话第二次跑到这里时
# max(提单时间) 已经是 2099-12-31，5.2 就退化成"读取 0 行"（同一份夹具反复用，必须幂等）。
exec_(boot, f"delete from `{SRC_DB}`.`小票表` where `提单编号` in ('NEW-R1','NEW-R2')")
exec_(boot, f"delete from `{DST_DB}`.`小票表`")
max_date = one(boot, f"select max(`提单时间`) from `{SRC_DB}`.`小票表`")
total_before = one(boot, f"select count(*) from `{SRC_DB}`.`小票表`")
t_inc = make_task(name="增量·小票表", sourceTable="小票表", targetTable="小票表",
                  syncMode="incremental", watermarkColumn="提单时间", keyColumns=["提单编号", "商品编码"])
r5a = server.execute_sync_task({"taskId": t_inc})
wm1 = watermark_of(t_inc)
rec("5.1 首次运行无水位 → 全量读取并建立水位",
    r5a["rowsRead"] == total_before and wm1 == max_date,
    f"读取 {r5a['rowsRead']} 行（源 {total_before}），水位 = {wm1}")

# 追加 2 行更新的日期（严格大于水位才抓得到 —— 这正是语义边界）
later = "2099-12-31"
exec_(boot, f"insert into `{SRC_DB}`.`小票表` (`提单时间`,`提单编号`,`商品编码`,`渠道`,`商品名称`) values"
            f" ('{later}','NEW-R1','P1','增量渠道','增量商品1'),"
            f" ('{later}','NEW-R2','P2','增量渠道','增量商品2')")
r5b = server.execute_sync_task({"taskId": t_inc})
wm2 = watermark_of(t_inc)
dst_rows = one(boot, f"select count(*) from `{DST_DB}`.`小票表`")
rec("5.2 增量只取水位之后的行", r5b["rowsRead"] == 2 and dst_rows == total_before + 2,
    f"读取 {r5b['rowsRead']} 行 → 目标 {dst_rows} 行，水位 {wm1} → {wm2}")
rec("5.3 水位只前进不倒退", str(wm2) > str(wm1), f"{wm1} → {wm2}")

r5c = server.execute_sync_task({"taskId": t_inc})
wm3 = watermark_of(t_inc)
dst_rows2 = one(boot, f"select count(*) from `{DST_DB}`.`小票表`")
rec("5.4 源无新数据 → 读取 0 行、目标不变、水位不动",
    r5c["rowsRead"] == 0 and dst_rows2 == dst_rows and wm3 == wm2,
    f"读取 {r5c['rowsRead']} 行，目标 {dst_rows2} 行，水位 {wm3}")

# ================================================================ 6) 失败不得推进水位
print("\n===== 6) 增量失败 · 水位不前进 =====")
t_bad = make_task(name="增量·坏键列", sourceTable="小票表", targetTable="小票表",
                  syncMode="incremental", watermarkColumn="提单时间", keyColumns=["不存在的列"])
failed = False
try:
    server.execute_sync_task({"taskId": t_bad})
except Exception as exc:  # noqa: BLE001
    failed = True
    fail_msg = str(exc)
rec("6.1 键列不存在 → 执行失败且报错可读", failed and "不存在的列" in fail_msg,
    fail_msg.replace("\n", " ⏎ ")[:150])
rec("6.2 失败时水位不得前进（否则下次会跳过没同步成功的行）",
    watermark_of(t_bad) in (None, ""), f"水位 = {watermark_of(t_bad)!r}")

# ================================================================ 7) 运行历史
print("\n===== 7) 运行历史 =====")
runs = server.list_sync_runs(t_full, limit=10)
rec("7.1 成功运行有历史记录", len(runs) >= 1 and runs[0]["status"] == "成功",
    f"{len(runs)} 条，最新 {runs[0]['status']}（{runs[0]['rowsRead']} 行）")
bad_runs = server.list_sync_runs(t_bad, limit=10)
rec("7.2 失败运行也有历史记录（且带可读原因）",
    len(bad_runs) >= 1 and bad_runs[0]["status"] == "失败" and "失败原因" in bad_runs[0]["message"],
    f"{len(bad_runs)} 条，状态 {bad_runs[0]['status'] if bad_runs else '-'}")
rec("7.3 历史里记了模式与源/目标标签",
    bool(runs[0]["syncModeLabel"]) and "小票表" in runs[0]["sourceLabel"],
    f"{runs[0]['syncModeLabel']} · {runs[0]['sourceLabel']} → {runs[0]['targetLabel']}")

# ================================================================ 汇总
print("\n" + "=" * 72)
passed = sum(1 for _, status, _ in results if status == OK)
print(f"结论：{passed} / {len(results)} 通过")
for case, status, detail in results:
    if status != OK:
        print(f"  ✗ {case} :: {detail}")
print("=" * 72)
sys.exit(0 if passed == len(results) else 1)
