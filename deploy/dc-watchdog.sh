#!/usr/bin/env bash
# 数据导表工具 —— 健康检查看门狗（跑在服务器上）
#
# 为什么需要它：
#   systemd 的 `Restart=always` **只在进程退出时**才触发。应用如果"卡死但不退出"
#   （GIL 被占、线程死锁、内存吃满在换页…），systemd 完全看不见，服务会一直挂着，
#   表现为 nginx 等满 60 秒后返回 504 —— 2026-09-29 就是这么掉线的。
#   本脚本从**外部**探测 /api/ping，连续失败就重启服务，并在重启前留存现场日志。
#
# 由 dc-watchdog.timer 每 2 分钟调用一次（见 deploy/install-watchdog.sh）。

set -u

SERVICE_NAME="${SERVICE_NAME:-data-converter}"
APP_DIR="${APP_DIR:-/opt/dc/app}"
ENV_FILE="${ENV_FILE:-$APP_DIR/.env}"
LOG_DIR="${LOG_DIR:-/opt/dc/runtime/logs}"
STATE_FILE="${STATE_FILE:-$LOG_DIR/watchdog.state}"
TIMEOUT="${TIMEOUT:-20}"      # 单次探测超时（秒）；nginx 上游超时是 60s，这里要明显更短
MAX_FAILS="${MAX_FAILS:-2}"   # 连续失败几次才重启，避免一次抖动就重启
TAG="dc-watchdog"

# 端口优先取环境变量，其次读应用的 .env，最后回落 51978
if [[ -z "${PORT:-}" ]]; then
  PORT="$(grep -E '^[[:space:]]*PORT=' "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2 | tr -d '[:space:]')"
fi
PORT="${PORT:-51978}"

mkdir -p "$LOG_DIR"

count_fails() {
  local raw
  raw="$(cat "$STATE_FILE" 2>/dev/null || echo 0)"
  [[ "$raw" =~ ^[0-9]+$ ]] || raw=0
  echo "$raw"
}

# 探测：-f 让 HTTP 4xx/5xx 也算失败；--max-time 防止它自己也卡住
if curl -fsS --max-time "$TIMEOUT" "http://127.0.0.1:$PORT/api/ping" >/dev/null 2>&1; then
  if [[ "$(count_fails)" != "0" ]]; then
    logger -t "$TAG" "健康检查恢复正常（端口 $PORT）"
  fi
  echo 0 > "$STATE_FILE"
  exit 0
fi

fails=$(( $(count_fails) + 1 ))
echo "$fails" > "$STATE_FILE"
logger -t "$TAG" "健康检查失败第 $fails/$MAX_FAILS 次（127.0.0.1:$PORT，超时 ${TIMEOUT}s）"

if [[ "$fails" -lt "$MAX_FAILS" ]]; then
  exit 0
fi

# ---- 重启前留存现场：下次才能查明"到底卡在哪"
STAMP="$(date +%Y%m%d-%H%M%S)"
DUMP="$LOG_DIR/watchdog-hang-$STAMP.log"
{
  echo "===== 检测到应用无响应，准备重启：$STAMP ====="
  echo "--- 服务状态 ---"
  systemctl status "$SERVICE_NAME" --no-pager -n 15 2>&1 | head -25
  echo
  echo "--- 进程快照（含内存/CPU/线程数）---"
  ps -eo pid,ppid,stat,pcpu,pmem,nlwp,etime,args --sort=-pmem 2>/dev/null | head -12
  echo
  echo "--- 内存与磁盘 ---"
  free -m 2>/dev/null | head -2
  df -h / 2>/dev/null | tail -1
  echo
  echo "--- systemd 日志最后 120 行 ---"
  journalctl -u "$SERVICE_NAME" --no-pager -n 120 2>/dev/null | tail -120
  echo
  echo "--- 应用自身日志最后 120 行 ---"
  tail -120 "$LOG_DIR/service.log" 2>/dev/null
} > "$DUMP" 2>&1

logger -t "$TAG" "连续 $fails 次无响应 → 重启 $SERVICE_NAME，现场已存 $DUMP"
systemctl restart "$SERVICE_NAME"
echo 0 > "$STATE_FILE"

# 重启后给应用一点启动时间再复核，结果同样写日志
sleep 15
if curl -fsS --max-time "$TIMEOUT" "http://127.0.0.1:$PORT/api/ping" >/dev/null 2>&1; then
  logger -t "$TAG" "重启后复核通过，服务已恢复"
else
  logger -t "$TAG" "⚠️ 重启后仍然无响应，请人工介入（现场日志 $DUMP）"
fi
