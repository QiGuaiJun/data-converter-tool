"""查询模块「SQL 控制台」专项回归测试（2026-09-21）。

背景：查询页原先只允许 SELECT / SHOW / DESCRIBE / EXPLAIN / WITH，且一次只能执行一条语句；
业主 2026-09-21 要求放开为「完整 SQL 控制台」（建表、删改、多语句都要能用），
写权限完全交给连接账号（本机连接是 root，数据库层没有兜底），
因此**危险语句必须走服务端一次性令牌确认**。

本文件钉死的行为契约：

1. 语句类型全放开：DDL / DML / 子查询 / CTE / 多语句脚本都能执行
2. 多语句按「引号 + 注释感知」切分（字符串里的分号不算分隔符）
3. 逐条提交语义：脚本**不是**原子的；第 N 条失败则第 N 条回滚、前 N-1 条保持生效，
   并通过 failedIndex 告知是第几条挂了
4. 危险语句（DROP / TRUNCATE / ALTER / GRANT / 无 WHERE 的 UPDATE、DELETE /
   无法识别的语句）必须先拿一次性令牌，令牌与「连接 + SQL 原文」绑定、只能消费一次
5. 每条语句回报 kind（resultset / affected / ddl）、affectedRows、elapsedMs
6. 导出链路只接受查询语句（写语句提前拦下，而不是等包装成子查询后报语法错）

隔离：导入 server 之前把 DATA_DIR / UPLOADS_DIR / EXPORTS_DIR 指向临时目录，
绝不触碰真实 runtime/（与 tests/ 下其它用例一致）。
"""

from __future__ import annotations

import atexit
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest

_tmp = tempfile.mkdtemp(prefix="dc_query_console_")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
os.environ["UPLOADS_DIR"] = str(Path(_tmp) / "uploads")
os.environ["EXPORTS_DIR"] = str(Path(_tmp) / "exports")

import server  # noqa: E402

SQLITE = {"targetDbType": "sqlite"}


@atexit.register
def _cleanup() -> None:
    shutil.rmtree(_tmp, ignore_errors=True)


def _payload(sql: str, token: str = "") -> dict[str, object]:
    body: dict[str, object] = {"targetDbType": "sqlite", "connectionId": "", "sql": sql}
    if token:
        body["confirmToken"] = token
    return body


def _run(sql: str) -> dict[str, object]:
    """执行脚本；若服务端要求确认，自动带令牌重发（模拟用户点「确认执行」）。"""
    first = server.run_readonly_query(_payload(sql))
    if first.get("needConfirm"):
        return server.run_readonly_query(_payload(sql, str(first["confirmToken"])))
    return first


@pytest.fixture(autouse=True)
def _fresh_table():
    """每例都从一张干净的表开始（temp 目录内的 sqlite 库会跨用例复用）。"""
    _run("drop table if exists qc_t")
    _run("create table qc_t (id integer, name text)")
    yield
    _run("drop table if exists qc_t")


# ---------------------------------------------------------------- 1. 语句类型放开


def test_select_returns_result_set():
    result = _run("select 1 as one, 'a' as two")
    assert result["failedIndex"] == 0
    first = result["statements"][0]
    assert first["kind"] == "resultset"
    assert first["resultSets"][0]["columns"] == ["one", "two"]
    assert first["resultSets"][0]["rows"] == [["1", "a"]]
    # 兼容旧字段（页面与复跑脚本仍读这几个顶层键）
    assert result["columns"] == ["one", "two"]
    assert result["rowCount"] == 1
    assert result["truncated"] is False


def test_subquery_and_cte_are_allowed():
    result = _run(
        "with base as (select 1 as a) "
        "select * from base where a in (select a from base) "
    )
    assert result["failedIndex"] == 0
    assert result["statements"][0]["resultSets"][0]["rows"] == [["1"]]


