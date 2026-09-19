from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# 运行数据默认落在项目内的 runtime/ 下（与 server.py 的 runtime_path()、
# restore_data_backup.py 保持一致）。2026-09-17 迁移前数据在 ROOT/data，
# 但那个目录早已废弃 —— 早期版本这里写死 ROOT/"data"/"imports.db"，
# 迁移后必然找不到库文件，备份直接失败（M10-001/M10-002）。
RUNTIME_ROOT = ROOT / "runtime"


def env_target(name: str, fallback: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    return Path(raw).resolve() if raw else fallback.resolve()


DATA_DIR = env_target("DATA_DIR", RUNTIME_ROOT / "data")
UPLOADS_DIR = env_target("UPLOADS_DIR", RUNTIME_ROOT / "uploads")
EXPORTS_DIR = env_target("EXPORTS_DIR", RUNTIME_ROOT / "exports")
DB_FILE = DATA_DIR / "imports.db"


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

    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    output = output_dir / f"data-converter-backup-{timestamp}.zip"

    manifest = {
        "createdAt": dt.datetime.now().isoformat(timespec="seconds"),
        "includes": ["data/imports.db"],
        # 记录实际取数目录，出问题时可对照 server.py 启动日志的前四行定位
        "source": {
            "dataDir": str(DATA_DIR),
            "uploadsDir": str(UPLOADS_DIR),
            "exportsDir": str(EXPORTS_DIR),
        },
        "warning": (
            "This package may contain database connection settings. Keep it private. "
            "Connection passwords are encrypted with a master key (DC_MASTER_KEY env var or "
            "the data/.secret_key file) that is intentionally NOT included in this backup. "
            "Restoring requires the same key; otherwise saved MySQL passwords cannot be decrypted."
        ),
    }
    if args.include_uploads:
        manifest["includes"].append("uploads")
    if args.include_exports:
        manifest["includes"].append("exports")

    if not DB_FILE.exists():
        raise SystemExit(
            f"Nothing to back up: database not found at {DB_FILE}."
            " Set DATA_DIR if your runtime data lives elsewhere."
        )

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        count = add_path(archive, DB_FILE, "data")
        if count == 0:
            raise SystemExit(f"Nothing to back up: {DB_FILE} could not be added to the archive.")
        if args.include_uploads:
            add_path(archive, UPLOADS_DIR, "uploads")
        if args.include_exports:
            add_path(archive, EXPORTS_DIR, "exports")

    print(output)


if __name__ == "__main__":
    main()

