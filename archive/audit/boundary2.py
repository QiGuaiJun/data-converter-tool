# -*- coding: utf-8 -*-
"""边界测试第二批：修正连接凭据字段后重测导出与查询边界。"""
from __future__ import annotations
import json
import os
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:51978"
R = []


def note(level, name, detail=""):
    R.append((level, name, detail))
    print({"OK": "  ok  ", "BUG": "  **BUG**  ", "WARN": "  warn  "}[level] + name + ((" | " + str(detail)) if detail else ""), flush=True)


def jpost(path, payload, timeout=120):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload, ensure_ascii=False).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, None
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


MYSQL = {"dbType": "mysql", "host": "127.0.0.1", "port": "3306",
         "user": "root", "password": "123456", "database": "dc_p2_test"}

# 先建一条连接，拿 connectionId
st, js = jpost("/api/connections", dict(MYSQL, name="bd-boundary-conn"))
CID = (js or {}).get("connection", {}).get("id")
print("connectionId =", CID)
if not CID:
    raise SystemExit("无法创建连接，终止")

# 同时准备 direct fields 形式（dbHost/dbUser/...）
DIRECT = {"targetDbType": "mysql", "dbHost": "127.0.0.1", "dbPort": "3306",
          "dbUser": "root", "dbPassword": "123456", "dbName": "dc_p2_test"}

print()
print("=" * 78)
print("2. 导出边界（修正后，两种凭据形式都测）")
print("=" * 78)
for label, base in (("connectionId", {**DIRECT, "connectionId": CID}), ("direct-fields", dict(DIRECT))):
    st, js = jpost("/api/export/run", {**base, "items": [{"type": "query", "name": "空", "sql": "select 1 as a where 1=0"}], "extension": "csv"})
    files = (js or {}).get("files") or []
    note("OK" if st == 200 else "BUG", "2.1[%s] 空结果集导出" % label,
         "status=%s files=%s rows=%s err=%s" % (st, [os.path.basename(f) for f in files], (js or {}).get("rows"), (js or {}).get("error", "")[:60]))

st, js = jpost("/api/export/run", {**DIRECT, "connectionId": CID, "items": [
    {"type": "query", "name": "超长名称" * 12, "sql": "select 1 as a"}], "extension": "xlsx"})
files = (js or {}).get("files") or []
if st == 200 and files:
    import zipfile, re as _re
    try:
        with zipfile.ZipFile(files[0]) as z:
            sheets = _re.findall(r'<sheet[^>]*name="([^"]+)"', z.read("xl/workbook.xml").decode("utf-8"))
    except Exception as e:
        sheets = ["<%s>" % e]
    note("OK" if all(len(s) <= 31 for s in sheets) else "BUG",
         "2.2b 超长名称 xlsx 导出（sheet 名须 ≤31 字符）",
         "sheets=%s 长度=%s" % (sheets, [len(s) for s in sheets]))
else:
    note("BUG", "2.2b 超长名称 xlsx 导出", "status=%s err=%s" % (st, (js or {}).get("error", "")[:80]))

st, js = jpost("/api/export/run", {**DIRECT, "connectionId": CID, "items": [{"type": "query", "name": "n", "sql": "select 5 as a"}],
                                   "extension": "csv", "exportFileName": "../../evil_name"})
files = (js or {}).get("files") or []
escaped = [f for f in files if ".." in os.path.normpath(f).split(os.sep)]
note("BUG" if escaped else "OK", "2.3 导出文件名路径穿越防护",
     "落盘=%s 越界=%s" % ([os.path.basename(f) for f in files], escaped or "无"))

st, js = jpost("/api/export/run", {**DIRECT, "connectionId": CID, "items": [{"type": "table", "table": "绝对不存在的表xyz"}], "extension": "csv"})
note("OK" if st == 400 else "WARN", "2.5 导出不存在的表被拒", "status=%s msg=%s" % (st, (js or {}).get("error", "")[:70]))

st, js = jpost("/api/export/preview", {**DIRECT, "connectionId": CID, "items": [
    {"type": "query", "name": "a", "sql": "select 1 as x"},
    {"type": "query", "name": "b", "sql": "select * from 不存在表_bd"},
    {"type": "query", "name": "c", "sql": "select 3 as z"}]})
pv = (js or {}).get("previews") or []
note("OK" if st == 200 and len(pv) == 3 and [p.get("ok") for p in pv] == [True, False, True] else "WARN",
     "2.6 导出预览坏项隔离（direct-fields 形式）", "ok序列=%s" % [p.get("ok") for p in pv])

print()
print("=" * 78)
print("3. 查询边界（修正后）")
print("=" * 78)
st, js = jpost("/api/query/run", {**DIRECT, "sql": "select * from export_large_people limit 1200"})
rows = (js or {}).get("rows") or []
note("OK" if st == 200 else "BUG", "3.1 大结果集（>1000 行）",
     "status=%s 返回行数=%s truncated=%s err=%s" % (st, len(rows), (js or {}).get("truncated"), (js or {}).get("error", "")[:60]))

for label, sql in (("3.2 前置块注释", "/* 注释 */ select 1 as a"),
                   ("3.2b 前置行注释", "-- 注释\nselect 1 as a"),
                   ("3.2c 注释+换行", "/* c */\n\nselect 1 as a")):
    st, js = jpost("/api/query/run", {**DIRECT, "sql": sql})
    note("OK" if st == 200 else "WARN", label, "status=%s cols=%s msg=%s" % (st, (js or {}).get("columns"), (js or {}).get("error", "")[:60]))

st, js = jpost("/api/query/run", {**DIRECT, "sql": "show tables"})
note("OK" if st == 200 else "WARN", "3.7 SHOW TABLES", "status=%s 行数=%s" % (st, len((js or {}).get("rows") or [])))

print()
print("=" * 78)
print("6. 表名/字段名特殊字符（SQL 注入面）")
print("=" * 78)
st, js = jpost("/api/query/run", {**DIRECT, "sql": "select 1 as a; drop table x"})
note("OK" if st == 400 else "BUG", "6.1 分号+写操作被拦", "status=%s msg=%s" % (st, (js or {}).get("error", "")[:60]))
st, js = jpost("/api/query/run", {**DIRECT, "sql": "select * from information_schema.tables limit 1"})
note("OK", "6.2 information_schema 可读（只读）", "status=%s" % st)
st, js = jpost("/api/target-table-details", {**DIRECT, "table": "x`; drop table y; --"})
note("OK" if st in (200, 400) else "BUG", "6.3 恶意表名的表结构查询", "status=%s msg=%s" % (st, (js or {}).get("error", "")[:60]))

print()
print("=" * 78)
bugs = [r for r in R if r[0] == "BUG"]
warns = [r for r in R if r[0] == "WARN"]
print("第二批汇总: 共 %d | BUG %d | WARN %d | OK %d" % (len(R), len(bugs), len(warns), len(R) - len(bugs) - len(warns)))
for lv, n, d in bugs: print("  [BUG ] %s | %s" % (n, d))
for lv, n, d in warns: print("  [WARN] %s | %s" % (n, d))
print("=" * 78)
