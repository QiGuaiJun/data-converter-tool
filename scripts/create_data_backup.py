from __future__ import annotations

import argparse
import datetime as dt
import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def add_path(archive: zipfile.ZipFile, source: Path, arc_prefix: str) -> int:
    if not source.exists():
        return 0
    if source.is_file():
        archive.write(source, f"{arc_prefix}/{source.name}")
        return 1
    count = 0
    for item in source.rglob("*"):
        if item.is_file():
            archive.write(item, f"{arc_prefix}/{item.relative_to(source).as_posix()}")
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a data backup package for deployment.")
    parser.add_argument("--include-uploads", action="store_true", help="Include uploaded source files.")
    parser.add_argument("--include-exports", action="store_true", help="Include exported result files.")
    parser.add_argument("--output-dir", default="deployment-data", help="Backup output directory.")
    args = parser.parse_args()

    output_dir = (ROOT / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    output = output_dir / f"data-converter-backup-{timestamp}.zip"

    manifest = {
        "createdAt": dt.datetime.now().isoformat(timespec="seconds"),
        "includes": ["data/imports.db"],
        "warning": "This package may contain database connection settings. Keep it private.",
    }
    if args.include_uploads:
        manifest["includes"].append("uploads")
    if args.include_exports:
        manifest["includes"].append("exports")

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        count = add_path(archive, ROOT / "data" / "imports.db", "data")
        if count == 0:
            raise SystemExit("data/imports.db does not exist. Nothing to back up.")
        if args.include_uploads:
            add_path(archive, ROOT / "uploads", "uploads")
        if args.include_exports:
            add_path(archive, ROOT / "exports", "exports")

    print(output)


if __name__ == "__main__":
    main()

