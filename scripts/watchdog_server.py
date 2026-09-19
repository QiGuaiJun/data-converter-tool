"""data-converter-tool 服务守护进程：常驻循环，检测 51978，未运行则拉起。

用途：防止系统空闲睡眠 / 会话结束导致服务进程被杀后无人重启。
自启方式：启动文件夹里的 DataToolWatchdog.vbs 在用户登录时以隐藏窗口调用本脚本
（pythonw.exe scripts/watchdog_server.py）。也可手动运行。

常驻语义（本脚本不是「检查一次就退出」的一次性脚本）：
  * 每 CHECK_INTERVAL 秒探测一次 51978；
  * 探测失败就拉起 server.py，并等待它通过 /api/ping 就绪；
  * 退出方式：Ctrl+C，或在 data/ 下创建停止标志文件 watchdog.stop。

单实例：用 Windows 命名互斥体保证同一时间只有一个守护进程在跑，第二个实例
启动即退出（CreateMutexW 返回既有句柄且 GetLastError()==ERROR_ALREADY_EXISTS），
纯标准库 ctypes，不引入新依赖。

识别「是不是我们的服务」：仅 TCP connect 成功不代表目标进程是本项目——端口可能
被别的程序占用，那种情况下守护进程会误以为「服务已在运行」而永远不拉起真正的服务。
因此 connect 成功后还要 GET /api/ping 并校验响应里 "ok" 为 true。

日志：data/watchdog.log（data/ 已 gitignore，可安全写）。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOST = "127.0.0.1"
PORT = 51978
BASE = Path(__file__).resolve().parent.parent  # 项目根目录
PYTHON = BASE / ".venv" / "Scripts" / "python.exe"


def _resolve_data_dir() -> Path:
    """运行数据目录：必须与 server.py 的 runtime_path("data") 用同一套规则。

    守护进程按这个路径写 server.log / watchdog.log，并在这里找停止标志文件。
    两处实现一旦不一致，日志会落到另一个目录，"服务为什么没起来"就无从查起。
    2026-09-17 起运行数据默认在项目内的 runtime/data。
    """
    raw = os.environ.get("DATA_DIR", "").strip()
    if raw:
        return Path(raw)
    return BASE / "runtime" / "data"


DATA_DIR = _resolve_data_dir()
WATCHDOG_LOG = DATA_DIR / "watchdog.log"
SERVER_LOG = DATA_DIR / "server.log"
STOP_FLAG = DATA_DIR / "watchdog.stop"

CHECK_INTERVAL = 10  # 秒：两轮探测之间的间隔
STARTUP_TIMEOUT = 30  # 秒：拉起后等待 /api/ping 就绪的上限
PING_PATH = "/api/ping"
MUTEX_NAME = f"data-converter-tool-watchdog-{PORT}"

# 本机默认要直连，绝不能被环境里的 HTTP_PROXY 兜走（否则 localhost 探测恒失败）。
_PING_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with WATCHDOG_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


class SingleInstanceLock:
    """基于 Windows 命名互斥体的单实例锁（纯 stdlib ctypes）。

    CreateMutexW 是「创建或打开」语义：同名互斥体已存在时它会返回既有句柄，并把
    GetLastError() 置为 ERROR_ALREADY_EXISTS(183)，据此判断是否已有实例在跑。
    句柄必须在进程存活期间一直持有（提前 CloseHandle 会让锁消失）。
    """

    ERROR_ALREADY_EXISTS = 183

    def __init__(self, name: str) -> None:
        self._name = name
        self._kernel32 = None
        self._handle: int | None = None

    def acquire(self) -> bool:
        """抢到锁返回 True；已有实例在跑返回 False；非 Windows 平台视为可运行。"""
        if os.name != "nt":
            return True
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.CreateMutexW(None, False, self._name)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateMutexW 调用失败")
        self._kernel32 = kernel32
        if ctypes.get_last_error() == self.ERROR_ALREADY_EXISTS:
            # 已有实例持有同名互斥体：把自己拿到的句柄关掉，不参与竞争。
            kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is not None and self._kernel32 is not None:
            try:
                self._kernel32.CloseHandle(self._handle)
            finally:
                self._handle = None


def port_is_open(timeout: float = 2.0) -> bool:
    """51978 上是否有进程在监听（不区分是不是我们的服务）。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((HOST, PORT))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def service_ping(timeout: float = 3.0) -> dict[str, object] | None:
    """请求 /api/ping；拿到 {"ok": true} 才返回响应体，否则返回 None。"""
    request = urllib.request.Request(f"http://{HOST}:{PORT}{PING_PATH}", method="GET")
    try:
        with _PING_OPENER.open(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    try:
        payload = json.loads(body or "{}")
    except ValueError:
        return None
    if isinstance(payload, dict) and payload.get("ok") is True:
        return payload
    return None


def is_running() -> bool:
    """True 仅当 51978 上跑的是**本项目**的服务。

    只做 TCP connect 不够：端口可能被别的程序占用，那时守护进程会误判「已在运行」
    而永远不拉起真正的服务。因此 connect 成功后还要 /api/ping 返回 ok=true。
    """
    if not port_is_open():
        return False
    return service_ping() is not None


def start_server() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PORT"] = str(PORT)
    # 显式传给子进程：让 server.py 的落点与上面算出来的 DATA_DIR 逐字一致，
    # 不依赖两边各算一次还恰好算得一样。
    env["DATA_DIR"] = str(DATA_DIR)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
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


def wait_until_ready(seconds: int = STARTUP_TIMEOUT) -> bool:
    """拉起后轮询 /api/ping，最多等 seconds 秒。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(1)
        if is_running():
            return True
    return False


def run_loop(interval: int = CHECK_INTERVAL) -> int:
    """常驻主循环：探测 -> 未运行则拉起 -> 间隔等待，直到 Ctrl+C 或停止标志。"""
    _log(
        f"守护启动：每 {interval} 秒探测 http://{HOST}:{PORT}{PING_PATH}；"
        f"停止方式 Ctrl+C 或创建 {STOP_FLAG}"
    )
    recoveries = 0
    try:
        while True:
            if STOP_FLAG.exists():
                _log(f"检测到停止标志文件 {STOP_FLAG}，守护退出。")
                return 0
            if is_running():
                time.sleep(interval)
                continue
            if port_is_open():
                # 端口被别的进程占着：等它释放，绝不抢占（抢了也起不来）。
                _log(f"{PORT} 已被其他进程占用，且 {PING_PATH} 不是本项目服务，等待其释放。")
                time.sleep(interval)
                continue
            _log("探测到服务未运行，正在拉起...")
            try:
                start_server()
            except Exception as exc:  # noqa: BLE001 - 守护必须活着，单次失败不能退出
                _log(f"启动服务失败：{exc}")
                time.sleep(interval)
                continue
            if wait_until_ready():
                recoveries += 1
                _log(f"服务已成功启动并监听（本进程累计拉起 {recoveries} 次）。")
            else:
                _log(f"服务启动超时（{STARTUP_TIMEOUT} 秒未通过 {PING_PATH}），请检查 {SERVER_LOG}。")
            time.sleep(interval)
    except KeyboardInterrupt:
        _log("收到 Ctrl+C，守护退出。")
        return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="data-converter-tool 服务守护进程（常驻）")
    parser.add_argument("--interval", type=int, default=CHECK_INTERVAL, help=f"探测间隔秒数，默认 {CHECK_INTERVAL}")
    parser.add_argument(
        "--once",
        action="store_true",
        help="只检查一轮：未运行则拉起并等待就绪，然后退出（兼容旧的一次性用法）",
    )
    return parser.parse_args(argv)


def run_once() -> int:
    """单次检查：服务未运行则拉起并等待就绪，然后退出（不做常驻）。"""
    if is_running():
        _log("服务已在运行，跳过启动。")
        return 0
    _log("检测到服务未运行，正在拉起...")
    try:
        start_server()
    except Exception as exc:  # noqa: BLE001
        _log(f"启动服务失败：{exc}")
        return 1
    if wait_until_ready():
        _log("服务已成功启动并监听。")
        return 0
    _log(f"服务启动超时（{STARTUP_TIMEOUT} 秒未通过 {PING_PATH}），请检查 {SERVER_LOG}。")
    return 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    # --once 是无状态的一次性检查，不需要也不能被常驻守护的互斥体挡住；
    # 单实例保护针对的是「同时跑起两个守护进程」。
    if args.once:
        return run_once()

    lock = SingleInstanceLock(MUTEX_NAME)
    try:
        acquired = lock.acquire()
    except OSError as exc:
        _log(f"单实例锁创建失败：{exc}；继续以无锁模式运行。")
        acquired = True
    if not acquired:
        _log("已有守护进程在运行（命名互斥体已存在），本实例退出，不会重复拉起服务。")
        return 0

    try:
        return run_loop(max(args.interval, 1))
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
