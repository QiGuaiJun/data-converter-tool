# -*- coding: utf-8 -*-
"""边界与对抗性测试：主动寻找未登记缺陷。全程在隔离环境（已由服务端 DATA_DIR 隔离）。"""
from __future__ import annotations
import io
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:51978"
R: list[tuple[str, str, str]] = []   # (级别, 名称, 说明)


def note(level, name, detail=""):
    R.append((level, name, detail))
    icon = {"OK": "  ok  ", "BUG": "  **BUG**  ", "WARN": "  warn  "}[level]
    print(icon + name + ((" | " + str(detail)) if detail else ""), flush=True)


def request(method, path, *, payload=None, raw_body=None, ctype=None, timeout=90):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    elif raw_body is not None:
        data = raw_body
        if ctype:
            headers["Content-Type"] = ctype
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        return 0, str(e).encode()


def jpost(path, payload, **kw):
    st, b = request("POST", path, payload=payload, **kw)
    try:
        return st, json.loads(b.decode("utf-8"))
    except Exception:
        return st, None


def jget(path, **kw):
    st, b = request("GET", path, **kw)
    try:
        return st, json.loads(b.decode("utf-8"))
    except Exception:
        return st, None


def mp(fields, files):
    bnd = "----bnd" + os.urandom(8).hex()
    out = bytearray()
    for k, v in fields.items():
        out += ("--%s\r\n" % bnd).encode()
        out += ('Content-Disposition: form-data; name="%s"\r\n\r\n' % k).encode()
        out += str(v).encode("utf-8") + b"\r\n"
    for name, fn, content, ct in files:
        out += ("--%s\r\n" % bnd).encode()
        out += ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n' % (name, fn)).encode()
        out += ("Content-Type: %s\r\n\r\n" % ct).encode()
        out += content + b"\r\n"
    out += ("--%s--\r\n" % bnd).encode()
    return bytes(out), "multipart/form-data; boundary=" + bnd


MYSQL = {"dbType": "mysql", "host": "127.0.0.1", "port": "3306",
         "user": "root", "password": "123456", "database": "dc_p2_test"}
MAP2 = json.dumps([
    {"sourceIndex": 0, "target": "name", "enabled": True, "defaultValue": "", "matchKey": False},
    {"sourceIndex": 1, "target": "amount", "enabled": True, "defaultValue": "", "matchKey": False},
])


def do_import(csv_bytes, table, filename="t.csv", extra=None, mapping=MAP2, mode="rebuild"):
    f = {"targetDbType": "sqlite", "importMode": mode, "tableName": table,
         "mapping": mapping, "tableCase": "lower", "fieldCase": "lower", "commitMode": "once"}
    if extra:
        f.update(extra)
    body, ct = mp(f, [("files", filename, csv_bytes, "text/csv")])
    st, b = request("POST", "/api/import", raw_body=body, ctype=ct)
    try:
        return st, json.loads(b.decode("utf-8"))
    except Exception:
        return st, b.decode("utf-8", "replace")[:200]


print("=" * 78)
print("1. 导入边界")
print("=" * 78)

# 1.1 只有表头的 CSV（0 数据行）
st, js = do_import("name,amount\n".encode("utf-8-sig"), "bd_header_only")
ok = st == 200 and js and js.get("ok") and js.get("summary", {}).get("rowsWritten") == 0
note("OK" if ok else "WARN", "1.1 仅表头 CSV（0 行）", "status=%s rowsWritten=%s" % (st, (js or {}).get("summary", {}).get("rowsWritten")))

# 1.2 完全空文件
st, js = do_import(b"", "bd_empty_file")
if st == 400:
    note("OK", "1.2 完全空文件被明确拒绝", "status=400 msg=%s" % (js or {}).get("error", "")[:50])
elif st == 200:
    note("WARN", "1.2 空文件返回 200（宽松处理）", "status=200 rowsWritten=%s" % (js or {}).get("summary", {}).get("rowsWritten"))
else:
    note("BUG", "1.2 空文件返回异常状态", "status=%s body=%s" % (st, js))

