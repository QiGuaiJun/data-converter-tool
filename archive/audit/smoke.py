# -*- coding: utf-8 -*-
"""data-converter-tool 端到端冒烟审计（只读 + 隔离环境）。
运行前需先以隔离 DATA_DIR/UPLOADS_DIR/EXPORTS_DIR 启动 51978 服务。
"""
from __future__ import annotations
import json
import os
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:51978"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" | " + str(detail)) if detail else ""), flush=True)
    return bool(ok)


def _h(headers) -> dict:
    """响应头统一小写 key（服务端返回的是 Content-type）。"""
    try:
        return {str(k).lower(): v for k, v in dict(headers or {}).items()}
    except Exception:  # noqa: BLE001
        return {}


def request(method, path, *, payload=None, raw_body=None, content_type=None, timeout=60):
    url = BASE + path
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif raw_body is not None:
        data = raw_body
        if content_type:
            headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return resp.status, body, _h(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), _h(exc.headers)
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc).encode("utf-8"), {}


def jget(path, **kw):
    status, body, headers = request("GET", path, **kw)
    try:
        return status, json.loads(body.decode("utf-8")), headers
    except Exception:
        return status, None, headers


def jpost(path, payload, **kw):
    status, body, headers = request("POST", path, payload=payload, **kw)
    try:
        return status, json.loads(body.decode("utf-8")), headers
    except Exception:
        return status, None, headers


def multipart(fields: dict, files: list[tuple[str, str, bytes]]) -> tuple[bytes, str]:
    boundary = "----audit" + uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += ("--%s\r\n" % boundary).encode()
        out += ('Content-Disposition: form-data; name="%s"\r\n\r\n' % k).encode()
        out += str(v).encode("utf-8") + b"\r\n"
    for name, filename, content in files:
        out += ("--%s\r\n" % boundary).encode()
        out += ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n' % (name, filename)).encode()
        out += b"Content-Type: text/csv\r\n\r\n"
        out += content + b"\r\n"
    out += ("--%s--\r\n" % boundary).encode()
    return bytes(out), "multipart/form-data; boundary=" + boundary


MYSQL = {"dbType": "mysql", "host": "127.0.0.1", "port": "3306",
         "user": "root", "password": "123456", "database": "dc_p2_test"}

print("=" * 78)
print("A. 健康检查与元信息")
print("=" * 78)
st, js, _ = jget("/api/ping")
check("A1 GET /api/ping -> 200", st == 200, "status=%s" % st)
check("A2 /api/ping 版本字段一致且=1.4.0",
      bool(js) and js.get("appVersion") == "1.4.0" and js.get("version") == "1.4.0",
      "appVersion=%s version=%s" % (js.get("appVersion") if js else None, js.get("version") if js else None))

st, js, _ = jget("/api/meta")
check("A3 GET /api/meta -> 200", st == 200, "status=%s" % st)
check("A4 /api/meta 版本=1.4.0", bool(js) and js.get("appVersion") == "1.4.0",
      "appVersion=%s" % (js.get("appVersion") if js else None))

st, js, _ = jget("/api/storage/status")
check("A5 GET /api/storage/status -> 200", st == 200, "status=%s" % st)

print()
print("=" * 78)
print("B. 静态资源可达性（全部页面 + 全部 JS/CSS）")
print("=" * 78)
pages = ["/", "/export.html", "/connections.html", "/query.html", "/tables.html",
         "/jobs.html", "/schedule.html", "/sync.html", "/api.html", "/feedback.html",
         "/docs.html", "/index.html"]
assets = ["/styles.css", "/theme.css", "/connections.css", "/sql-editor.css",
          "/app.js", "/export.js", "/query.js", "/tables.js", "/jobs.js",
          "/schedule.js", "/connections.js", "/sql-editor.js", "/module-pages.js",
          "/version.js", "/docs.js", "/favicon.svg"]
bad_pages, bad_assets = [], []
for p in pages:
    st, body, _ = request("GET", p)
    if st != 200 or len(body) < 200:
        bad_pages.append("%s(status=%s,len=%s)" % (p, st, len(body)))
check("B1 全部 %d 个页面返回 200 且非空" % len(pages), not bad_pages, ", ".join(bad_pages) or "全部 OK")

