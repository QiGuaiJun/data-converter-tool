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


def prepare_environment() -> tuple[Path, int]:
    root = runtime_root()
    data_root = root / "data"
    uploads_root = root / "uploads"
    exports_root = root / "exports"
    logs_root = root / "logs"

    for path in (data_root, uploads_root, exports_root, logs_root):
        path.mkdir(parents=True, exist_ok=True)

    port = int(os.environ.get("PORT") or find_free_port())
    os.environ.setdefault("HOST", "127.0.0.1")
    os.environ["PORT"] = str(port)
    os.environ.setdefault("DATA_DIR", str(data_root))
    os.environ.setdefault("UPLOADS_DIR", str(uploads_root))
    os.environ.setdefault("EXPORTS_DIR", str(exports_root))
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
