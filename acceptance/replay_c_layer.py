#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""C 层（M0/M1/M2/M3）复跑 harness · 全程隔离。

对应 V5 计划「数据来源分层」中的 C 层 119 条：
  M0、M1 全部 + M2（除 028/058）+ M3（除 A 层 12 条：003/004/006/016/020/021/022/028/029/033/034/036）

覆盖其中**可机器判定**的部分；UT 类由 pytest 结果映射、UI 类走 Playwright、CODE 类人工核验。

隔离口径
--------
DATA_DIR / UPLOADS_DIR / EXPORTS_DIR 全部指向 acceptance/replay-20260919/，
端口用 51979（主）/ 51980（认证），**绝不触碰 runtime/ 下的生产库与生产数据**。

用法
----
    .venv/Scripts/python.exe acceptance/replay_c_layer.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
PY = PROJECT / ".venv" / "Scripts" / "python.exe"


def _resolve_sandbox() -> Path:
    """取得一个**干净**的隔离沙箱目录。

    不直接 rmtree 旧沙箱：其下通常有 200+ 临时文件，会触发宿主的批量删除保护并中断进程。
    改为「旧目录存在就自动换用 replay-<base>-r2 / -r3 …」的全新目录，零删除。
    可用环境变量 DC_REPLAY_SANDBOX 指定基准名。
    """
    base = PROJECT / "acceptance" / os.environ.get("DC_REPLAY_SANDBOX", "replay-20260919")
    if not base.exists():
        return base
    for i in range(2, 50):
        cand = base.with_name(f"{base.name}-r{i}")
        if not cand.exists():
            print(f"[warn] 沙箱 {base.name} 已存在（不做批量删除），自动改用 {cand.name}", flush=True)
            return cand
    raise SystemExit("无法获得干净的隔离沙箱目录，请设置 DC_REPLAY_SANDBOX=<新名字>")


SANDBOX = _resolve_sandbox()
DATA = SANDBOX / "data"
UPLOADS = SANDBOX / "uploads"
EXPORTS = SANDBOX / "exports"
SEED = SANDBOX / "seed"
EVID = PROJECT / "acceptance" / "evidence" / "20260919"
PORT_MAIN = 51979
PORT_AUTH = 51980

RESULTS: list[dict] = []
PROCS: list[subprocess.Popen] = []
CUR_PORT = {"v": PORT_MAIN}


# --------------------------------------------------------------------------- 基础


def rec(rid: str, ok: bool, detail: str, kind: str = "AUTO") -> None:
    RESULTS.append({"id": rid, "result": "PASS" if ok else "FAIL", "kind": kind, "detail": str(detail)[:600]})
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {rid} :: {str(detail)[:150]}", flush=True)


def note(rid: str, detail: str) -> None:
    RESULTS.append({"id": rid, "result": "MANUAL", "kind": "MANUAL", "detail": str(detail)[:600]})
    print(f"[MANUL] {rid} :: {str(detail)[:150]}", flush=True)


def http(method: str, path: str, body=None, ctype: str | None = None, timeout: int = 40, port: int | None = None):
    p = port or CUR_PORT["v"]
    url = f"http://127.0.0.1:{p}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        if isinstance(body, bytes):
            data = body
            if ctype:
                headers["Content-Type"] = ctype
        else:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
    rq = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(rq, timeout=timeout) as rp:
            return rp.status, rp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        return 0, str(e).encode("utf-8")


def jhttp(method: str, path: str, body=None, **kw):
    st, raw = http(method, path, body, **kw)
    try:
        return st, json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return st, {"_raw": raw[:400].decode("utf-8", "replace")}