def test_ddl_and_dml_actually_persist():
    _run("insert into qc_t (id, name) values (1, 'x'), (2, 'y')")
    _run("update qc_t set name = 'z' where id = 1")
    result = _run("select count(*) as n from qc_t where name = 'z'")
    assert result["statements"][0]["resultSets"][0]["rows"] == [["1"]]

    # DDL：新增列后能写能读
    _run("alter table qc_t add column memo text")
    _run("update qc_t set memo = 'm' where id = 2")
    assert _run("select memo from qc_t where id = 2")["statements"][0]["resultSets"][0]["rows"] == [["m"]]

    affected = _run("delete from qc_t where id = 1")
    assert affected["statements"][0]["kind"] == "affected"
    assert affected["statements"][0]["affectedRows"] == 1
    assert affected["totals"]["affectedRows"] == 1


def test_create_table_as_select():
    _run("drop table if exists qc_ctas")
    result = _run("create table qc_ctas as select 1 as a union all select 2")
    assert result["failedIndex"] == 0
    assert result["statements"][0]["kind"] == "ddl"
    assert _run("select count(*) as n from qc_ctas")["statements"][0]["resultSets"][0]["rows"] == [["2"]]
    _run("drop table if exists qc_ctas")


# ---------------------------------------------------------------- 2. 多语句切分


def test_multi_statement_executes_every_statement():
    result = _run("select 1; select 2; insert into qc_t (id) values (9)")
    totals = result["totals"]
    assert totals["statements"] == 3
    assert totals["resultSets"] == 2
    assert totals["affectedRows"] == 1
    assert [item["index"] for item in result["statements"]] == [1, 2, 3]


def test_semicolon_inside_literal_is_not_a_separator():
    result = _run("select ';' as semi; select 2")
    assert result["totals"]["statements"] == 2
    assert result["statements"][0]["resultSets"][0]["rows"] == [[";"]]


def test_pure_comment_script_is_rejected():
    with pytest.raises(ValueError):
        server.run_readonly_query(_payload("-- 只有注释\n/* 没有语句 */"))


def test_statement_limit_is_enforced():
    script = ";".join(["select 1"] * (server.QUERY_MAX_STATEMENTS + 1))
    with pytest.raises(ValueError) as excinfo:
        server.run_readonly_query(_payload(script))
    assert "最多执行" in str(excinfo.value)


# ---------------------------------------------------------------- 3. 失败语义


def test_failure_reports_index_and_keeps_earlier_statements():
    """逐条提交：第 2 条失败时，第 1 条已经生效（脚本不是原子的）。"""
    result = _run("insert into qc_t (id) values (7); select * from no_such_table_qq; select 3")
    assert result["failedIndex"] == 2
    assert result["statements"][-1]["index"] == 2
    assert result["statements"][-1]["error"]
    # 第 3 条不再执行（出错即停）
    assert all(item["index"] != 3 for item in result["statements"])
    # 第 1 条的写入保留
    assert _run("select count(*) as n from qc_t")["statements"][0]["resultSets"][0]["rows"] == [["1"]]
    assert result["message"].startswith("共 3 条语句，第 2 条失败")


# ---------------------------------------------------------------- 4. 危险语句令牌


@pytest.mark.parametrize(
    "sql,level",
    [
        ("select 1", "safe"),
        ("with t as (select 1 as a) select * from t", "safe"),
        ("insert into qc_t (id) values (1)", "write"),
        ("update qc_t set id = 1 where id = 2", "write"),
        ("delete from qc_t where id = 1", "write"),
        ("create table if not exists qc_x (id int)", "write"),
        ("update qc_t set id = 1", "danger"),
        ("delete from qc_t", "danger"),
        ("drop table qc_t", "danger"),
        ("truncate table qc_t", "danger"),
        ("alter table qc_t add column z int", "danger"),
        ("grant select on *.* to 'x'@'%'", "danger"),
        ("select * from qc_t into outfile '/tmp/a.txt'", "danger"),
        ("vacuum", "danger"),
    ],
)
def test_risk_classification(sql, level):
    assert server.classify_sql_risk(sql)["level"] == level


