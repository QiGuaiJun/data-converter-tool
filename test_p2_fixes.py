"""P2 修复专项回归测试。

覆盖：
- P2-12 「跳过自上次导入后未曾更新过的文件」：内容指纹 + 目标身份键 + run_import_batch 跳过链路
- P2-7  导出对象行数：SQLite 精确计数；MySQL 小表 COUNT(*) 精确值且 rowsApproximate=False
- P2-13 「表注释作为文件名」：MySQL 表注释生效、显式文件名优先、开关关闭回退表名、SQLite 不受影响

MySQL 用例依赖本机 MySQL（连接参数可用环境变量覆盖），不可连接时自动跳过。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

# 隔离测试环境：数据/上传/导出落临时目录，不触碰真实 data/ uploads/ exports/
_tmp = tempfile.mkdtemp(prefix="dc_p2_")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
os.environ["UPLOADS_DIR"] = str(Path(_tmp) / "uploads")
os.environ["EXPORTS_DIR"] = str(Path(_tmp) / "exports")

import server

MYSQL_FIELDS = {
    "targetDbType": "mysql",
    "dbHost": os.environ.get("DC_TEST_MYSQL_HOST", "127.0.0.1"),
    "dbPort": os.environ.get("DC_TEST_MYSQL_PORT", "3306"),
    "dbUser": os.environ.get("DC_TEST_MYSQL_USER", "root"),
    "dbPassword": os.environ.get("DC_TEST_MYSQL_PASSWORD", "123456"),
    "dbName": os.environ.get("DC_TEST_MYSQL_DB", "dc_p2_test"),
}


def mysql_available() -> bool:
    try:
        import pymysql

        conn = pymysql.connect(
            host=MYSQL_FIELDS["dbHost"],
            port=int(MYSQL_FIELDS["dbPort"]),
            user=MYSQL_FIELDS["dbUser"],
            password=MYSQL_FIELDS["dbPassword"],
            connect_timeout=3,
        )
        with conn.cursor() as cursor:
            cursor.execute(
                f"create database if not exists {MYSQL_FIELDS['dbName']} default character set utf8mb4"
            )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


MYSQL_READY = mysql_available()


def _drop_mysql_tables(*tables: str) -> None:
    """清理本文件在共享 MySQL 库中创建的夹具表。

    测试库（默认 dc_p2_test）是长期存在的共享库，用例必须自行收尾，
    否则每跑一次就残留一批垃圾表。
    """
    if not MYSQL_READY:
        return
    try:
        conn = server.connect_target_db(MYSQL_FIELDS)
    except Exception:
        return
    try:
        with conn.cursor() as cursor:
            for table in tables:
                cursor.execute(f"drop table if exists {table}")
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def sqlite_import_fields(table: str) -> dict[str, str]:
    return {
        "targetDbType": "sqlite",
        "importMode": "rebuild",
        "tableName": table,
        "mapping": json.dumps(
            [
                {"sourceIndex": 0, "target": "name", "enabled": True, "defaultValue": "", "matchKey": False},
                {"sourceIndex": 1, "target": "amount", "enabled": True, "defaultValue": "", "matchKey": False},
            ]
        ),
        "tableCase": "lower",
        "fieldCase": "lower",
        "commitMode": "once",
    }


def sqlite_table_count(table: str) -> int:
    with server.connect_db() as conn:
        return int(conn.execute(f"select count(*) from {table}").fetchone()[0])


# ---------------------------------------------------------------------------
# P2-12：跳过未更新文件
# ---------------------------------------------------------------------------


def test_p2_12_fingerprint_stable_and_sensitive() -> None:
    path = Path(_tmp) / "fp_source.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    first = server.import_file_fingerprint(path)
    second = server.import_file_fingerprint(path)
    assert first == second, "同一文件两次指纹必须一致"
    path.write_text("a,b\n1,3\n", encoding="utf-8")
    third = server.import_file_fingerprint(path)
    assert third != first, "内容变化后指纹必须变化"


def test_p2_12_file_key_includes_target_identity() -> None:
    base = {"targetDbType": "mysql", "connectionId": "conn1"}
    key_table_a = server.import_file_key("f.csv", {**base, "tableName": "t1"})
    key_table_b = server.import_file_key("f.csv", {**base, "tableName": "t2"})
    key_conn_b = server.import_file_key("f.csv", {**base, "connectionId": "conn2", "tableName": "t1"})
    assert key_table_a != key_table_b, "同一文件导入不同表不能算已导入过"
    assert key_table_a != key_conn_b, "同一文件导入不同连接不能算已导入过"


def test_p2_12_run_import_batch_skips_unchanged_file() -> None:
    upload = Path(_tmp) / "uploads" / "p2_skip_seen.csv"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_text("name,amount\nA,1\nB,2\n", encoding="utf-8-sig")
    uploaded = server.UploadedFile("p2_skip_seen.csv", upload)
    fields = sqlite_import_fields("p2_skip_seen")

    # 首次导入：无指纹记录，正常写入
    results, failures, skipped = server.run_import_batch([uploaded], dict(fields))
    assert not failures and skipped == 0
    assert results[0]["rowsWritten"] == 2
    assert sqlite_table_count("p2_skip_seen") == 2

    # 手动插入一行制造差异：若跳过失效（rebuild 重导），差异行会被清掉
    with server.connect_db() as conn:
        conn.execute("insert into p2_skip_seen (name, amount) values ('EXTRA', 9)")
    assert sqlite_table_count("p2_skip_seen") == 3

    # 二次导入 + skipSeenFile：指纹命中，整文件跳过，表数据保持原样
    skip_fields = dict(fields)
    skip_fields["skipSeenFile"] = "true"
    results2, failures2, skipped2 = server.run_import_batch([uploaded], skip_fields)
    assert not failures2 and skipped2 == 1
    assert "跳过" in str(results2[0]["message"])
    assert results2[0]["rowsWritten"] == 0
    assert sqlite_table_count("p2_skip_seen") == 3, "命中跳过后不得重写目标表"

    # 文件内容更新后：指纹变化，必须重新导入
    upload.write_text("name,amount\nA,1\nB,2\nC,3\n", encoding="utf-8-sig")
    results3, failures3, skipped3 = server.run_import_batch([uploaded], dict(skip_fields))
    assert not failures3 and skipped3 == 0
    assert results3[0]["rowsWritten"] == 3
    assert sqlite_table_count("p2_skip_seen") == 3

    # 更新后的指纹已记录：再次导入同内容继续跳过
    results4, _, skipped4 = server.run_import_batch([uploaded], dict(skip_fields))
    assert skipped4 == 1


# ---------------------------------------------------------------------------
# P2-7：导出对象行数精确化
# ---------------------------------------------------------------------------


def test_p2_7_sqlite_sources_are_precise() -> None:
    with server.connect_db() as conn:
        conn.execute("drop table if exists p2_rowcount")
        conn.execute("create table p2_rowcount (id integer, name text)")
        conn.executemany("insert into p2_rowcount values (?, ?)", [(i, f"n{i}") for i in range(777)])
    sources = server.export_sources({"targetDbType": "sqlite"})
    entry = next(item for item in sources if item["name"] == "p2_rowcount")
    assert entry["rows"] == 777
    assert entry["rowsApproximate"] is False


@pytest.mark.skipif(not MYSQL_READY, reason="本机 MySQL 不可用")
def test_p2_7_mysql_small_table_uses_exact_count() -> None:
    try:
        conn = server.connect_target_db(MYSQL_FIELDS)
        try:
            with conn.cursor() as cursor:
                cursor.execute("drop table if exists p2_rowcount_t")
                cursor.execute("create table p2_rowcount_t (id int primary key, name varchar(32))")
                cursor.executemany("insert into p2_rowcount_t values (%s, %s)", [(i, f"n{i}") for i in range(1234)])
            conn.commit()
        finally:
            conn.close()

        sources = server.export_sources(MYSQL_FIELDS)
        entry = next(item for item in sources if item["name"] == "p2_rowcount_t")
        assert entry["rows"] == 1234, "≤10 万行的表必须返回 COUNT(*) 精确值"
        assert entry["rowsApproximate"] is False
    finally:
        _drop_mysql_tables("p2_rowcount_t")


# ---------------------------------------------------------------------------
# P2-13：表注释作为文件名
# ---------------------------------------------------------------------------


def _mysql_fixture_table(table: str, comment: str, rows: int) -> None:
    conn = server.connect_target_db(MYSQL_FIELDS)
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"drop table if exists {table}")
            cursor.execute(
                f"create table {table} (id int primary key, name varchar(32)) comment='{comment}'"
            )
            cursor.executemany(f"insert into {table} values (%s, %s)", [(i, f"n{i}") for i in range(rows)])
        conn.commit()
    finally:
        conn.close()


@pytest.mark.skipif(not MYSQL_READY, reason="本机 MySQL 不可用")
def test_p2_13_comment_as_filename_mysql() -> None:
    try:
        _mysql_fixture_table("p2_comment_t", "P2注释文件名", 3)

        def run(extra: dict[str, str]) -> str:
            payload = {
                **MYSQL_FIELDS,
                "items": [{"type": "table", "table": "p2_comment_t", "name": "p2_comment_t"}],
                "extension": "xlsx",
            }
            payload.update(extra)
            result = server.run_export_job(payload)
            assert result["rows"] == 3
            return Path(result["files"][0]).stem

        # 开关开启且未指定文件名 → 表注释作为文件名
        assert run({"commentAsFileName": "true"}) == "P2注释文件名"
        # 显式文件名优先于表注释
        assert run({"commentAsFileName": "true", "outputName": "explicit_name"}) == "explicit_name"
        # 开关关闭 → 表名
        assert run({}) == "p2_comment_t"
    finally:
        _drop_mysql_tables("p2_comment_t")


def test_p2_13_sqlite_unaffected_by_flag() -> None:
    with server.connect_db() as conn:
        conn.execute("drop table if exists p2_comment_sqlite")
        conn.execute("create table p2_comment_sqlite (id integer, name text)")
        conn.execute("insert into p2_comment_sqlite values (1, 'a')")
    result = server.run_export_job(
        {
            "targetDbType": "sqlite",
            "items": [{"type": "table", "table": "p2_comment_sqlite", "name": "p2_comment_sqlite"}],
            "extension": "csv",
            "commentAsFileName": "true",
        }
    )
    assert Path(result["files"][0]).stem == "p2_comment_sqlite"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
