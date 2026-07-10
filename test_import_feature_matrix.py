from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook

import server


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


def main() -> None:
    reset()
    upload_dir = Path("uploads")
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
    assert csv_result["rowsWritten"] == 2
    assert csv_result["rowsSkipped"] == 1

    csv_path.write_text("Name,Amount,Dept\nAlice,99,Ops\nCarol,12,Ops\n", encoding="utf-8-sig")
    csv_fields["importMode"] = "update"
    update_result = server.import_uploaded_file(server.UploadedFile("mx.csv", csv_path), csv_fields)
    assert update_result["rowsWritten"] == 1
    assert update_result["rowsUpdated"] == 1

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
    assert json_result["rowsWritten"] == 2

    xml_path = upload_dir / "mx.xml"
    xml_path.write_text("<rows><row><name>A</name><qty>1</qty></row><row><name>B</name><qty>2</qty></row></rows>", encoding="utf-8")
    xml_result = server.import_uploaded_file(
        server.UploadedFile("mx.xml", xml_path),
        {"importMode": "rebuild", "tableName": "mx_xml", "rowTag": "row", "fieldCase": "lower", "tableCase": "lower"},
    )
    assert xml_result["rowsWritten"] == 2

    workbook = Workbook()
    first = workbook.active
    first.title = "First"
    first.append(["Code", "Value"])
    first.append(["A", 1])
    second = workbook.create_sheet("Second")
    second.append(["Code", "Value"])
    second.append(["B", 2])
    excel_path = upload_dir / "mx.xlsx"
    workbook.save(excel_path)
    excel_first = server.import_uploaded_file(
        server.UploadedFile("mx.xlsx", excel_path),
        {"importMode": "rebuild", "tableName": "mx_excel", "sheetName": "First", "fieldCase": "lower", "tableCase": "lower"},
    )
    excel_second = server.import_uploaded_file(
        server.UploadedFile("mx.xlsx", excel_path),
        {"importMode": "rebuild", "tableName": "mx_excel_second", "sheetName": "Second", "fieldCase": "lower", "tableCase": "lower"},
    )
    assert excel_first["rowsWritten"] == 1
    assert excel_second["rowsWritten"] == 1

    all_sheet = server.import_uploaded_file(
        server.UploadedFile("mx.xlsx", excel_path),
        {"importMode": "rebuild", "sheetMode": "all", "fieldCase": "lower", "tableCase": "lower", "tableNameRule": "sheet"},
    )
    assert all_sheet["rowsWritten"] == 2

    pinyin_path = upload_dir / "员工.csv"
    pinyin_path.write_text("姓名,金额\n张三,8\n", encoding="utf-8-sig")
    pinyin_result = server.import_uploaded_file(
        server.UploadedFile("员工.csv", pinyin_path),
        {"importMode": "rebuild", "tablePinyin": "true", "fieldPinyin": "true", "tableCase": "lower", "fieldCase": "lower"},
    )
    assert pinyin_result["tableName"] == "yg"
    assert pinyin_result["columns"] == ["xm", "je"]

    pipe_path = upload_dir / "mx_pipe.csv"
    pipe_path.write_text("Name,Amount|A,1|B,2|", encoding="utf-8")
    pipe_result = server.import_uploaded_file(
        server.UploadedFile("mx_pipe.csv", pipe_path),
        {"importMode": "rebuild", "tableName": "mx_pipe", "delimiter": ",", "lineDelimiter": "|", "fieldCase": "lower", "tableCase": "lower"},
    )
    assert pipe_result["rowsWritten"] == 2

    date_path = upload_dir / "mx_date.csv"
    date_path.write_text("Name,When\nA,2026/07/06\n", encoding="utf-8")
    date_result = server.import_uploaded_file(
        server.UploadedFile("mx_date.csv", date_path),
        {"importMode": "rebuild", "tableName": "mx_date", "dateColumns": "When:%Y/%m/%d", "fieldCase": "lower", "tableCase": "lower"},
    )
    assert date_result["rowsWritten"] == 1

    resume_path = upload_dir / "mx_resume.csv"
    resume_path.write_text("Name,Amount\nA,1\nB,2\nC,3\n", encoding="utf-8")
    resume_fields = {"importMode": "rebuild", "tableName": "mx_resume", "resumeImport": "true", "fieldCase": "lower", "tableCase": "lower", "batchRows": "1"}
    tabular = server.read_tabular_file(resume_path, resume_fields)
    cols, data_rows, _, _ = server.build_target_data(tabular, resume_fields, "mx_resume.csv")
    key = server.checkpoint_key(server.UploadedFile("mx_resume.csv", resume_path), "mx_resume", resume_fields)
    with server.connect_target_db(resume_fields) as conn:
        server.target_create_or_expand_table(conn, "mx_resume", cols, data_rows, True, True, resume_fields)
        server.target_insert_rows(conn, "mx_resume", cols, data_rows[:1], resume_fields)
        conn.commit()
    server.set_checkpoint(key, 1)
    resume_result = server.import_uploaded_file(server.UploadedFile("mx_resume.csv", resume_path), resume_fields)
    assert resume_result["rowsWritten"] == 2

    with server.connect_db() as conn:
        export_path = server.export_query_to_excel(conn, "select name, amount from mx_csv order by name", "mx_matrix_result.xlsx")
    assert Path(export_path).exists()

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
                "after_each_sql": rows("mx_sql_marker"),
                "query_export": export_path,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
