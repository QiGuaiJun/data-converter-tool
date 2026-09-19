#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""飞书「问题清单」41 条重新验证 · API/服务端侧。

背景：飞书多维表格里 41 条问题中 29 条标「已修复已验证」、12 条未闭环。
用户要求**闭环项也要重新测试**，不能只信表里的状态。本脚本对可在服务端验证的条目
逐条重跑断言，产出**逐条判定**（PASS/FAIL/CONFIRMED-OPEN/MANUAL）。

隔离：独立沙箱 `acceptance/replay-feishu-run<N>/`，端口 51985，不触碰 runtime/ 生产数据。

用法：
    PYTHONPATH= CODEBUDDY_SAFE_DELETE_ENABLED=0 CODEBUDDY_SAFE_DELETE_SANDBOX=0 \
        .venv/Scripts/python.exe acceptance/replay_feishu_issues.py
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
PY = PROJECT / ".venv" / "Scripts" / "python.exe"


def _resolve_sandbox() -> Path:
    base = PROJECT / "acceptance" / os.environ.get("DC_REPLAY_SANDBOX_F", "replay-feishu")
    if not base.exists():
        return base
    for i in range(2, 50):
        cand = base.with_name(f"{base.name}-r{i}")
        if not cand.exists():
            print(f"[warn] 沙箱 {base.name} 已存在，改用 {cand.name}", flush=True)
            return cand
    raise SystemExit("无可用干净沙箱")


SANDBOX = _resolve_sandbox()
DATA = SANDBOX / "data"
UPLOADS = SANDBOX / "uploads"
EXPORTS = SANDBOX / "exports"
SEED = SANDBOX / "seed"
EVID = PROJECT / "acceptance" / "evidence" / "20260919"
PORT = 51985

RESULTS: list[dict] = []
PROCS: list[subprocess.Popen] = []


def rec(rid: str, verdict: str, detail: str) -> None:
    RESULTS.append({"id": rid, "verdict": verdict, "detail": str(detail)[:700]})
    print(f"[{verdict:<13}] {rid} :: {str(detail)[:165]}", flush=True)


def http(method: str, path: str, body=None, ctype: str | None = None, timeout: int = 120):
    url = f"http://127.0.0.1:{PORT}{path}"
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


def mp(files, fields):
    b = "----dcfeishubnd"
    out = b""
    for k, v in fields.items():
        out += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8")
    for k, (fn, data) in files.items():
        out += (f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{fn}"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n").encode("utf-8")
        out += data + b"\r\n"
    out += f"--{b}--\r\n".encode("utf-8")
    return out, f"multipart/form-data; boundary={b}"


def sql(query: str, args=()):
    con = sqlite3.connect(f"file:{DATA / 'imports.db'}?mode=ro", uri=True)
    try:
        return list(con.execute(query, args))
    finally:
        con.close()


def cols(table: str):
    return [(r[1], (r[2] or "").lower(), r[5]) for r in sql(f"pragma table_info({table})")]


def imp(name: str, text: str, **fields):
    body, ctype = mp({"file": (name, text.encode("utf-8-sig"))}, {"targetDbType": "sqlite", **fields})
    return jhttp("POST", "/api/import", body, ctype=ctype)


# 类型类断言必须打在 MySQL 上：SQLite 上日期列**故意**建成 text
# （infer_column_types 里 db_type=="sqlite" 分支直接给 text_type；SQLite 无原生日期类型，
#  值经注册适配器写成 ISO 文本）。P3-27 的 UT 也明确断言「SQLite 导入时间应为文本」。
MYSQL = {"targetDbType": "mysql", "dbHost": "127.0.0.1", "dbPort": 3306, "dbUser": "root",
         "dbPassword": "123456", "dbName": "dc_p2_test"}


def imp_mysql(name: str, text: str, table: str, **extra):
    body, ctype = mp({"file": (name, text.encode("utf-8-sig"))},
                     {**MYSQL, "importMode": "rebuild", "tableName": table, **extra})
    return jhttp("POST", "/api/import", body, ctype=ctype)


def mysql_exec(sql_text: str):
    """在测试库执行 SQL（用于建表/收尾）。走 query 步骤所在的自带连接不可用时退回 mysql 客户端不可行，
    这里用 server 的 MySQL 连接辅助复用导入请求的 beforeAllSql。"""
    body, ctype = mp({"file": ("_probe.csv", b"a\n1\n")},
                     {**MYSQL, "importMode": "rebuild", "tableName": "_probe_tmp",
                      "beforeAllSql": sql_text, "commitMode": "once"})
    return jhttp("POST", "/api/import", body, ctype=ctype)


def mysql_describe(table: str):
    """读 MySQL 表的列名与 DATA_TYPE（判定 date/datetime 必须看真实库）。"""
    import subprocess as sp

    py = str(PY)
    code = (
        "import pymysql,json,sys\n"
        "c=pymysql.connect(host='127.0.0.1',port=3306,user='root',password='123456',database='dc_p2_test')\n"
        f"cur=c.cursor(); cur.execute(\"select column_name,data_type from information_schema.columns "
        f"where table_schema=database() and table_name={table!r} order by ordinal_position\")\n"
        "print(json.dumps([[r[0],r[1]] for r in cur.fetchall()]))\n"
    )
    p = sp.run([py, "-c", code], capture_output=True, timeout=60)
    out = (p.stdout or b"").decode("utf-8", "replace").strip()
    try:
        return dict(json.loads(out))
    except Exception:  # noqa: BLE001
        return {"_error": out or (p.stderr or b"").decode("utf-8", "replace")[:200]}


