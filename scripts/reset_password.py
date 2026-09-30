#!/usr/bin/env python
"""重置某个账号的登录密码 —— 应急通道（break-glass）。

**为什么需要它**：`bootstrap_admin_from_env()` 只在 `_users` 为空时建首个管理员。
所以一旦「唯一的管理员忘了密码」，界面上就没有任何入口能挽回 —— 谁也进不去，
连"让管理员重置"这条路也断了。这个脚本就是那时唯一的出口。

**安全性**：它需要能在这台机器上执行命令（服务器 SSH / 本机）。
换句话说，跑得动它的人本来就已经具备改数据库的能力，不额外扩大攻击面。
它不做任何网络暴露，也不写入登录页。

用法
----
列出所有账号（确认用户名）::

    ./.venv/Scripts/python.exe scripts/reset_password.py

重置（交互式输入新密码，不回显）::

    ./.venv/Scripts/python.exe scripts/reset_password.py admin

重置（一次性给出新密码，适合脚本化）::

    ./.venv/Scripts/python.exe scripts/reset_password.py admin --password 'NewPass!2026'

云端服务器::

    cd /opt/dc/app && /opt/dc/venv/bin/python scripts/reset_password.py admin

注意：重置会同时清除登录失败计数与锁定状态，并**作废该账号的全部已登录会话**。
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_env_file() -> str | None:
    """像 systemd 的 EnvironmentFile 那样先加载应用的 .env。

    必须做：云端部署把 `DATA_DIR` 指到 `/opt/dc/runtime/data`（在应用目录**之外**），
    直接跑脚本时环境变量不存在，会去连 `ROOT/runtime/data/imports.db` —— 那是另一个库，
    表现为"账号不存在"，很容易误判成账号被删了。
    """
    for candidate in (os.environ.get("DC_ENV_FILE"), ROOT / ".env"):
        if not candidate:
            continue
        path = Path(candidate)
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            # 已显式设置的以环境为准（便于临时覆盖）
            os.environ.setdefault(key, value)
        return str(path)
    return None


def main() -> int:
    env_used = load_env_file()

    import server  # noqa: PLC0415  —— 必须在加载 .env 之后导入（DB_PATH 在导入时确定）

    parser = argparse.ArgumentParser(description="重置账号密码（应急通道）")
    parser.add_argument("username", nargs="?", help="要重置的账号名称；不给则只列出所有账号")
    parser.add_argument("--password", help="新密码（至少 8 位）；不给则交互式输入")
    args = parser.parse_args()

    print(f"元数据库 DB_PATH = {server.DB_PATH}")
    if env_used:
        print(f"已加载环境文件  = {env_used}")
    else:
        print("提示：未找到 .env，按默认路径取库；若提示账号不存在，请先确认是否连对了库。")
    print()

    if not args.username:
        rows = server.list_users()
        if not rows:
            print("当前库里没有任何账号。")
            return 0
        print(f"共 {len(rows)} 个账号：")
        for row in rows:
            state = "启用" if row.get("enabled") else "停用"
            print(f"  - {row.get('username'):<20} 角色={row.get('role'):<9} {state}")
        print("\n要重置密码，请把账号名称作为参数再跑一次。")
        return 0

    user = server.find_user(args.username)
    if not user:
        print(f"找不到账号：{args.username}")
        print("（用不带参数的方式运行可以看到全部账号）")
        return 2

    password = args.password
    if not password:
        first = getpass.getpass(f"为 {args.username} 输入新密码（至少 8 位，输入时不显示）：")
        again = getpass.getpass("再输一次确认：")
        if first != again:
            print("两次输入不一致，已取消。")
            return 2
        password = first

    try:
        # 复用管理端同一套逻辑（哈希、清失败计数与锁定、作废旧会话），
        # 不另写一份，避免两处行为漂移。
        _user, changes = server.update_user({"id": str(user["id"]), "password": password}, None)
    except ValueError as error:
        print(f"重置失败：{error}")
        return 2

    print(f"已重置账号「{args.username}」的密码。动作记录：{'、'.join(changes) or '（无变化）'}")
    print("请用新密码登录，并在登录后立即在右上角「改密码」里改成你自己记得住的密码。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
