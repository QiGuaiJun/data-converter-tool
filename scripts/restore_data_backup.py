from __future__ import annotations

import argparse
import os
import shutil
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def target_path(name: str, fallback: Path) -> Path:
    return Path(os.environ.get(name, str(fallback))).resolve()


def safe_extract(archive: zipfile.ZipFile, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for member in archive.infolist():
        if member.is_dir():
            continue
        destination = (target / member.filename).resolve()
        try:
            destination.relative_to(target)
        except ValueError as exc:
            raise RuntimeError(f"Unsafe archive path: {member.filename}") from exc
        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as src, destination.open("wb") as dst:
            shutil.copyfileobj(src, dst)


def copy_tree_contents(source: Path, target: Path) -> None:
    if not source.exists():
        return
    target.mkdir(parents=True, exist_ok=True)
    for item in source.rglob("*"):
        if item.is_file():
            destination = target / item.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore a deployment data backup package.")
    parser.add_argument("backup", help="Path to data-converter-backup-*.zip")
    parser.add_argument("--target-root", default="", help="Temporary extraction root. Defaults to project root.")
    args = parser.parse_args()

    backup = Path(args.backup).resolve()
    if not backup.exists():
        raise SystemExit(f"Backup not found: {backup}")

    extract_root = Path(args.target_root).resolve() if args.target_root else ROOT
    with zipfile.ZipFile(backup, "r") as archive:
        safe_extract(archive, extract_root)

    data_dir = target_path("DATA_DIR", ROOT / "data")
    uploads_dir = target_path("UPLOADS_DIR", ROOT / "uploads")
    exports_dir = target_path("EXPORTS_DIR", ROOT / "exports")

    copy_tree_contents(extract_root / "data", data_dir)
    copy_tree_contents(extract_root / "uploads", uploads_dir)
    copy_tree_contents(extract_root / "exports", exports_dir)

    print(f"Restored backup: {backup}")
    print(f"DATA_DIR: {data_dir}")
    print(f"UPLOADS_DIR: {uploads_dir}")
    print(f"EXPORTS_DIR: {exports_dir}")


if __name__ == "__main__":
    main()

