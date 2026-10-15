#!/usr/bin/env bash
# 作业 / 定时任务「防重复提交 + 对话框状态重置」走查（对应验收报告 Q-002 / Q-003）。
# 起一个**隔离沙箱**服务（自己的 DATA_DIR），跑 Playwright，再收尾。
# 不会碰用户的 runtime/data/imports.db，也不会连生产库。
# ⚠️ 端口固定 51984；跑之前确认没别的实例占着（netstat -ano | grep 51984）。
set -u
export PATH="/c/Windows/System32:/c/Windows:/usr/bin:/bin:$PATH"

PROJECT="D:/ProjectDevelopment/data-converter-tool"
NODE="C:/Users/Administrator/.workbuddy/binaries/node/versions/22.22.2-6/node.exe"
PY="$PROJECT/.venv/Scripts/python.exe"
WALK="$PROJECT/acceptance/ui_walk_jobs_dup.js"

export NODE_PATH="C:/Users/Administrator/.workbuddy/binaries/node/workspace/node_modules"
export CODEBUDDY_SAFE_DELETE_ENABLED=0
export CODEBUDDY_SAFE_DELETE_SANDBOX=0
export PORT=51984

SANDBOX="$(mktemp -d /tmp/jobs_dup_XXXXXX)"
export DATA_DIR="$SANDBOX/data"

# 1) 预置 1 个作业 + 1 个定时任务
#    定时任务的 startAt 故意放在 **10 分钟后**、间隔 600 秒 ——
#    否则调度线程（每 5 秒扫一次）可能在走查过程中真的触发一次作业执行。
"$PY" - <<PY
import sys, datetime as dt
sys.path.insert(0, r"$PROJECT")
import server

job = server.save_job({
    "name": "走查-防重复-源作业",
    "steps": [{
        "id": "s1", "name": "空跑一步", "type": "query", "enabled": True,
        "continueOnError": False,
        "config": {"targetDbType": "sqlite", "sql": "select 1"},
    }],
})
start = (dt.datetime.now() + dt.timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
server.save_schedule({
    "name": "走查-防重复-定时",
    "jobId": job["id"],
    "enabled": True,
    "startAt": start,
    "endAt": "2099-12-31 23:59:59",
    "rule": {"mode": "interval", "amount": 600, "unit": "seconds"},
    "logRetentionDays": 3,
})
print("seeded: job=%s startAt=%s" % (job["id"], start))
PY

# 2) 起服务
"$PY" "$PROJECT/server.py" > /tmp/jobs_dup_server.log 2>&1 &
SRV=$!
sleep 6

# 3) 走查
export BASE_URL="http://127.0.0.1:51984"
"$NODE" "$WALK"
CODE=$?

kill $SRV 2>/dev/null
echo "== stopped (sandbox: $SANDBOX) =="
exit $CODE
