#!/usr/bin/env bash
# 同步页面端到端走查：起一个**隔离沙箱**服务（自己的 DATA_DIR），跑 Playwright，再收尾。
# 不会碰用户的 runtime/data/imports.db，也不会连生产库（沙箱里只有 dc_sync_src / dc_sync_dst）。
# ⚠️ 端口固定 51983；跑之前确保没别的实例占着（netstat -ano | grep 51983）。
set -u
export PATH="/c/Windows/System32:/c/Windows:/usr/bin:/bin:$PATH"

PROJECT="D:/ProjectDevelopment/data-converter-tool"
NODE="C:/Users/Administrator/.workbuddy/binaries/node/versions/22.22.2-6/node.exe"
PY="$PROJECT/.venv/Scripts/python.exe"
WALK="$PROJECT/acceptance/ui_walk_sync.js"

export NODE_PATH="C:/Users/Administrator/.workbuddy/binaries/node/workspace/node_modules"
export CODEBUDDY_SAFE_DELETE_ENABLED=0
export CODEBUDDY_SAFE_DELETE_SANDBOX=0
export PORT=51983

SANDBOX="$(mktemp -d /tmp/sync_ui_XXXXXX)"
export DATA_DIR="$SANDBOX/data"

# 1) 预置两个连接（源 dc_sync_src / 目标 dc_sync_dst）
"$PY" - <<PY
import sys
sys.path.insert(0, r"$PROJECT")
import server
now = server.now_text()
with server.connect_db() as conn:
    for cid, name, db in [("src", "源库 dc_sync_src", "dc_sync_src"),
                          ("dst", "目标库 dc_sync_dst", "dc_sync_dst")]:
        conn.execute(
            "insert or replace into _db_connections (id,name,db_type,host,port,user_name,"
            "password,db_name,charset,ssl_enabled,ssl_ca,ssl_cert,ssl_key,created_at,updated_at)"
            " values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, name, "mysql", "127.0.0.1", 3306, "root", server.encode_secret("123456"),
             db, "utf8mb4", 0, "", "", "", now, now))
print("connections seeded")
PY

# 2) 起服务
"$PY" "$PROJECT/server.py" > /tmp/sync_server.log 2>&1 &
SRV=$!
sleep 6

# 3) 走查
"$NODE" "$WALK"
CODE=$?

kill $SRV 2>/dev/null
echo "== stopped =="
exit $CODE