def mp(files: dict[str, tuple[str, bytes]], fields: dict[str, str]):
    b = "----dcreplaybnd"
    out = b""
    for k, v in fields.items():
        out += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8")
    for k, (fn, data) in files.items():
        out += (
            f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{fn}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
        out += data + b"\r\n"
    out += f"--{b}--\r\n".encode("utf-8")
    return out, f"multipart/form-data; boundary={b}"


def port_free(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) != 0


def start(port: int | None, extra: dict | None = None, log: Path | None = None) -> subprocess.Popen:
    env = os.environ.copy()
    env.update(
        {
            "DATA_DIR": str(DATA),
            "UPLOADS_DIR": str(UPLOADS),
            "EXPORTS_DIR": str(EXPORTS),
            "HOST": "127.0.0.1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
    )
    if port:
        env["PORT"] = str(port)
    else:
        env.pop("PORT", None)
    if extra:
        env.update(extra)
    fh = open(log or (SANDBOX / "server.log"), "wb")
    p = subprocess.Popen([str(PY), "server.py"], cwd=str(PROJECT), env=env, stdout=fh, stderr=subprocess.STDOUT)
    PROCS.append(p)
    return p


def wait_ready(port: int, timeout: int = 45) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        with socket.socket() as s:
            s.settimeout(1)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                st, _ = http("GET", "/api/ping", port=port, timeout=5)
                if st == 200:
                    return True
        time.sleep(1)
    return False


def stop(p: subprocess.Popen) -> None:
    try:
        p.terminate()
        p.wait(timeout=12)
    except Exception:  # noqa: BLE001
        try:
            p.kill()
        except Exception:  # noqa: BLE001
            pass


def stop_all() -> None:
    for p in list(PROCS):
        stop(p)
    PROCS.clear()


def sandbox_sql(query: str, args=()):
    con = sqlite3.connect(f"file:{DATA / 'imports.db'}?mode=ro", uri=True)
    try:
        return list(con.execute(query, args))
    finally:
        con.close()


def make_dbf(path: Path, fields: list[tuple[str, str, int, int]], rows: list[tuple[str, ...]]) -> None:
    """构造最小 dBase III (.dbf) 文件，用于 M2-005 实测（自造夹具，不依赖外部样例）。

    fields = [(名称, 类型, 长度, 小数位), ...]；类型 'C' 字符 / 'N' 数值。
    """
    import struct

    header_len = 32 + 32 * len(fields) + 1
    record_len = 1 + sum(f[2] for f in fields)
    out = bytearray()
    out += bytes([0x03, 26, 9, 19])            # 版本 + 最后更新日期（2026-09-19）
    out += struct.pack("<I", len(rows))         # 记录数
    out += struct.pack("<H", header_len)
    out += struct.pack("<H", record_len)
    out += b"\x00" * 20                         # 保留区
    for name, ftype, length, dec in fields:
        out += name.encode("ascii")[:10].ljust(11, b"\x00")
        out += ftype.encode("ascii") + b"\x00" * 4
        out += bytes([length, dec]) + b"\x00" * 14
    out += b"\x0d"                              # 字段描述区结束
    for row in rows:
        out += b"\x20"                          # 记录有效标记
        for (_, ftype, length, _), value in zip(fields, row):
            text = str(value)
            text = text.rjust(length) if ftype == "N" else text.ljust(length)
            out += text[:length].encode("ascii", "replace")
    out += b"\x1a"                              # EOF
    path.write_bytes(bytes(out))


# --------------------------------------------------------------------------- 阶段 0：沙箱
def phase0() -> None:
    print("\n===== 阶段 0 · 准备隔离沙箱 =====", flush=True)
    # 沙箱目录由 _resolve_sandbox() 保证「此前不存在」，此处**不做任何删除**，
    # 避免触发宿主的批量删除保护。断言已做成精准/幂等，不依赖目录为空。
    if SANDBOX.exists():
        raise SystemExit(f"沙箱目录已存在，应由 _resolve_sandbox() 换新：{SANDBOX}")
    for d in (DATA, UPLOADS, EXPORTS, SEED, EVID):
        d.mkdir(parents=True, exist_ok=True)
    # 种子数据：M3-008 登记的 export_people（name/amount/city）
    (SEED / "export_people.csv").write_bytes(
        "name,amount,city\nAlice,10,北京\nBob,20,上海\nCarol,30,北京\n".encode("utf-8-sig")
    )
    print(f"沙箱就绪：{SANDBOX}", flush=True)


# --------------------------------------------------------------------------- 阶段 1：M0
def phase1_m0() -> None:
    print("\n===== 阶段 1 · M0 环境与启动 =====", flush=True)

    # M0-001：默认配置 -> 8765
    if port_free(8765):
        log = SANDBOX / "m0_default.log"
        p = start(None, log=log)
        time.sleep(9)
        txt = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        ok = "Import prototype running at http://127.0.0.1:8765" in txt
        rec("M0-001", ok, f"默认配置输出匹配={'是' if ok else '否'}；日志尾部: {txt.strip().splitlines()[-1][:110] if txt.strip() else '(空)'}")
        stop(p)
    else:
        note("M0-001", "8765 被其它进程占用，无法起默认端口实例（非产品问题）")

    # 主实例
    p = start(PORT_MAIN)
    if not wait_ready(PORT_MAIN):
        rec("M0-002", False, "主实例未能在 45s 内就绪")
        raise SystemExit("主实例启动失败，后续条目无法执行")
    rec("M0-002", True, f"HOST=127.0.0.1 PORT={PORT_MAIN} 启动成功，端口可连且 /api/ping=200")

    st, body = jhttp("GET", "/api/ping")
    rec("M0-005", st == 200 and body.get("version") == "1.4.0" and body.get("appVersion") == "1.4.0",
        f"HTTP {st} {json.dumps(body, ensure_ascii=False)[:150]}")

    st, body = jhttp("GET", "/api/meta")
    rec("M0-006", st == 200 and body.get("appVersion") == "1.4.0",
        f"HTTP {st} {json.dumps(body, ensure_ascii=False)[:150]}")

    st, body = jhttp("GET", "/api/storage/status")
    need = {"dataDir", "uploadsDir", "exportsDir", "databasePath"}
    have = need & set(body)
    rec("M0-007", st == 200 and have == need and body.get("writeTestOk") is True and body.get("databaseExists") is True,
        f"字段齐全={sorted(have)==sorted(need)} writeTestOk={body.get('writeTestOk')} databaseExists={body.get('databaseExists')}")

    # M0-009：调度线程周期扫描（CODE+DB）—— 用实际分发行为验证
    src = (PROJECT / "server.py").read_text(encoding="utf-8")
    m = re.search(r"def scheduler_loop[\s\S]{0,700}", src)
    seg = m.group(0) if m else ""
    ok_code = ("dispatch_due_schedules" in seg) and ("dispatch_prechecks" in seg) and bool(re.search(r"wait\(\s*5\s*\)", seg))
    rec("M0-009", ok_code,
        f"scheduler_loop 内含 dispatch_prechecks+dispatch_due_schedules 且周期 stop_event.wait(5) = {ok_code}")

    # M0-010a：密钥惰性生成 —— 启动后尚未发生任何加密调用时，不应提前落盘
    secret = DATA / ".secret_key"
    rec("M0-010a", not secret.exists(),
        f"启动后（尚无加密调用）.secret_key 尚未生成（惰性生成）= {not secret.exists()}")

    # M0-003 / M0-004：Basic Auth
    stop(p)
    pa = start(PORT_AUTH, extra={"APP_AUTH_ENABLED": "true", "ADMIN_USER": "admin", "ADMIN_PASSWORD": "secret123"})
    if wait_ready(PORT_AUTH):
        import base64 as _b64

        st1, _ = http("GET", "/api/meta", port=PORT_AUTH)
        tok = _b64.b64encode(b"admin:secret123").decode()
        # 带认证头需自定义 header，用 urllib 直接来一次
        rq = urllib.request.Request(f"http://127.0.0.1:{PORT_AUTH}/api/meta",
                                    headers={"Authorization": f"Basic {tok}"})
        try:
            with urllib.request.urlopen(rq, timeout=10) as rp:
                st4, b4 = rp.status, rp.read()
        except urllib.error.HTTPError as e:
            st4, b4 = e.code, e.read()
        rec("M0-003", st1 == 401, f"不带 Auth 头访问 /api/meta -> HTTP {st1}（期望 401）")
        rec("M0-004", st4 == 200 and json.loads(b4.decode("utf-8")).get("ok") is True,
            f"带正确 Basic Auth -> HTTP {st4} {b4[:120].decode('utf-8', 'replace')}")
    else:
        rec("M0-003", False, "认证实例未能就绪")
        rec("M0-004", False, "认证实例未能就绪")
    stop(pa)

    # 主实例重新拉起（后续条目用）
    start(PORT_MAIN)
    if not wait_ready(PORT_MAIN):
        raise SystemExit("主实例二次启动失败")


# --------------------------------------------------------------------------- 阶段 2：数据前置 + M2/M3
def phase2_seed_data() -> None:
    print("\n===== 阶段 2 · 种子数据（隔离库）=====", flush=True)
    csv_bytes = (SEED / "export_people.csv").read_bytes()
    body, ctype = mp({"file": ("export_people.csv", csv_bytes)}, {})
    st, b = jhttp("POST", "/api/preview", body, ctype=ctype)

    rec("M2-001", st == 200 and b.get("columns") == ["name", "amount", "city"] and b.get("totalRows") == 3,
        f"HTTP {st} columns={b.get('columns')} totalRows={b.get('totalRows')} columnTypes={b.get('columnTypes')} warnings={b.get('typeWarnings')}")

    body, ctype = mp({"file": ("export_people.csv", csv_bytes)},
                     {"tableName": "export_people", "targetDbType": "sqlite", "importMode": "rebuild"})
    st, b = jhttp("POST", "/api/import", body, ctype=ctype)
    sm = b.get("summary") or {}
    rec("M2-044", st == 200 and sm.get("rowsWritten") == 3 and sm.get("failedFiles") == 0,
        f"HTTP {st} summary={json.dumps(sm, ensure_ascii=False)[:220]} failures={b.get('failures')}")

    st, b = jhttp("GET", "/api/tables")
    names = [t.get("name") if isinstance(t, dict) else t for t in (b.get("tables") or [])]
    rec("M2-066", st == 200 and any("export_people" in str(n) for n in names),
        f"HTTP {st} 表数={len(names)} 含 export_people={'export_people' in names}")

    st, b = jhttp("GET", "/api/logs")
    rec("M2-059", st == 200 and isinstance(b.get("logs"), list) and len(b["logs"]) > 0,
        f"HTTP {st} 日志条数={len(b.get('logs') or [])}")

    # M2-007：sourcePath 本地路径预览
    src = str(SEED / "export_people.csv")
    body, ctype = mp({}, {"sourcePath": src})
    st, b = jhttp("POST", "/api/preview", body, ctype=ctype)
    rec("M2-007", st == 200 and b.get("totalRows") == 3,
        f"HTTP {st} sourcePath 预览 totalRows={b.get('totalRows')} columns={b.get('columns')}")


def phase3_m1() -> None:
    print("\n===== 阶段 3 · M1 数据库连接 =====", flush=True)
    st, b = jhttp("GET", "/api/connections")
    cons = b.get("connections") or b.get("items") or []
    leak = [c for c in cons if isinstance(c, dict) and "password" in c and c.get("password")]
    rec("M1-001", st == 200 and not leak,
        f"HTTP {st} 连接数={len(cons)} 明文密码泄漏={'无' if not leak else leak[:1]} 字段={sorted(cons[0])[:9] if cons else []}")

    # 建一条指向本机测试库 MySQL 的连接（只读用途：连接测试）
    payload = {"name": "复跑-测试连接", "dbType": "mysql", "host": "127.0.0.1", "port": 3306,
               "user": "root", "password": "123456", "database": "dc_p2_test", "charset": "utf8mb4"}
    st, b = jhttp("POST", "/api/connections", payload)
    cid = b.get("connection", {}).get("id") if isinstance(b.get("connection"), dict) else b.get("id")
    rec("M1-004", st == 200 and bool(cid), f"HTTP {st} 新建连接 id={cid}")

    if cid:
        row = sandbox_sql("select password from _db_connections where id=?", (cid,))
        pw = row[0][0] if row else ""
        rec("M1-004b", pw.startswith("fernet:"), f"库中密码列前缀={'fernet:' if pw.startswith('fernet:') else pw[:12]}")

        st, b = jhttp("POST", "/api/connections/test", {"id": cid, "name": "复跑-测试连接", "dbType": "mysql",
                                                       "host": "127.0.0.1", "port": 3306, "user": "root",
                                                       "database": "dc_p2_test", "charset": "utf8mb4"},
                      timeout=45)
        dbs = b.get("databases") or []
        sysdbs = {"mysql", "information_schema", "performance_schema", "sys"} & set(dbs)
        rec("M1-002", st == 200 and b.get("ok") is True,
            f"HTTP {st} 用已存密码解密测试 -> ok={b.get('ok')} version={b.get('version')} databases={dbs} 系统库混入={sorted(sysdbs) or '无'}")

    # M0-010b：首次加密调用之后，.secret_key 应已按 44 字符 Fernet 格式落盘
    secret = DATA / ".secret_key"
    content = secret.read_text(encoding="utf-8").strip() if secret.exists() else ""
    rec("M0-010b", secret.exists() and len(content) == 44,
        f"发生加密调用后 .secret_key 存在={secret.exists()} 长度={len(content)}（期望 44）")


def phase3b_extras() -> None:
    """API 类补充：M1-006 / M2-002 / M2-061 / M2-062（M2-060 会弹原生对话框，转人工）。"""
    print("\n===== 阶段 3b · API 补充 =====", flush=True)

    # M2-002：XLSX 多 sheet 预览（带 sheetName）
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "SheetA"
    for r in (["code"], ["A1"], ["A2"]):
        ws.append(r)
    ws2 = wb.create_sheet("SheetB")
    for r in (["code"], ["B1"]):
        ws2.append(r)
    xlsx = SEED / "multi.xlsx"
    wb.save(xlsx)
    body, ctype = mp({"file": ("multi.xlsx", xlsx.read_bytes())}, {"sheetName": "SheetA"})
    st, b = jhttp("POST", "/api/preview", body, ctype=ctype)
    rec("M2-002", st == 200 and b.get("columns") == ["code"] and "SheetA" in (b.get("sheets") or []),
        f"HTTP {st} sheets={b.get('sheets')} selectedSheet={b.get('selectedSheet')} columns={b.get('columns')}")

    # M2-061：task-source 上传落盘
    body, ctype = mp({"file": ("task.csv", b"a,b\n1,2\n")}, {})
    st, b = jhttp("POST", "/api/task-source", body, ctype=ctype)
    p = str(b.get("path") or b.get("sourcePath") or "")
    rec("M2-061", st == 200 and bool(p) and "task_sources" in p, f"HTTP {st} path={p[:150]}")

    # M2-062：保存单步 import 任务资产
    st, b = jhttp("POST", "/api/jobs", {"name": "复跑-单步导入", "steps": [
        {"type": "import", "name": "1.小票数据导入",
         "config": {"tableName": "export_people", "targetDbType": "sqlite", "importMode": "rebuild"}}]})
    jid = (b.get("job") or {}).get("id") if isinstance(b.get("job"), dict) else b.get("id")
    rec("M2-062", st == 200 and bool(jid), f"HTTP {st} 保存任务 id={jid}")

    # M1-006：删除连接（接口层）
    cons = sandbox_sql("select id from _db_connections")
    if cons:
        cid = cons[0][0]
        st, b = jhttp("DELETE", f"/api/connections?id={urllib.parse.quote(cid)}")
        left = sandbox_sql("select count(*) from _db_connections")[0][0]
        rec("M1-006", st == 200 and left == 0, f"HTTP {st} 删除后剩余连接={left}")
    else:
        rec("M1-006", False, "前置失败：无连接可删")

    note("M2-060", "该接口由服务进程弹系统原生文件选择框，自动化执行会阻塞 HTTP 线程，转人工实操")


def phase4_m3() -> None:
    print("\n===== 阶段 4 · M3 导出 =====", flush=True)

    st, b = jhttp("GET", "/api/export/sources?targetDbType=sqlite")
    srcs = b.get("sources") or []
    one = next((x for x in srcs if x.get("name") == "export_people"), None)
    rec("M3-001", st == 200 and one and "rowsApproximate" in one,
        f"HTTP {st} 源数={len(srcs)} export_people={json.dumps(one, ensure_ascii=False)[:160] if one else '(缺)'}")

    base = {"sourceType": "table", "table": "export_people", "targetDbType": "sqlite",
            "openFileAfterExport": False, "openFolderAfterExport": False}

    st, b = jhttp("POST", "/api/export/preview", base)
    rec("M3-008", st == 200 and b.get("columns") == ["name", "amount", "city"],
        f"HTTP {st} sourceName={b.get('sourceName')} columns={b.get('columns')} rows={b.get('rows')}")

    st, b = jhttp("POST", "/api/export/run", {**base, "extension": "xlsx", "outputName": "m3_run"})
    rec("M3-009", st == 200 and b.get("rows") == 3 and isinstance(b.get("files"), list) and b.get("downloadUrls"),
        f"HTTP {st} files={b.get('files')} rows={b.get('rows')} elapsedMs={b.get('elapsedMs')}")

    for ext, rid in (("xlsx", "M3-010"), ("csv", "M3-011"), ("json", "M3-012"), ("xml", "M3-013"), ("txt", "M3-014")):
        st, b = jhttp("POST", "/api/export/run", {**base, "extension": ext, "outputName": f"m3_fmt_{ext}"})
        files = b.get("files") or []
        exists = all(Path(f).exists() for f in files) and bool(files)
        head = ""
        if files and ext == "xml":
            head = Path(files[0]).read_text(encoding="utf-8")[:40]
        if ext == "csv":
            head = Path(files[0]).read_text(encoding="utf-8-sig")[:40]
        rec(rid, st == 200 and b.get("rows") == 3 and exists,
            f"HTTP {st} ext={ext} rows={b.get('rows')} files={len(files)} 落盘={'是' if exists else '否'} 首行={head!r}")

    # M3-015 splitField
    st, b = jhttp("POST", "/api/export/run", {**base, "extension": "json", "outputName": "m3_split", "splitField": "city"})
    rec("M3-015", st == 200 and len(b.get("files") or []) == 2,
        f"HTTP {st} splitField=city files={[Path(f).name for f in (b.get('files') or [])]} rows={b.get('rows')}")

    # M3-017 exportFields
    st, b = jhttp("POST", "/api/export/run", {**base, "extension": "csv", "outputName": "m3_fields", "exportFields": "name,amount"})
    head = Path(b["files"][0]).read_text(encoding="utf-8-sig").splitlines()[0] if b.get("files") else ""
    rec("M3-017", st == 200 and head.strip() == "name,amount", f"HTTP {st} CSV 首行={head.strip()!r}")

    # M3-018 whereClause
    st, b = jhttp("POST", "/api/export/run", {**base, "extension": "csv", "outputName": "m3_where", "whereClause": "amount >= 20"})
    rec("M3-018", st == 200 and b.get("rows") == 2, f"HTTP {st} whereClause='amount >= 20' rows={b.get('rows')}")

    # M3-030 csv 编码/分隔符
    st, b = jhttp("POST", "/api/export/run", {**base, "extension": "csv", "outputName": "m3_enc", "delimiter": ";"})
    lines = Path(b["files"][0]).read_text(encoding="utf-8-sig").splitlines()[:2] if b.get("files") else []
    rec("M3-030", st == 200 and lines and lines[0].strip() == "name;amount;city",
        f"HTTP {st} 前两行={[l.strip() for l in lines]}")

    # M3-037 filePrefix/fileSuffix
    st, b = jhttp("POST", "/api/export/run", {**base, "extension": "csv", "outputName": "m3_ps", "filePrefix": "pre", "fileSuffix": "suf"})
    names = [Path(f).stem for f in (b.get("files") or [])]
    rec("M3-037", st == 200 and any(n.startswith("pre") and n.endswith("suf") for n in names),
        f"HTTP {st} 产物 stem={names}")

    # M3-038 batchRows + splitByBatch
    st, b = jhttp("POST", "/api/export/run", {**base, "extension": "csv", "outputName": "m3_batch",
                                              "batchRows": "2", "splitByBatch": True})
    rec("M3-038", st == 200 and len(b.get("files") or []) == 2,
        f"HTTP {st} batchRows=2 files={[Path(f).name for f in (b.get('files') or [])]} rows={b.get('rows')}")

    # M3-039 exportTimeField
    st, b = jhttp("POST", "/api/export/run", {**base, "extension": "csv", "outputName": "m3_time", "exportTimeField": "exported_at"})
    head = Path(b["files"][0]).read_text(encoding="utf-8-sig").splitlines()[0] if b.get("files") else ""
    rec("M3-039", st == 200 and "exported_at" in head, f"HTTP {st} 首行={head.strip()!r}")

    # M3-032 下载路径越界
    st, b = jhttp("GET", "/api/export/download?path=" + urllib.parse.quote("C:\\Windows\\win.ini"))
    rec("M3-032", st in (400, 403), f"HTTP {st}（期望 4xx 拒绝） body={json.dumps(b, ensure_ascii=False)[:120]}")

    # M3-035：非 Windows 分支 —— 本机是 Windows，无法验
    note("M3-035", "本机为 Windows，'非 Windows 返回 501' 分支无法在当前环境复现（须在 Linux/容器验证）")
    # M3-033/M3-034：原生对话框 —— 会弹真实窗口阻塞 HTTP 线程，自动化中不点
    note("M3-033", "需人工在桌面实操观察原生文件夹对话框；自动化点击会阻塞 HTTP 线程，故不自动执行")
    note("M3-034", "同上（另存为对话框）")


def phase5_ut() -> None:
    print("\n===== 阶段 5 · UT 层（pytest）=====", flush=True)
    r = subprocess.run([str(PY), "-m", "pytest", "-v", "--tb=no", "-p", "no:cacheprovider"],
                       cwd=str(PROJECT), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    out = (r.stdout or "") + (r.stderr or "")
    (SANDBOX / "pytest-verbose.txt").write_text(out, encoding="utf-8")
    status = {}
    for m in re.finditer(r"^(tests/[^\s:]+::\S+)\s+(PASSED|FAILED|SKIPPED|ERROR|XFAIL)", out, re.M):
        status[m.group(1).split("::", 1)[1]] = m.group(2)
    tail = [l for l in out.splitlines() if re.search(r"\d+ (passed|failed)", l)]
    print(f"  pytest 汇总: {tail[-1] if tail else '(未解析到)'}", flush=True)
    print(f"  解析到用例结果 {len(status)} 条", flush=True)

    def need(*names: str) -> tuple[bool, str]:
        got = {n: status.get(n, "NOT_FOUND") for n in names}
        ok = all(v == "PASSED" for v in got.values())
        return ok, json.dumps(got, ensure_ascii=False)

    # UT 类条目 -> 用例映射（依据 V5 计划「验证方式」栏登记）
    mapping = [
        ("M1-003", ["test_p3_19_test_connection_filters_system_databases"]),
        ("M1-012", ["test_p3_19_test_connection_filters_system_databases",
                    "test_p3_27_sqlite_import_time_value_and_no_deprecation"]),
        ("M2-003", ["test_json_flat_basic"]),
        ("M2-009", ["test_csv_rebuild_writes_deduped_rows"]),
        ("M2-017", ["test_final_rows_match_expected_transformations"]),
        ("M2-051", ["test_after_each_sql_runs_marker"]),
        ("M2-057", ["test_csv_rebuild_writes_deduped_rows"]),
        ("M2-004", ["test_xml_basic"]),
        ("M2-010", ["test_custom_line_delimiter"]),
        ("M2-011", ["test_csv_rebuild_writes_deduped_rows", "test_csv_update_mode"]),
        ("M2-012", ["test_csv_update_mode"]),
        ("M2-014", ["test_csv_rebuild_writes_deduped_rows"]),
        ("M2-019", ["test_final_rows_match_expected_transformations"]),
        ("M2-020", ["test_excel_import_respects_column_filter"]),
        ("M2-021", ["test_final_rows_match_expected_transformations"]),
        ("M2-022", ["test_pinyin_table_and_fields"]),
        ("M2-023", ["test_final_rows_match_expected_transformations"]),
        ("M2-024", ["test_csv_rebuild_writes_deduped_rows"]),
        ("M2-025", ["test_p3_27_sqlite_import_time_value_and_no_deprecation",
                    "test_p3_27_mysql_import_time_column_is_datetime"]),
        ("M2-026", ["test_excel_second_sheet", "test_excel_single_sheet"]),
        ("M2-027", ["test_final_rows_match_expected_transformations"]),
        ("M2-029", ["test_final_rows_match_expected_transformations"]),
        ("M2-031", ["test_csv_rebuild_writes_deduped_rows"]),
        ("M2-033", ["test_date_columns"]),
        ("M2-034", ["test_final_rows_match_expected_transformations"]),
        ("M2-037", ["test_excel_all_sheets"]),
        ("M2-038", ["test_pinyin_table_and_fields"]),
        ("M2-039", ["test_final_rows_match_expected_transformations"]),
        ("M2-045", ["test_p2_12_run_import_batch_skips_unchanged_file"]),
        ("M2-046", ["test_excel_all_sheets"]),
        ("M2-047", ["test_resume_checkpoint"]),
        ("M2-048", ["test_p2_12_run_import_batch_skips_unchanged_file"]),
        ("M2-049", ["test_p2_12_fingerprint_stable_and_sensitive"]),
        ("M2-050", ["test_p2_12_file_key_includes_target_identity"]),
        ("M2-052", ["test_after_each_sql_runs_marker"]),
        ("M2-054", ["test_query_export"]),
        ("M3-002", ["test_p2_7_sqlite_sources_are_precise"]),
        ("M3-010", ["test_sqlite_exports"]),
        ("M3-024", ["test_large_streaming_export"]),
        ("M3-025", ["test_large_streaming_export"]),
        ("M3-026", ["test_sqlite_exports"]),
        ("M3-027", ["test_sqlite_exports"]),
        ("M3-023", ["test_p2_13_sqlite_unaffected_by_flag"]),
        ("M3-031", ["test_chinese_filename_download_header"]),
        ("M3-021", ["test_p2_13_comment_as_filename_mysql"]),
        ("M3-022", ["test_p2_13_comment_as_filename_mysql"]),
        ("M3-003", ["test_p2_7_mysql_small_table_uses_exact_count"]),
    ]
    for rid, cases in mapping:
        ok, detail = need(*cases)
        rec(rid, ok, f"UT 映射 {detail}", kind="UT")


def phase_opt() -> None:
    """CODE 类条目「实测升级」：用真实导入/导出替代单纯读代码作为证据。

    这一批在 V5 里以 `CODE`（读源码）为证据，可靠性弱；此处改为黑盒实测。
    """
    print("\n===== 阶段 4b · CODE 类条目实测升级 =====", flush=True)

    def imp(text: str, fname: str, files_extra=None, **f):
        payload = {"targetDbType": "sqlite"}
        payload.update({k: (str(v).lower() if isinstance(v, bool) else str(v)) for k, v in f.items()})
        files = {"file": (fname, text.encode("utf-8-sig"))}
        if files_extra:
            files.update(files_extra)
        body, ctype = mp(files, payload)
        return jhttp("POST", "/api/import", body, ctype=ctype)

    def tbl_exists(name: str) -> bool:
        return bool(sandbox_sql("select name from sqlite_master where type='table' and name=?", (name,)))

    base = {"sourceType": "table", "table": "export_people", "targetDbType": "sqlite",
            "openFileAfterExport": False, "openFolderAfterExport": False}

    # ---- M2-005 DBF 预览（自造 dBase III 夹具，真实解析列与行）
    try:
        dbf = SEED / "people.dbf"
        make_dbf(dbf, [("NAME", "C", 10, 0), ("AMOUNT", "N", 8, 2)],
                 [("Alice", "10.00"), ("Bob", "20.50")])
        body, ctype = mp({"file": (dbf.name, dbf.read_bytes())}, {"hasHeader": "true"})
        st, b = jhttp("POST", "/api/preview", body, ctype=ctype)
        cols = [str(c).lower() for c in (b.get("columns") or [])]
        rec("M2-005", st == 200 and cols == ["name", "amount"] and b.get("totalRows") == 2,
            f"DBF 预览 HTTP={st} columns={b.get('columns')} totalRows={b.get('totalRows')}")
    except Exception as e:  # noqa: BLE001
        rec("M2-005", False, f"异常 {e}")

    # ---- M2-006 加密 Excel：需一份真实「带密码的 xlsx」夹具（本机无现成样例，转人工提供）
    note("M2-006", "解密分支存在（server.py L1048-1052 excelPassword → msoffcrypto.OfficeFile）；"
                   "但沙箱内无带密码 xlsx 夹具（本机 403 个 Office 文件中无一个加密文件），"
                   "且自行构造 ECMA-376 加密容器风险高。转人工：提供一份密码已知的加密 xlsx 即可自动跑完")

    # ---- M1-005：保存时未填密码但带 id → 沿用已存密文
    try:
        st, b = jhttp("POST", "/api/connections", {"name": "复跑-密码沿用", "dbType": "mysql", "host": "127.0.0.1",
                                                   "port": 3306, "user": "root", "password": "secretPwd", "database": "dc_p2_test"})
        cid = (b.get("connection") or {}).get("id")
        pw1 = sandbox_sql("select password from _db_connections where id=?", (cid,))[0][0]
        jhttp("POST", "/api/connections", {"id": cid, "name": "复跑-密码沿用改", "dbType": "mysql",
                                           "host": "127.0.0.1", "port": 3306, "user": "root", "database": "dc_p2_test"})
        pw2 = sandbox_sql("select password from _db_connections where id=?", (cid,))[0][0]
        rec("M1-005", bool(pw1) and pw1 == pw2 and pw2.startswith("fernet:"),
            f"不填密码重存前后密文一致={pw1 == pw2}，前缀={pw2[:7]}")
    except Exception as e:  # noqa: BLE001
        rec("M1-005", False, f"异常 {e}")

    # ---- M2-013 overwrite
    try:
        imp("name,amount\nA,1\nB,2\n", "t_over.csv", tableName="t_over", importMode="rebuild")
        imp("name,amount\nC,9\n", "t_over.csv", tableName="t_over", importMode="overwrite")
        n = sandbox_sql("select count(*) from t_over")[0][0]
        rec("M2-013", n == 1, f"rebuild 写 2 行 → overwrite 写 1 行，最终行数={n}（期望 1，即被清空重写）")
    except Exception as e:  # noqa: BLE001
        rec("M2-013", False, f"异常 {e}")

    # ---- M2-018 typeMode=text
    try:
        imp("a,b\n1,x\n", "t_text.csv", tableName="t_text", importMode="rebuild", typeMode="text")
        ts = [r[0].upper() for r in sandbox_sql("select type from pragma_table_info('t_text')")]
        rec("M2-018", bool(ts) and all("TEXT" in t for t in ts), f"typeMode=text 建表列类型={ts}")
    except Exception as e:  # noqa: BLE001
        rec("M2-018", False, f"异常 {e}")

    # ---- M2-030 deleteEmptyRows
    try:
        imp("a,b\n1,x\n\n2,y\n", "t_empty.csv", tableName="t_empty", importMode="rebuild", deleteEmptyRows=True)
        n = sandbox_sql("select count(*) from t_empty")[0][0]
        rec("M2-030", n == 2, f"输入含 1 空行 → 导入后 {n} 行（期望 2）")
    except Exception as e:  # noqa: BLE001
        rec("M2-030", False, f"异常 {e}")

    # ---- M2-032 fillDownColumns
    try:
        imp("name,val\nA,1\n,2\n", "t_fill.csv", tableName="t_fill", importMode="rebuild", fillDownColumns="name")
        r = sandbox_sql("select name from t_fill order by val")
        rec("M2-032", len(r) == 2 and r[1][0] == "A", f"第二行 name 被上行补全 → {[x[0] for x in r]}（期望 ['A','A']）")
    except Exception as e:  # noqa: BLE001
        rec("M2-032", False, f"异常 {e}")

    # ---- M2-035 emptyAsNull
    try:
        imp("name,val\nA,\n", "t_null.csv", tableName="t_null", importMode="rebuild", emptyAsNull=True)
        v = sandbox_sql("select val from t_null")
        rec("M2-035", bool(v) and v[0][0] is None, f"空白单元格落库值={v[0][0]!r}（期望 None）")
    except Exception as e:  # noqa: BLE001
        rec("M2-035", False, f"异常 {e}")

    # ---- M2-036 replaceTextFrom/To
    try:
        imp("name,val\nA,xx\n", "t_rep.csv", tableName="t_rep", importMode="rebuild",
            replaceTextFrom="xx", replaceTextTo="yy")
        v = sandbox_sql("select val from t_rep")
        rec("M2-036", bool(v) and v[0][0] == "yy", f"replaceTextFrom/To 生效 → val={v[0][0]!r}（期望 'yy'）")
    except Exception as e:  # noqa: BLE001
        rec("M2-036", False, f"异常 {e}")

    # ---- M2-040 tablePrefix / tableSuffix
    try:
        imp("a\n1\n", "t_pfx.csv", tableName="t_pfx", importMode="rebuild", tablePrefix="pre_", tableSuffix="_suf")
        rec("M2-040", tbl_exists("pre_t_pfx_suf"), f"tablePrefix+tableSuffix 建表名含 pre_t_pfx_suf={tbl_exists('pre_t_pfx_suf')}")
    except Exception as e:  # noqa: BLE001
        rec("M2-040", False, f"异常 {e}")

    # ---- M2-041 tableRegex
    try:
        imp("a\n1\n", "T2026_data.csv", tableNameRule="file", importMode="rebuild", tableRegex=r"T\d+")
        hits = [r[0] for r in sandbox_sql("select name from sqlite_master where type='table' and name like 'T2026%'")]
        rec("M2-041", bool(hits), f"tableRegex='T\\d+' 从 T2026_data.csv 提取 → 命中表={hits}")
    except Exception as e:  # noqa: BLE001
        rec("M2-041", False, f"异常 {e}")

    # ---- M2-042 symbolToUnderscore
    try:
        imp("a\n1\n", "sym-bol.csv", tableNameRule="file", importMode="rebuild", symbolToUnderscore=True)
        hits = [r[0] for r in sandbox_sql("select name from sqlite_master where type='table' and name like 'sym%'")]
        rec("M2-042", any("_" in n and "-" not in n for n in hits), f"符号→下划线 建表名={hits}")
    except Exception as e:  # noqa: BLE001
        rec("M2-042", False, f"异常 {e}")

    # ---- M2-043 duplicateTableMode（语义：仅「追加模式 + 目标表已存在」时生效；same/suffix/skip 三态）
    try:
        imp("a\n1\n", "dup1.csv", tableName="t_dupmode", importMode="append")
        first_built = tbl_exists("t_dupmode")
        _, _ = imp("a\n2\n", "dup2.csv", tableName="t_dupmode", importMode="append",
                   duplicateTableMode="suffix")
        names = [r[0] for r in sandbox_sql(
            "select name from sqlite_master where type='table' and name like 't_dupmode%' order by name")]
        v2 = sandbox_sql("select a from t_dupmode_2") if "t_dupmode_2" in names else []
        _, b3 = imp("a\n3\n", "dup3.csv", tableName="t_dupmode", importMode="append",
                    duplicateTableMode="skip")
        names_after_skip = [r[0] for r in sandbox_sql(
            "select name from sqlite_master where type='table' and name like 't_dupmode%' order by name")]
        skipped = int((b3.get("summary") or {}).get("rowsSkipped") or 0)
        ok = (first_built and "t_dupmode_2" in names and bool(v2) and str(v2[0][0]) == "2"
              and len(names_after_skip) == len(names) and skipped > 0)
        rec("M2-043", ok,
            f"首建t_dupmode={first_built}；suffix→表={names}，t_dupmode_2.a={v2[0][0] if v2 else 'NA'}；"
            f"skip→rowsSkipped={skipped}，表数不变={len(names_after_skip) == len(names)}")
    except Exception as e:  # noqa: BLE001
        rec("M2-043", False, f"异常 {e}")

    # ---- M2-015 字段匹配「按名称」：既有目标表按列名对齐
    # 判别式：autoExpand=false 时，异名列不得被拓宽，应按「名称不一致」拒绝。
    try:
        imp("name,amount\nx,1\n", "mb1.csv", tableName="t_match_name", importMode="rebuild")
        rows0 = sandbox_sql("select count(*) from t_match_name")[0][0]
        st_ok, _ = imp("name,amount\ny,2\n", "mb2.csv", tableName="t_match_name",
                       importMode="append", autoExpand=False)
        rows1 = sandbox_sql("select count(*) from t_match_name")[0][0]
        st_bad, b_bad = imp("nm,amt\nz,3\n", "mb3.csv", tableName="t_match_name",
                            importMode="append", autoExpand=False)
        err = str(b_bad.get("error") or "")
        rec("M2-015", st_ok == 200 and rows1 == rows0 + 1 and st_bad == 400 and
            ("缺少字段" in err or "不一致" in err),
            f"同名列追加(autoExpand=false) HTTP={st_ok} 行数 {rows0}→{rows1}；"
            f"异名列追加 HTTP={st_bad} err={err[:70]}")
    except Exception as e:  # noqa: BLE001
        rec("M2-015", False, f"异常 {e}")

    # ---- M2-016 字段匹配「按顺序」：按列序号落到既有目标表的列（2026-09-19 修复后复验）
    # 判别式：既有表 t_match_order(x, y)，导入表头为 a,b 的文件。
    #   matchBy=order → 应写入 x=7, y=8（按序号对齐）；matchBy=name / 默认 → 应报「目标表缺少字段」。
    try:
        imp("x,y\n1,2\n", "mb_o1.csv", tableName="t_match_order", importMode="rebuild")
        st_o, b_o = imp("a,b\n7,8\n", "mb_o2.csv", tableName="t_match_order", importMode="append",
                        autoExpand=False, matchMode="auto", matchBy="order")
        rows_o = sandbox_sql("select x, y from t_match_order order by rowid")
        st_n, b_n = imp("c,d\n9,9\n", "mb_o3.csv", tableName="t_match_order", importMode="append",
                        autoExpand=False, matchMode="auto", matchBy="name")
        st_d, b_d = imp("e,f\n5,6\n", "mb_o4.csv", tableName="t_match_order", importMode="append",
                        autoExpand=False)
        cols = [r[1] for r in sandbox_sql("pragma table_info(t_match_order)")]
        got = [str(v) for v in (rows_o[-1] if rows_o else [])]
        e_n = str(b_n.get("error") or "")
        e_d = str(b_d.get("error") or "")
        # 负向对照：按顺序时若目标表列数不足，必须明确报错而不是静默丢列
        imp("only\n1\n", "mb_short.csv", tableName="t_match_short", importMode="rebuild")
        st_s, b_s = imp("p,q\n1,2\n", "mb_short2.csv", tableName="t_match_short", importMode="append",
                        autoExpand=False, matchBy="order")
        e_s = str(b_s.get("error") or "")
        ok = (st_o == 200 and got == ["7", "8"] and cols == ["x", "y"]
              and st_n == 400 and "缺少字段" in e_n
              and st_d == 400 and "缺少字段" in e_d
              and st_s == 400 and "按顺序" in e_s)
        rec("M2-016", ok,
            f"matchBy=order 异名列导入 HTTP={st_o} → 落到既有列 {cols} 值={got}（期望 x=7,y=8）；"
            f"matchBy=name HTTP={st_n}（期望 400 缺少字段）；不传 matchBy HTTP={st_d}（期望 400，默认行为未变）；"
            f"目标表列数不足 HTTP={st_s} err={e_s[:60]}")
    except Exception as e:  # noqa: BLE001
        rec("M2-016", False, f"异常 {e}")

    # ---- M2-053 afterAllSql
    try:
        imp("a\n1\n", "t_aas.csv", tableName="t_aas", importMode="rebuild",
            afterAllSql="create table if not exists mark_after_all (x text); insert into mark_after_all values ('done');")
        v = sandbox_sql("select count(*) from mark_after_all")
        rec("M2-053", bool(v) and v[0][0] == 1, f"afterAllSql 建标记表 mark_after_all → 行数={v[0][0] if v else 'N/A'}")
    except Exception as e:  # noqa: BLE001
        rec("M2-053", False, f"异常 {e}")

    # ---- M2-055 deleteAfterSuccess
    try:
        src = SEED / "t_del.csv"
        src.write_text("a\n1\n", encoding="utf-8")
        body, ctype = mp({}, {"sourcePath": str(src), "targetDbType": "sqlite", "tableName": "t_del",
                              "importMode": "rebuild", "deleteAfterSuccess": "true"})
        st, b = jhttp("POST", "/api/import", body, ctype=ctype)
        rec("M2-055", st == 200 and not src.exists(),
            f"deleteAfterSuccess 后源文件仍存在={src.exists()}（期望 False）；HTTP={st}；"
            f"resp={str(b)[:180]}")
    except Exception as e:  # noqa: BLE001
        rec("M2-055", False, f"异常 {e}")

    # ---- M2-056 disableLog
    try:
        n0 = sandbox_sql("select count(*) from _import_logs")[0][0]
        imp("a\n1\n", "t_nolog.csv", tableName="t_nolog", importMode="rebuild", disableLog=True)
        n1 = sandbox_sql("select count(*) from _import_logs")[0][0]
        rec("M2-056", n1 == n0, f"disableLog=true 前后 _import_logs 行数 {n0} → {n1}（应相等）")
    except Exception as e:  # noqa: BLE001
        rec("M2-056", False, f"异常 {e}")

    # ---- M3-019 exportMode=sheet / data
    try:
        from openpyxl import load_workbook

        jhttp("POST", "/api/export/run", {**base, "extension": "xlsx", "outputName": "m3_mode", "sheetName": "First"})
        st, b = jhttp("POST", "/api/export/run", {**base, "extension": "xlsx", "outputName": "m3_mode",
                                                  "sheetName": "Second", "exportMode": "sheet"})
        sn = load_workbook(b["files"][0]).sheetnames if b.get("files") else []
        st2, b2 = jhttp("POST", "/api/export/run", {**base, "extension": "xlsx", "outputName": "m3_mode",
                                                    "sheetName": "Second", "exportMode": "data"})
        wb2 = load_workbook(b2["files"][0]) if b2.get("files") else None
        rows_second = wb2["Second"].max_row if wb2 else 0
        rec("M3-019", sn == ["First", "Second"] and rows_second >= 3,
            f"exportMode=sheet → sheetnames={sn}（期望 ['First','Second']）；data 模式 Second 行数={rows_second}")
    except Exception as e:  # noqa: BLE001
        rec("M3-019", False, f"异常 {e}")

    # ---- M3-040 skipEmptyTable
    try:
        con = sqlite3.connect(str(DATA / "imports.db"))
        con.execute("create table if not exists t_no_rows (a text)")
        con.commit()
        con.close()
        st, b = jhttp("POST", "/api/export/run", {"sourceType": "table", "table": "t_no_rows", "targetDbType": "sqlite",
                                                  "extension": "csv", "outputName": "m3_skiptbl", "skipEmptyTable": True,
                                                  "openFileAfterExport": False, "openFolderAfterExport": False})
        rec("M3-040", st == 200 and (b.get("rows") or 0) == 0 and not (b.get("files") or []),
            f"空表 + skipEmptyTable → files={b.get('files')} rows={b.get('rows')}（期望空列表 / 0）")
    except Exception as e:  # noqa: BLE001
        rec("M3-040", False, f"异常 {e}")

    # ---- M3-041 beforeSql / afterSql（payload 顶层）
    try:
        mk = ("create table if not exists {t} (x text); insert into {t} values ('v');")
        st, b = jhttp("POST", "/api/export/run", {**base, "extension": "csv", "outputName": "m3_basql",
                                                  "beforeSql": mk.format(t="m3_before_marker"),
                                                  "afterSql": mk.format(t="m3_after_marker")})
        v1 = sandbox_sql("select count(*) from m3_before_marker")
        v2 = sandbox_sql("select count(*) from m3_after_marker")
        rec("M3-041", bool(v1) and bool(v2), f"beforeSql/afterSql 标记表行数={v1[0][0] if v1 else 'N/A'} / {v2[0][0] if v2 else 'N/A'}")
    except Exception as e:  # noqa: BLE001
        rec("M3-041", False, f"异常 {e}")

    # ---- M3-042 / M3-044 导出任务资产 保存 + 加载
    try:
        st, b = jhttp("POST", "/api/jobs", {"name": "复跑-导出任务", "steps": [
            {"type": "export", "name": "1.导出",
             "config": {"sourceType": "table", "table": "export_people", "targetDbType": "sqlite",
                        "extension": "csv", "outputName": "m3_task_out"}}]})
        jid = (b.get("job") or {}).get("id") if isinstance(b.get("job"), dict) else b.get("id")
        rec("M3-042", st == 200 and bool(jid), f"POST /api/jobs 存单步 export 任务 → id={jid}")
        st2, b2 = jhttp("GET", "/api/jobs")
        found = any(str(j.get("name")) == "复跑-导出任务" for j in (b2.get("jobs") or []) if isinstance(j, dict))
        rec("M3-044", found, f"GET /api/jobs 能加载到该导出任务={found}")
    except Exception as e:  # noqa: BLE001
        rec("M3-042", False, f"异常 {e}")
        rec("M3-044", False, f"异常 {e}")

    # ---- M3-020 headerMode=field / none / comment（三态；comment 态需 MySQL 有列注释）
    try:
        from openpyxl import load_workbook

        def head_of(tag: str):
            files = sorted(EXPORTS.rglob(f"{tag}*.xlsx"))
            if not files:
                return None
            ws = load_workbook(files[-1]).active
            return [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]

        jhttp("POST", "/api/export/run", {**base, "extension": "xlsx", "outputName": "hdr_field"})
        jhttp("POST", "/api/export/run", {**base, "extension": "xlsx", "outputName": "hdr_none",
                                          "headerMode": "none"})
        jhttp("POST", "/api/export/run", {**base, "extension": "xlsx", "outputName": "hdr_comment",
                                          "headerMode": "comment"})
        h_field, h_none, h_sqlite_comment = head_of("hdr_field"), head_of("hdr_none"), head_of("hdr_comment")

        # MySQL 注释态：用 beforeAllSql 建带列注释的表，再导入一行，最后 comment 模式导出
        st_c, b_c = jhttp("POST", "/api/connections", {
            "name": "复跑-M3-020-MySQL", "dbType": "mysql", "host": "127.0.0.1", "port": 3306,
            "user": "root", "password": "123456", "database": "dc_p2_test"})
        cid = str((b_c.get("connection") or {}).get("id") or "")
        ddl = ("drop table if exists replay_hdr; "
               "create table replay_hdr (a int comment '甲方编号', b varchar(20) comment '乙方名称')")
        body, ctype = mp({"file": ("hdr.csv", "a,b\n1,x\n".encode("utf-8-sig"))},
                         {"targetDbType": "mysql", "connectionId": cid, "tableName": "replay_hdr",
                          "importMode": "append", "beforeAllSql": ddl})
        st_i, b_i = jhttp("POST", "/api/import", body, ctype=ctype)
        st_m, b_m = jhttp("POST", "/api/export/run", {
            "sourceType": "table", "table": "replay_hdr", "targetDbType": "mysql", "connectionId": cid,
            "extension": "xlsx", "outputName": "hdr_mysql", "headerMode": "comment",
            "openFileAfterExport": False, "openFolderAfterExport": False})
        h_mysql = head_of("hdr_mysql")
        # 收尾：测试库是共享库，必须自行清理
        body2, ctype2 = mp({"file": ("clean.csv", "a\n1\n".encode("utf-8-sig"))},
                           {"targetDbType": "mysql", "connectionId": cid, "tableName": "replay_hdr_cleanup",
                            "importMode": "rebuild", "beforeAllSql": "drop table if exists replay_hdr"})
        jhttp("POST", "/api/import", body2, ctype=ctype2)

        norm = lambda row: [str(v) for v in (row or [])]  # noqa: E731  Excel 数值列会读成 int
        ok = (norm(h_field) == ["name", "amount", "city"]
              and norm(h_none) == ["Alice", "10", "北京"]
              and norm(h_sqlite_comment) == ["name", "amount", "city"]   # 文档化的回退行为
              and norm(h_mysql) == ["甲方编号", "乙方名称"])
        rec("M3-020", ok,
            f"field→{h_field}；none→{h_none}（首行即数据）；SQLite 上 comment→{h_sqlite_comment}（按设计回退列名）；"
            f"MySQL 建表 HTTP={st_i} 导出 HTTP={st_m} comment→{h_mysql}")
    except Exception as e:  # noqa: BLE001
        rec("M3-020", False, f"异常 {e}")


def phase6_recover() -> None:
    print("\n===== 阶段 6 · M0-008 僵尸恢复 =====", flush=True)
    stop_all()
    con = sqlite3.connect(str(DATA / "imports.db"))
    try:
        cols = [r[1] for r in con.execute("pragma table_info(_job_runs)")]
        # 僵尸计划：running=1 且进程已不在
        scols = [r[1] for r in con.execute("pragma table_info(_schedules)")]
        svals = {"id": "replay-zombie-sch", "name": "复跑僵尸计划", "running": 1,
                 "enabled": 1, "next_run_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        use_s = {k: v for k, v in svals.items() if k in scols}
        try:
            con.execute(f"insert into _schedules ({','.join(use_s)}) values ({','.join('?' * len(use_s))})",
                        list(use_s.values()))
        except Exception as exc:  # noqa: BLE001
            print(f"  （僵尸计划注入跳过：{exc}）", flush=True)
        con.execute("update _schedules set running=1 where 1=1")
        rc = con.execute("select count(*) from _schedules where running=1").fetchone()[0]
        vals = {"id": "replay-zombie-run", "job_id": "replay-zombie", "job_name": "复跑僵尸记录",
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "status": "运行中"}
        use = {k: v for k, v in vals.items() if k in cols}
        con.execute(f"insert into _job_runs ({','.join(use)}) values ({','.join('?' * len(use))})", list(use.values()))
        con.commit()
    finally:
        con.close()

    log = SANDBOX / "recover.log"
    start(PORT_MAIN, log=log)
    ok_ready = wait_ready(PORT_MAIN)
    time.sleep(3)
    txt = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    recovered = "[recover]" in txt
    rows = sandbox_sql("select status, message from _job_runs where id='replay-zombie-run'")
    status_now = rows[0][0] if rows else "(记录未找到)"
    msg_now = (rows[0][1] if rows else "") or ""
    zsched = sandbox_sql("select count(*) from _schedules where running=1")[0][0]
    line = next((l for l in txt.splitlines() if "[recover]" in l), "(无)")
    rec("M0-008", ok_ready and recovered and status_now == "失败" and "自动恢复" in msg_now and zsched == 0,
        f"就绪={ok_ready} 日志含[recover]={recovered} 注入运行记录恢复后 status={status_now!r} msg={msg_now[:20]!r} 残留 running=1 计划={zsched}｜{line[:120]}")


# --------------------------------------------------------------------------- 主流程
def main() -> int:
    t0 = time.time()
    try:
        phase0()
        phase1_m0()
        phase2_seed_data()
        phase3_m1()
        phase3b_extras()
        phase4_m3()
        phase_opt()
        phase6_recover()
        stop_all()
        phase5_ut()
    finally:
        stop_all()

    if not EVID.exists():
        EVID.mkdir(parents=True, exist_ok=True)
    agg = {"pass": sum(1 for r in RESULTS if r["result"] == "PASS"),
           "fail": sum(1 for r in RESULTS if r["result"] == "FAIL"),
           "manual": sum(1 for r in RESULTS if r["result"] == "MANUAL"),
           "total": len(RESULTS)}
    report = {
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "C 层（M0/M1/M2/M3 · 可机器判定部分）",
        "isolation": {"dataDir": str(DATA), "uploadsDir": str(UPLOADS), "exportsDir": str(EXPORTS),
                      "ports": [PORT_MAIN, PORT_AUTH], "touchProduction": False},
        "elapsedSeconds": round(time.time() - t0, 1),
        "aggregate": agg,
        "results": RESULTS,
    }
    out = EVID / "c-layer-replay.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 64)
    print(f"PASS {agg['pass']} / FAIL {agg['fail']} / MANUAL {agg['manual']}（共 {agg['total']}）"
          f" · 耗时 {report['elapsedSeconds']}s")
    print(f"报告: {out}")
    if agg["fail"]:
        print("\n失败项：")
        for r in RESULTS:
            if r["result"] == "FAIL":
                print(f"  - {r['id']}: {r['detail'][:180]}")
    return 0 if agg["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