# 1.3 超宽表（120 列）
cols = ["c%d" % i for i in range(120)]
wide = (",".join(cols) + "\n" + ",".join(str(i) for i in range(120)) + "\n").encode("utf-8-sig")
wide_map = json.dumps([{"sourceIndex": i, "target": cols[i], "enabled": True, "defaultValue": "", "matchKey": False} for i in range(120)])
st, js = do_import(wide, "bd_wide", mapping=wide_map)
ok = st == 200 and js and js.get("ok") and js.get("summary", {}).get("rowsWritten") == 1
note("OK" if ok else "BUG", "1.3 超宽表（120 列）导入", "status=%s written=%s err=%s" % (st, (js or {}).get("summary", {}).get("rowsWritten"), (js or {}).get("error")))

# 1.4 超长单元格（20000 字符）
long_cell = "x" * 20000
st, js = do_import(("name,amount\n%s,1\n" % long_cell).encode("utf-8-sig"), "bd_longcell")
ok = st == 200 and js and js.get("ok") and js.get("summary", {}).get("rowsWritten") == 1
note("OK" if ok else "BUG", "1.4 超长单元格（20k 字符）", "status=%s written=%s err=%s" % (st, (js or {}).get("summary", {}).get("rowsWritten"), (js or {}).get("error")))

# 1.5 GBK 编码中文文件
gbk = "name,amount\n中文甲,1\n中文乙,2\n".encode("gbk")
st, js = do_import(gbk, "bd_gbk")
if st == 200 and js and js.get("ok") and js.get("summary", {}).get("rowsWritten") == 2:
    note("OK", "1.5 GBK 编码文件识别成功", "written=2")
else:
    note("WARN", "1.5 GBK 文件处理结果", "status=%s written=%s err=%s" % (st, (js or {}).get("summary", {}).get("rowsWritten"), (js or {}).get("error")))

# 1.6 文件名含特殊字符与中文
st, js = do_import("name,amount\nA,1\n".encode("utf-8-sig"), "bd_weirdname", filename="测试 文件(1)#&.csv")
note("OK" if st == 200 else "BUG", "1.6 文件名含中文/特殊字符", "status=%s" % st)

# 1.7 重复列名
st, js = do_import("name,name\n1,2\n".encode("utf-8-sig"), "bd_dupcol",
                   mapping=json.dumps([{"sourceIndex": 0, "target": "a", "enabled": True, "defaultValue": "", "matchKey": False},
                                       {"sourceIndex": 1, "target": "a", "enabled": True, "defaultValue": "", "matchKey": False}]))
note("OK" if st == 400 else "WARN", "1.7 映射到同名列（重复 target）", "status=%s msg=%s" % (st, (js or {}).get("error", "")[:60]))

# 1.8 表名注入（含反引号/分号）
st, js = do_import("name,amount\nA,1\n".encode("utf-8-sig"), "bd_inj`; drop table x; --")
if st == 200:
    note("WARN", "1.8 恶意表名被接受（需确认是否安全转义）", "tableName=%s" % (js or {}).get("tableName"))
else:
    note("OK", "1.8 恶意表名被拒绝", "status=%s" % st)

# 1.9 大批量（5000 行）性能与正确性
rows = "".join("%d,%d\n" % (i, i) for i in range(5000))
t0 = time.time()
st, js = do_import(("name,amount\n" + rows).encode("utf-8-sig"), "bd_big")
el = time.time() - t0
ok = st == 200 and js and js.get("summary", {}).get("rowsWritten") == 5000
note("OK" if ok else "BUG", "1.9 5000 行批量导入", "status=%s written=%s 耗时=%.2fs" % (st, (js or {}).get("summary", {}).get("rowsWritten"), el))

print()
print("=" * 78)
print("2. 导出边界")
print("=" * 78)

# 2.1 空结果集导出
st, js = jpost("/api/export/run", {**MYSQL, "items": [{"type": "query", "name": "空", "sql": "select 1 as a where 1=0"}], "extension": "csv"})
files = (js or {}).get("files") or []
note("OK" if st == 200 else "BUG", "2.1 空结果集导出", "status=%s files=%s rows=%s" % (st, [os.path.basename(f) for f in files], (js or {}).get("rows")))

# 2.2 超长 sheet 名（>31 字符，Excel 硬限制）
st, js = jpost("/api/export/preview", {**MYSQL, "items": [
    {"type": "query", "name": "这是一个非常非常非常长的查询名称超过了三十一个字符的限制", "sql": "select 1 as a"}]})
ok = st == 200
note("OK" if ok else "BUG", "2.2 超长查询名预览", "status=%s name=%s" % (st, (js or {}).get("sourceName", "")[:40]))

