"""查询模块限制探针（只读侦查用）。

目的：把「查询模块当前到底能跑什么 / 卡在哪一步」钉成可复跑的证据，而不是靠读代码猜。
覆盖：SELECT / 子查询 / CTE / DDL / DML / 多语句 / 注释 / EXPLAIN / SHOW。

自带隔离实例（默认端口 51992、沙箱 acceptance/replay-query-probe），只写沙箱，不碰 runtime/。
用法：python acceptance/probe_query_limits.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("PORT_Q_PROBE", "51992"))
BASE = f"http://127.0.0.1:{PORT}"
SANDBOX = ROOT / "acceptance" / os.environ.get("DC_REPLAY_SANDBOX_Q_PROBE", "replay-query-probe")

CASES: list[tuple[str, str]] = [
    ("纯 SELECT", "select 1 as a"),
    ("子查询(派生表)", "select * from (select 1 as a union all select 2) t order by a"),
    ("标量子查询", "select (select 1) as a"),
    ("IN 子查询", "select 1 as a where 1 in (select 1)"),
    ("CTE(WITH)", "with t as (select 1 as a) select * from t"),
    ("CTE+子查询", "with t as (select 1 as a) select * from t where a in (select a from t)"),
    ("EXPLAIN", "explain select 1"),
    ("带行注释", "-- 注释\nselect 1 as a"),
    ("带块注释", "/* c */ select 1 as a"),
    ("尾分号单句", "select 1;"),
    ("多语句", "select 1; select 2"),
    ("多语句(读写混合)", "select 1; select 2; select 3"),
    ("CREATE TABLE", "create table if not exists q_dml (id int)"),
    ("CREATE TABLE AS SELECT", "create table if not exists q_dml2 as select 1 as a"),
    ("ALTER TABLE", "alter table q_dml add column b int"),
    ("INSERT", "insert into q_dml (id) values (1)"),
    ("INSERT ... SELECT(子查询)", "insert into q_dml (id) select 2"),
    ("UPDATE(带 WHERE)", "update q_dml set id = 3 where id = 1"),
    ("UPDATE(无 WHERE)", "update q_dml set id = 9"),
    ("DELETE(带 WHERE)", "delete from q_dml where id = 3"),
    ("DELETE(无 WHERE)", "delete from q_dml"),
    ("DELETE + IN 子查询", "delete from q_dml where id in (select 1)"),
    ("DROP TABLE", "drop table if exists q_drop_me"),
    ("TRUNCATE", "truncate table q_dml"),
    ("SHOW TABLES", "show tables"),
    ("CALL 存储过程", "call some_proc()"),
    ("SET 变量", "set @x = 1"),
    ("USE 库", "use mysql"),
    ("GRANT", "grant select on *.* to 'x'@'%'"),
]

# SQLite 引擎本身不支持的语句：在 SQLite 沙箱里报语法错属正常，
# 不能算「工具限制」，单独标注（MySQL 侧另有验证）。
SQLITE_UNSUPPORTED_KEYWORDS = {"show", "call", "set", "use", "grant", "revoke", "truncate"}


def api(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        BASE + url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, {"_raw": raw[:400]}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"_raw": raw[:400]}


def wait_ready(timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(BASE + "/api/ping", timeout=3) as resp:
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.6)
    return False


def main() -> int:
    SANDBOX.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "DATA_DIR": str(SANDBOX / "data"),
        "UPLOADS_DIR": str(SANDBOX / "uploads"),
        "EXPORTS_DIR": str(SANDBOX / "exports"),
        "HOST": "127.0.0.1",
        "PORT": str(PORT),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONPATH": "",
    }
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    log = open(SANDBOX / "server.log", "w", encoding="utf-8")
    proc = subprocess.Popen([str(python), "server.py"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    results: list[dict] = []
    try:
        if not wait_ready():
            print("服务未就绪，日志尾部：")
            print((SANDBOX / "server.log").read_text(encoding="utf-8", errors="replace")[-1500:])
            return 2
        for label, sql in CASES:
            payload = {"targetDbType": "sqlite", "connectionId": "", "sql": sql}
            status, body = api("POST", "/api/query/run", payload)
            confirm_note = ""
            if status == 200 and body.get("needConfirm"):
                # 危险语句：先确认拦截生效，再带一次性令牌重发，验证确认闭环
                reasons = "；".join(r for item in body.get("dangerous", []) for r in item.get("reasons", []))
                token = body.get("confirmToken") or ""
                status, body = api("POST", "/api/query/run", {**payload, "confirmToken": token})
                confirm_note = f"｜已确认({reasons[:60]})"
            if status == 200 and body.get("ok") is not False:
                if body.get("needConfirm"):
                    verdict = "需确认(未执行)"
                    detail = "危险语句被拦截，等待确认"
                else:
                    totals = body.get("totals") or {}
                    verdict = "执行通过"
                    detail = (f"语句={totals.get('statements')} 结果集={totals.get('resultSets')} "
                              f"影响行={totals.get('affectedRows')} 失败={totals.get('failed')}")
                    if body.get("failedIndex"):
                        failed = next((s for s in body.get("statements", []) if s.get("index") == body["failedIndex"]), {})
                        err = str(failed.get("error") or body.get("message"))
                        keyword = str(failed.get("keyword") or "")
                        if keyword in SQLITE_UNSUPPORTED_KEYWORDS:
                            verdict = "SQLite 不支持(非工具限制)"
                        else:
                            verdict = "执行报错"
                        detail = err[:150]
            else:
                verdict = "被拒/报错"
                detail = str(body.get("error") or body)[:160]
            detail = f"{detail}{confirm_note}"
            results.append({"case": label, "sql": sql, "http": status, "verdict": verdict, "detail": detail})
            print(f"[{status}] {label:<22} {verdict} :: {detail}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()

    tag = os.environ.get("DC_PROBE_TAG", "after")
    out = ROOT / "acceptance" / "evidence" / "20260921" / f"query-limits-{tag}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"), "base": BASE, "tag": tag, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告：{out}")
    tally: dict[str, int] = {}
    for row in results:
        tally[row["verdict"]] = tally.get(row["verdict"], 0) + 1
    print(f"判定分布：{json.dumps(tally, ensure_ascii=False)}（共 {len(results)} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