for a in assets:
    st, body, _ = request("GET", a)
    if st != 200 or len(body) < 50:
        bad_assets.append("%s(status=%s,len=%s)" % (a, st, len(body)))
check("B2 全部 %d 个静态资源返回 200 且非空" % len(assets), not bad_assets, ", ".join(bad_assets) or "全部 OK")

st, body, hdr = request("GET", "/theme.css")
ct = (hdr.get("content-type") or "").lower()
check("B3 theme.css Content-Type 为 css", "css" in ct, ct)
st, body, hdr = request("GET", "/docs.js")
ct = (hdr.get("content-type") or "").lower()
check("B4 docs.js Content-Type 为 javascript", "javascript" in ct, ct)

print()
print("=" * 78)
print("C. 连接管理 CRUD")
print("=" * 78)
conn_name = "audit-smoke-conn"
st, js, _ = jpost("/api/connections", dict(MYSQL, name=conn_name))
conn_id = (js or {}).get("connection", {}).get("id") if js else None
check("C1 保存 MySQL 连接 -> 200 且返回 id", st == 200 and bool(conn_id), "status=%s id=%s" % (st, conn_id))
check("C2 保存响应不含明文密码",
      bool(js) and "123456" not in json.dumps(js, ensure_ascii=False),
      "响应片段=%s" % json.dumps(js, ensure_ascii=False)[:160] if js else "无")

st, js, _ = jpost("/api/connections/test", dict(MYSQL))
check("C3 连接测试 -> ok 且能列出库", st == 200 and bool(js and js.get("ok")), "status=%s ok=%s" % (st, (js or {}).get("ok")))
dbs = [d.lower() for d in (js or {}).get("databases", [])]
check("C4 连接测试不泄露系统库", not ({"information_schema", "mysql", "performance_schema", "sys"} & set(dbs)),
      "databases 数=%d" % len(dbs))

st, js, _ = jget("/api/connections")
ids = [c.get("id") for c in (js or {}).get("connections", [])]
check("C5 连接列表包含新建连接", st == 200 and conn_id in ids, "共 %d 条" % len(ids))
check("C6 连接列表不含明文密码",
      bool(js) and "123456" not in json.dumps(js, ensure_ascii=False), "ok" if js else "无")

st, js, _ = jpost("/api/connections", {"dbType": "postgres", "host": "x", "user": "y"})
check("C7 非 MySQL 类型被明确拒绝（产品限制已知）", st == 400, "status=%s msg=%s" % (st, (js or {}).get("error", "")[:60]))

print()
print("=" * 78)
print("D. 表列表（含连接式）")
print("=" * 78)
st, js, _ = jpost("/api/target-tables", {**MYSQL, "connectionId": conn_id})
tabs = (js or {}).get("tables") or []
if not tabs and isinstance(js, dict):
    for k in ("items", "data", "list"):
        if isinstance(js.get(k), list):
            tabs = js[k]
check("D1 连接式列出目标库表 -> 200 且非空", st == 200 and len(tabs) > 0, "status=%s 表数=%d" % (st, len(tabs)))
st, js, _ = jpost("/api/tables", {**MYSQL, "connectionId": conn_id})
check("D2 /api/tables 兼容 POST -> 200", st == 200, "status=%s" % st)

print()
print("=" * 78)
print("E. 导入（sqlite 目标，隔离安全）")
print("=" * 78)
mapping = json.dumps([
    {"sourceIndex": 0, "target": "name", "enabled": True, "defaultValue": "", "matchKey": False},
    {"sourceIndex": 1, "target": "amount", "enabled": True, "defaultValue": "", "matchKey": False},
])
csv_bytes = "name,amount\n审计甲,11\n审计乙,22\n审计丙,33\n".encode("utf-8-sig")
body, ct = multipart({
    "targetDbType": "sqlite", "importMode": "rebuild", "tableName": "audit_smoke_import",
    "mapping": mapping, "tableCase": "lower", "fieldCase": "lower", "commitMode": "once",
}, [("files", "audit_smoke.csv", csv_bytes)])
st, raw, _ = request("POST", "/api/import", raw_body=body, content_type=ct)
try:
    js = json.loads(raw.decode("utf-8"))
except Exception:
    js = None
