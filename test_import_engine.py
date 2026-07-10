from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook

import server


def reset_tables() -> None:
    with server.connect_db() as conn:
        for table in ["qa_import", "qa_json", "qa_excel"]:
            conn.execute(f"drop table if exists {table}")
        conn.execute("delete from _import_logs where table_name like 'qa_%'")


def main() -> None:
    reset_tables()
    upload_dir = Path("uploads")
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
    json_path.write_text(json.dumps([{"city": "Shanghai", "qty": 3}, {"city": "Beijing", "qty": 5}], ensure_ascii=False), encoding="utf-8")
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
        {"importMode": "rebuild", "tableName": "qa_excel", "sheetName": "SheetA", "fieldCase": "lower", "tableCase": "lower", "columnFilter": "Code"},
    )

    with server.connect_db() as conn:
        rows = conn.execute("select name, amount, dept, batch from qa_import order by name").fetchall()
        export = server.export_query_to_excel(conn, "select name, amount from qa_import order by name", "qa_result.xlsx")

    assert first["rowsWritten"] == 2
    assert second["rowsWritten"] == 1
    assert second["rowsUpdated"] == 1
    assert third["rowsWritten"] == 2
    assert fourth["columns"] == ["code"]
    assert [tuple(row) for row in rows] == [("Alice", 99, "Ops", "batch1"), ("Bob", 0, "Sales", "batch1"), ("Carol", 12, "Ops", "batch1")]
    assert Path(export).exists()
    print("import engine checks passed")


if __name__ == "__main__":
    main()