st, js = jpost("/api/export/run", {**MYSQL, "items": [
    {"type": "query", "name": "超长名称" * 12, "sql": "select 1 as a"}], "extension": "xlsx"})
files = (js or {}).get("files") or []
ok = st == 200 and files
note("OK" if ok else "BUG", "2.2b 超长名称 xlsx 导出（sheet 名截断）", "status=%s files=%s" % (st, [os.path.basename(f) for f in files] if files else (js or {}).get("error")))

# 2.3 导出文件名含非法路径字符
st, js = jpost("/api/export/run", {**MYSQL, "items": [{"type": "query", "name": "n", "sql": "select 5 as a"}],
                                   "extension": "csv", "exportFileName": "../../evil_name"})
files = (js or {}).get("files") or []
escaped = any(".." in os.path.normpath(f).split(os.sep) for f in files)
note("BUG" if (files and escaped) else "OK", "2.3 导出文件名路径穿越防护",
     "文件名=%s" % ([os.path.basename(f) for f in files] if files else (js or {}).get("error")))

# 2.4 不支持扩展名
st, js = jpost("/api/export/run", {**MYSQL, "items": [{"type": "query", "name": "n", "sql": "select 1"}], "extension": "exe"})
note("OK" if st == 400 else "BUG", "2.4 非法扩展名被拒", "status=%s msg=%s" % (st, (js or {}).get("error", "")[:50]))

# 2.5 表名不存在
st, js = jpost("/api/export/run", {**MYSQL, "items": [{"type": "table", "table": "绝对不存在的表xyz"}], "extension": "csv"})
note("OK" if st == 400 else "WARN", "2.5 导出不存在的表被拒", "status=%s msg=%s" % (st, (js or {}).get("error", "")[:60]))

print()
print("=" * 78)
print("3. 查询边界")
print("=" * 78)

# 3.1 大结果集截断（limit 1000）
st, js = jpost("/api/query/run", {**MYSQL, "sql": "select * from export_large_people limit 1200"})
rows = (js or {}).get("rows") or []
note("OK" if st == 200 and len(rows) <= 1000 else "WARN", "3.1 大结果集截断到 1000 行",
     "status=%s 返回行数=%s truncated=%s" % (st, len(rows), (js or {}).get("truncated")))

# 3.2 SQL 带注释
st, js = jpost("/api/query/run", {**MYSQL, "sql": "/* 注释 */ select 1 as a"})
note("OK" if st == 200 else "WARN", "3.2 前置块注释的 SELECT", "status=%s cols=%s" % (st, (js or {}).get("columns")))

# 3.3 小写 select
st, js = jpost("/api/query/run", {**MYSQL, "sql": "select 2 as b"})
note("OK" if st == 200 else "BUG", "3.3 小写 select", "status=%s" % st)

# 3.4 前导空白 + 换行的 SELECT
st, js = jpost("/api/query/run", {**MYSQL, "sql": "\n   select 3 as c\n"})
note("OK" if st == 200 else "BUG", "3.4 前导空白/换行的 SELECT", "status=%s" % st)

# 3.5 只读 CTE（WITH）
st, js = jpost("/api/query/run", {**MYSQL, "sql": "with t as (select 1 as a) select * from t"})
note("OK" if st == 200 else "WARN", "3.5 WITH 只读查询", "status=%s" % st)

# 3.6 超长 SQL
st, js = jpost("/api/query/run", {**MYSQL, "sql": "select " + ",".join("1 as c%d" % i for i in range(500))})
note("OK" if st == 200 else "WARN", "3.6 500 列的 SELECT", "status=%s 列数=%s" % (st, len((js or {}).get("columns") or [])))

print()
print("=" * 78)
print("4. 并发与稳定性")
print("=" * 78)

# 4.1 并发 8 个查询
errs = []


def worker(i):
    try:
        s, j = jpost("/api/query/run", {**MYSQL, "sql": "select %d as v" % i})
        if s != 200:
            errs.append((i, s))
    except Exception as e:  # noqa: BLE001
        errs.append((i, str(e)))


ths = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
t0 = time.time()
for t in ths: t.start()
for t in ths: t.join()
note("OK" if not errs else "BUG", "4.1 并发 8 个查询", "耗时=%.2fs 失败=%s" % (time.time() - t0, errs or "0"))