check("E1 sqlite 导入 3 行 -> ok", st == 200 and bool(js and js.get("ok")), "status=%s raw=%s" % (st, raw.decode("utf-8", "replace")[:200]))
if js and js.get("ok"):
    s = js.get("summary", {})
    check("E2 导入行数=3 且无失败文件", s.get("rowsWritten") == 3 and s.get("failedFiles") == 0, json.dumps(s, ensure_ascii=False))

st, raw, _ = request("POST", "/api/import", raw_body=b"", content_type="multipart/form-data; boundary=x")
check("E3 空导入请求被拒绝（400 而非 500）", st == 400, "status=%s" % st)

print()
print("=" * 78)
print("F. 查询")
print("=" * 78)
st, js, _ = jpost("/api/query/run", {**MYSQL, "connectionId": conn_id, "sql": "select 1 as audit_col"})
check("F1 合法 SELECT -> 200 且有列", st == 200 and bool(js and js.get("columns")), "status=%s cols=%s" % (st, (js or {}).get("columns")))
st, js, _ = jpost("/api/query/run", {**MYSQL, "connectionId": conn_id, "sql": "drop table audit_x"})
check("F2 写操作被只读白名单拦截", st == 400, "status=%s msg=%s" % (st, (js or {}).get("error", "")[:70]))
st, js, _ = jpost("/api/query/run", {**MYSQL, "connectionId": conn_id, "sql": "select 1; select 2"})
check("F3 多语句被拒绝", st == 400, "status=%s msg=%s" % (st, (js or {}).get("error", "")[:70]))
st, js, _ = jpost("/api/queries", {"name": "审计查询", "sql": "select 1", "connectionId": conn_id})
qid = (js or {}).get("query", {}).get("id") if js else None
check("F4 保存查询 -> 200", st == 200 and bool(qid), "status=%s id=%s" % (st, qid))

print()
print("=" * 78)
print("G. 导出")
print("=" * 78)
st, js, _ = jpost("/api/export/sources", {**MYSQL, "connectionId": conn_id})
check("G1 /api/export/sources -> 200", st == 200, "status=%s" % st)

items2 = [
    {"type": "query", "name": "审计一", "sql": "select 1 as a"},
    {"type": "query", "name": "审计二", "sql": "select 2 as b, 3 as c"},
]
st, js, _ = jpost("/api/export/preview", {**MYSQL, "connectionId": conn_id, "items": items2})
pv = (js or {}).get("previews") if js else None
check("G2 多查询预览返回 2 个结果集（M12-001）", st == 200 and isinstance(pv, list) and len(pv) == 2,
      "status=%s previews=%s" % (st, len(pv) if isinstance(pv, list) else pv))
if isinstance(pv, list) and len(pv) == 2:
    check("G3 两个结果集列数不同（证明独立取数）",
          len(pv[0].get("columns", [])) == 1 and len(pv[1].get("columns", [])) == 2,
          "%s vs %s" % (pv[0].get("columns"), pv[1].get("columns")))
    check("G4 顶层向后兼容字段仍在",
          "sourceName" in (js or {}) and "columns" in (js or {}) and "rows" in (js or {}),
          "keys=%s" % sorted(js.keys())[:12])

items_bad = [
    {"type": "query", "name": "好", "sql": "select 1 as a"},
    {"type": "query", "name": "坏", "sql": "select * from 绝对不存在的表_audit"},
    {"type": "query", "name": "好二", "sql": "select 3 as z"},
]
st, js, _ = jpost("/api/export/preview", {**MYSQL, "connectionId": conn_id, "items": items_bad})
pv = (js or {}).get("previews") if js else None
if isinstance(pv, list) and len(pv) == 3:
    check("G5 坏 SQL 被隔离（中间项失败、其余正常）",
          pv[1].get("ok") is False and pv[0].get("ok") is not False and pv[2].get("ok") is not False,
          "ok 序列=%s" % [p.get("ok") for p in pv])
else:
    check("G5 坏 SQL 被隔离", False, "previews=%s" % (pv if not isinstance(pv, list) else len(pv)))

st, js, _ = jpost("/api/export/run", {**MYSQL, "connectionId": conn_id, "items": items2, "extension": "xlsx"})
files = (js or {}).get("files") if js else None
check("G6a 多查询 xlsx 导出 -> 单工作簿（设计行为：一文件多 sheet）",
      st == 200 and isinstance(files, list) and len(files) == 1,
      "status=%s files=%s" % (st, files if not files else [os.path.basename(f) for f in files]))


