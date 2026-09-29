#!/usr/bin/env bash
# 安装/更新健康检查看门狗（在服务器上以 root 执行）
#
#   sudo bash deploy/install-watchdog.sh
#
# 幂等：重复执行只会覆盖脚本与 unit 文件并重载，不会重复登记。
#
# 装完后：
#   systemctl list-timers dc-watchdog.timer     # 看下次执行时间
#   journalctl -t dc-watchdog -n 20             # 看看门狗日志
#   ls -l /opt/dc/runtime/logs/watchdog-hang-*  # 卡死现场（如果有过）

set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-data-converter}"
TARGET_BIN="${TARGET_BIN:-/usr/local/bin/dc-watchdog.sh}"
SCRIPT_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/dc-watchdog.sh"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "需要 root 权限：sudo bash $0" >&2
  exit 1
fi

if [[ ! -f "$SCRIPT_SRC" ]]; then
  echo "找不到 $SCRIPT_SRC（请在仓库的 deploy/ 目录下执行）" >&2
  exit 1
fi

echo "==> 安装看门狗脚本到 $TARGET_BIN"
install -m 755 "$SCRIPT_SRC" "$TARGET_BIN"

echo "==> 写入 systemd unit"
cat > /etc/systemd/system/dc-watchdog.service <<EOF
[Unit]
Description=Data Converter healthcheck watchdog (detects hung service and restarts it)

[Service]
Type=oneshot
# 卡死现场要写盘、重启要等复核，给足时间
TimeoutStartSec=120
ExecStart=$TARGET_BIN
EOF

cat > /etc/systemd/system/dc-watchdog.timer <<'EOF'
[Unit]
Description=Run Data Converter healthcheck every 2 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
AccuracySec=15s
Unit=dc-watchdog.service

[Install]
WantedBy=timers.target
EOF

echo "==> 重载并启用定时器"
systemctl daemon-reload
systemctl enable --now dc-watchdog.timer >/dev/null

echo
echo "==> 立即跑一次做自检"
if "$TARGET_BIN"; then
  echo "自检通过：应用当前健康（或已被自动拉回）"
else
  echo "⚠️ 自检返回非 0 —— 脚本自身可能有问题，请查看：journalctl -t dc-watchdog -n 30" >&2
fi

echo
systemctl list-timers dc-watchdog.timer --no-pager | head -3
echo
echo "完成。看门狗每 2 分钟探一次 http://127.0.0.1:<PORT>/api/ping："
echo "  · 连续 2 次失败 → 先把现场（进程/内存/systemd 与应用日志）存到"
echo "    /opt/dc/runtime/logs/watchdog-hang-<时间>.log，再重启 $SERVICE_NAME"
echo "  · 重启 15 秒后自动复核，结果写进 journal（journalctl -t dc-watchdog）"