# 4.2 并发 4 个导入到不同表
ierrs = []


def iworker(i):
    try:
        s, j = do_import(("name,amount\nA%d,1\n" % i).encode("utf-8-sig"), "bd_conc_%d" % i)
        if s != 200:
            ierrs.append((i, s, (j or {}).get("error")))
    except Exception as e:  # noqa: BLE001
        ierrs.append((i, str(e)))


ths = [threading.Thread(target=iworker, args=(i,)) for i in range(4)]
t0 = time.time()
for t in ths: t.start()
for t in ths: t.join()
note("OK" if not ierrs else "BUG", "4.2 并发 4 个导入", "耗时=%.2fs 失败=%s" % (time.time() - t0, ierrs or "0"))

# 4.3 连续 60 次调用（资源泄漏与稳定性）
bad = 0
t0 = time.time()
for i in range(60):
    s, j = jget("/api/ping")
    if s != 200: bad += 1
note("OK" if bad == 0 else "BUG", "4.3 连续 60 次 /api/ping", "耗时=%.2fs 失败=%d" % (time.time() - t0, bad))

# 4.4 连续 12 次多查询预览（检查句柄/连接泄漏）
bad = 0
t0 = time.time()
for i in range(12):
    s, j = jpost("/api/export/preview", {**MYSQL, "items": [{"type": "query", "name": "a", "sql": "select 1 as x"}, {"type": "query", "name": "b", "sql": "select 2 as y"}]})
    if s != 200: bad += 1
note("OK" if bad == 0 else "BUG", "4.4 连续 12 次多查询预览（连接泄漏检查）", "耗时=%.2fs 失败=%d" % (time.time() - t0, bad))

print()
print("=" * 78)
print("5. 定时任务边界")
print("=" * 78)
st, js = jpost("/api/jobs", {"name": "bd-job", "steps": [{"name": "s", "type": "query", "enabled": True,
                                                          "config": {**MYSQL, "sql": "select 1"}}]})
jid = (js or {}).get("job", {}).get("id")
# 5.1 once 模式 past 时间
st, js = jpost("/api/schedules", {"name": "bd-once-past", "jobId": jid,
                                  "rule": {"mode": "once"}, "startAt": "2020-01-01 00:00:00", "enabled": True})
nra = (js or {}).get("schedule", {}).get("nextRunAt")
note("OK" if st == 200 else "WARN", "5.1 once 模式 + 过去时间", "status=%s nextRunAt=%r" % (st, nra))
# 5.2 interval seconds
st, js = jpost("/api/schedules", {"name": "bd-int-sec", "jobId": jid,
                                  "rule": {"mode": "interval", "amount": 30, "unit": "seconds"}, "enabled": True})
note("OK" if st == 200 and (js or {}).get("schedule", {}).get("nextRunAt") else "WARN",
     "5.2 interval/seconds 模式", "nextRunAt=%s" % (js or {}).get("schedule", {}).get("nextRunAt"))
# 5.3 非法 rule mode
st, js = jpost("/api/schedules", {"name": "bd-bad-rule", "jobId": jid, "rule": {"mode": "不存在的模式"}, "enabled": True})
note("OK" if st == 400 else "WARN", "5.3 非法 rule mode", "status=%s nextRunAt=%s" % (st, (js or {}).get("schedule", {}).get("nextRunAt")))
# 5.4 endAt 早于现在
st, js = jpost("/api/schedules", {"name": "bd-ended", "jobId": jid,
                                  "rule": {"mode": "daily", "time": "09:00:00"}, "endAt": "2020-01-01 00:00:00", "enabled": True})
note("OK", "5.4 已过期 endAt 的 daily 任务", "status=%s nextRunAt=%r" % (st, (js or {}).get("schedule", {}).get("nextRunAt")))

print()
print("=" * 78)
bugs = [r for r in R if r[0] == "BUG"]
warns = [r for r in R if r[0] == "WARN"]
print("边界测试汇总: 共 %d 项 | BUG %d | WARN %d | OK %d" % (len(R), len(bugs), len(warns), len(R) - len(bugs) - len(warns)))
for lv, n, d in bugs:
    print("  [BUG ] %s | %s" % (n, d))
for lv, n, d in warns:
    print("  [WARN] %s | %s" % (n, d))
print("=" * 78)