def xlsx_sheets(path):
    import re as _re
    import zipfile
    with zipfile.ZipFile(path) as z:
        xml = z.read("xl/workbook.xml").decode("utf-8")
    return _re.findall(r'<sheet[^>]*name="([^"]+)"', xml)


if isinstance(files, list) and files:
    exists = [os.path.isfile(f) for f in files]
    check("G6b 导出文件真实落盘且非空",
          all(exists) and all(os.path.getsize(f) > 0 for f in files if os.path.isfile(f)),
          str([(os.path.basename(f), os.path.getsize(f) if os.path.isfile(f) else "缺失") for f in files]))
    try:
        sheets = xlsx_sheets(files[0])
    except Exception as exc:  # noqa: BLE001
        sheets = ["<解析失败:%s>" % exc]
    check("G6c xlsx 内含 2 个 sheet（两个查询各自独立）",
          len(sheets) == 2 and sheets == ["审计一", "审计二"], "sheets=%s" % sheets)
    url = "/api/export/download?path=" + urllib.parse.quote(files[0])
    st, body, _ = request("GET", url)
    check("G8 下载接口可取回导出文件", st == 200 and len(body) > 0, "status=%s bytes=%d" % (st, len(body)))

# csv 扩展名走另一条分支：每个 item 一个文件
st, js, _ = jpost("/api/export/run", {**MYSQL, "connectionId": conn_id, "items": items2, "extension": "csv"})
cfiles = (js or {}).get("files") if js else None
check("G6d 多查询 csv 导出 -> 2 个独立文件",
      st == 200 and isinstance(cfiles, list) and len(cfiles) == 2,
      "status=%s files=%s" % (st, cfiles if not cfiles else [os.path.basename(f) for f in cfiles]))
if isinstance(cfiles, list) and len(cfiles) == 2:
    check("G6e 两个 csv 内容各自独立（未互相覆盖）",
          all(os.path.isfile(f) and os.path.getsize(f) > 0 for f in cfiles)
          and open(cfiles[0], encoding="utf-8-sig").read() != open(cfiles[1], encoding="utf-8-sig").read(),
          str([(os.path.basename(f), os.path.getsize(f) if os.path.isfile(f) else "缺失") for f in cfiles]))

# 安全：路径穿越探测
st, body, _ = request("GET", "/api/export/download?path=" + urllib.parse.quote("C:/Windows/win.ini"))
check("G9 下载接口拒绝目录外路径（无路径穿越）", st != 200 or b"[fonts]" not in body,
      "status=%s 泄露=%s" % (st, b"win.ini" in body or b"[fonts]" in body))

print()
print("=" * 78)
print("H. 作业")
print("=" * 78)
steps = [{"name": "审计查询步", "type": "query", "enabled": True,
          "config": {**MYSQL, "connectionId": conn_id, "sql": "select 1 as a"}}]
st, js, _ = jpost("/api/jobs", {"name": "审计作业", "steps": steps})
jid = (js or {}).get("job", {}).get("id") if js else None
check("H1 保存作业 -> 200", st == 200 and bool(jid), "status=%s id=%s" % (st, jid))
st, js, _ = jpost("/api/jobs", {"name": "审计作业无步骤", "steps": []})
check("H2 空步骤作业被拒绝", st == 400, "status=%s msg=%s" % (st, (js or {}).get("error", "")[:50]))
st, js, _ = jpost("/api/jobs", {"name": "含同步步骤", "steps": [{"name": "s", "type": "sync", "enabled": True, "config": {}}]})
check("H3 sync 步骤被明确拒绝（产品限制）", st == 400, "status=%s msg=%s" % (st, (js or {}).get("error", "")[:50]))
if jid:
    st, js, _ = jpost("/api/jobs/run", {"id": jid})
    run = (js or {}).get("run") if js else None
    check("H4 立即执行作业 -> 200 且有状态", st == 200 and bool(run and run.get("status")),
          "status=%s run.status=%s" % (st, (run or {}).get("status")))
    st, js, _ = jget("/api/job-runs?jobId=" + jid)
    check("H5 作业运行记录可查", st == 200, "status=%s runs=%s" % (st, len((js or {}).get("runs", []) or [])))

