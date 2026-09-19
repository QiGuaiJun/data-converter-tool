"""导入功能矩阵检查（格式 × 选项组合）。

两种跑法完全等价：
    python test_import_feature_matrix.py     # 脚本入口，末尾打印各特性结果 JSON
    pytest -q test_import_feature_matrix.py  # 每个特性一个用例

为什么改成这样：原文件只有一个 ``main()``，模块里没有任何 ``def test_*``，
pytest 收集 0 个用例却显示通过（假绿）。现在每个特性拆成独立 ``def test_xxx``，
断言只有一处定义（``run_matrix()`` 缓存一次执行结果）。

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



def reset() -> None:
    with server.connect_db() as conn:
        for table in [
            "mx_csv",
            "mx_csv_update",
            "mx_json",
            "mx_xml",
            "mx_excel",
            "mx_excel_second",
            "first",
            "second",
            "yg",
            "mx_pipe",
            "mx_date",
            "mx_resume",
            "mx_sql_marker",
        ]:
            conn.execute(f"drop table if exists {table}")
        conn.execute("delete from _import_logs where table_name like 'mx_%'")


def rows(table: str) -> list[tuple]:
    with server.connect_db() as conn:
        return [tuple(row) for row in conn.execute(f"select * from {table}").fetchall()]


_MATRIX: dict[str, object] | None = None


def run_matrix() -> dict[str, object]:
    """跑一遍全部格式/选项组合并缓存结果（首次调用执行，之后复用）。"""
    global _MATRIX
    if _MATRIX is not None:
        return _MATRIX

    reset()
    upload_dir = Path(_tmp) / "uploads"
    upload_dir.mkdir(exist_ok=True)

    csv_path = upload_dir / "mx.csv"
    csv_path.write_text("Name,Amount,Dept\n Alice ,10,Sales\nBob,,Sales\nBob,,Sales\n", encoding="utf-8-sig")
    csv_fields = {
        "importMode": "rebuild",
        "tableName": "mx_csv",
        "mapping": json.dumps(
            [
                {"sourceIndex": 0, "target": "Name", "enabled": True, "defaultValue": "", "matchKey": True},
                {"sourceIndex": 1, "target": "Amount", "enabled": True, "defaultValue": "0", "matchKey": False},
                {"sourceIndex": 2, "target": "Dept", "enabled": True, "defaultValue": "", "matchKey": False},
            ]
        ),
        "trimValues": "true",
        "zeroForNumber": "true",
        "dedupeColumns": "Name",
        "fieldCase": "lower",
        "tableCase": "lower",
        "autoPkField": "id",
        "importTimeField": "imported_at",
        "fixedValue": "batch-a",
        "fixedValueField": "batch",
        "afterEachSql": "create table if not exists mx_sql_marker (name text); insert into mx_sql_marker values ('after_each');",
    }
    csv_result = server.import_uploaded_file(server.UploadedFile("mx.csv", csv_path), csv_fields)

    csv_path.write_text("Name,Amount,Dept\nAlice,99,Ops\nCarol,12,Ops\n", encoding="utf-8-sig")
    csv_fields["importMode"] = "update"
    update_result = server.import_uploaded_file(server.UploadedFile("mx.csv", csv_path), csv_fields)

    json_path = upload_dir / "mx.json"
    json_path.write_text(
        json.dumps(
            [
                {"city": "Shanghai", "qty": 3, "extra": {"level": "A"}},
                {"city": "Beijing", "qty": 5, "extra": {"level": "B"}},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    json_result = server.import_uploaded_file(
        server.UploadedFile("mx.json", json_path),
        {"importMode": "rebuild", "tableName": "mx_json", "fieldCase": "lower", "tableCase": "lower"},
    )

    xml_path = upload_dir / "mx.xml"
    xml_path.write_text(
        "<rows><row><name>A</name><qty>1</qty></row><row><name>B</name><qty>2</qty></row></rows>",
        encoding="utf-8",
    )
    xml_result = server.import_uploaded_file(
        server.UploadedFile("mx.xml", xml_path),
        {"importMode": "rebuild", "tableName": "mx_xml", "rowTag": "row", "fieldCase": "lower", "tableCase": "lower"},
    )

    workbook = Workbook()
    first_sheet = workbook.active
    first_sheet.title = "First"
    first_sheet.append(["Code", "Value"])
    first_sheet.append(["A", 1])
    second_sheet = workbook.create_sheet("Second")
    second_sheet.append(["Code", "Value"])
    second_sheet.append(["B", 2])
    excel_path = upload_dir / "mx.xlsx"
    workbook.save(excel_path)
    excel_first = server.import_uploaded_file(
        server.UploadedFile("mx.xlsx", excel_path),
        {"importMode": "rebuild", "tableName": "mx_excel", "sheetName": "First", "fieldCase": "lower", "tableCase": "lower"},
    )
    excel_second = server.import_uploaded_file(
        server.UploadedFile("mx.xlsx", excel_path),
        {
            "importMode": "rebuild",
            "tableName": "mx_excel_second",
            "sheetName": "Second",
            "fieldCase": "lower",
            "tableCase": "lower",
        },
    )

    all_sheet = server.import_uploaded_file(
        server.UploadedFile("mx.xlsx", excel_path),
        {"importMode": "rebuild", "sheetMode": "all", "fieldCase": "lower", "tableCase": "lower", "tableNameRule": "sheet"},
    )

    pinyin_path = upload_dir / "员工.csv"
    pinyin_path.write_text("姓名,金额\n张三,8\n", encoding="utf-8-sig")
    pinyin_result = server.import_uploaded_file(
        server.UploadedFile("员工.csv", pinyin_path),
        {"importMode": "rebuild", "tablePinyin": "true", "fieldPinyin": "true", "tableCase": "lower", "fieldCase": "lower"},
    )

    pipe_path = upload_dir / "mx_pipe.csv"
    pipe_path.write_text("Name,Amount|A,1|B,2|", encoding="utf-8")
    pipe_result = server.import_uploaded_file(
        server.UploadedFile("mx_pipe.csv", pipe_path),
        {
            "importMode": "rebuild",
            "tableName": "mx_pipe",
            "delimiter": ",",
            "lineDelimiter": "|",
            "fieldCase": "lower",
            "tableCase": "lower",
        },
    )

    date_path = upload_dir / "mx_date.csv"
    date_path.write_text("Name,When\nA,2026/07/06\n", encoding="utf-8")
    date_result = server.import_uploaded_file(
        server.UploadedFile("mx_date.csv", date_path),
        {"importMode": "rebuild", "tableName": "mx_date", "dateColumns": "When:%Y/%m/%d", "fieldCase": "lower", "tableCase": "lower"},
    )

    resume_path = upload_dir / "mx_resume.csv"
    resume_path.write_text("Name,Amount\nA,1\nB,2\nC,3\n", encoding="utf-8")
    resume_fields = {
        "importMode": "rebuild",
        "tableName": "mx_resume",
        "resumeImport": "true",
        "fieldCase": "lower",
        "tableCase": "lower",
        "batchRows": "1",
    }
    tabular = server.read_tabular_file(resume_path, resume_fields)
    cols, data_rows, _, _ = server.build_target_data(tabular, resume_fields, "mx_resume.csv")
    key = server.checkpoint_key(server.UploadedFile("mx_resume.csv", resume_path), "mx_resume", resume_fields)
    with server.connect_target_db(resume_fields) as conn:
        server.target_create_or_expand_table(conn, "mx_resume", cols, data_rows, True, True, resume_fields)
        server.target_insert_rows(conn, "mx_resume", cols, data_rows[:1], resume_fields)
        conn.commit()
    server.set_checkpoint(key, 1)
    resume_result = server.import_uploaded_file(server.UploadedFile("mx_resume.csv", resume_path), resume_fields)

    with server.connect_db() as conn:
        export_path = server.export_query_to_excel(conn, "select name, amount from mx_csv order by name", "mx_matrix_result.xlsx")

    _MATRIX = {
        "csv_result": csv_result,
        "update_result": update_result,
        "json_result": json_result,
        "xml_result": xml_result,
        "excel_first": excel_first,
        "excel_second": excel_second,
        "all_sheet": all_sheet,
        "pinyin_result": pinyin_result,
        "pipe_result": pipe_result,
        "date_result": date_result,
        "resume_result": resume_result,
        "export_path": str(export_path),
        "after_each_sql": rows("mx_sql_marker"),
    }
    return _MATRIX


# ---------------------------------------------------------------------------
# 检查项：每个特性一个用例
# ---------------------------------------------------------------------------


def test_csv_clean_dedupe() -> None:
    """CSV：trim + 去重后写 2 行、跳过 1 行重复。"""
    result = run_matrix()["csv_result"]
    assert result["rowsWritten"] == 2
    assert result["rowsSkipped"] == 1


def test_csv_update_mode() -> None:
    result = run_matrix()["update_result"]
    assert result["rowsWritten"] == 1
    assert result["rowsUpdated"] == 1


def test_json_flat_basic() -> None:
    assert run_matrix()["json_result"]["rowsWritten"] == 2


def test_xml_basic() -> None:
    assert run_matrix()["xml_result"]["rowsWritten"] == 2


def test_excel_single_sheet() -> None:
    assert run_matrix()["excel_first"]["rowsWritten"] == 1


def test_excel_second_sheet() -> None:
    assert run_matrix()["excel_second"]["rowsWritten"] == 1


def test_excel_all_sheets() -> None:
    """sheetMode=all：两个 sheet 合计写 2 行。"""
    assert run_matrix()["all_sheet"]["rowsWritten"] == 2


def test_pinyin_table_and_fields() -> None:
    """中文表名/字段名转拼音首字母：员工 -> yg，姓名/金额 -> xm/je。"""
    result = run_matrix()["pinyin_result"]
    assert result["tableName"] == "yg"
    assert result["columns"] == ["xm", "je"]


def test_custom_line_delimiter() -> None:
    assert run_matrix()["pipe_result"]["rowsWritten"] == 2


def test_date_columns() -> None:
    assert run_matrix()["date_result"]["rowsWritten"] == 1


def test_resume_checkpoint() -> None:
    """断点续传：已写过第 1 行，续跑只补余下 2 行。"""
    assert run_matrix()["resume_result"]["rowsWritten"] == 2


def test_after_each_sql_runs_marker() -> None:
    """afterEachSql 必须「每次导入后」都执行：矩阵里 rebuild + update 共 2 次导入 -> 2 行 marker。"""
    marker = run_matrix()["after_each_sql"]
    assert len(marker) == 2
    assert all(row == ("after_each",) for row in marker)


def test_query_export() -> None:
    assert Path(str(run_matrix()["export_path"])).exists()


TESTS = (
    test_csv_clean_dedupe,
    test_csv_update_mode,
    test_json_flat_basic,
    test_xml_basic,
    test_excel_single_sheet,
    test_excel_second_sheet,
    test_excel_all_sheets,
    test_pinyin_table_and_fields,
    test_custom_line_delimiter,
    test_date_columns,
    test_resume_checkpoint,
    test_after_each_sql_runs_marker,
    test_query_export,
)


def main() -> None:
    """脚本入口：逐个跑上面的检查项，任一断言失败即中断。"""
    matrix = run_matrix()
    for check in TESTS:
        check()
    print(
        json.dumps(
            {
                "csv_clean_dedupe": "ok",
                "update_mode": "ok",
                "json_flat_basic": "ok",
                "xml_basic": "ok",
                "excel_single_sheet": "ok",
                "excel_all_sheets": "ok",
                "pinyin_table_fields": "ok",
                "custom_line_delimiter": "ok",
                "date_columns": "ok",
                "resume_checkpoint": "ok",
                "after_each_sql": matrix["after_each_sql"],
                "query_export": matrix["export_path"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print("import feature matrix checks passed")


if __name__ == "__main__":
    main()