def start() -> subprocess.Popen:
    env = os.environ.copy()
    env.update({"DATA_DIR": str(DATA), "UPLOADS_DIR": str(UPLOADS), "EXPORTS_DIR": str(EXPORTS),
                "HOST": "127.0.0.1", "PORT": str(PORT), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    fh = open(SANDBOX / "server.log", "wb")
    p = subprocess.Popen([str(PY), "server.py"], cwd=str(PROJECT), env=env, stdout=fh, stderr=subprocess.STDOUT)
    PROCS.append(p)
    return p


def stop_all() -> None:
    for p in list(PROCS):
        try:
            p.terminate()
            p.wait(timeout=12)
        except Exception:  # noqa: BLE001
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass
    PROCS.clear()


# =========================================================================== 导入类


def probe_import_group() -> None:
    print("\n===== 导入类（P1-1 / P2-6 / P2-10 / P2-11 / P2-12 / P3-27） =====", flush=True)

    # P1-1 日期列自动识别（原报：永远导入成 text）—— 必须打在 MySQL 上看真实列类型
    try:
        st, b = imp_mysql("p1_1.csv", "日期,金额\n2026-01-02,10\n2026-01-03,20\n", "f_p1_1")
        decl = mysql_describe("f_p1_1")
        ok = st == 200 and decl.get("日期") in {"date", "datetime"}
        rec("P1-1", "PASS" if ok else "FAIL",
            f"MySQL typeMode=auto 导入日期列 HTTP={st} → 真实列类型={decl}"
            f"（期望 date/datetime；SQLite 侧为设计上固定 text，不作判据）")
    except Exception as e:  # noqa: BLE001
        rec("P1-1", "FAIL", f"异常 {e}")

    # P2-11 指定日期列 dateColumns 强制类型（同样在 MySQL 上判）
    try:
        st, b = imp_mysql("p2_11.csv", "d,金额\n2026/01/02,10\n", "f_p2_11", dateColumns="d")
        decl = mysql_describe("f_p2_11")
        ok = st == 200 and decl.get("d") in {"date", "datetime"}
        rec("P2-11", "PASS" if ok else "FAIL",
            f"dateColumns='d' 且源为 2026/01/02（MySQL）HTTP={st} → 真实列类型={decl}"
            f"（期望 d 为 date/datetime）")
    except Exception as e:  # noqa: BLE001
        rec("P2-11", "FAIL", f"异常 {e}")

    # P2-6 科学计数法 / 超大整数精度
    try:
        big = "12345678901234567890"
        sci = "1.2345678901234e+30"
        imp("p2_6.csv", f"big,sci\n{big},{sci}\n", tableName="f_p2_6", importMode="rebuild")
        rows = sql("select big, sci from f_p2_6")
        got = [str(v) for v in rows[0]] if rows else []
        decl = {n: t for n, t, _ in cols("f_p2_6")}
        ok = got == [big, sci]
        rec("P2-6", "PASS" if ok else "FAIL",
            f"导入超大整数/科学计数法 → 落库值={got}（期望原样 {[big, sci]}）；声明类型={decl}")
    except Exception as e:  # noqa: BLE001
        rec("P2-6", "FAIL", f"异常 {e}")

    # P2-10 自动主键
    try:
        st, b = imp("p2_10.csv", "a,b\nx,1\ny,2\n", tableName="f_p2_10", importMode="rebuild",
                    autoPkField="id")
        c = cols("f_p2_10")
        pk = [n for n, _, ispk in c if ispk]
        vals = [r[0] for r in sql("select id from f_p2_10 order by id")]
        ok = st == 200 and pk == ["id"] and vals == [1, 2]
        rec("P2-10", "PASS" if ok else "FAIL",
            f"autoPkField=id HTTP={st} 主键列={pk} 值={vals}（期望 id / [1,2]）；err={str(b.get('error') or '')[:80]}")
    except Exception as e:  # noqa: BLE001
        rec("P2-10", "FAIL", f"异常 {e}")

    # P3-27 导入时间列建成 text（原报：建成 text）—— SQLite 侧 UT 明确断言「应为文本」，
    # 真实类型断言打在 MySQL 上；两侧都实跑对应 UT。
    try:
        env = os.environ.copy()
        env.update({"DATA_DIR": str(DATA), "UPLOADS_DIR": str(UPLOADS), "EXPORTS_DIR": str(EXPORTS),
                    "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        p = subprocess.run([str(PY), "-m", "pytest", "-q", "-p", "no:cacheprovider",
                            "tests/test_p3_fixes.py::test_p3_27_sqlite_import_time_value_and_no_deprecation",
                            "tests/test_p3_fixes.py::test_p3_27_mysql_import_time_column_is_datetime"],
                           cwd=str(PROJECT), env=env, capture_output=True, timeout=300)
        tail = (p.stdout or b"").decode("utf-8", "replace").strip().splitlines()[-1:]
        st, b = imp_mysql("p3_27.csv", "a\n1\n", "f_p3_27", importTimeField="imported_at")
        decl = mysql_describe("f_p3_27")
        ok = p.returncode == 0 and decl.get("imported_at") in {"datetime", "date"}
        rec("P3-27", "PASS" if ok else "FAIL",
            f"UT（SQLite 文本 + MySQL datetime）→ {tail[0] if tail else 'NA'}；"
            f"MySQL 实测 imported_at 类型={decl.get('imported_at')}（期望 datetime）")
    except Exception as e:  # noqa: BLE001
        rec("P3-27", "FAIL", f"异常 {e}")

    # P2-12 跳过未更新文件（原报：死开关）
    try:
        txt = "a\n1\n"
        st1, _ = imp("p2_12.csv", txt, tableName="f_p2_12", importMode="append", skipSeenFile="true")
        st2, b2 = imp("p2_12.csv", txt, tableName="f_p2_12", importMode="append", skipSeenFile="true")
        sm = b2.get("summary") or {}
        ok = st1 == 200 and st2 == 200 and (sm.get("skippedFiles") or 0) >= 1
        rec("P2-12", "PASS" if ok else "FAIL",
            f"同名同内容文件连导两次 → 第二次 summary={json.dumps(sm, ensure_ascii=False)}"
            f"（期望 skippedFiles≥1）")
    except Exception as e:  # noqa: BLE001
        rec("P2-12", "FAIL", f"异常 {e}")

    # 改进C 导入结果明细报告：必须能说出「第几行、为什么被跳过」
    try:
        # 造一行重复：第 3 行与第 1 行在 a 列上相同 → dedupeColumns=a 必定丢掉一行
        st, b = imp("gc.csv", "a,b\n1,x\n2,y\n1,z\n", tableName="f_imp_c",
                    importMode="rebuild", dedupeColumns="a")
        sm = b.get("summary") or {}
        details = b.get("skipDetails") or []
        with_row = [d for d in details if d.get("dataRow")]
        ok = (st == 200 and sm.get("rowsSkipped") == 1 and bool(with_row)
              and all(d.get("reason") for d in with_row))
        rec("改进C", "PASS" if ok else "FAIL",
            f"dedupeColumns=a 去重 → summary.rowsSkipped={sm.get('rowsSkipped')}（期望 1）；"
            f"顶层 skipDetails={len(details)} 条，样例={json.dumps(details[:2], ensure_ascii=False)}")
    except Exception as e:  # noqa: BLE001
        rec("改进C", "FAIL", f"异常 {e}")

    # 改进A 预览：类型推断 + 类型预警 + **目标表结构对照**（原先 /api/target-table-details 是死接口）
    try:
        body, ctype = mp({"file": ("ga.csv", b"a,b\n1,2026-01-02\n")},
                         {"targetDbType": "sqlite", "hasHeader": "true"})
        st, b = jhttp("POST", "/api/preview", body, ctype=ctype)
        has_types = bool(b.get("columnTypes"))
        imp("ga_tbl.csv", "a,b\n1,2026-01-02\n", tableName="f_imp_a", importMode="rebuild")
        st2, b2 = jhttp("POST", "/api/target-table-details", {"targetDbType": "sqlite", "name": "f_imp_a"})
        table = (b2 or {}).get("table") or {}
        columns = table.get("columns") or []
        app_js = (PROJECT / "public" / "app.js").read_text(encoding="utf-8", errors="replace")
        wired = "target-table-details" in app_js and "refreshTargetTableInfo" in app_js
        ok = st == 200 and has_types and st2 == 200 and bool(columns) and wired
        rec("改进A", "PASS" if ok else "FAIL",
            f"预览 columnTypes={json.dumps(b.get('columnTypes'), ensure_ascii=False)}；"
            f"目标表结构接口 HTTP={st2} 列={[c.get('name') for c in columns]}；"
            f"前端已接线（源列→目标列对照）={wired}")
    except Exception as e:  # noqa: BLE001
        rec("改进A", "FAIL", f"异常 {e}")

    # P3-20 默认数据转换是否有提示（清洗规则清单）
    try:
        body, ctype = mp({"file": ("gb.csv", b"a,b\n1,\n3,x\n")},
                         {"targetDbType": "sqlite", "hasHeader": "true"})
        st, b = jhttp("POST", "/api/preview", body, ctype=ctype)
        hint_keys = [k for k in b if any(w in k.lower() for w in ("clean", "rule", "transform", "trim"))]
        rec("P3-20", "CONFIRMED-OPEN" if not hint_keys else "PASS",
            f"预览响应键={sorted(b.keys())}；清洗/规则类键={hint_keys}（空 = 预览页仍不展示将应用的清洗规则 → 未闭环）")
    except Exception as e:  # noqa: BLE001
        rec("P3-20", "FAIL", f"异常 {e}")

    # P3-17 坏文件是否报英文原文（未闭环项复核）
    try:
        msgs = {}
        for name, payload in (("fake.xlsx", b"not a zip"), ("fake.xls", b"\x00\x01garbage"),
                              ("fake.json", b"{not json")):
            body, ctype = mp({"file": (name, payload)}, {"targetDbType": "sqlite"})
            st, b = jhttp("POST", "/api/preview", body, ctype=ctype)
            msgs[name] = str(b.get("error") or b.get("_raw") or "")[:70]
        raw_english = [k for k, v in msgs.items() if v and all(ord(ch) < 128 for ch in v)]
        rec("P3-17", "CONFIRMED-OPEN" if raw_english else "PASS",
            f"伪文件错误文案={json.dumps(msgs, ensure_ascii=False)}"
            f"（仍为纯英文的={raw_english} → 未闭环）")
    except Exception as e:  # noqa: BLE001
        rec("P3-17", "FAIL", f"异常 {e}")

    # P3-18 断连是否报中文（未闭环项复核）
    try:
        st, b = jhttp("POST", "/api/connections/test", {
            "dbType": "mysql", "host": "127.0.0.1", "port": 3399, "user": "root",
            "password": "x", "database": "nope"})
        err = str(b.get("error") or "")
        chinese = any("\u4e00" <= ch <= "\u9fff" for ch in err)
        head_is_driver = err.strip().startswith("(2003")
        rec("P3-18", "CONFIRMED-OPEN" if (head_is_driver or not chinese) else "PASS",
            f"MySQL 断连 HTTP={st} error={err[:150]}（以驱动原文开头={head_is_driver}，含中文={chinese} → 未闭环）")
    except Exception as e:  # noqa: BLE001
        rec("P3-18", "FAIL", f"异常 {e}")

    # 改进F 删除源文件是否进回收站 / 有二次确认
    try:
        src = SEED / "gf_del.csv"
        src.write_text("a\n1\n", encoding="utf-8")
        body, ctype = mp({}, {"sourcePath": str(src), "targetDbType": "sqlite", "tableName": "f_gf",
                              "importMode": "rebuild", "deleteAfterSuccess": "true"})
        st, _ = jhttp("POST", "/api/import", body, ctype=ctype)
        gone = not src.exists()
        trash_cli = (PROJECT / "server.py").read_text(encoding="utf-8")
        uses_trash = "send2trash" in trash_cli or "SHFileOperation" in trash_cli
        rec("改进F", "CONFIRMED-OPEN" if (gone and not uses_trash) else "PASS",
            f"deleteAfterSuccess 后源文件已消失={gone}；是否走回收站={uses_trash}"
            f"（直接 unlink、无回收站无二次确认 → 未闭环）")
    except Exception as e:  # noqa: BLE001
        rec("改进F", "FAIL", f"异常 {e}")

    # 收尾：dc_p2_test 是长期存在的共享库，用例必须自行清理
    try:
        mysql_exec("drop table if exists f_p1_1, f_p2_11, f_p3_27, _probe_tmp")
        left = mysql_describe("f_p1_1")
        print(f"  [cleanup] MySQL 临时表已清理，复核 f_p1_1 残留={left or '无'}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"  [cleanup] 清理失败（需人工确认）：{e}", flush=True)


# =========================================================================== 导出类


def probe_export_group() -> None:
    print("\n===== 导出类（P1-5 / P2-7 / P2-8 / P2-13） =====", flush=True)

    rows = 2500
    (SEED / "many.csv").write_text("n,v\n" + "".join(f"{i},v{i}\n" for i in range(rows)),
                                   encoding="utf-8-sig")
    imp("many.csv", (SEED / "many.csv").read_text(encoding="utf-8-sig"),
        tableName="f_many", importMode="rebuild")

    # P1-5 按批次分割导出是否丢数据
    try:
        st, b = jhttp("POST", "/api/export/run", {
            "sourceType": "table", "table": "f_many", "targetDbType": "sqlite",
            "extension": "csv", "outputName": "f_batch", "splitByBatch": "true", "batchRows": 1000,
            "openFileAfterExport": False, "openFolderAfterExport": False})
        files = sorted(EXPORTS.rglob("f_batch*"))
        total = 0
        for f in files:
            text = f.read_text(encoding="utf-8-sig", errors="replace").strip().splitlines()
            total += len([ln for ln in text[1:] if ln.strip()])
        sm = b.get("summary") or {}
        ok = st == 200 and len(files) == 3 and total == rows
        rec("P1-5", "PASS" if ok else "FAIL",
            f"2500 行 / batchRows=1000 → 产出 {len(files)} 个文件，累计数据行={total}"
            f"（期望 3 个 / {rows} 行，无静默丢失）；err={str(b.get('error') or '')[:60]}")
    except Exception as e:  # noqa: BLE001
        rec("P1-5", "FAIL", f"异常 {e}")

    # P2-7 行数是否精确（SQLite 侧应为精确 COUNT）
    try:
        st, b = jhttp("POST", "/api/export/sources", {"targetDbType": "sqlite"})
        items = b.get("sources") or b.get("tables") or []
        hit = [i for i in items if str(i.get("name")) == "f_many"]
        exact = bool(hit) and hit[0].get("rows") == rows and hit[0].get("rowsApproximate") is False
        rec("P2-7", "PASS" if exact else "FAIL",
            f"SQLite 源行数={hit[0].get('rows') if hit else 'NA'} rowsApproximate="
            f"{hit[0].get('rowsApproximate') if hit else 'NA'}（期望 {rows} / False）")
    except Exception as e:  # noqa: BLE001
        rec("P2-7", "FAIL", f"异常 {e}")

    # P2-8 目标文件夹不存在是否自动创建
    try:
        target = EXPORTS / "auto" / "nested" / "deep"
        st, b = jhttp("POST", "/api/export/run", {
            "sourceType": "table", "table": "f_many", "targetDbType": "sqlite",
            "extension": "csv", "outputName": "f_auto", "exportFolder": str(target),
            "openFileAfterExport": False, "openFolderAfterExport": False})
        made = target.exists() and any(target.glob("f_auto*"))
        rec("P2-8", "PASS" if st == 200 and made else "FAIL",
            f"导出到不存在的多级目录 → HTTP={st} 目录已创建且落盘={made}")
    except Exception as e:  # noqa: BLE001
        rec("P2-8", "FAIL", f"异常 {e}")

    # P2-13 表注释作文件名（SQLite 无注释 → 回退；MySQL 侧由 UT 覆盖）
    try:
        import re
        ut = (PROJECT / "tests" / "test_p2_fixes.py").read_text(encoding="utf-8")
        has_case = bool(re.search(r"def test_p2_13_comment_as_filename_mysql", ut))
        env = os.environ.copy()
        env.update({"DATA_DIR": str(DATA), "UPLOADS_DIR": str(UPLOADS), "EXPORTS_DIR": str(EXPORTS),
                    "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        p = subprocess.run([str(PY), "-m", "pytest", "-q", "-p", "no:cacheprovider",
                            "tests/test_p2_fixes.py::test_p2_13_comment_as_filename_mysql"],
                           cwd=str(PROJECT), env=env, capture_output=True, timeout=180)
        passed = p.returncode == 0 and b"1 passed" in (p.stdout or b"")
        rec("P2-13", "PASS" if (has_case and passed) else "FAIL",
            f"MySQL 侧用例存在={has_case} 实跑={'1 passed' if passed else (p.stdout or b'').decode('utf-8','replace')[-120:]}")
    except Exception as e:  # noqa: BLE001
        rec("P2-13", "FAIL", f"异常 {e}")


# =========================================================================== 安全类


def probe_security_group() -> None:
    print("\n===== 安全类（P1-3 / P1-4 / P2-14） =====", flush=True)

    # P1-3 前端硬编码凭据 + 密码随 GET URL 明文
    try:
        hits = {}
        for js in (PROJECT / "public").glob("*.js"):
            text = js.read_text(encoding="utf-8", errors="replace")
            found = [w for w in ("admin", "password=", "Authorization: Basic", "APP_AUTH") if w in text]
            if found:
                hits[js.name] = found
        conn_js = (PROJECT / "public" / "connections.js").read_text(encoding="utf-8", errors="replace")
        pw_in_url = bool([m for m in ("?password", "&password", "password=") if m in conn_js])
        rec("P1-3", "PASS" if not pw_in_url else "FAIL",
            f"前端 js 中出现的敏感词={hits or '无'}；connections.js 是否把密码拼进 URL={pw_in_url}"
            f"（密码走 POST body、GET 列表不含 password 键）")
    except Exception as e:  # noqa: BLE001
        rec("P1-3", "FAIL", f"异常 {e}")

    # P1-4 密码存储格式
    try:
        st, b = jhttp("POST", "/api/connections", {"name": "feishu-复核", "dbType": "mysql",
                                                   "host": "127.0.0.1", "port": 3306, "user": "root",
                                                   "password": "PlainPwd!7", "database": "dc_p2_test"})
        cid = str((b.get("connection") or {}).get("id") or "")
        row = sql("select password from _db_connections where id = ?", (cid,))
        pw = row[0][0] if row else ""
        st2, b2 = jhttp("GET", "/api/connections")
        items = b2.get("connections") or []
        no_key = all("password" not in c for c in items)
        rec("P1-4", "PASS" if pw.startswith("fernet:") and "PlainPwd!7" not in pw and no_key else "FAIL",
            f"入库前缀={pw[:7]} 含明文={'PlainPwd!7' in pw}；GET 列表含 password 键={not no_key}（期望 False）")
    except Exception as e:  # noqa: BLE001
        rec("P1-4", "FAIL", f"异常 {e}")

    # P2-14 下载接口路径校验
    try:
        st, b = jhttp("GET", "/api/export/download?path=C%3A%5CWindows%5Cwin.ini")
        err = str(b.get("error") or "")
        rec("P2-14", "PASS" if st == 400 and "不在允许下载的目录内" in err else "FAIL",
            f"越界下载 HTTP={st} err={err[:80]}")
    except Exception as e:  # noqa: BLE001
        rec("P2-14", "FAIL", f"异常 {e}")


# =========================================================================== 调度/作业类


def probe_schedule_group() -> None:
    print("\n===== 调度/作业类（P2-9 / BUG-29 / BUG-30 / 改进B） =====", flush=True)

    # BUG-29 引用 task_sources 一次性副本应给出明确中文报错
    try:
        ts = DATA / "task_sources" / "sub"
        ts.mkdir(parents=True, exist_ok=True)
        (ts / "copy.csv").write_text("a\n1\n", encoding="utf-8-sig")
        st, b = jhttp("POST", "/api/jobs", {"name": "feishu-BUG29", "steps": [{
            "name": "导入", "type": "import", "enabled": True, "continueOnError": False,
            "config": {"sourcePath": str(ts / "copy.csv"), "tableName": "f_bug29",
                       "targetDbType": "sqlite", "importMode": "rebuild"}}]})
        jid = str((b.get("job") or {}).get("id") or "")
        st2, b2 = jhttp("POST", "/api/jobs/run", {"id": jid})
        text = str((b2.get("run") or {}).get("message") or "") + str(b2.get("error") or "")
        ok = "一次性副本" in text
        rec("BUG-29", "PASS" if ok else "FAIL",
            f"引用 task_sources 副本执行 → HTTP={st2} 命中「一次性副本」提示={ok}；文案={text[:110]}")
    except Exception as e:  # noqa: BLE001
        rec("BUG-29", "FAIL", f"异常 {e}")

    # 改进B 作业导入步骤通过 importJobId 引用完整导入配置
    try:
        st, b = jhttp("POST", "/api/jobs", {"name": "feishu-导入任务", "steps": [{
            "name": "任务步骤", "type": "import", "enabled": True, "continueOnError": False,
            "config": {"sourcePath": str(SEED / "gb_task.csv"), "tableName": "f_gaib",
                       "targetDbType": "sqlite", "importMode": "rebuild"}}]})
        (SEED / "gb_task.csv").write_text("a,b\n1,2\n", encoding="utf-8-sig")
        task_id = str((b.get("job") or {}).get("id") or "")
        st2, b2 = jhttp("POST", "/api/jobs", {"name": "feishu-引用改B", "steps": [{
            "name": "引用", "type": "import", "enabled": True, "continueOnError": False,
            "config": {"importJobId": task_id}}]})
        jid = str((b2.get("job") or {}).get("id") or "")
        st3, b3 = jhttp("POST", "/api/jobs/run", {"id": jid})
        cnt = sql("select count(*) from f_gaib")
        ok = st3 == 200 and (b3.get("run") or {}).get("status") == "成功" and cnt[0][0] == 1
        rec("改进B", "PASS" if ok else "FAIL",
            f"importJobId 引用导入任务 → 运行 status={(b3.get('run') or {}).get('status')} "
            f"目标表行数={cnt[0][0]}（期望 成功 / 1）；err={str(b3.get('error') or '')[:80]}")
    except Exception as e:  # noqa: BLE001
        rec("改进B", "FAIL", f"异常 {e}")

    # BUG-30 立即执行回写定时任务状态
    try:
        st, b = jhttp("POST", "/api/jobs", {"name": "feishu-BUG30", "steps": [
            {"name": "q", "type": "query", "enabled": True, "continueOnError": False,
             "config": {"sql": "select 1", "targetDbType": "sqlite"}}]})
        jid = str((b.get("job") or {}).get("id") or "")
        _, b2 = jhttp("POST", "/api/schedules", {"name": "feishu-调度30", "jobId": jid,
                                                "enabled": False,
                                                "rule": {"mode": "interval", "amount": 30, "unit": "minutes"}})
        sid = str((b2.get("schedule") or {}).get("id") or "")
        jhttp("POST", "/api/jobs/run", {"id": jid, "scheduleId": sid})
        row = sql("select last_run_at, last_status from _schedules where id = ?", (sid,))
        ok = bool(row) and bool(row[0][0]) and row[0][1] in {"成功", "失败", "跳过"}
        rec("BUG-30", "PASS" if ok else "FAIL",
            f"带 scheduleId 立即执行 → last_run_at={row[0][0] if row else 'NA'} "
            f"last_status={row[0][1] if row else 'NA'}（期望均非空）")
    except Exception as e:  # noqa: BLE001
        rec("BUG-30", "FAIL", f"异常 {e}")

    # P2-9 running 无启动恢复 → 用进程内探针（复用 B 层脚本）
    try:
        st, b = jhttp("POST", "/api/jobs", {"name": "feishu-恢复9宿主", "steps": [
            {"name": "q", "type": "query", "enabled": True, "continueOnError": False,
             "config": {"sql": "select 1", "targetDbType": "sqlite"}}]})
        host9 = str((b.get("job") or {}).get("id") or "")
        st, b = jhttp("POST", "/api/schedules", {"name": "feishu-恢复9", "jobId": host9,
                                                 "enabled": False,
                                                 "rule": {"mode": "interval", "amount": 60, "unit": "minutes"}})
        sid = str((b.get("schedule") or {}).get("id") or "")
        if not sid:
            raise RuntimeError(f"调度创建失败：{b}")
        env = os.environ.copy()
        p = subprocess.run([str(PY), "acceptance/probe_b_inprocess.py", "recover", str(DATA),
                            "--seed-schedule", sid], cwd=str(PROJECT), env=env,
                           capture_output=True, timeout=120)
        out = (p.stdout or b"").decode("utf-8", "replace")
        row = sql("select running from _schedules where id = ?", (sid,))
        ok = bool(row) and int(row[0][0]) == 0 and "recovered=" in out
        rec("P2-9", "PASS" if ok else "FAIL",
            f"注入 running=1 后调用 recover_interrupted_runs → running={row[0][0] if row else 'NA'}"
            f"；{out.strip().splitlines()[-1][:110] if out.strip() else ''}")
    except Exception as e:  # noqa: BLE001
        rec("P2-9", "FAIL", f"异常 {e}")

    # P3-25 导入期间页面是否被阻塞（并发性）
    try:
        big = "n,v\n" + "".join(f"{i},x{i}\n" for i in range(8000))
        (SEED / "slow.csv").write_text(big, encoding="utf-8-sig")
        lat = {}

        def slow():
            imp("slow.csv", big, tableName="f_slow", importMode="rebuild")

        t = threading.Thread(target=slow)
        t.start()
        time.sleep(0.35)
        for _ in range(3):
            t0 = time.time()
            st, _ = http("GET", "/api/ping", timeout=10)
            lat[st] = round((time.time() - t0) * 1000)
        t.join(timeout=180)
        fastest = min(lat.values()) if lat else 99999
        ok = 200 in lat and fastest < 3000
        rec("P3-25", "PASS" if ok else "FAIL",
            f"大文件导入进行中并发请求 /api/ping → 延迟(ms)={lat}（最快 {fastest}ms，期望 <3000ms）")
    except Exception as e:  # noqa: BLE001
        rec("P3-25", "FAIL", f"异常 {e}")

    # 改进E 关闭程序是否提示定时任务将停用（静态判定）
    try:
        handlers = []
        for js in (PROJECT / "public").glob("*.js"):
            text = js.read_text(encoding="utf-8", errors="replace")
            if "beforeunload" in text or "onbeforeunload" in text:
                handlers.append(js.name)
        rec("改进E", "CONFIRMED-OPEN" if not handlers else "PASS",
            f"前端是否存在关闭确认钩子（beforeunload）→ {handlers or '无'}（无 = 仍未闭环）")
    except Exception as e:  # noqa: BLE001
        rec("改进E", "FAIL", f"异常 {e}")


# =========================================================================== 静态/入口类


def probe_static_group() -> None:
    print("\n===== 静态/入口类（P3-26 / P3-21 / 改进D / 改进H / 改进G / P3-19 / P2-16） =====", flush=True)

    # P3-26 favicon
    try:
        st_ico, _ = http("GET", "/favicon.ico")
        st_svg, _ = http("GET", "/favicon.svg")
        html = (PROJECT / "public" / "index.html").read_text(encoding="utf-8", errors="replace")
        linked = "favicon" in html
        rec("P3-26", "PASS" if (st_ico == 200 or st_svg == 200) and linked else "FAIL",
            f"/favicon.ico HTTP={st_ico}、/favicon.svg HTTP={st_svg}；index.html 已声明 favicon={linked}")
    except Exception as e:  # noqa: BLE001
        rec("P3-26", "FAIL", f"异常 {e}")

    # P3-21 四个入口是否仍是占位页 + 手册是否仍是占位
    try:
        mod = (PROJECT / "public" / "module-pages.js").read_text(encoding="utf-8", errors="replace")
        docs_js = PROJECT / "public" / "docs.js"
        placeholder_pages = [p.name for p in (PROJECT / "public").glob("*.html")
                             if "module-pages.js" in p.read_text(encoding="utf-8", errors="replace")]
        docs_is_placeholder = "占位" in (docs_js.read_text(encoding="utf-8", errors="replace")[:2000]) if docs_js.exists() else True
        # 修复要求：占位页必须显式标注「未开放」，否则用户会以为功能已可用。
        marked = [p for p in sorted(placeholder_pages)
                  if f'status: "未开放"' in mod and p.replace(".html", "") in mod]
        badges_ok = mod.count('status: "未开放"') >= 3 and "module-status" in mod
        rec("P3-21", "PASS" if (badges_ok and len(marked) >= 3) else "PARTIAL",
            f"仍用 module-pages.js 渲染的占位页={len(placeholder_pages)} 个（按设计保留）；"
            f"已标注「未开放」的模块数={mod.count('status: \"未开放\"')}（期望 ≥3）、"
            f"徽标渲染已接入={('module-status' in mod)}；"
            f"docs.js 体积={docs_js.stat().st_size if docs_js.exists() else 0}B、像占位页={docs_is_placeholder}")

        # 改进D 手册内容体量
        djs = docs_js.read_text(encoding="utf-8", errors="replace") if docs_js.exists() else ""
        n_docs = djs.count("title:") + djs.count("desc:")
        rec("改进D", "PARTIAL" if len(djs) > 15000 else "CONFIRMED-OPEN",
            f"docs.js 体积={len(djs)}B，可辨识的条目字段数≈{n_docs}"
            f"（备注称「docs.html 无实质内容、仅占位句」已过时；但内容是否覆盖核心场景需人工判断）")
    except Exception as e:  # noqa: BLE001
        rec("P3-21", "FAIL", f"异常 {e}")
        rec("改进D", "FAIL", f"异常 {e}")

    # 改进H 回归测试是否进仓库
    try:
        tests = sorted((PROJECT / "tests").glob("test_*.py")) if (PROJECT / "tests").exists() else []
        acc = sorted((PROJECT / "acceptance").glob("*.py")) if (PROJECT / "acceptance").exists() else []
        env = os.environ.copy()
        env.update({"DATA_DIR": str(DATA), "UPLOADS_DIR": str(UPLOADS), "EXPORTS_DIR": str(EXPORTS)})
        p = subprocess.run([str(PY), "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=str(PROJECT),
                           env=env, capture_output=True, timeout=600)
        tail = (p.stdout or b"").decode("utf-8", "replace").strip().splitlines()[-1:]
        rec("改进H", "PASS" if tests and "passed" in (tail[0] if tail else "") else "FAIL",
            f"tests/ 用例文件={len(tests)} 个、acceptance/ 脚本={len(acc)} 个；`pytest -q` → {tail[0] if tail else 'NA'}"
            f"（备注称「tests/ 未建立、AI 改动无护栏」已过时）")
    except Exception as e:  # noqa: BLE001
        rec("改进H", "FAIL", f"异常 {e}")

    # 改进G 素色模式 + 版本号
    try:
        styles = (PROJECT / "public" / "styles.css").read_text(encoding="utf-8", errors="replace")
        shell_js = (PROJECT / "public" / "shell.js")
        shell_src = shell_js.read_text(encoding="utf-8", errors="replace") if shell_js.exists() else ""
        css_ok = "body.plain-mode" in styles
        toggle_ok = "plain-mode-toggle" in styles and "dc_plain_mode_v1" in shell_src
        pages_with_shell = [p.name for p in (PROJECT / "public").glob("*.html")
                            if "/shell.js" in p.read_text(encoding="utf-8", errors="replace")]
        ver_js = (PROJECT / "public" / "version.js")
        has_ver = ver_js.exists() or "appVersion" in (PROJECT / "public" / "module-pages.js").read_text(encoding="utf-8")
        st, b = jhttp("GET", "/api/meta")
        ok = has_ver and css_ok and toggle_ok and len(pages_with_shell) >= 11
        rec("改进G", "PASS" if ok else "PARTIAL",
            f"版本号入口={has_ver}（/api/meta.appVersion={b.get('appVersion')}）；"
            f"素色模式 CSS 规则={css_ok}、开关+持久化={toggle_ok}（键 dc_plain_mode_v1）；"
            f"已引入全站外观脚本的页面={len(pages_with_shell)} 个（期望 11）")
    except Exception as e:  # noqa: BLE001
        rec("改进G", "FAIL", f"异常 {e}")

    # P3-19 系统库过滤（UT）
    try:
        env = os.environ.copy()
        env.update({"DATA_DIR": str(DATA), "UPLOADS_DIR": str(UPLOADS), "EXPORTS_DIR": str(EXPORTS)})
        p = subprocess.run([str(PY), "-m", "pytest", "-q", "-p", "no:cacheprovider",
                            "tests/test_p3_fixes.py::test_p3_19_test_connection_filters_system_databases"],
                           cwd=str(PROJECT), env=env, capture_output=True, timeout=180)
        ok = p.returncode == 0 and b"1 passed" in (p.stdout or b"")
        rec("P3-19", "PASS" if ok else "FAIL",
            f"UT test_p3_19_test_connection_filters_system_databases → "
            f"{'1 passed' if ok else (p.stdout or b'').decode('utf-8','replace')[-120:]}")
    except Exception as e:  # noqa: BLE001
        rec("P3-19", "FAIL", f"异常 {e}")

    # P2-16 编辑器草稿（localStorage）
    try:
        app = (PROJECT / "public" / "app.js").read_text(encoding="utf-8", errors="replace")
        exp = (PROJECT / "public" / "export.js").read_text(encoding="utf-8", errors="replace")
        ok = "IMPORT_DRAFT_KEY" in app and "localStorage" in exp
        rec("P2-16", "PASS" if ok else "FAIL",
            f"导入草稿键存在={('IMPORT_DRAFT_KEY' in app)}；导出侧使用 localStorage={('localStorage' in exp)}")
    except Exception as e:  # noqa: BLE001
        rec("P2-16", "FAIL", f"异常 {e}")


def main() -> int:
    print(f"沙箱：{SANDBOX}", flush=True)
    for d in (DATA, UPLOADS, EXPORTS, SEED):
        d.mkdir(parents=True, exist_ok=True)
    start()
    t0 = time.time()
    while time.time() - t0 < 45:
        st, _ = http("GET", "/api/ping", timeout=5)
        if st == 200:
            break
        time.sleep(1)
    print(f"服务就绪：http://127.0.0.1:{PORT}\n", flush=True)
    try:
        probe_import_group()
        probe_export_group()
        probe_security_group()
        probe_schedule_group()
        probe_static_group()
    finally:
        stop_all()

    tally: dict[str, int] = {}
    for r in RESULTS:
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    print("\n" + "=" * 64, flush=True)
    print(f"逐条判定：{json.dumps(tally, ensure_ascii=False)}（共 {len(RESULTS)} 条）", flush=True)
    EVID.mkdir(parents=True, exist_ok=True)
    (EVID / "feishu-issues-recheck.json").write_text(
        json.dumps({"sandbox": str(SANDBOX), "results": RESULTS}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"报告：{EVID / 'feishu-issues-recheck.json'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
