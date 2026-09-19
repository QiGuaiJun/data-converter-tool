from __future__ import annotations

import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path


APP_NAME = "DataConverterTool"
DEFAULT_PORT = 51978
MAX_PORT = 52050


def runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def find_free_port(preferred: int = DEFAULT_PORT) -> int:
    for port in range(preferred, MAX_PORT + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError(f"No free local port found between {preferred} and {MAX_PORT}.")


def runtime_data_root(root: Path) -> Path:
    """运行数据根目录：与 server.py 的 runtime_path() / watchdog 用同一套规则。

    2026-09-17 起运行数据统一落在源码（或 exe）目录内的 runtime/ 下，整个项目
    就是一个文件夹；同目录下已有旧版的 data/ 时沿用旧目录，避免出现两份数据。
    """
    nested = root / "runtime"
    if not nested.exists() and (root / "imports.db").exists():
        return root  # 兼容早期「exe 同级直接放数据」的桌面版布局
    return nested


def prepare_environment() -> tuple[Path, int]:
    data_root = runtime_data_root(runtime_root())
    data_dir = data_root / "data"
    uploads_dir = data_root / "uploads"
    exports_dir = data_root / "exports"
    logs_root = data_root / "logs"

    for path in (data_dir, uploads_dir, exports_dir, logs_root):
        path.mkdir(parents=True, exist_ok=True)

    port = int(os.environ.get("PORT") or find_free_port())
    os.environ.setdefault("HOST", "127.0.0.1")
    os.environ["PORT"] = str(port)
    os.environ.setdefault("DATA_DIR", str(data_dir))
    os.environ.setdefault("UPLOADS_DIR", str(uploads_dir))
    os.environ.setdefault("EXPORTS_DIR", str(exports_dir))
    os.environ.setdefault("APP_AUTH_ENABLED", "false")

    return logs_root, port


def open_browser_later(port: int) -> None:
    def run() -> None:
        time.sleep(1.2)
        webbrowser.open(f"http://127.0.0.1:{port}/")

    threading.Thread(target=run, daemon=True).start()


def write_crash_log(logs_root: Path) -> None:
    crash_log = logs_root / "desktop-launcher-error.log"
    crash_log.write_text(traceback.format_exc(), encoding="utf-8")


def main() -> None:
    logs_root, port = prepare_environment()
    open_browser_later(port)

    try:
        import server

        server.main()
    except Exception:
        write_crash_log(logs_root)
        raise


if __name__ == "__main__":
    main()
