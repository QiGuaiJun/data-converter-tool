"""P3 修复专项回归测试。

覆盖：
- P3-27 「导入时间列建成 text」：importTimeField 现在写入 dt.datetime 对象，
  MySQL 端推断为 datetime 列类型；SQLite 端经显式适配器落为 "YYYY-MM-DD HH:MM:SS" 文本，
  且不再触发 Python 3.12 弃用的 sqlite3 默认日期适配器告警。

MySQL 用例依赖本机 MySQL（连接参数可用环境变量覆盖），不可连接时自动跳过。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import warnings
from pathlib import Path

import pytest

# 隔离测试环境：数据/上传/导出落临时目录，不触碰真实 data/ uploads/ exports/
_tmp = tempfile.mkdtemp(prefix="dc_p3_")
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

MAPPING = json.dumps(
    [
        {"sourceIndex": 0, "target": "name", "enabled": True, "defaultValue": "", "matchKey": False},
        {"sourceIndex": 1, "target": "amount", "enabled": True, "defaultValue": "", "matchKey": False},
    ]
)

TS_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def make_upload(name: str) -> server.UploadedFile:
    upload = Path(_tmp) / "uploads" / name
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_text("name,amount\nA,1\nB,2\n", encoding="utf-8-sig")
    return server.UploadedFile(name, upload)


# ---------------------------------------------------------------------------
# P3-27：导入时间列建成 datetime（而非 text）
# ---------------------------------------------------------------------------


def test_p3_27_sqlite_import_time_value_and_no_deprecation() -> None:
    uploaded = make_upload("p3_27_sqlite.csv")
    fields = {
        "targetDbType": "sqlite",
        "importMode": "rebuild",
        "tableName": "p3_27_sqlite",
        "mapping": MAPPING,
        "importTimeField": "imported_at",
        "tableCase": "lower",
        "fieldCase": "lower",
        "commitMode": "once",
    }

    # 弃用告警升级为错误：若仍走 Python 3.12 默认适配器（而非显式注册的），这里会炸
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        results, failures, _ = server.run_import_batch([uploaded], fields)
    assert not failures and results[0]["rowsWritten"] == 2

    with server.connect_db() as conn:
        rows = conn.execute("select name, imported_at from p3_27_sqlite order by name").fetchall()
    assert len(rows) == 2
    for _, ts in rows:
        assert TS_PATTERN.match(str(ts)), f"SQLite 导入时间应为 YYYY-MM-DD HH:MM:SS 文本，实际: {ts!r}"


@pytest.mark.skipif(not MYSQL_READY, reason="本机 MySQL 不可用")
def test_p3_27_mysql_import_time_column_is_datetime() -> None:
    uploaded = make_upload("p3_27_mysql.csv")
    fields = {
        **MYSQL_FIELDS,
        "importMode": "rebuild",
        "tableName": "p3_27_mysql",
        "mapping": MAPPING,
        "importTimeField": "imported_at",
        "tableCase": "lower",
        "fieldCase": "lower",
        "commitMode": "once",
    }

    results, failures, _ = server.run_import_batch([uploaded], fields)
    assert not failures and results[0]["rowsWritten"] == 2

    conn = server.connect_target_db(MYSQL_FIELDS)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "select data_type from information_schema.columns "
                "where table_schema = %s and table_name = 'p3_27_mysql' and column_name = 'imported_at'",
                (MYSQL_FIELDS["dbName"],),
            )
            data_type = cursor.fetchone()[0]
            cursor.execute("select imported_at from p3_27_mysql order by name limit 1")
            ts = cursor.fetchone()[0]
    finally:
        conn.close()

    assert data_type == "datetime", f"导入时间列必须是 datetime，实际: {data_type}"
    assert TS_PATTERN.match(str(ts)), f"MySQL 导入时间值异常: {ts!r}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
