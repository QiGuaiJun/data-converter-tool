"""data-converter-tool 服务守护进程：检测 51978 是否监听，未运行则拉起。

用途：防止系统空闲睡眠 / 会话结束导致服务进程被杀后无人重启。
自启方式：启动文件夹里的 DataTool服务守护.vbs 在用户登录时以隐藏窗口调用本脚本
（pythonw.exe scripts/watchdog_server.py）。也可手动运行。

幂等：服务已在运行则直接跳过；每次动作都写 data/watchdog.log 便于排查。
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

HOST = "127.0.0.1"
PORT = 51978
BASE = Path(__file__).resolve().parent.parent  # 项目根目录
PYTHON = BASE / ".venv" / "Scripts" / "python.exe"
DATA_DIR = BASE / "data"
WATCHDOG_LOG = DATA_DIR / "watchdog.log"
SERVER_LOG = DATA_DIR / "server.log"


def _log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with WATCHDOG_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def is_running() -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    try:
        sock.connect((HOST, PORT))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def start_server() -> None:
    env = dict(os.environ)
    env["PORT"] = str(PORT)
    flags = (
        getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )
    log_fh = SERVER_LOG.open("a", encoding="utf-8")
    try:
        subprocess.Popen(
            [str(PYTHON), "server.py"],
            cwd=str(BASE),
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
    finally:
        log_fh.close()


def main() -> int:
    if is_running():
        _log("服务已在运行，跳过启动。")
        return 0
    _log("检测到服务未运行，正在拉起...")
    try:
        start_server()
    except Exception as exc:
        _log(f"启动服务失败：{exc}")
        return 1
    for _ in range(20):
        time.sleep(1)
        if is_running():
            _log("服务已成功启动并监听。")
            return 0
    _log("服务启动超时（20 秒未监听），请检查 data/server.log。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
