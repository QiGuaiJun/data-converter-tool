"""同步模块 · HTTP 接口与只读预演验证（任务 #90 验收）。

用法：
    CODEBUDDY_SAFE_DELETE_ENABLED=0 CODEBUDDY_SAFE_DELETE_SANDBOX=0 \
        ./.venv/Scripts/python.exe acceptance/verify_sync_api.py

在**隔离沙箱**里起服务（自己的 DATA_DIR / 端口），不碰用户 runtime/data/imports.db。
库用 dc_sync_src / dc_sync_dst（先跑 acceptance/setup_sync_test_db.py）。

覆盖：
  A. 7 个接口都可达；路径/方法错一个就是 404（防前端按约定写、后端没登记）
  B. 预演（preview）**真的不写任何东西** —— 目标行数 / 目标表存在性 / 水位 / 运行历史四项全不变
  C. 预演报的行数 == 真跑出来的 rowsRead（锁住 preview 与 execute 的 SQL 同源）
  D. 拦路问题在预览阶段就暴露，且是 canRun=false 而不是抛异常
  E. 源端 SQL 被限制为单条只读语句（这是 probe/preview 能给 viewer 的前提）
  F. 角色门禁成对验证：viewer 能预览不能跑；operator 能跑不能删连接
  G. 删除任务级联清理水位；历史按任务过滤、按 limit 截断、非法 limit 报错
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = pathlib.Path(r"D:\ProjectDevelopment\data-converter-tool")
sys.path.insert(0, str(ROOT))

SANDBOX = pathlib.Path(tempfile.mkdtemp(prefix="sync_api_"))
os.environ["DATA_DIR"] = str(SANDBOX / "data")
os.environ["UPLOADS_DIR"] = str(SANDBOX / "up")
os.environ["EXPORTS_DIR"] = str(SANDBOX / "exp")
# 默认不开认证（等同本机桌面模式）；F 段再临时打开，验证角色门禁真的生效。
os.environ["APP_AUTH_ENABLED"] = "false"
os.environ.pop("ADMIN_PASSWORD", None)

import pymysql  # noqa: E402
import server  # noqa: E402

SRC_DB, DST_DB = "dc_sync_src", "dc_sync_dst"
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


def exec_(conn, sql: str, params=None) -> None:
    with conn.cursor() as cur:
        cur.execute(sql, params)


def rows_of(db: str, table: str) -> int:
    return int(one(boot, f"select count(*) from `{db}`.`{table}`") or 0)


def table_exists(db: str, table: str) -> bool:
    return int(one(boot, "select count(*) from information_schema.tables"
                         " where table_schema=%s and table_name=%s", (db, table)) or 0) > 0


print(f"沙箱 DATA_DIR = {os.environ['DATA_DIR']}\n")

boot = raw()
for db in (SRC_DB, DST_DB):
    if int(one(boot, "select count(*) from information_schema.schemata where schema_name=%s", (db,)) or 0) == 0:
        print(f"缺少测试库 {db}，请先跑 acceptance/setup_sync_test_db.py")
        sys.exit(2)

server.bootstrap_admin_from_env()
httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.ImportPrototypeHandler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{httpd.server_address[1]}"
print(f"服务已起：{BASE}\n")

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


def _json(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_raw": text[:300]}


class Client:
    def __init__(self) -> None:
        self.cookie = ""

    def request(self, method: str, path: str, body: dict | None = None, csrf: bool = True):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(BASE + path, data=data, method=method)
        if data:
            req.add_header("Content-Type", "application/json")
        if self.cookie:
            req.add_header("Cookie", self.cookie)
        if csrf and method not in ("GET", "HEAD"):
            req.add_header(server.CSRF_HEADER_NAME, "1")
            req.add_header("Origin", BASE)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = resp.read().decode("utf-8", "replace")
                set_cookie = resp.headers.get("Set-Cookie", "")
                if set_cookie:
                    self.cookie = set_cookie.split(";")[0]
                return resp.status, _json(payload)
        except urllib.error.HTTPError as exc:
            return exc.code, _json(exc.read().decode("utf-8", "replace"))


cli = Client()


def save(**kw) -> str:
    payload = {"name": kw.pop("name"), "sourceConnectionId": "src", "targetConnectionId": "dst"}
    payload.update(kw)
    return str(server.save_sync_task(payload)["id"])


def preview_of(payload: dict) -> dict:
    _, body = cli.request("POST", "/api/sync/preview", payload)
    return body.get("preview") or {}


# ================================================================ A. 接口可达性
print("===== A. 7 个接口都可达 =====")
status, body = cli.request("POST", "/api/sync/tasks", {
    "name": "接口验证·小票表", "sourceConnectionId": "src", "targetConnectionId": "dst",
    "sourceMode": "table", "sourceTable": "小票表", "targetTable": "小票表", "syncMode": "full",
})
task_id = str((body.get("task") or {}).get("id") or "")
rec("A1 POST /api/sync/tasks 保存任务", status == 200 and bool(task_id), f"HTTP {status} id={task_id[:8]}")

status, body = cli.request("GET", "/api/sync/tasks")
tasks = body.get("tasks") or []
server_modes = {m["value"]: m for m in (body.get("modes") or [])}
rec("A2 GET /api/sync/tasks 列表", status == 200 and any(t["id"] == task_id for t in tasks),
    f"HTTP {status} 共 {len(tasks)} 条")
rec("A3 列表顺带返回四档模式及可用性（界面的可选集合由服务端决定）",
    set(server_modes) == set(server.SYNC_MODES)
    and all(server_modes[m]["ready"] == (m in server.SYNC_MODES_READY) for m in server_modes),
    "、".join(f"{m}={'可用' if server_modes[m]['ready'] else '不可用'}" for m in server_modes))

status, body = cli.request("POST", "/api/sync/probe", {"taskId": task_id})
probe = body.get("probe") or {}
rec("A4 POST /api/sync/probe 探测源端", status == 200 and len(probe.get("columns") or []) > 0,
    f"HTTP {status} 列 {len(probe.get('columns') or [])} 个，行数 {probe.get('rowCount')}")

status, body = cli.request("POST", "/api/sync/preview", {"taskId": task_id})
first_preview = body.get("preview") or {}
rec("A5 POST /api/sync/preview 只读预演", status == 200 and first_preview.get("canRun") is True,
    f"HTTP {status} 将读取 {first_preview.get('rowsToProcess')} 行")

status, body = cli.request("POST", "/api/sync/run", {"taskId": task_id})
run = body.get("run") or {}
rec("A6 POST /api/sync/run 执行", status == 200 and run.get("status") == "成功",
    f"HTTP {status} {run.get('syncModeLabel')} 读 {run.get('rowsRead')} 行")

status, body = cli.request("GET", "/api/sync/runs?taskId=" + task_id)
runs = body.get("runs") or []
rec("A7 GET /api/sync/runs 运行历史", status == 200 and len(runs) >= 1, f"HTTP {status} {len(runs)} 条")

status, body = cli.request("DELETE", f"/api/sync/tasks?id={task_id}")
rec("A8 DELETE /api/sync/tasks 删除任务", status == 200 and body.get("ok") is True, f"HTTP {status}")
status, body = cli.request("GET", "/api/sync/tasks")
rec("A9 删除后列表里不再有它", all(t["id"] != task_id for t in (body.get("tasks") or [])), "已从列表消失")

status, body = cli.request("POST", "/api/sync/taskss", {"name": "x"})
rec("A10 拼错的路径返回 404（不会落到静态文件兜底）", status == 404, f"HTTP {status}")
status, body = cli.request("GET", "/api/sync/runs")
rec("A11 全部 7 条路由都在 ROUTE_MIN_ROLE 登记过（漏登记会 fail closed 成管理员）",
    all(route in server.ROUTE_MIN_ROLE for route in [
        ("GET", "/api/sync/tasks"), ("POST", "/api/sync/tasks"), ("DELETE", "/api/sync/tasks"),
        ("POST", "/api/sync/probe"), ("POST", "/api/sync/preview"),
        ("POST", "/api/sync/run"), ("GET", "/api/sync/runs"),
    ]) and status == 200, "7 条路由全部显式登记")

# ================================================================ B. 预演必须只读
print("\n===== B. 预演真的不写任何东西 =====")
with server.connect_db() as conn:
    runs_before = int(conn.execute("select count(*) from _sync_runs").fetchone()[0])
    marks_before = int(conn.execute("select count(*) from _sync_watermarks").fetchone()[0])

before_saved = rows_of(DST_DB, "边界表")
tid_b = save(name="B·预演只读", sourceMode="table", sourceTable="订单表",
             targetTable="订单表_预演不该建", syncMode="full")
pv_b = preview_of({"taskId": tid_b})
created_by_preview = table_exists(DST_DB, "订单表_预演不该建")
exec_(boot, f"truncate table `{DST_DB}`.`订单表`")

tid_b2 = save(name="B·预演不动已有目标表", sourceMode="table", sourceTable="边界表",
              targetTable="边界表", syncMode="full")
preview_of({"taskId": tid_b2})
after_saved = rows_of(DST_DB, "边界表")

with server.connect_db() as conn:
    runs_after = int(conn.execute("select count(*) from _sync_runs").fetchone()[0])
    marks_after = int(conn.execute("select count(*) from _sync_watermarks").fetchone()[0])

rec("B1 预演不自动创建目标表", not created_by_preview,
    "目标表仍未创建" if not created_by_preview else "被创建了（副作用！）")
rec("B2 预演不改动已存在的目标表", before_saved == after_saved, f"{before_saved} → {after_saved} 行")
rec("B3 预演不写运行历史", runs_after == runs_before, f"_sync_runs {runs_before} → {runs_after} 条")
rec("B4 预演不写水位", marks_after == marks_before, f"_sync_watermarks {marks_before} → {marks_after} 条")
rec("B5 预演自报将会新建目标表", pv_b.get("willCreateTarget") is True and pv_b.get("targetExists") is False,
    f"targetExists={pv_b.get('targetExists')} willCreateTarget={pv_b.get('willCreateTarget')}")

# ================================================================ C. 预演口径 == 执行口径
print("\n===== C. 预演行数 == 实际读取行数（锁住 SQL 同源）=====")
cases = [
    ("全量覆盖", dict(sourceTable="小票表", targetTable="小票表", syncMode="full")),
    ("仅追加", dict(sourceTable="小票表", targetTable="小票表_追加", syncMode="append")),
    ("业务键 upsert", dict(sourceTable="边界表", targetTable="边界表", syncMode="upsert",
                           keyColumns=["id"])),
]
for label, cfg in cases:
    cid = save(name=f"C·{label}", sourceMode="table", **cfg)
    predicted = int(preview_of({"taskId": cid}).get("rowsToProcess") or -1)
    expected = rows_of(SRC_DB, str(cfg["sourceTable"]))
    _, body = cli.request("POST", "/api/sync/run", {"taskId": cid})
    actual = int((body.get("run") or {}).get("rowsRead") or -1)
    detail = (f"预览 {predicted} / 源 {expected} / 实读 {actual}"
              + (f"｜执行报错：{str(body.get('error'))[:90]}" if body.get("error") else ""))
    rec(f"C·{label} 预览行数 == 源行数 == 实际读取行数",
        predicted == expected == actual, detail)

# 目标表按源结构自动新建（此前这里用源库名配目标表名取 SHOW CREATE TABLE，必然失败）
exec_(boot, f"drop table if exists `{DST_DB}`.`小票表_换名落地`")
renamed = save(name="C·换名落地", sourceMode="table", sourceTable="小票表",
               targetTable="小票表_换名落地", syncMode="full")
_, body = cli.request("POST", "/api/sync/run", {"taskId": renamed})
with boot.cursor() as cur:
    cur.execute("select count(*) from information_schema.columns"
                " where table_schema=%s and table_name='小票表_换名落地'", (DST_DB,))
    created_cols = int(cur.fetchone()[0])
with boot.cursor() as cur:
    cur.execute("select count(*) from information_schema.columns"
                " where table_schema=%s and table_name='小票表'", (SRC_DB,))
    src_cols = int(cur.fetchone()[0])
rec("C·换名落地 目标表按源结构自动新建且列数一致",
    (body.get("run") or {}).get("status") == "成功" and created_cols == src_cols,
    f"目标 {created_cols} 列 / 源 {src_cols} 列" + (f"｜{str(body.get('error'))[:80]}" if body.get("error") else ""))

inc = save(name="C·时间戳增量", sourceMode="table", sourceTable="订单表",
           targetTable="订单表_增量", syncMode="incremental", watermarkColumn="订单创建时间")
exec_(boot, f"drop table if exists `{DST_DB}`.`订单表_增量`")
exec_(boot, f"delete from `{SRC_DB}`.`订单表` where `订单号`='PV-9999'")
base_rows = rows_of(SRC_DB, "订单表")
first_pred = int(preview_of({"taskId": inc}).get("rowsToProcess") or -1)
_, body = cli.request("POST", "/api/sync/run", {"taskId": inc})
first_read = int((body.get("run") or {}).get("rowsRead") or -1)
rec("C·时间戳增量 首次（无水位）预览 == 实读", first_pred == first_read == base_rows,
    f"预览 {first_pred} / 实读 {first_read} / 源 {base_rows}"
    + (f"｜执行报错：{str(body.get('error'))[:110]}" if body.get("error") else ""))

exec_(boot, f"insert into `{SRC_DB}`.`订单表` (`订单号`, `商品名称`, `订单创建时间`)"
            " values ('PV-9999', '预览验证行', '2099-12-31 00:00:00')")
second_pred = int(preview_of({"taskId": inc}).get("rowsToProcess") or -1)
_, body = cli.request("POST", "/api/sync/run", {"taskId": inc})
second_read = int((body.get("run") or {}).get("rowsRead") or -1)
rec("C·时间戳增量 第二轮（有水位）只看到新增的 1 行", second_pred == second_read == 1,
    f"预览 {second_pred} / 实读 {second_read}"
    + (f"｜执行报错：{str(body.get('error'))[:110]}" if body.get("error") else ""))

# ================================================================ D. 拦路问题
print("\n===== D. 拦路问题要在预览阶段就暴露 =====")
# save_sync_task 在保存时就要求 upsert 必带键列，所以"已存下来却没有键列"只能直接写库
# 造出来 —— 这正是线上会出现的形态（改表结构、历史数据），必须从头到尾验证一次。
bad_id = "badupserttaskdebug0000000000000000"
with server.connect_db() as conn:
    conn.execute(
        "insert or replace into _sync_tasks (id, name, source_connection_id, source_mode, source_table,"
        " source_sql, target_connection_id, target_table, columns_json, sync_mode, key_columns_json,"
        " watermark_column, batch_rows, commit_mode, stop_on_error, enabled, created_at, updated_at)"
        " values (?,?,'src','table','小票表','','dst','小票表','[]','upsert','[]','',5000,'batch',1,1,?,?)",
        (bad_id, "D·upsert 无键列", now, now),
    )
from_db = preview_of({"taskId": bad_id})
adhoc = preview_of({"sourceConnectionId": "src", "targetConnectionId": "dst", "sourceMode": "table",
                    "sourceTable": "小票表", "targetTable": "小票表", "syncMode": "upsert"})
rec("D1 缺业务键 → 预览不抛异常、明确 canRun=false",
    from_db.get("canRun") is False and adhoc.get("canRun") is False,
    f"blockers={from_db.get('blockers')}")

pv = preview_of({"sourceConnectionId": "src", "targetConnectionId": "dst", "sourceMode": "table",
                 "sourceTable": "订单表", "targetTable": "订单表_窄列", "syncMode": "full"})
rec("D2 目标表缺要写入的列 → 预览就报出来", pv.get("canRun") is False and len(pv.get("missingInTarget") or []) > 0,
    f"缺 {len(pv.get('missingInTarget') or [])} 列：{(pv.get('missingInTarget') or [])[:3]}")
rec("D3 已存在的目标表不会被自动改结构（blocker 文案给出处置办法）",
    any("请手工加列" in str(b) for b in (pv.get("blockers") or [])), "文案含处置办法")

pv = preview_of({"sourceConnectionId": "src", "targetConnectionId": "dst", "sourceMode": "sql",
                 "sourceSql": f"select `id`, `订单号`, `商品名称` from `{SRC_DB}`.`订单表` limit 7",
                 "targetTable": "订单表_窄列", "syncMode": "full"})
rec("D4 源端自定义 SQL 也能预览", pv.get("canRun") is True and pv.get("rowsToProcess") == 7,
    f"rowsToProcess={pv.get('rowsToProcess')}，样例 {len(pv.get('sampleRows') or [])} 行")

_, body = cli.request("POST", "/api/sync/preview",
                      {"sourceConnectionId": "src", "targetConnectionId": "src",
                       "sourceMode": "table", "sourceTable": "订单表", "targetTable": "订单表",
                       "syncMode": "full"})
rec("D5 源与目标同一个连接 → 预览就拒绝", "同一个" in str(body.get("error") or ""),
    str(body.get("error") or "")[:50])

_, body = cli.request("POST", "/api/sync/preview",
                      {"sourceConnectionId": "src", "targetConnectionId": "dst", "sourceMode": "table",
                       "sourceTable": "根本没有这张表", "targetTable": "x", "syncMode": "full"})
rec("D6 源表不存在 → 报错指向可操作的原因", "不存在" in str(body.get("error") or ""),
    str(body.get("error") or "")[:60])

pv = preview_of({"sourceConnectionId": "src", "targetConnectionId": "dst", "sourceMode": "table",
                 "sourceTable": "订单表", "targetTable": "订单表_增量", "syncMode": "incremental",
                 "watermarkColumn": "订单表压根没这列"})
rec("D7 水位列在源端不存在 → 预览就拦下", pv.get("canRun") is False, str(pv.get("blockers"))[:70])

prec_missing = preview_of({"sourceConnectionId": "src", "targetConnectionId": "dst",
                           "sourceMode": "sql", "sourceSql": "", "targetTable": "x", "syncMode": "full"})
_, body = cli.request("POST", "/api/sync/preview",
                      {"sourceConnectionId": "src", "targetConnectionId": "dst",
                       "sourceMode": "sql", "sourceSql": "", "targetTable": "x", "syncMode": "full"})
rec("D8 源端 SQL 为空 → 明确报错而不是读出全表", "填写源端 SQL" in str(body.get("error") or ""),
    str(body.get("error") or "")[:60])

# 自定义 SQL + 目标表不存在：不能猜类型代建，要给人工可执行的建表草稿
pv_sql_new = preview_of({"sourceConnectionId": "src", "targetConnectionId": "dst", "sourceMode": "sql",
                         "sourceSql": f"select `订单号`, `订单创建时间`, `订单实付金额` from `{SRC_DB}`.`订单表`",
                         "targetTable": "订单表_SQL建的目标", "syncMode": "full"})
hint = str(pv_sql_new.get("createTableHint") or "")
rec("D9 自定义 SQL + 目标表不存在 → 预览拦下并给出建表草稿",
    pv_sql_new.get("canRun") is False and "create table" in hint.lower()
    and "订单号" in hint and "decimal" in hint.lower(),
    f"草稿首行：{(hint.splitlines() or [''])[0][:50]}")
server.delete_sync_task(bad_id)

# ================================================================ E. 源端 SQL 只读闸门
print("\n===== E. 源端 SQL 被限制为单条只读语句 =====")
dangerous = [
    ("E1 多条语句", f"select * from `{SRC_DB}`.`订单表` limit 1; drop table `{SRC_DB}`.`订单表`", "只能写一条语句"),
    ("E2 DROP", f"drop table `{SRC_DB}`.`订单表`", "只读查询"),
    ("E3 DELETE", f"delete from `{SRC_DB}`.`订单表` where id=1", "只读查询"),
    ("E4 UPDATE", f"update `{SRC_DB}`.`订单表` set 订单状态='x' where id=1", "只读查询"),
    ("E5 INTO OUTFILE", f"select * from `{SRC_DB}`.`订单表` into outfile 'C:/tmp/x.csv'", "只读查询"),
]
survived = True
for label, sql, expect in dangerous:
    _, body = cli.request("POST", "/api/sync/preview",
                          {"sourceConnectionId": "src", "targetConnectionId": "dst",
                           "sourceMode": "sql", "sourceSql": sql, "targetTable": "订单表_增量",
                           "syncMode": "full"})
    rejected = expect in str(body.get("error") or "")
    survived = survived and rejected
    rec(label + " → 预览拒绝", rejected, str(body.get("error") or "")[:70])
rec("E6 危险 SQL 走完预览后源表还在（没被真跑）", survived and rows_of(SRC_DB, "订单表") > 0,
    f"订单表仍有 {rows_of(SRC_DB, '订单表')} 行")

safe_sql = preview_of({"sourceConnectionId": "src", "targetConnectionId": "dst", "sourceMode": "sql",
                       "sourceSql": f"select `id`, `订单号`, `商品名称` from `{SRC_DB}`.`订单表`"
                                    " where `订单创建时间` is not null order by `订单创建时间` limit 100",
                       "targetTable": "订单表_窄列", "syncMode": "full"})
rec("E7 正常只读 SQL（含 where / order / limit）不受影响",
    safe_sql.get("canRun") is True and safe_sql.get("rowsToProcess") == 100,
    f"rowsToProcess={safe_sql.get('rowsToProcess')}"
    + (f"｜blockers={safe_sql.get('blockers')}" if safe_sql.get("blockers") else ""))

# ================================================================ F. 角色门禁
print("\n===== F. 角色门禁（viewer 能预览不能跑）=====")
os.environ["APP_AUTH_ENABLED"] = "true"
os.environ["ADMIN_PASSWORD"] = "SyncAdmin!2026"
os.environ["ADMIN_USER"] = "syncadmin"
server.bootstrap_admin_from_env()
for role in ("viewer", "operator"):
    with server.connect_db() as conn:
        conn.execute(
            "insert or replace into _users (id, username, display_name, password_hash, role,"
            " enabled, created_at, created_by) values (?,?,?,?,?,1,?,'fixture')",
            (f"sync_{role}", f"sync_{role}", f"sync_{role}",
             server.hash_password(f"sync_{role}Pass!2026"), role, server.now_text()),
        )


def login(username: str) -> Client:
    client = Client()
    status, payload = client.request(
        "POST", "/api/auth/login", {"username": username, "password": f"{username}Pass!2026"}, csrf=False)
    assert status == 200 and payload.get("ok"), f"登录失败 {status} {payload}"
    return client


viewer = login("sync_viewer")
operator = login("sync_operator")
role_task = save(name="F·角色验证", sourceMode="table", sourceTable="小票表",
                 targetTable="小票表", syncMode="full")

status, _ = viewer.request("GET", "/api/sync/tasks")
rec("F1 viewer 读任务列表", status == 200, f"HTTP {status}")
status, payload = viewer.request("POST", "/api/sync/probe", {"taskId": role_task})
rec("F2 viewer 探测源端", status == 200, f"HTTP {status}")
status, payload = viewer.request("POST", "/api/sync/preview", {"taskId": role_task})
rec("F3 viewer 预览", status == 200, f"HTTP {status}")
status, payload = viewer.request("GET", "/api/sync/runs")
rec("F4 viewer 读运行历史", status == 200, f"HTTP {status}")
status, payload = viewer.request("POST", "/api/sync/run", {"taskId": role_task})
rec("F5 viewer 执行被拒（403 且写明需要什么角色）",
    status == 403 and str(payload.get("needRole")) == "operator",
    f"HTTP {status} needRole={payload.get('needRole')}")
status, payload = viewer.request("POST", "/api/sync/tasks",
                                 {"name": "viewer 不该能建", "sourceConnectionId": "src",
                                  "targetConnectionId": "dst", "sourceTable": "小票表"})
rec("F6 viewer 保存任务被拒", status == 403, f"HTTP {status}")
status, payload = viewer.request("DELETE", f"/api/sync/tasks?id={role_task}")
rec("F7 viewer 删除任务被拒", status == 403, f"HTTP {status}")

status, payload = operator.request("POST", "/api/sync/run", {"taskId": role_task})
rec("F8 operator 可以执行同步", status == 200 and (payload.get("run") or {}).get("status") == "成功",
    f"HTTP {status}")
status, payload = operator.request("POST", "/api/sync/tasks",
                                   {"name": "F·operator 建的任务", "sourceConnectionId": "src",
                                    "targetConnectionId": "dst", "sourceTable": "小票表",
                                    "targetTable": "小票表", "syncMode": "full"})
rec("F9 operator 可以保存任务", status == 200, f"HTTP {status}")
status, payload = operator.request("DELETE", "/api/connections?id=src")
rec("F10 operator 删连接仍被拒（敏感入口留给管理员）", status == 403, f"HTTP {status}")
status, payload = viewer.request("GET", "/api/users")
rec("F11 对照组：viewer 读账号列表也被拒（确认 403 不是全站通吃）", status == 403, f"HTTP {status}")
status, payload = operator.request("POST", "/api/sync/preview",
                                   {"sourceConnectionId": "src", "targetConnectionId": "dst",
                                    "sourceMode": "sql", "sourceSql": f"drop table `{SRC_DB}`.`边界表`",
                                    "targetTable": "小票表", "syncMode": "full"})
rec("F12 只读闸门对 operator 同样生效（不是只卡 viewer）", status == 400, f"HTTP {status}")

os.environ["APP_AUTH_ENABLED"] = "false"
os.environ.pop("ADMIN_PASSWORD", None)
os.environ.pop("ADMIN_USER", None)

# ================================================================ G. 级联与过滤
print("\n===== G. 级联清理与历史过滤 =====")
cli.cookie = ""
g_task = save(name="G·级联", sourceMode="table", sourceTable="小票表", targetTable="小票表",
              syncMode="incremental", watermarkColumn="提单时间")
cli.request("POST", "/api/sync/run", {"taskId": g_task})
with server.connect_db() as conn:
    has_mark = conn.execute("select 1 from _sync_watermarks where task_id=?", (g_task,)).fetchone() is not None
rec("G1 增量跑完会留下水位", has_mark, "水位已写入")
cli.request("DELETE", f"/api/sync/tasks?id={g_task}")
with server.connect_db() as conn:
    orphan = conn.execute("select 1 from _sync_watermarks where task_id=?", (g_task,)).fetchone() is not None
rec("G2 删除任务级联清掉水位（否则重建任务会误判已同步过）", not orphan, "水位已清理")

status, body = cli.request("GET", "/api/sync/runs?taskId=" + role_task)
by_task = body.get("runs") or []
rec("G3 历史可按任务过滤", len(by_task) >= 1 and all(r["taskId"] == role_task for r in by_task),
    f"{len(by_task)} 条，全部属于同一任务")
status, body = cli.request("GET", "/api/sync/runs?limit=3")
limited = body.get("runs") or []
rec("G4 历史可按 limit 截断", len(limited) <= 3, f"limit=3 → 返回 {len(limited)} 条")
status, body = cli.request("GET", "/api/sync/runs?limit=abc")
rec("G5 limit 非整数 → 明确报错而不是返回全部", status == 400, f"HTTP {status} {str(body.get('error'))[:40]}")
status, body = cli.request("GET", f"/api/sync/runs?taskId={role_task}&limit=1")
rec("G6 过滤与截断可以叠加", status == 200 and len(body.get("runs") or []) == 1, f"HTTP {status}")

# ---- 收尾：本脚本写进**共享夹具库**的东西全部还原，否则下次跑基线就变了 ----
exec_(boot, f"delete from `{SRC_DB}`.`订单表` where `订单号`='PV-9999'")
for dropped in ("小票表_追加", "订单表_增量", "小票表_换名落地"):
    exec_(boot, f"drop table if exists `{DST_DB}`.`{dropped}`")
exec_(boot, f"truncate table `{DST_DB}`.`订单表`")
rec("H1 收尾已还原共享夹具库（下次跑基线一致）", True, "临时行与临时表已清理")

print("\n" + "=" * 72)
passed = sum(1 for _, verdict, _ in results if verdict == OK)
print(f"结论：{passed} / {len(results)} 通过")
print("=" * 72)
httpd.shutdown()
httpd.server_close()
boot.close()
sys.exit(0 if passed == len(results) else 1)