def test_dangerous_statement_requires_one_time_token():
    first = server.run_readonly_query(_payload("delete from qc_t"))
    assert first["needConfirm"] is True
    assert first["dangerous"][0]["index"] == 1
    assert first["dangerous"][0]["reasons"]
    token = str(first["confirmToken"])
    assert token
    # 表里没有数据，但这句 sql 本身合法：带令牌后真正执行
    second = server.run_readonly_query(_payload("delete from qc_t", token))
    assert not second.get("needConfirm")
    assert second["failedIndex"] == 0


def test_confirm_token_is_single_use():
    first = server.run_readonly_query(_payload("delete from qc_t"))
    token = str(first["confirmToken"])
    server.run_readonly_query(_payload("delete from qc_t", token))
    again = server.run_readonly_query(_payload("delete from qc_t", token))
    assert again["needConfirm"] is True  # 令牌已作废，必须重新确认


def test_confirm_token_is_bound_to_sql_text():
    # 两次都用高危语句，但 SQL 原文不同：令牌指纹只认原文
    first = server.run_readonly_query(_payload("drop table if exists qc_t"))
    token = str(first["confirmToken"])
    other = server.run_readonly_query(_payload("delete from qc_t", token))
    assert other["needConfirm"] is True
    assert str(other["confirmToken"]) != token

    # 同一 SQL 换连接（fingerprint 含连接标识）同样不认旧令牌
    another_connection = server.run_readonly_query(
        {"targetDbType": "sqlite", "connectionId": "other-connection", "sql": "drop table if exists qc_t", "confirmToken": token}
    )
    assert another_connection["needConfirm"] is True


def test_dangerous_statement_inside_script_blocks_whole_script():
    result = server.run_readonly_query(_payload("select 1; drop table qc_t"))
    assert result["needConfirm"] is True
    assert [item["index"] for item in result["dangerous"]] == [2]
    # 未确认时整段脚本一条都没执行（响应里根本不带 statements）
    assert "statements" not in result
    assert result["rowCount"] == 0


# ---------------------------------------------------------------- 5. 导出链路防呆


@pytest.mark.parametrize("sql", ["select 1", "with t as (select 1 as a) select * from t", "show tables"])
def test_export_source_accepts_queries(sql):
    assert server.ensure_query_source(sql)


@pytest.mark.parametrize("sql", ["delete from qc_t", "drop table qc_t", "insert into qc_t (id) values (1)"])
def test_export_source_rejects_writes(sql):
    with pytest.raises(ValueError) as excinfo:
        server.ensure_query_source(sql)
    assert "只支持查询语句" in str(excinfo.value)


def test_export_source_rejects_multi_statement():
    with pytest.raises(ValueError) as excinfo:
        server.ensure_query_source("select 1; select 2")
    assert "只能是一条查询语句" in str(excinfo.value)


# ---------------------------------------------------------------- 6. 隔离自检


def test_runtime_database_is_untouched():
    """所有用例都必须落在临时目录里的库，真实 runtime/ 不能被写。"""
    # 全量跑时 pytest 可能先导入别的用例文件、由那一份先设置 DATA_DIR，
    # 因此这里只断言「库在系统临时目录下」，不绑定到本文件自己的 _tmp。
    db_path = Path(server.DB_PATH).resolve()
    assert Path(tempfile.gettempdir()).resolve() in db_path.parents, db_path
    real = Path(__file__).resolve().parent.parent / "runtime" / "data" / "imports.db"
    if real.exists():
        size_before = real.stat().st_size
        _run("create table if not exists qc_probe (id int)")
        assert real.stat().st_size == size_before
        conn = sqlite3.connect(f"file:{real.as_posix()}?mode=ro", uri=True)
        try:
            tables = {row[0] for row in conn.execute("select name from sqlite_master where type='table'")}
        finally:
            conn.close()
        assert "qc_probe" not in tables