print()
print("=" * 78)
print("I. 定时任务")
print("=" * 78)
if jid:
    st, js, _ = jpost("/api/schedules", {"name": "审计定时", "jobId": jid,
                                        "rule": {"mode": "interval", "amount": "1", "unit": "hours"},
                                        "enabled": True})
    sid = (js or {}).get("schedule", {}).get("id") if js else None
    nra = (js or {}).get("schedule", {}).get("nextRunAt") if js else None
    check("I1 创建定时任务 -> 200 且算出 nextRunAt", st == 200 and bool(sid) and bool(nra),
          "status=%s id=%s nextRunAt=%s" % (st, sid, nra))
    st, js, _ = jpost("/api/schedules", {"name": "挂在不存在作业上", "jobId": "不存在的job", "rule": {"mode": "once"}})
    check("I2 引用不存在作业被拒绝", st == 400, "status=%s" % st)
    if sid:
        st, js, _ = jpost("/api/schedules/pause", {"id": sid})
        check("I3 暂停定时任务 -> enabled=0", st == 200 and (js or {}).get("schedule", {}).get("enabled") in (0, False),
              "enabled=%s" % (js or {}).get("schedule", {}).get("enabled"))
        st, js, _ = jpost("/api/schedules/start", {"id": sid})
        check("I4 启动定时任务 -> enabled=1 且 nextRunAt 非空",
              st == 200 and (js or {}).get("schedule", {}).get("enabled") in (1, True)
              and bool((js or {}).get("schedule", {}).get("nextRunAt")),
              "enabled=%s next=%s" % ((js or {}).get("schedule", {}).get("enabled"), (js or {}).get("schedule", {}).get("nextRunAt")))

print()
print("=" * 78)
print("J. 文档中心")
print("=" * 78)
st, js, _ = jget("/api/docs")
check("J1 /api/docs 索引 -> 200", st == 200, "status=%s" % st)
if isinstance(js, dict):
    idx = js.get("index") or js.get("docs") or []
    check("J2 文档索引非空", len(idx) > 0, "条目=%d keys=%s" % (len(idx), sorted(js.keys())))
    if idx:
        first = idx[0]
        did = first.get("id") if isinstance(first, dict) else None
        if did:
            st, js2, _ = jget("/api/docs?id=" + urllib.parse.quote(str(did)))
            html = (js2 or {}).get("html") or ""
            check("J3 单篇文档可取且有正文", st == 200 and len(html) > 200,
                  "id=%s status=%s html=%d 字节" % (did, st, len(html)))
    # 全部文档逐个可加载
    bad_docs = []
    for item in idx:
        did = item.get("id") if isinstance(item, dict) else None
        if not did:
            continue
        st, js2, _ = jget("/api/docs?id=" + urllib.parse.quote(str(did)))
        if st != 200 or len(((js2 or {}).get("html") or "")) < 100:
            bad_docs.append("%s(status=%s)" % (did, st))
    check("J4 索引中 %d 篇文档全部可加载" % len(idx), not bad_docs, ", ".join(bad_docs) or "全部 OK")

print()
print("=" * 78)
print("K. 错误处理与安全")
print("=" * 78)
st, js, _ = jpost("/api/__no_such_endpoint__", {})
check("K1 未知接口 -> 404", st == 404, "status=%s body=%s" % (st, (js or {}).get("error", "")))
st, js, _ = request("POST", "/api/query/run", raw_body=b"{bad json", content_type="application/json")[:2] + ({},)
check("K2 坏 JSON -> 4xx 而非 500", 400 <= st < 500, "status=%s" % st)
st, body, _ = request("GET", "/../../../../Windows/win.ini")
check("K3 静态路径穿越被阻止", st != 200 or b"[fonts]" not in body, "status=%s" % st)
st, js, _ = jget("/api/export/download?path=../../../../Windows/win.ini")
check("K4 下载路径穿越被阻止", st != 200, "status=%s" % st)

print()
print("=" * 78)
tot = len(RESULTS)
passed = sum(1 for _, ok, _ in RESULTS if ok)
failed = tot - passed
print("冒烟汇总: 共 %d 项 | PASS %d | FAIL %d" % (tot, passed, failed))
if failed:
    print("失败项:")
    for n, ok, d in RESULTS:
        if not ok:
            print("  - %s | %s" % (n, d))
print("=" * 78)
sys.exit(0 if failed == 0 else 1)
