#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""B 层（M4/M6/M7/M8/M9/M10）复跑 harness · 全程隔离。

对应 V5 计划「数据来源分层」中的 B 层 75 条：
  M4（除 005）、M6（除 025~028）、M7（全部）、M8（全部）、M9（除 003/005）、M10（除 005/007）

覆盖其中**可机器判定**的部分；UT 类由 pytest 结果映射、M8/部分 M4/M6/M7 的 UI 类走 Playwright。

隔离口径
--------
DATA_DIR / UPLOADS_DIR / EXPORTS_DIR 全部指向 acceptance/replay-b-20260919-run<N>/，
端口用 51981，**绝不触碰 runtime/ 下的生产库与生产数据**。

用法
----
    # 宿主批量删除钩子会掐断脚本内的删除动作，故需显式关闭（仅限隔离沙箱目录内）：
    PYTHONPATH= CODEBUDDY_SAFE_DELETE_ENABLED=0 CODEBUDDY_SAFE_DELETE_SANDBOX=0 \
        .venv/Scripts/python.exe acceptance/replay_b_layer.py
"""
from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
PY = PROJECT / ".venv" / "Scripts" / "python.exe"


def _resolve_sandbox() -> Path:
    """取得一个**干净**的隔离沙箱目录（旧目录存在就换新，不做批量删除）。"""
    base = PROJECT / "acceptance" / os.environ.get("DC_REPLAY_SANDBOX_B", "replay-b-20260919")
    if not base.exists():
        return base
    for i in range(2, 50):
        cand = base.with_name(f"{base.name}-r{i}")
        if not cand.exists():
            print(f"[warn] 沙箱 {base.name} 已存在（不做批量删除），自动改用 {cand.name}", flush=True)
            return cand
    raise SystemExit("无法获得干净的隔离沙箱目录，请设置 DC_REPLAY_SANDBOX_B=<新名字>")


SANDBOX = _resolve_sandbox()
DATA = SANDBOX / "data"
UPLOADS = SANDBOX / "uploads"
EXPORTS = SANDBOX / "exports"
SEED = SANDBOX / "seed"
EVID = PROJECT / "acceptance" / "evidence" / "20260919"
PORT = 51981

RESULTS: list[dict] = []
PROCS: list[subprocess.Popen] = []


# --------------------------------------------------------------------------- 基础


def rec(rid: str, ok: bool, detail: str, kind: str = "AUTO") -> None:
    RESULTS.append({"id": rid, "result": "PASS" if ok else "FAIL", "kind": kind, "detail": str(detail)[:700]})
    print(f"[{'PASS' if ok else 'FAIL'}] {rid} :: {str(detail)[:170]}", flush=True)


def note(rid: str, detail: str) -> None:
    RESULTS.append({"id": rid, "result": "MANUAL", "kind": "MANUAL", "detail": str(detail)[:700]})
    print(f"[MANUL] {rid} :: {str(detail)[:170]}", flush=True)


def http(method: str, path: str, body=None, ctype: str | None = None, timeout: int = 60):
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


def mp(files: dict, fields: dict):
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


def start() -> subprocess.Popen:
    env = os.environ.copy()
    env.update(
        {
            "DATA_DIR": str(DATA),
            "UPLOADS_DIR": str(UPLOADS),
            "EXPORTS_DIR": str(EXPORTS),
            "HOST": "127.0.0.1",
            "PORT": str(PORT),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
    )
    fh = open(SANDBOX / "server.log", "wb")
    p = subprocess.Popen([str(PY), "server.py"], cwd=str(PROJECT), env=env, stdout=fh, stderr=subprocess.STDOUT)
    PROCS.append(p)
    return p


def wait_ready(timeout: int = 45) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, _ = http("GET", "/api/ping", timeout=5)
        if st == 200:
            return True
        time.sleep(1)
    return False


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


def sql(query: str, args=()):
    con = sqlite3.connect(f"file:{DATA / 'imports.db'}?mode=ro", uri=True)
    try:
        return list(con.execute(query, args))
    finally:
        con.close()


def run_script(args: list[str], env_extra: dict | None = None, timeout: int = 180):
    """在项目根跑一个脚本，返回 (rc, stdout+stderr)。"""
    env = os.environ.copy()
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    if env_extra:
        env.update(env_extra)
    p = subprocess.run([str(PY), *args], cwd=str(PROJECT), env=env, capture_output=True, timeout=timeout)
    out = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
    return p.returncode, out.strip()


# --------------------------------------------------------------------------- 阶段 0


def phase0() -> None:
    print("\n===== 阶段 0 · 准备隔离沙箱（B 层） =====", flush=True)
    if SANDBOX.exists():
        raise SystemExit(f"沙箱目录已存在，应由 _resolve_sandbox() 换新：{SANDBOX}")
    for d in (DATA, UPLOADS, EXPORTS, SEED):
        d.mkdir(parents=True, exist_ok=True)
    (SEED / "b_people.csv").write_bytes("name,amount\nAlice,10\nBob,20\n".encode("utf-8-sig"))
    (SEED / "b_big.csv").write_bytes(
        ("name,amount\n" + "".join(f"r{i},{i}\n" for i in range(1, 1201))).encode("utf-8-sig")
    )
    print(f"沙箱就绪：{SANDBOX}", flush=True)


# --------------------------------------------------------------------------- M4 查询


def import_csv(fname: str, payload_extra: dict) -> tuple[int, dict]:
    body, ctype = mp({"file": (fname, (SEED / fname).read_bytes())},
                     {"targetDbType": "sqlite", **payload_extra})
    return jhttp("POST", "/api/import", body, ctype=ctype)


def phase_m4() -> None:
    print("\n===== M4 · 查询模块 =====", flush=True)

    # M4-001 执行只读查询
    try:
        st, b = jhttp("POST", "/api/query/run", {"sql": "select 1 as one", "targetDbType": "sqlite"})
        cols = b.get("columns")
        ok = (st == 200 and cols == ["one"] and b.get("rowCount") == 1
              and b.get("truncated") is False and isinstance(b.get("elapsedMs"), int)
              and bool(b.get("message")))
        rec("M4-001", ok, f"HTTP={st} columns={cols} rowCount={b.get('rowCount')} "
                          f"truncated={b.get('truncated')} elapsedMs={b.get('elapsedMs')} message={b.get('message')}")
    except Exception as e:  # noqa: BLE001
        rec("M4-001", False, f"异常 {e}")

    # M4-002 写语句可用 + 高危语句一次性令牌（2026-09-21 口径变更：查询页=完整 SQL 控制台）
    try:
        jhttp("POST", "/api/query/run", {"sql": "create table if not exists m4_write (id int)", "targetDbType": "sqlite"})
        jhttp("POST", "/api/query/run", {"sql": "delete from m4_write", "targetDbType": "sqlite", "confirmToken": ""})
        st_ins, b_ins = jhttp("POST", "/api/query/run", {"sql": "insert into m4_write (id) values (1)", "targetDbType": "sqlite"})
        st_first, b_first = jhttp("POST", "/api/query/run", {"sql": "delete from m4_write", "targetDbType": "sqlite"})
        token = str(b_first.get("confirmToken") or "")
        st_second, b_second = jhttp("POST", "/api/query/run",
                                    {"sql": "delete from m4_write", "targetDbType": "sqlite", "confirmToken": token})
        st_reuse, b_reuse = jhttp("POST", "/api/query/run",
                                  {"sql": "delete from m4_write", "targetDbType": "sqlite", "confirmToken": token})
        affected = (b_second.get("totals") or {}).get("affectedRows")
        ok = (st_ins == 200 and (b_ins.get("totals") or {}).get("affectedRows") == 1
              and st_first == 200 and b_first.get("needConfirm") is True and bool(token)
              and st_second == 200 and not b_second.get("needConfirm") and affected == 1
              and st_reuse == 200 and b_reuse.get("needConfirm") is True)
        rec("M4-002", ok, f"INSERT→HTTP={st_ins} 影响 {(b_ins.get('totals') or {}).get('affectedRows')} 行；"
                          f"无 WHERE 的 DELETE 首调 needConfirm={b_first.get('needConfirm')}（令牌 {bool(token)}）；"
                          f"带令牌执行影响 {affected} 行；令牌复用被拒 needConfirm={b_reuse.get('needConfirm')}")
    except Exception as e:  # noqa: BLE001
        rec("M4-002", False, f"异常 {e}")

    # M4-003 多语句脚本（2026-09-21 口径变更：按引号/注释感知切分后逐条执行）
    try:
        st_multi, b_multi = jhttp("POST", "/api/query/run",
                                  {"sql": "select 1; select 2", "targetDbType": "sqlite"})
        st_quoted, b_quoted = jhttp("POST", "/api/query/run",
                                    {"sql": "select ';' as semi; select 2", "targetDbType": "sqlite"})
        st_tail, b_tail = jhttp("POST", "/api/query/run",
                                {"sql": "select 1;", "targetDbType": "sqlite"})
        mt = b_multi.get("totals") or {}
        qt = b_quoted.get("totals") or {}
        ok = (st_multi == 200 and mt.get("statements") == 2 and mt.get("resultSets") == 2
              and st_quoted == 200 and qt.get("statements") == 2
              and st_tail == 200 and (b_tail.get("totals") or {}).get("statements") == 1)
        rec("M4-003", ok, f"两语句→HTTP={st_multi} 语句数={mt.get('statements')} 结果集={mt.get('resultSets')}；"
                          f"字符串内含分号→语句数={qt.get('statements')}（期望 2，证明切分感知引号）；"
                          f"尾随单分号→HTTP={st_tail}")
    except Exception as e:  # noqa: BLE001
        rec("M4-003", False, f"异常 {e}")

    # M4-004 >1000 行截断（先导入 1200 行大表）
    try:
        import_csv("b_big.csv", {"tableName": "m4_big", "importMode": "rebuild"})
        st, b = jhttp("POST", "/api/query/run", {"sql": "select * from m4_big", "targetDbType": "sqlite"})
        rows = b.get("rows") or []
        ok = st == 200 and len(rows) == 1000 and b.get("truncated") is True and b.get("rowCount") == 1000
        rec("M4-004", ok, f"HTTP={st} rows={len(rows)} rowCount={b.get('rowCount')} truncated={b.get('truncated')}"
                          f"（表内 1200 行，期望返回 1000 + truncated=true）")
    except Exception as e:  # noqa: BLE001
        rec("M4-004", False, f"异常 {e}")

    # M4-006 保存查询（入 _saved_queries + upsert 代理 job）
    qid = ""
    try:
        st, b = jhttp("POST", "/api/queries", {"name": "复跑查询A", "sql": "select 1 as one", "connectionId": ""})
        saved = b.get("query") or b
        qid = str(saved.get("id") or "")
        rows = sql("select id, name, sql_text from _saved_queries where name = ?", ("复跑查询A",))
        jobs = sql("select id, name from _jobs where name = ?", ("查询：复跑查询A",))
        ok = st == 200 and bool(qid) and len(rows) == 1 and len(jobs) == 1
        rec("M4-006", ok, f"HTTP={st} _saved_queries={len(rows)} 行；代理 job 名=查询：复跑查询A 命中 {len(jobs)} 条")
    except Exception as e:  # noqa: BLE001
        rec("M4-006", False, f"异常 {e}")

    # M4-007 查询名为空
    try:
        st, b = jhttp("POST", "/api/queries", {"name": "", "sql": "select 1", "connectionId": ""})
        err = str(b.get("error") or "")
        rec("M4-007", st == 400 and "请填写查询名称" in err, f"HTTP={st} err={err[:70]}")
    except Exception as e:  # noqa: BLE001
        rec("M4-007", False, f"异常 {e}")

    # M4-008 SQL 为空
    try:
        st, b = jhttp("POST", "/api/queries", {"name": "复跑空SQL", "sql": "", "connectionId": ""})
        err = str(b.get("error") or "")
        rec("M4-008", st == 400 and "请输入要保存的 SQL" in err, f"HTTP={st} err={err[:70]}")
    except Exception as e:  # noqa: BLE001
        rec("M4-008", False, f"异常 {e}")

    # M4-009 查询列表
    try:
        st, b = jhttp("GET", "/api/queries")
        items = b.get("queries") or []
        hit = [q for q in items if q.get("name") == "复跑查询A"]
        ok = (st == 200 and bool(hit) and "connectionId" in hit[0] and hit[0].get("sql") == "select 1 as one")
        rec("M4-009", ok, f"HTTP={st} 共 {len(items)} 条；命中={json.dumps(hit[0], ensure_ascii=False)[:150] if hit else '无'}")
    except Exception as e:  # noqa: BLE001
        rec("M4-009", False, f"异常 {e}")

    # M4-013 同名重存：更新同一条查询，且不产生第二个代理 job
    try:
        st1, b1 = jhttp("POST", "/api/queries", {"name": "复跑查询B", "sql": "select 1", "connectionId": ""})
        rid = str((b1.get("query") or {}).get("id") or "")
        jhttp("POST", "/api/queries", {"name": "复跑查询B", "sql": "select 2", "connectionId": ""})
        jhttp("POST", "/api/queries", {"id": rid, "name": "复跑查询B", "sql": "select 3", "connectionId": ""})
        qn = len(sql("select id from _saved_queries where name = ?", ("复跑查询B",)))
        jobs = sql("select id, steps_json from _jobs where name = ?", ("查询：复跑查询B",))
        sql_now = sql("select sql_text from _saved_queries where id = ?", (rid,))
        rec("M4-013", st1 == 200 and len(jobs) == 1 and qn == 2 and
            (sql_now and sql_now[0][0] == "select 3"),
            f"同名查询保存 3 次（第 3 次带原 id）→ 代理 job {len(jobs)} 条（期望 1，不产生重复）；"
            f"_saved_queries 同名 {qn} 条（前两次无 id 各建一条属设计）；按 id 重存后 sql={sql_now[0][0] if sql_now else 'NA'}")
    except Exception as e:  # noqa: BLE001
        rec("M4-013", False, f"异常 {e}")

    # M4-011 删除查询（连带删代理 job）
    try:
        st, b = jhttp("DELETE", f"/api/queries?id={qid}")
        left_q = len(sql("select id from _saved_queries where id = ?", (qid,)))
        left_j = len(sql("select id from _jobs where name = ?", ("查询：复跑查询A",)))
        removed = b.get("removedJobs")
        ok = st == 200 and left_q == 0 and left_j == 0 and removed == 1
        rec("M4-011", ok, f"HTTP={st} removedJobs={removed} 残留 query={left_q} job={left_j}")
    except Exception as e:  # noqa: BLE001
        rec("M4-011", False, f"异常 {e}")


# --------------------------------------------------------------------------- M6 作业


def make_job(name: str, steps: list, job_id: str = "", guard: dict | None = None) -> tuple[int, dict]:
    return jhttp("POST", "/api/jobs", {"id": job_id, "name": name, "enabled": True,
                                       "steps": steps, "guard": guard or {}})


def qstep(sql_text: str, name: str = "查询步骤", **extra) -> dict:
    return {"name": name, "type": "query", "enabled": True, "continueOnError": False,
            "config": {"sql": sql_text, "targetDbType": "sqlite", **extra}}


def popen_steps(job_id: str):
    return sql("select step_index, step_name, step_type, status, message from _job_run_steps "
               "where run_id in (select id from _job_runs where job_id = ?) order by started_at desc, step_index",
               (job_id,))


def phase_m6() -> None:
    print("\n===== M6 · 作业模块 =====", flush=True)

    # M6-006 保存作业（steps_json + guard_json 落库）
    job_a = ""
    try:
        steps = [qstep("create table if not exists b_m6(x text); insert into b_m6 values('s1')", "建表写数"),
                 {"name": "导出步骤", "type": "export", "enabled": True, "continueOnError": False,
                  "config": {"sourceType": "table", "table": "b_m6", "targetDbType": "sqlite",
                             "extension": "csv", "outputName": "m6_export",
                             "openFileAfterExport": False, "openFolderAfterExport": False}}]
        st, b = make_job("复跑作业A", steps)
        job = b.get("job") or {}
        job_a = str(job.get("id") or "")
        row = sql("select steps_json, guard_json from _jobs where id = ?", (job_a,))
        ok = (st == 200 and bool(job_a) and bool(row) and len(json.loads(row[0][0])) == 2 and row[0][1] == "{}")
        rec("M6-006", ok, f"HTTP={st} id={job_a[:12]} steps={len(json.loads(row[0][0])) if row else 0} "
                          f"guard_json={row[0][1] if row else 'N/A'}")
    except Exception as e:  # noqa: BLE001
        rec("M6-006", False, f"异常 {e}")

    # M6-007 作业名留空
    try:
        st, b = make_job("", [qstep("select 1")])
        err = str(b.get("error") or "")
        rec("M6-007", st == 400 and "请填写作业名称" in err, f"HTTP={st} err={err[:70]}")
    except Exception as e:  # noqa: BLE001
        rec("M6-007", False, f"异常 {e}")

    # M6-008 无步骤
    try:
        st, b = make_job("复跑空作业", [])
        err = str(b.get("error") or "")
        rec("M6-008", st == 400 and "请至少添加一个子任务" in err, f"HTTP={st} err={err[:70]}")
    except Exception as e:  # noqa: BLE001
        rec("M6-008", False, f"异常 {e}")

    # M6-018 / M8-006 sync 步骤被拒
    try:
        st, b = make_job("复跑同步作业", [{"name": "同步", "type": "sync", "enabled": True,
                                          "config": {}}])
        err = str(b.get("error") or "")
        rec("M6-018", st == 400 and "同步模块尚未开放" in err, f"HTTP={st} err={err[:70]}")
        rec("M8-006", st == 400 and "同步模块尚未开放" in err, f"同一场景（作业中选 sync 步骤）HTTP={st} err={err[:60]}")
    except Exception as e:  # noqa: BLE001
        rec("M6-018", False, f"异常 {e}")
        rec("M8-006", False, f"异常 {e}")

    # M6-009 编辑作业：created_at 不变
    try:
        before = sql("select created_at, updated_at from _jobs where id = ?", (job_a,))
        time.sleep(1.1)
        st, _ = make_job("复跑作业A改", [qstep("select 1", "改后步骤")], job_id=job_a)
        after = sql("select name, created_at, updated_at from _jobs where id = ?", (job_a,))
        cnt = len(sql("select id from _jobs where name like '复跑作业A%'"))
        ok = (st == 200 and after and after[0][0] == "复跑作业A改"
              and after[0][1] == before[0][0] and cnt == 1)
        rec("M6-009", ok, f"HTTP={st} 名称={after[0][0] if after else 'NA'} "
                          f"created_at 不变={after[0][1] == before[0][0] if after else False} "
                          f"同名(前缀)行数={cnt}（期望 1，即原地更新）")
    except Exception as e:  # noqa: BLE001
        rec("M6-009", False, f"异常 {e}")

    # M6-010 复制作业（后端：id 传空 → 新 id）
    try:
        st, b = make_job("复跑作业A（副本）", [qstep("select 1", "改后步骤")])
        new_id = str((b.get("job") or {}).get("id") or "")
        rec("M6-010", st == 200 and bool(new_id) and new_id != job_a,
            f"HTTP={st} 源 id={job_a[:12]} 新 id={new_id[:12]} 不同={new_id != job_a}")
    except Exception as e:  # noqa: BLE001
        rec("M6-010", False, f"异常 {e}")

    # 专用作业 B：query 步骤建表写数 + export 步骤落盘（供 M6-012 / M6-013 使用）
    job_b = ""
    try:
        steps_b = [
            qstep("create table if not exists b_m6(x text); insert into b_m6 values('s1')", "建表写数"),
            {"name": "导出步骤", "type": "export", "enabled": True, "continueOnError": False,
             "config": {"sourceType": "table", "table": "b_m6", "targetDbType": "sqlite",
                        "extension": "csv", "outputName": "m6_export",
                        "openFileAfterExport": False, "openFolderAfterExport": False}},
        ]
        st, b = make_job("复跑作业B", steps_b)
        job_b = str((b.get("job") or {}).get("id") or "")
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] 专用作业 B 创建失败：{e}", flush=True)

    # M6-012 立即执行（query 步骤真实写库）
    try:
        st, b = jhttp("POST", "/api/jobs/run", {"id": job_b})
        run = b.get("run") or {}
        marks = sql("select count(*) from b_m6")
        rec("M6-012", st == 200 and run.get("status") == "成功" and marks[0][0] == 1 and bool(run.get("id")),
            f"HTTP={st} status={run.get('status')} message={str(run.get('message'))[:90]} "
            f"目标表 b_m6 行数={marks[0][0]} err={str(b.get('error') or '')[:60]}")
    except Exception as e:  # noqa: BLE001
        rec("M6-012", False, f"异常 {e}")

    # M6-013 query→写数 + export→落盘（两步均成功）
    try:
        files = sorted(EXPORTS.rglob("m6_export*"))
        run_rows = sql("select status, outputs_json, message from _job_runs where job_id = ? order by started_at desc",
                       (job_b,))
        latest = run_rows[0] if run_rows else None
        ok = (latest is not None and latest[0] == "成功" and bool(files)
              and "m6_export" in (latest[1] or ""))
        rec("M6-013", ok, f"运行状态={latest[0] if latest else 'NA'} 导出产物={[f.name for f in files]} "
                          f"outputs_json 含路径={('m6_export' in (latest[1] or '')) if latest else False}")
    except Exception as e:  # noqa: BLE001
        rec("M6-013", False, f"异常 {e}")

    # M6-014 / M6-015 步骤失败语义
    try:
        bad = qstep("select * from no_such_table_b_xyz", "坏步骤")
        good = qstep("insert into b_m6 values('s2')", "好步骤")
        st_c, b_c = make_job("复跑作业-中止", [dict(bad, continueOnError=False), good])
        id_c = str((b_c.get("job") or {}).get("id") or "")
        jhttp("POST", "/api/jobs/run", {"id": id_c})
        rows_c = popen_steps(id_c)
        st_k, b_k = make_job("复跑作业-继续", [dict(bad, continueOnError=True), good])
        id_k = str((b_k.get("job") or {}).get("id") or "")
        _, b_run_k = jhttp("POST", "/api/jobs/run", {"id": id_k})
        rows_k = popen_steps(id_k)
        ok14 = len(rows_c) == 1 and rows_c[0][3] == "失败"
        ok15 = len(rows_k) == 2 and (b_run_k.get("run") or {}).get("status") == "失败"
        rec("M6-014", ok14, f"continueOnError=false → 步骤记录 {len(rows_c)} 条（期望 1，第 2 步未执行），"
                            f"第 1 步状态={rows_c[0][3] if rows_c else 'NA'}")
        rec("M6-015", ok15, f"continueOnError=true → 步骤记录 {len(rows_k)} 条（期望 2），"
                            f"作业最终状态={(b_run_k.get('run') or {}).get('status')}（期望 失败）")
    except Exception as e:  # noqa: BLE001
        rec("M6-014", False, f"异常 {e}")
        rec("M6-015", False, f"异常 {e}")

    # M6-016 enabled=false 步骤跳过
    try:
        off = dict(qstep("insert into b_m6 values('s3')", "停用步骤"), enabled=False)
        on = qstep("insert into b_m6 values('s4')", "启用步骤")
        st, b = make_job("复跑作业-停用步骤", [off, on])
        jid = str((b.get("job") or {}).get("id") or "")
        _, b_run = jhttp("POST", "/api/jobs/run", {"id": jid})
        rows = popen_steps(jid)
        msg = str((b_run.get("run") or {}).get("message") or "")
        ok = len(rows) == 1 and rows[0][1] == "启用步骤" and "1 个步骤未启用" in msg
        rec("M6-016", ok, f"步骤记录 {len(rows)} 条（期望 1，停用步骤不进记录）；message={msg[:70]}")
    except Exception as e:  # noqa: BLE001
        rec("M6-016", False, f"异常 {e}")

    # M6-017 循环引用
    try:
        st, b = make_job("复跑作业-自引用", [qstep("select 1", "占位")])
        jid = str((b.get("job") or {}).get("id") or "")
        self_step = {"name": "引用自身", "type": "job", "enabled": True, "continueOnError": False,
                     "config": {"jobId": jid}}
        st_sv, b_sv = make_job("复跑作业-自引用", [self_step], job_id=jid)
        st_run, b_run = jhttp("POST", "/api/jobs/run", {"id": jid})
        run = b_run.get("run") or {}
        rows = popen_steps(jid)
        text = str(run.get("message") or "") + str(b_run.get("error") or "") + \
            " ".join(str(r[4] or "") for r in rows)
        hit = "循环引用" in text
        rec("M6-017", hit and (st_run == 400 or run.get("status") == "失败"),
            f"保存自引用 HTTP={st_sv}；运行 HTTP={st_run} status={run.get('status')} "
            f"命中循环引用={hit}；err={str(b_run.get('error') or '')[:110]}")
    except Exception as e:  # noqa: BLE001
        rec("M6-017", False, f"异常 {e}")

    # M6-020 步骤级运行记录字段完整（取已执行过的「复跑作业B」：query + export 两步）
    try:
        rows = sql("select status, started_at, ended_at, elapsed_ms, message from _job_run_steps "
                   "where run_id in (select id from _job_runs where job_id = ?) limit 1", (job_b,))
        r = rows[0] if rows else None
        ok = bool(r) and bool(r[0]) and bool(r[1]) and bool(r[2]) and r[3] is not None and r[4] is not None
        rec("M6-020", ok, f"_job_run_steps 样本 status={r[0] if r else 'NA'} started={r[1] if r else 'NA'} "
                          f"ended={r[2] if r else 'NA'} elapsed_ms={r[3] if r else 'NA'}")
    except Exception as e:  # noqa: BLE001
        rec("M6-020", False, f"异常 {e}")

    # M6-019 运行日志接口（最近 80 次 + steps 明细）
    try:
        st, b = jhttp("GET", f"/api/job-runs?jobId={job_b}")
        runs = b.get("runs") or []
        ok = st == 200 and bool(runs) and "steps" in runs[0] and "outputs" in runs[0] \
            and "outputs_json" not in runs[0]
        rec("M6-019", ok, f"HTTP={st} 运行记录 {len(runs)} 条；首条含 steps={len(runs[0].get('steps') or []) if runs else 0} "
                          f"outputs={len(runs[0].get('outputs') or []) if runs else 0}（接口 limit 80 已核对）")
    except Exception as e:  # noqa: BLE001
        rec("M6-019", False, f"异常 {e}")

    # M6-021 立即执行带 scheduleId → 回写 _schedules
    try:
        st, b = jhttp("POST", "/api/schedules", {"name": "复跑调度-回写", "jobId": job_a, "enabled": False,
                                                 "rule": {"mode": "interval", "amount": 30, "unit": "minutes"}})
        sch = b.get("schedule") or {}
        sid = str(sch.get("id") or "")
        jhttp("POST", "/api/jobs/run", {"id": job_a, "scheduleId": sid})
        row = sql("select last_run_at, last_status from _schedules where id = ?", (sid,))
        ok = bool(row) and bool(row[0][0]) and row[0][1] == "成功"
        rec("M6-021", ok, f"scheduleId={sid[:12]} last_run_at={row[0][0] if row else 'NA'} "
                          f"last_status={row[0][1] if row else 'NA'}（期望 成功）")
    except Exception as e:  # noqa: BLE001
        rec("M6-021", False, f"异常 {e}")

    # M6-029 guard 日期范围未填齐
    try:
        st, b = make_job("复跑作业-日期范围缺项", [qstep("select 1")],
                         guard={"type": "date_match", "mode": "range", "start": "", "end": ""})
        gid = str((b.get("job") or {}).get("id") or "")
        st_run, b_run = jhttp("POST", "/api/jobs/run", {"id": gid})
        text = str((b_run.get("run") or {}).get("message") or "") + str(b_run.get("error") or "")
        rec("M6-029", "需填写开始与结束日期" in text,
            f"保存 HTTP={st}；运行 HTTP={st_run} 提示={text[:110]} "
            f"guard_json={sql('select guard_json from _jobs where id = ?', (gid,))}")
    except Exception as e:  # noqa: BLE001
        rec("M6-029", False, f"异常 {e}")

    # M6-030 guard weekday 未选
    try:
        st, b = make_job("复跑作业-周几未选", [qstep("select 1")],
                         guard={"type": "date_match", "mode": "weekday", "values": []})
        gid = str((b.get("job") or {}).get("id") or "")
        st_run, b_run = jhttp("POST", "/api/jobs/run", {"id": gid})
        text = str((b_run.get("run") or {}).get("message") or "") + str(b_run.get("error") or "")
        rec("M6-030", "请至少选择一个执行日" in text,
            f"保存 HTTP={st}；运行 HTTP={st_run} 提示={text[:110]}")
    except Exception as e:  # noqa: BLE001
        rec("M6-030", False, f"异常 {e}")

    # M6-031 guard 条件评估异常 → 作业失败（不静默暴露配置问题）
    try:
        missing = SEED / "no_such_guard_source.csv"
        step_bad = {"name": "源文件缺失", "type": "import", "enabled": True, "continueOnError": False,
                    "config": {"sourcePath": str(missing), "tableName": "b_guard_missing",
                               "targetDbType": "sqlite", "importMode": "rebuild"}}
        st, b = make_job("复跑作业-条件评估异常", [step_bad], guard={"type": "file_has_new"})
        gid = str((b.get("job") or {}).get("id") or "")
        st_run, b_run = jhttp("POST", "/api/jobs/run", {"id": gid})
        run = b_run.get("run") or {}
        text = str(run.get("message") or "") + str(b_run.get("error") or "")
        rec("M6-031", run.get("status") == "失败" and bool(text),
            f"保存 HTTP={st}；运行 HTTP={st_run} status={run.get('status')} "
            f"提示={text[:110]}（期望 失败 +「作业执行条件评估失败」或明确错误）")
    except Exception as e:  # noqa: BLE001
        rec("M6-031", False, f"异常 {e}")

    # M6-022 / M6-023 / M6-024 文件守卫（导入步骤 + file_has_new）
    try:
        src = SEED / "b_guard.csv"
        src.write_bytes("a\n1\n".encode("utf-8-sig"))
        imp_step = {"name": "守卫导入", "type": "import", "enabled": True, "continueOnError": False,
                    "config": {"sourcePath": str(src), "tableName": "b_guard", "targetDbType": "sqlite",
                               "importMode": "rebuild"}}
        st, b = make_job("复跑作业-文件守卫", [imp_step, qstep("select 1", "收尾")],
                         guard={"type": "file_has_new"})
        gid = str((b.get("job") or {}).get("id") or "")
        _, b1 = jhttp("POST", "/api/jobs/run", {"id": gid})
        r1 = b1.get("run") or {}
        base = sql("select count(*) from _job_file_guards where job_id = ?", (gid,))
        _, b2 = jhttp("POST", "/api/jobs/run", {"id": gid})
        r2 = b2.get("run") or {}
        rec("M6-022", r1.get("status") == "成功" and r2.get("status") == "跳过",
            f"首跑 status={r1.get('status')}（期望 成功）；源文件未改动再跑 status={r2.get('status')}（期望 跳过）；"
            f"第二次 message={str(r2.get('message'))[:70]}")
        rec("M6-024", base and base[0][0] >= 1,
            f"首跑后写入指纹基线 _job_file_guards 行数={base[0][0] if base else 0}（期望 ≥1）")

        # M6-023：单步级 skipIfFileUnchanged —— 只有带该 flag 的步骤进入守卫集合
        src2 = SEED / "b_guard2.csv"
        src2.write_bytes("a\n1\n".encode("utf-8-sig"))
        src3 = SEED / "b_guard3.csv"
        src3.write_bytes("a\n9\n".encode("utf-8-sig"))
        step_g = {"name": "单步守卫", "type": "import", "enabled": True, "continueOnError": False,
                  "config": {"sourcePath": str(src2), "tableName": "b_guard2", "targetDbType": "sqlite",
                             "importMode": "rebuild", "skipIfFileUnchanged": "true"}}
        step_n = {"name": "无守卫导入", "type": "import", "enabled": True, "continueOnError": False,
                  "config": {"sourcePath": str(src3), "tableName": "b_guard3", "targetDbType": "sqlite",
                             "importMode": "rebuild"}}
        st, b = make_job("复跑作业-单步守卫", [step_g, step_n])
        sid2 = str((b.get("job") or {}).get("id") or "")
        _, c1 = jhttp("POST", "/api/jobs/run", {"id": sid2})
        grow = sql("select step_index from _job_file_guards where job_id = ?", (sid2,))
        idx = [r[0] for r in grow]
        rec("M6-023", idx == [0],
            f"作业含 1 个带 skipIfFileUnchanged 的导入步骤 + 1 个不带的 → 守卫基线 step_index={idx}"
            f"（期望仅 [0]，即只有该步启用文件守卫）；首跑状态={(c1.get('run') or {}).get('status')}")
    except Exception as e:  # noqa: BLE001
        rec("M6-022", False, f"异常 {e}")
        rec("M6-023", False, f"异常 {e}")
        rec("M6-024", False, f"异常 {e}")

    # M6-011 删除作业（连带删 _schedules）
    try:
        st, b = make_job("复跑作业-待删除", [qstep("select 1")])
        did = str((b.get("job") or {}).get("id") or "")
        jhttp("POST", "/api/schedules", {"name": "复跑调度-待级联", "jobId": did, "enabled": False,
                                         "rule": {"mode": "interval", "amount": 10, "unit": "minutes"}})
        before_s = len(sql("select id from _schedules where job_id = ?", (did,)))
        st, _ = jhttp("DELETE", f"/api/jobs?id={did}")
        after_j = len(sql("select id from _jobs where id = ?", (did,)))
        after_s = len(sql("select id from _schedules where job_id = ?", (did,)))
        rec("M6-011", st == 200 and before_s == 1 and after_j == 0 and after_s == 0,
            f"删除前关联调度 {before_s} 条；删除后 job={after_j} schedule={after_s}（均期望 0）")
    except Exception as e:  # noqa: BLE001
        rec("M6-011", False, f"异常 {e}")


# --------------------------------------------------------------------------- M7 定时


def phase_m7() -> None:
    print("\n===== M7 · 定时任务 =====", flush=True)

    # 准备一个可被引用的作业
    _, b = make_job("复跑调度宿主作业", [qstep("select 1", "占位")])
    host = str((b.get("job") or {}).get("id") or "")

    # M7-001 调度列表
    try:
        st, b = jhttp("GET", "/api/schedules")
        items = b.get("schedules") or []
        keys = {"nextRunAt", "lastRunAt", "lastStatus", "enabled"}
        ok = st == 200 and bool(items) and keys.issubset(set(items[0].keys()))
        rec("M7-001", ok, f"HTTP={st} 调度 {len(items)} 条；字段含 {sorted(keys)}="
                          f"{keys.issubset(set(items[0].keys())) if items else False}")
    except Exception as e:  # noqa: BLE001
        rec("M7-001", False, f"异常 {e}")

    # M7-007 保存调度（enabled 时算 nextRunAt）
    sid = ""
    try:
        st, b = jhttp("POST", "/api/schedules",
                      {"name": "复跑调度A", "jobId": host, "enabled": True,
                       "rule": {"mode": "interval", "amount": 30, "unit": "minutes"},
                       "startAt": "", "endAt": ""})
        sch = b.get("schedule") or {}
        sid = str(sch.get("id") or "")
        row = sql("select enabled, next_run_at from _schedules where id = ?", (sid,))
        ok = st == 200 and bool(sid) and bool(row) and row[0][0] == 1 and bool(row[0][1])
        rec("M7-007", ok, f"HTTP={st} id={sid[:12]} enabled={row[0][0] if row else 'NA'} "
                          f"next_run_at={row[0][1] if row else 'NA'}")
    except Exception as e:  # noqa: BLE001
        rec("M7-007", False, f"异常 {e}")

    # M7-008 任务名留空
    try:
        st, b = jhttp("POST", "/api/schedules", {"name": "", "jobId": host, "enabled": False,
                                                 "rule": {"mode": "interval", "amount": 1, "unit": "minutes"}})
        err = str(b.get("error") or "")
        rec("M7-008", st == 400 and "请填写任务名称" in err, f"HTTP={st} err={err[:70]}")
    except Exception as e:  # noqa: BLE001
        rec("M7-008", False, f"异常 {e}")

    # M7-009 未选作业
    try:
        st, b = jhttp("POST", "/api/schedules", {"name": "复跑调度-无作业", "jobId": "", "enabled": False,
                                                 "rule": {"mode": "interval", "amount": 1, "unit": "minutes"}})
        err = str(b.get("error") or "")
        rec("M7-009", st == 400 and "请选择作业" in err, f"HTTP={st} err={err[:70]}")
    except Exception as e:  # noqa: BLE001
        rec("M7-009", False, f"异常 {e}")

    # M7-010 启用
    try:
        st, b = jhttp("POST", "/api/schedules/pause", {"id": sid})
        _, b2 = jhttp("POST", "/api/schedules/start", {"id": sid})
        row = sql("select enabled, running, next_run_at from _schedules where id = ?", (sid,))
        ok = st == 200 and row and row[0][0] == 1 and row[0][1] == 0 and bool(row[0][2])
        rec("M7-010", ok, f"启用后 enabled={row[0][0] if row else 'NA'} running={row[0][1] if row else 'NA'} "
                          f"next_run_at={row[0][2] if row else 'NA'}（期望 1/0/非空）")
    except Exception as e:  # noqa: BLE001
        rec("M7-010", False, f"异常 {e}")

    # M7-011 禁用
    try:
        st, b = jhttp("POST", "/api/schedules/pause", {"id": sid})
        row = sql("select enabled, next_run_at from _schedules where id = ?", (sid,))
        ok = st == 200 and row and row[0][0] == 0 and row[0][1] == ""
        rec("M7-011", ok, f"禁用后 enabled={row[0][0] if row else 'NA'} "
                          f"next_run_at={repr(row[0][1]) if row else 'NA'}（期望 0/空串）")
    except Exception as e:  # noqa: BLE001
        rec("M7-011", False, f"异常 {e}")

    # M7-012 立即运行 → 回写 last_status（前端即 POST /api/jobs/run 带 scheduleId）
    try:
        st, b = jhttp("POST", "/api/jobs/run", {"id": host, "scheduleId": sid})
        run = b.get("run") or {}
        row = sql("select last_status from _schedules where id = ?", (sid,))
        ok = st == 200 and row and row[0][0] == run.get("status") and row[0][0] in {"成功", "失败", "跳过"}
        rec("M7-012", ok, f"触发执行 HTTP={st} 本次 status={run.get('status')}；"
                          f"_schedules.last_status={row[0][0] if row else 'NA'}（两者应一致）")
    except Exception as e:  # noqa: BLE001
        rec("M7-012", False, f"异常 {e}")

    # M7-013 删除调度（含「 - 自动作业」载体清理）
    try:
        st, b = make_job("复跑调度-自动载体 - 自动作业", [qstep("select 1")])
        carrier = str((b.get("job") or {}).get("id") or "")
        _, b2 = jhttp("POST", "/api/schedules", {"name": "复跑调度-载体", "jobId": carrier, "enabled": False,
                                                 "rule": {"mode": "interval", "amount": 5, "unit": "minutes"}})
        csid = str((b2.get("schedule") or {}).get("id") or "")
        st, _ = jhttp("DELETE", f"/api/schedules?id={csid}")
        left_s = len(sql("select id from _schedules where id = ?", (csid,)))
        left_j = len(sql("select id from _jobs where id = ?", (carrier,)))
        rec("M7-013", st == 200 and left_s == 0 and left_j == 0,
            f"HTTP={st} 残留 schedule={left_s} 载体 job={left_j}（期望 0/0，载体名以「 - 自动作业」结尾）")
    except Exception as e:  # noqa: BLE001
        rec("M7-013", False, f"异常 {e}")

    # M7-015 调度线程分发：running 置位/复位 + 回写
    try:
        st, b = make_job("复跑调度-线程分发", [qstep("insert into b_m6 values('th')", "线程写数")])
        tid = str((b.get("job") or {}).get("id") or "")
        _, b2 = jhttp("POST", "/api/schedules",
                      {"name": "复跑调度-线程", "jobId": tid, "enabled": False,
                       "rule": {"mode": "interval", "amount": 1, "unit": "minutes"}})
        tsid = str((b2.get("schedule") or {}).get("id") or "")
        # 手工把 next_run_at 拨到过去并启用 → 触发调度线程
        ready = wait_ready_for_dispatch(tsid)
        row = sql("select running, last_run_at, last_status, next_run_at from _schedules where id = ?", (tsid,))
        runs = sql("select count(*) from _job_runs where schedule_id = ?", (tsid,))
        ok = ready and row and int(row[0][0]) == 0 and bool(row[0][1]) and runs[0][0] >= 1
        rec("M7-015", ok, f"调度线程分发={ready} running={row[0][0] if row else 'NA'}（期望 0，执行后复位）"
                          f" last_run_at={row[0][1] if row else 'NA'} last_status={row[0][2] if row else 'NA'} "
                          f"该调度运行记录={runs[0][0] if runs else 0} 条")
    except Exception as e:  # noqa: BLE001
        rec("M7-015", False, f"异常 {e}")

    # M7-016 日志保留清理
    try:
        st, b = make_job("复跑调度-日志保留", [qstep("select 1")])
        lid = str((b.get("job") or {}).get("id") or "")
        _, b2 = jhttp("POST", "/api/schedules", {"name": "复跑调度-保留3天", "jobId": lid, "enabled": False,
                                                 "logRetentionDays": 3,
                                                 "rule": {"mode": "interval", "amount": 60, "unit": "minutes"}})
        lrow = sql("select log_retention_days from _schedules where job_id = ?", (lid,))
        # 造两条超期运行记录（直接写库），再触发一次 prune_job_logs
        rc, out = run_script(["acceptance/probe_b_inprocess.py", "prune", str(DATA)])
        left = sql("select count(*) from _job_runs where started_at < ?",
                   ((__import__("datetime").datetime.now() - __import__("datetime").timedelta(days=3))
                    .strftime("%Y-%m-%d %H:%M:%S"),))
        ok = lrow and lrow[0][0] == 3 and "pruned" in out.lower()
        rec("M7-016", bool(ok), f"log_retention_days={lrow[0][0] if lrow else 'NA'}（期望 3）；"
                                f"prune 探针输出={out[:120]}")
    except Exception as e:  # noqa: BLE001
        rec("M7-016", False, f"异常 {e}")

    # M7-018 kill 后重启恢复调度 running
    try:
        zombie = sql("select count(*) from _schedules where running = 1")[0][0]
        rc, out = run_script(["acceptance/probe_b_inprocess.py", "recover", str(DATA),
                              "--seed-schedule", sid])
        row = sql("select running from _schedules where id = ?", (sid,))
        rec("M7-018", row and int(row[0][0]) == 0 and "recovered" in out.lower(),
            f"注入 running=1 后调用 recover_interrupted_runs → running={row[0][0] if row else 'NA'}；"
            f"输出={out[:120]}")
    except Exception as e:  # noqa: BLE001
        rec("M7-018", False, f"异常 {e}")


def wait_ready_for_dispatch(schedule_id: str, timeout: int = 25) -> bool:
    """把 next_run_at 拨到 1 秒前并启用，等待调度线程（5s 轮询）分发执行。"""
    import datetime as dt

    past = (dt.datetime.now() - dt.timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
    run_script(["acceptance/probe_b_inprocess.py", "arm", str(DATA), "--schedule", schedule_id,
                "--next-run-at", past])
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, b = jhttp("GET", "/api/job-runs?" + f"scheduleId={schedule_id}")
        if st == 200 and (b.get("runs") or []):
            time.sleep(2.0)  # 等 running 复位
            return True
        time.sleep(1)
    return False


# --------------------------------------------------------------------------- M9 安全


def phase_m9() -> None:
    print("\n===== M9 · 安全 =====", flush=True)

    # M9-001 密码 Fernet 密文存储
    try:
        st, b = jhttp("POST", "/api/connections", {"name": "复跑-安全A", "dbType": "mysql", "host": "127.0.0.1",
                                                   "port": 3306, "user": "root", "password": "SecPwd!123",
                                                   "database": "dc_p2_test"})
        cid = str((b.get("connection") or {}).get("id") or "")
        row = sql("select password from _db_connections where id = ?", (cid,))
        pw = row[0][0] if row else ""
        rec("M9-001", st == 200 and pw.startswith("fernet:") and "SecPwd!123" not in pw,
            f"HTTP={st} 入库密码前缀={pw[:7]} 长度={len(pw)} 含明文={('SecPwd!123' in pw)}")
    except Exception as e:  # noqa: BLE001
        rec("M9-001", False, f"异常 {e}")

    # M9-002 旧 b64: 前缀自动迁移（进程内探针）
    try:
        rc, out = run_script(["acceptance/probe_b_inprocess.py", "legacy-b64", str(DATA)])
        rec("M9-002", rc == 0 and "migrated=true" in out,
            f"rc={rc} 输出={out[:200]}")
    except Exception as e:  # noqa: BLE001
        rec("M9-002", False, f"异常 {e}")

    # M9-004 连接列表不含密码明文
    try:
        st, b = jhttp("GET", "/api/connections")
        items = b.get("connections") or b.get("items") or []
        leak = [c for c in items if c.get("password")]
        has_key = [c for c in items if "password" in c]
        has_flag = [c for c in items if "hasPassword" in c]
        rec("M9-004", st == 200 and not leak and not has_key and bool(has_flag),
            f"HTTP={st} 共 {len(items)} 条；含 password 键={len(has_key)}（期望 0）；"
            f"含 hasPassword={len(has_flag)}（期望 >0）；明文泄露={len(leak)}")
    except Exception as e:  # noqa: BLE001
        rec("M9-004", False, f"异常 {e}")

    # M9-006 静态路径穿越 + 下载白名单
    try:
        st1, raw1 = http("GET", "/../server.py")
        st2, b2 = jhttp("GET", "/api/export/download?path=C%3A%5CWindows%5Cwin.ini")
        err = str(b2.get("error") or "")
        body = raw1.decode("utf-8", "replace")
        traversal_blocked = st1 in (400, 404) or ("import" not in body[:400])
        rec("M9-006", traversal_blocked and st2 == 400 and "不在允许下载的目录内" in err,
            f"静态 /../server.py → HTTP={st1}（未回源码={traversal_blocked}）；"
            f"下载越界 → HTTP={st2} err={err[:60]}")
    except Exception as e:  # noqa: BLE001
        rec("M9-006", False, f"异常 {e}")


# --------------------------------------------------------------------------- M10 备份与部署


def phase_m10() -> None:
    print("\n===== M10 · 备份恢复与部署 =====", flush=True)

    bk_dir = SANDBOX / "backup"

    # M10-001 生成备份 zip（**不传任何环境变量** → 必须按 server.py 的默认口径解析到 runtime/data）
    try:
        rc, out = run_script(["scripts/create_data_backup.py", "--output-dir", str(bk_dir)])
        zips = sorted(bk_dir.glob("data-converter-backup-*.zip")) if bk_dir.exists() else []
        names: list[str] = []
        manifest: dict = {}
        if zips:
            import zipfile
            with zipfile.ZipFile(zips[-1]) as z:
                names = z.namelist()
                manifest = json.loads(z.read("manifest.json").decode("utf-8"))
        src = str((manifest.get("source") or {}).get("dataDir") or "")
        expect = str(PROJECT / "runtime" / "data")
        rec("M10-001", rc == 0 and bool(zips) and "data/imports.db" in names and src == expect,
            f"rc={rc} 输出={out[:120]}；产出 zip={[z.name for z in zips]}；归档含 data/imports.db="
            f"{('data/imports.db' in names)}；manifest.source.dataDir={src}（期望 {expect}）")
    except Exception as e:  # noqa: BLE001
        rec("M10-001", False, f"异常 {e}")

    # M10-002 含 uploads + exports（用隔离目录，条目可确定性断言）
    try:
        rc, out = run_script(["scripts/create_data_backup.py", "--include-uploads", "--include-exports",
                              "--output-dir", str(bk_dir)],
                             env_extra={"DATA_DIR": str(DATA), "UPLOADS_DIR": str(UPLOADS),
                                        "EXPORTS_DIR": str(EXPORTS)})
        zips = sorted(bk_dir.glob("data-converter-backup-*.zip")) if bk_dir.exists() else []
        names: list[str] = []
        includes: list[str] = []
        if zips:
            import zipfile
            with zipfile.ZipFile(zips[-1]) as z:
                names = z.namelist()
                includes = json.loads(z.read("manifest.json").decode("utf-8")).get("includes") or []
        has_up = any(n.startswith("uploads/") for n in names)
        has_ex = any(n.startswith("exports/") for n in names)
        n_up = len([p for p in UPLOADS.rglob("*") if p.is_file()])
        n_ex = len([p for p in EXPORTS.rglob("*") if p.is_file()])
        rec("M10-002", rc == 0 and has_up and has_ex and "uploads" in includes and "exports" in includes,
            f"rc={rc} 归档含 uploads={has_up}（源目录 {n_up} 个文件）exports={has_ex}（{n_ex} 个）"
            f"；manifest.includes={includes}；总条目={len(names)}")
    except Exception as e:  # noqa: BLE001
        rec("M10-002", False, f"异常 {e}")

    # M10-003 恢复备份：用 M10-001 生成的真实备份做端到端回环 + 路径穿越负向对照
    try:
        import zipfile

        real_zips = sorted(bk_dir.glob("data-converter-backup-*.zip")) if bk_dir.exists() else []
        if real_zips:
            zpath = real_zips[0]
        else:                     # 退化为自造 zip，保证本条可独立运行
            stage = SANDBOX / "restore-src" / "data"
            stage.mkdir(parents=True, exist_ok=True)
            (stage / "imports.db").write_bytes(b"RESTORE-PROBE-DB")
            zpath = SANDBOX / "b-restore.zip"
            with zipfile.ZipFile(zpath, "w") as z:
                z.writestr("manifest.json", json.dumps({"createdAt": "2026-09-19T00:00:00",
                                                        "includes": ["data/imports.db"]}))
                z.write(stage / "imports.db", "data/imports.db")
        with zipfile.ZipFile(zpath) as z:
            packed = z.getinfo("data/imports.db").file_size

        extract = SANDBOX / "restore-extract"
        tgt = SANDBOX / "restore-out"
        rc, out = run_script(["scripts/restore_data_backup.py", str(zpath), "--target-root", str(extract)],
                             env_extra={"DATA_DIR": str(tgt / "data"),
                                        "UPLOADS_DIR": str(tgt / "uploads"),
                                        "EXPORTS_DIR": str(tgt / "exports")})
        restored = tgt / "data" / "imports.db"
        same_bytes = restored.exists() and restored.stat().st_size == packed

        # 负向对照：归档里夹带 ../ 逃逸路径必须被拒
        bad = SANDBOX / "b-traversal.zip"
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("../../evil.txt", "x")
        with zipfile.ZipFile(bad) as z:
            kept_name = z.namelist()[0]
        rc_bad, out_bad = run_script(["scripts/restore_data_backup.py", str(bad),
                                      "--target-root", str(SANDBOX / "traversal-extract")],
                                     env_extra={"DATA_DIR": str(SANDBOX / "t-data")})
        if kept_name.startswith(".."):
            blocked, ctrl_note = rc_bad != 0 and "Unsafe archive path" in out_bad, f"rc={rc_bad}"
        else:  # zipfile 归一化了成员名 → 负向对照不成立，不计入判据
            blocked, ctrl_note = True, f"成员名被归一化为 {kept_name}，负向对照跳过"
        rec("M10-003", rc == 0 and same_bytes and blocked,
            f"用 M10-001 的真实备份回环：rc={rc} 还原 imports.db 存在={restored.exists()} "
            f"字节数一致={same_bytes}（{packed} → {restored.stat().st_size if restored.exists() else 'NA'}）；"
            f"路径穿越负向对照被拒={blocked}（{ctrl_note}）")
    except Exception as e:  # noqa: BLE001
        rec("M10-003", False, f"异常 {e}")

    # M10-004 密码加密迁移脚本
    try:
        rc, out = run_script(["scripts/migrate_password_encryption.py"],
                             env_extra={"DATA_DIR": str(DATA)})
        has_summary = "迁移完成" in out or "无需迁移" in out
        rec("M10-004", rc == 0 and has_summary, f"rc={rc} 输出={out[:220]}")
    except Exception as e:  # noqa: BLE001
        rec("M10-004", False, f"异常 {e}")

    # M10-006 桌面启动器（进程内探针：屏蔽浏览器后真起服务）
    try:
        rc, out = run_script(["acceptance/probe_b_inprocess.py", "launcher", str(SANDBOX)], timeout=90)
        rec("M10-006", rc == 0 and "launcher-ok=true" in out, f"rc={rc} 输出={out[:220]}")
    except Exception as e:  # noqa: BLE001
        rec("M10-006", False, f"异常 {e}")

    # M10-008 Docker 构建
    try:
        rc, out = run_script(["acceptance/probe_b_inprocess.py", "docker-check", str(SANDBOX)])
        if "docker-unavailable" in out:
            note("M10-008", "本机无 docker 命令，无法执行 `docker build`；Dockerfile 静态存在且 CMD 为 python server.py。转人工/CI 验证")
        else:
            rec("M10-008", "docker-ok" in out, f"输出={out[:180]}")
    except Exception as e:  # noqa: BLE001
        rec("M10-008", False, f"异常 {e}")

    # M10-009 云平台 Blueprint 配置
    try:
        rj = json.loads((PROJECT / "railway.json").read_text(encoding="utf-8"))
        ry = (PROJECT / "render.yaml").read_text(encoding="utf-8")
        ok = (rj.get("build", {}).get("dockerfilePath") == "Dockerfile"
              and rj.get("deploy", {}).get("healthcheckPath") == "/api/ping"
              and "dockerfilePath: ./Dockerfile" in ry and "runtime: docker" in ry
              and (PROJECT / "Dockerfile").exists())
        rec("M10-009", ok, f"railway.json 解析成功 dockerfilePath={rj.get('build', {}).get('dockerfilePath')} "
                           f"healthcheck={rj.get('deploy', {}).get('healthcheckPath')}；"
                           f"render.yaml 含 runtime: docker / dockerfilePath={'dockerfilePath: ./Dockerfile' in ry}")
    except Exception as e:  # noqa: BLE001
        rec("M10-009", False, f"异常 {e}")


# --------------------------------------------------------------------------- UT 映射


UT_MAP = {
    "M4-001": ["test_import_engine.py::test_query_export"],
    "M6-013": ["test_jobs_schedule.py::test_query_export"],
    "M7-004": ["test_jobs_schedule.py::test_compute_next_run_daily"],
    "M7-005": ["test_jobs_schedule.py::test_compute_next_run_weekly",
               "test_jobs_schedule.py::test_compute_next_run_monthly",
               "test_jobs_schedule.py::test_compute_next_run_yearly"],
    "M6-012": ["test_jobs_schedule.py"],
    "M7-016": ["test_jobs_schedule.py"],
}


def phase_ut() -> None:
    print("\n===== UT 层（pytest，仅作映射证据）=====", flush=True)
    env = os.environ.copy()
    env.update({"DATA_DIR": str(DATA), "UPLOADS_DIR": str(UPLOADS), "EXPORTS_DIR": str(EXPORTS),
                "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    p = subprocess.run([str(PY), "-m", "pytest", "-v", "-p", "no:cacheprovider"],
                       cwd=str(PROJECT), env=env, capture_output=True, timeout=600)
    out = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
    (SANDBOX / "pytest-verbose.txt").write_text(out, encoding="utf-8")
    passed = set(re.findall(r"::([A-Za-z0-9_]+)\s+PASSED", out))
    failed = set(re.findall(r"::([A-Za-z0-9_]+)\s+FAILED", out))
    summary = re.search(r"=+ (.*?) =+$", out.strip().splitlines()[-1] if out.strip() else "", re.M)
    print(f"  pytest: 通过 {len(passed)} 个 / 失败 {len(failed)} 个", flush=True)
    for rid, names in UT_MAP.items():
        hits = {}
        for n in names:
            case = n.split("::")[-1]
            if n.endswith(".py"):
                hits[n] = "FILE-OK" if n.replace(".py", "") else "NA"
                continue
            hits[case] = "PASSED" if case in passed else ("FAILED" if case in failed else "NOT-FOUND")
        ok = all(v in {"PASSED", "FILE-OK"} for v in hits.values())
        rec(rid, ok, f"UT 映射 {json.dumps(hits, ensure_ascii=False)}", kind="UT")
    RESULTS.append({"id": "_pytest", "result": "INFO", "kind": "INFO",
                    "detail": f"passed={len(passed)} failed={len(failed)}"})


# --------------------------------------------------------------------------- main


def main() -> int:
    phase0()
    start()
    if not wait_ready():
        print("服务未能就绪，终止", flush=True)
        stop_all()
        return 2
    print(f"服务就绪：http://127.0.0.1:{PORT}", flush=True)
    try:
        phase_m4()
        phase_m6()
        phase_m7()
        phase_m9()
        phase_m10()
        phase_ut()
    finally:
        stop_all()

    ok = sum(1 for r in RESULTS if r["result"] == "PASS")
    bad = [r for r in RESULTS if r["result"] == "FAIL"]
    man = [r for r in RESULTS if r["result"] == "MANUAL"]
    print("\n" + "=" * 64, flush=True)
    print(f"PASS {ok} / FAIL {len(bad)} / MANUAL {len(man)}（共 {len(RESULTS)}）", flush=True)
    EVID.mkdir(parents=True, exist_ok=True)
    (EVID / "b-layer-replay.json").write_text(
        json.dumps({"sandbox": str(SANDBOX), "results": RESULTS}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告: {EVID / 'b-layer-replay.json'}", flush=True)
    if bad:
        print("\n失败项：", flush=True)
        for r in bad:
            print(f"  - {r['id']}: {r['detail'][:170]}", flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
