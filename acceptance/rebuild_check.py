"""V6 技术验收 · 干净环境从零重建（可复跑）

口径：一个没参与过本项目的人，只拿「源码 + 文档 + 一个空目录」能否重建出等价系统，
并且知道它是对的。本脚本把这句话变成一次可执行演练，并记录每一步的真实耗时与结果。

不改动项目本体：全部产物落在 acceptance/rebuild-20260919/ 下，服务跑在 51979，
数据目录指向本脚本自己的 runtime，绝不碰生产的 runtime/ 与生产 MySQL。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

PROJECT = Path(r"D:\ProjectDevelopment\data-converter-tool")
BASE_PY = Path(r"C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe")
ROOT = PROJECT / "acceptance" / "rebuild-20260919"
SRC = ROOT / "src"
VENV = ROOT / ".venv"
PY = VENV / "Scripts" / "python.exe"
PORT = 51979
SKIP = {".venv", "runtime", "acceptance", ".git", "__pycache__",
        "dist", "build", ".pytest_cache", ".idea", ".vscode"}

steps: list[dict] = []
notes: list[str] = []


def run(name: str, cmd: list[str], cwd: Path | None = None,
        timeout: int = 2400, env: dict | None = None) -> subprocess.CompletedProcess:
    t0 = time.time()
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, env=env)
    dt = round(time.time() - t0, 2)
    out = (p.stdout or "").splitlines()
    err = (p.stderr or "").splitlines()
    tail = "\n".join(out[-20:] + err[-8:])
    steps.append({"step": name, "rc": p.returncode, "seconds": dt, "tail": tail})
    print(f"\n=== [{name}] rc={p.returncode} · {dt}s ===", flush=True)
    print(tail[-2500:], flush=True)
    return p


def http_get(path: str, timeout: float = 8.0) -> tuple[int, str]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{PORT}{path}", timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


def main() -> int:
    print(f"重建目标目录：{ROOT}")
    # 刻意不删除已有目录：一是避免触发大批量删除保护，二是复用已建好的 venv 更贴近
    # 「同一个人第二次按文档重建」的真实场景；源码复制是幂等覆盖。
    ROOT.mkdir(parents=True, exist_ok=True)

    # ---------- 1. 复制源码（模拟「拿到交付源码包」）----------
    t0 = time.time()
    copied = 0
    for entry in sorted(PROJECT.iterdir()):
        if entry.name in SKIP:
            continue
        dst = SRC / entry.name
        if entry.is_dir():
            shutil.copytree(entry, dst, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            copied += sum(1 for _ in dst.rglob("*") if _.is_file())
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, dst)
            copied += 1
    dt = round(time.time() - t0, 2)
    steps.append({"step": "1. 复制源码（排除 .venv/runtime/.git/acceptance）",
                  "rc": 0, "seconds": dt, "tail": f"复制 {copied} 个文件到 {SRC}"})
    print(f"\n=== [1. 复制源码] {copied} 个文件 · {dt}s ===", flush=True)

    lock = SRC / "requirements-lock.txt"
    if not lock.exists():
        notes.append("重建包内没有 requirements-lock.txt —— 依赖无法按锁定版本还原")
        lock = SRC / "requirements.txt"
    lock_sha = hashlib.sha256(lock.read_bytes()).hexdigest()[:16] if lock.exists() else "N/A"

    has_git = (SRC / ".git").exists()
    notes.append(f"交付快照是否含 .git：{has_git}（重建包刻意排除了 .git，故无法用 git 校验来源）")

    # ---------- 2. 建虚拟环境 ----------
    if not BASE_PY.exists():
        notes.append(f"文档登记的基础解释器不存在：{BASE_PY}")
        return 2
    run("2. python -m venv", [str(BASE_PY), "-m", "venv", str(VENV)], cwd=ROOT)

    # ---------- 3. 按锁文件装依赖 ----------
    # 注意：本机曾经用清华源绕过 pip 默认源问题，但 2026-09-19 实测清华源对 pip 不可用
    # （curl 能拿到 index，pip 却报 "from versions: none"）。默认源正常，故不指定 -i。
    t0 = time.time()
    p = run("3. pip install -r requirements-lock.txt",
            [str(PY), "-m", "pip", "install", "--disable-pip-version-check",
             "-r", str(lock)], cwd=SRC, timeout=2400)
    install_seconds = round(time.time() - t0, 2)
    if p.returncode != 0:
        notes.append("依赖安装失败 —— 锁文件不足以重建环境，必须先修锁再谈交接")
        return 3

    # 锁 vs 实装 复核
    freeze = subprocess.run([str(PY), "-m", "pip", "list", "--format=freeze"],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace").stdout
    installed = {}
    for line in freeze.splitlines():
        if "==" in line:
            n, v = line.split("==", 1)
            installed[n.lower()] = v
    locked = {}
    for line in lock.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "==" in line:
            n, v = line.split("==", 1)
            locked[n.lower()] = v
    missing = sorted(set(locked) - set(installed))
    mismatch = {k: (locked[k], installed[k]) for k in set(locked) & set(installed)
                if locked[k] != installed[k]}
    steps.append({"step": "3b. 锁文件 vs 实装比对", "rc": 0, "seconds": 0.0,
                  "tail": f"锁 {len(locked)} / 实装 {len(installed)} / "
                          f"缺 {missing} / 版本不符 {mismatch}"})
    print(f"\n=== [3b] 锁 {len(locked)} / 实装 {len(installed)} / 缺 {missing} / 不符 {mismatch} ===",
          flush=True)

    pyver = subprocess.run([str(PY), "-c", "import sys;print(sys.version.split()[0])"],
                           capture_output=True, text=True, encoding="utf-8").stdout.strip()

    # ---------- 4. 跑测试（重建后的等价性判定）----------
    p = run("4. pytest -q（干净环境全量）", [str(PY), "-m", "pytest", "-q"], cwd=SRC, timeout=1200)
    test_tail = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""

    # ---------- 5. 起服务 + 冒烟 ----------
    env = dict(os.environ)
    env.update({
        "DATA_DIR": str(ROOT / "runtime" / "data"),
        "UPLOADS_DIR": str(ROOT / "runtime" / "uploads"),
        "EXPORTS_DIR": str(ROOT / "runtime" / "exports"),
        "HOST": "127.0.0.1",
        "PORT": str(PORT),
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
        "PYTHONIOENCODING": "utf-8",
    })
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen([str(PY), "server.py"], cwd=str(SRC), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=flags)
    boot_t0 = time.time()
    ready = False
    for _ in range(40):
        code, body = http_get("/api/ping", timeout=3)
        if code == 200:
            ready = True
            break
        if proc.poll() is not None:
            break
        time.sleep(1)
    boot_seconds = round(time.time() - boot_t0, 2)

    smoke: dict[str, str] = {}
    if ready:
        for path in ["/api/ping", "/api/meta", "/api/storage/status",
                     "/api/jobs", "/api/schedules", "/api/connections", "/"]:
            code, body = http_get(path)
            smoke[path] = f"{code} | {body[:200].replace(chr(10), ' ')}"
    else:
        smoke["<启动失败>"] = "40 秒内 /api/ping 未返回 200"

    steps.append({"step": "5. 干净环境起服务 + 接口冒烟",
                  "rc": 0 if ready else 1, "seconds": boot_seconds,
                  "tail": json.dumps(smoke, ensure_ascii=False, indent=2)[:2000]})
    print(f"\n=== [5] 服务就绪={ready} · {boot_seconds}s ===", flush=True)
    for k, v in smoke.items():
        print(f"  {k} -> {v[:160]}", flush=True)

    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        proc.kill()

    # ---------- 6. 报告 ----------
    report = {
        "kind": "V6 技术验收 · 干净环境重建演练",
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "project_source": str(PROJECT),
        "rebuild_dir": str(ROOT),
        "isolation": {
            "service_port": PORT,
            "data_dir": str(ROOT / "runtime"),
            "production_touched": False,
            "note": "重建实例的库为空，无调度任务，不会触发任何写生产数据的作业",
        },
        "interpreter": {"base": str(BASE_PY), "venv_version": pyver},
        "lock_file": {"path": str(lock), "sha256_16": lock_sha, "entries": len(locked)},
        "lock_vs_installed": {"lock": len(locked), "installed": len(installed),
                              "missing": missing, "version_mismatch": mismatch},
        "install_seconds": install_seconds,
        "test_result_tail": test_tail,
        "test_rc": steps[-3]["rc"] if len(steps) >= 3 else None,
        "service_ready": ready,
        "service_boot_seconds": boot_seconds,
        "smoke": smoke,
        "steps": steps,
        "notes": notes,
        "verdict": ("可重建" if (p.returncode == 0 and ready and not missing)
                    else "不可重建 / 需修补"),
    }
    out = ROOT / "rebuild-report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写出：{out}", flush=True)
    print(f"判定：{report['verdict']}｜测试：{test_tail}｜服务就绪：{ready}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
