"""导入引擎端到端检查。

两种跑法完全等价：
    python test_import_engine.py        # 脚本入口，全部通过时打印 "import engine checks passed"
    pytest -q test_import_engine.py     # 每个检查项一个用例

为什么改成这样：原文件只有一个 ``main()``，模块里没有任何 ``def test_*``，
所以 pytest 收集到 0 个用例却「绿色通过」——那是假绿。现在把每个检查项
拆成独立的 ``def test_xxx(): ... assert``，断言只有一处定义（``run_pipeline()``
缓存一次执行结果），脚本入口和 pytest 走同一套断言。

隔离：模块导入时就把 DATA_DIR / UPLOADS_DIR / EXPORTS_DIR 指向临时目录，
绝不触碰真实 data/ uploads/ exports/。
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import tempfile
from pathlib import Path

from openpyxl import Workbook

# 隔离测试环境：数据/上传/导出落临时目录，不触碰真实 data/ uploads/ exports/
_tmp = tempfile.mkdtemp(prefix="dc_test_")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
os.environ["UPLOADS_DIR"] = str(Path(_tmp) / "uploads")
os.environ["EXPORTS_DIR"] = str(Path(_tmp) / "exports")

import server

# sqlite3 连接作为 with 语句使用时只提交事务、不 close，句柄会一直占着临时库文件；
# Windows 上这会挡住隔离目录的删除，把 %TEMP%/dc_test_* 越攒越多。这里记录句柄，
# 进程退出时统一关闭再删目录，保证跑完 %TEMP% 零残留。
_TRACKED_CONNECTIONS: list[object] = []
_ORIGINAL_CONNECT_DB = server.connect_db


def _tracking_connect_db():
    conn = _ORIGINAL_CONNECT_DB()
    _TRACKED_CONNECTIONS.append(conn)
    return conn


server.connect_db = _tracking_connect_db


@atexit.register
def _remove_isolated_tmp() -> None:
    for conn in _TRACKED_CONNECTIONS:
        try:
            conn.close()
        except Exception:  # pragma: no cover - 关闭失败不影响测试结论
            pass
    shutil.rmtree(_tmp, ignore_errors=True)



def reset_tables() -> None:
    with server.connect_db() as conn:
        for table in ["qa_import", "qa_json", "qa_excel"]:
            conn.execute(f"drop table if exists {table}")
        conn.execute("delete from _import_logs where table_name like 'qa_%'")


_PIPELINE: dict[str, object] | None = None


def run_pipeline() -> dict[str, object]:
    """跑一遍完整导入流程并缓存结果（首次调用执行，之后复用）。

    流程本身顺序有状态（rebuild → update → 目标表最终内容），因此只执行一次，
    各 ``test_*`` 只对自己的检查项断言，避免重复跑导致结果互相污染。
    """
    global _PIPELINE
    if _PIPELINE is not None:
        return _PIPELINE

    reset_tables()
    upload_dir = Path(_tmp) / "uploads"
    upload_dir.mkdir(exist_ok=True)

    csv_path = upload_dir / "qa.csv"
    csv_path.write_text("Name,Amount,Dept\n Alice ,10,Sales\nBob,,Sales\nBob,,Sales\n", encoding="utf-8-sig")
    fields = {
        "importMode": "rebuild",
        "tableName": "qa_import",
        "mapping": json.dumps(
            [
                {"sourceIndex": 0, "target": "Name", "enabled": True, "defaultValue": "", "matchKey": True},
                {"sourceIndex": 1, "target": "Amount", "enabled": True, "defaultValue": "0", "matchKey": False},
                {"sourceIndex": 2, "target": "Dept", "enabled": True, "defaultValue": "", "matchKey": False},
            ]
        ),
        "trimValues": "true",
        "emptyAsNull": "false",
        "zeroForNumber": "true",
        "dedupeColumns": "Name",
        "fieldCase": "lower",
        "fieldReplaceFrom": "space",
        "fieldReplaceTo": "_",
        "tableCase": "lower",
        "autoPkField": "id",
        "importTimeField": "imported_at",
        "sheetNameField": "source_name",
        "fixedValue": "batch1",
        "fixedValueField": "batch",
    }
    first = server.import_uploaded_file(server.UploadedFile("qa.csv", csv_path), fields)

    fields["importMode"] = "update"
    csv_path.write_text("Name,Amount,Dept\nAlice,99,Ops\nCarol,12,Ops\n", encoding="utf-8-sig")
    second = server.import_uploaded_file(server.UploadedFile("qa.csv", csv_path), fields)

    json_path = upload_dir / "qa.json"
    json_path.write_text(
        json.dumps([{"city": "Shanghai", "qty": 3}, {"city": "Beijing", "qty": 5}], ensure_ascii=False),
        encoding="utf-8",
    )
    third = server.import_uploaded_file(
        server.UploadedFile("qa.json", json_path),
        {"importMode": "rebuild", "tableName": "qa_json", "fieldCase": "lower", "tableCase": "lower"},
    )

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "SheetA"
    sheet.append(["Code", "Value"])
    sheet.append(["A", 1])
    sheet.append(["B", 2])
    xlsx_path = upload_dir / "qa.xlsx"
    workbook.save(xlsx_path)
    fourth = server.import_uploaded_file(
        server.UploadedFile("qa.xlsx", xlsx_path),
        {
            "importMode": "rebuild",
            "tableName": "qa_excel",
            "sheetName": "SheetA",
            "fieldCase": "lower",
            "tableCase": "lower",
            "columnFilter": "Code",
        },
    )

    with server.connect_db() as conn:
        rows = conn.execute("select name, amount, dept, batch from qa_import order by name").fetchall()
        export = server.export_query_to_excel(conn, "select name, amount from qa_import order by name", "qa_result.xlsx")

    _PIPELINE = {
        "first": first,
        "second": second,
        "third": third,
        "fourth": fourth,
        "rows": [tuple(row) for row in rows],
        "export": str(export),
    }
    return _PIPELINE


# ---------------------------------------------------------------------------
# 检查项
# ---------------------------------------------------------------------------


def test_csv_rebuild_writes_deduped_rows() -> None:
    """rebuild 模式：去重后写 2 行（重复的 Bob 只留一行）。"""
    assert run_pipeline()["first"]["rowsWritten"] == 2


def test_csv_update_mode_writes_one_and_updates_one() -> None:
    """update 模式：Carol 新增 1 行、Alice 命中匹配键更新 1 行。"""
    second = run_pipeline()["second"]
    assert second["rowsWritten"] == 1
    assert second["rowsUpdated"] == 1


def test_json_rebuild_writes_rows() -> None:
    assert run_pipeline()["third"]["rowsWritten"] == 2


def test_excel_import_respects_column_filter() -> None:
    """columnFilter=Code：只保留 code 一列。"""
    assert run_pipeline()["fourth"]["columns"] == ["code"]


def test_final_rows_match_expected_transformations() -> None:
    """trim / 空值补默认值 / 去重 / 固定值字段 全部落到最终数据上。"""
    assert run_pipeline()["rows"] == [
        ("Alice", 99, "Ops", "batch1"),
        ("Bob", 0, "Sales", "batch1"),
        ("Carol", 12, "Ops", "batch1"),
    ]


def test_query_export_produces_excel_file() -> None:
    assert Path(str(run_pipeline()["export"])).exists()


TESTS = (
    test_csv_rebuild_writes_deduped_rows,
    test_csv_update_mode_writes_one_and_updates_one,
    test_json_rebuild_writes_rows,
    test_excel_import_respects_column_filter,
    test_final_rows_match_expected_transformations,
    test_query_export_produces_excel_file,
)


def main() -> None:
    """脚本入口：逐个跑上面的检查项，任一断言失败即中断。

    注意：pytest 的 ``pytest.skip.Exception`` 继承自 ``BaseException``，
    ``except Exception`` 抓不到它；本模块不使用 skip，因此这里不做包装，
    让异常直接冒泡、以非零码退出。
    """
    pipeline = run_pipeline()
    for check in TESTS:
        check()
    print(
        json.dumps(
            {
                "csv_rebuild": pipeline["first"]["rowsWritten"],
                "csv_update_written": pipeline["second"]["rowsWritten"],
                "csv_update_updated": pipeline["second"]["rowsUpdated"],
                "json_rows": pipeline["third"]["rowsWritten"],
                "excel_columns": pipeline["fourth"]["columns"],
                "export": pipeline["export"],
            },
            ensure_ascii=False,
        )
    )
    print("import engine checks passed")


if __name__ == "__main__":
    main()
