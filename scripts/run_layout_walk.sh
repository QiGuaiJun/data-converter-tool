#!/usr/bin/env bash
# 布局 / 响应式 / 跨主题 / 跨浏览器 体检（对应验收报告 Q-006 / Q-007 / Q-009）。
# 起一个**隔离沙箱**服务（自己的 DATA_DIR），跑 Playwright，再收尾。
# 不会碰用户的 runtime/data/imports.db，也不会连生产库。
# ⚠️ 端口固定 51985；跑之前确认没别的实例占着（netstat -ano | grep 51985）。
set -u
export PATH="/c/Windows/System32:/c/Windows:/usr/bin:/bin:$PATH"

PROJECT="D:/ProjectDevelopment/data-converter-tool"
NODE="C:/Users/Administrator/.workbuddy/binaries/node/versions/22.22.2-6/node.exe"
PY="$PROJECT/.venv/Scripts/python.exe"
WALK="$PROJECT/acceptance/ui_walk_layout_responsive.js"

export NODE_PATH="C:/Users/Administrator/.workbuddy/binaries/node/workspace/node_modules"
export CODEBUDDY_SAFE_DELETE_ENABLED=0
export CODEBUDDY_SAFE_DELETE_SANDBOX=0
export PORT=51985

SANDBOX="$(mktemp -d /tmp/layout_walk_XXXXXX)"
export DATA_DIR="$SANDBOX/data"

# 1) 预置数据。名字**故意取长的**（中英混排 + 超长），
#    否则"文字截断"这条判据在没有内容的空列表上根本测不出来。
"$PY" - <<PY
import sys, datetime as dt
sys.path.insert(0, r"$PROJECT")
import server

now = server.now_text()
with server.connect_db() as conn:
    for cid, name in [("long", "生产环境订单库（只读副本，华东-上海可用区B）"),
                      ("short", "本地测试库")]:
        conn.execute(
            "insert or replace into _db_connections (id,name,db_type,host,port,user_name,"
            "password,db_name,charset,ssl_enabled,ssl_ca,ssl_cert,ssl_key,created_at,updated_at)"
            " values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, name, "mysql", "127.0.0.1", 3306, "root", server.encode_secret("123456"),
             "lcdp_fe", "utf8mb4", 0, "", "", "", now, now))

job = server.save_job({
    "name": "每天凌晨把华东销售明细导出到共享盘并同步到数据仓库（长名字压测）",
    "steps": [{
        "id": "s1", "name": "空跑一步", "type": "query", "enabled": True,
        "continueOnError": False,
        "config": {"targetDbType": "sqlite", "sql": "select 1"},
    }],
})
start = (dt.datetime.now() + dt.timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
server.save_schedule({
    "name": "每月1号与15号凌晨两点半执行月度对账与库存快照（长名字压测）",
    "jobId": job["id"], "enabled": True, "startAt": start,
    "endAt": "2099-12-31 23:59:59",
    "rule": {"mode": "interval", "amount": 900, "unit": "seconds"},
    "logRetentionDays": 3,
})
print("seeded ok")
PY

# 2) 起服务
"$PY" "$PROJECT/server.py" > /tmp/layout_walk_server.log 2>&1 &
SRV=$!
sleep 6

# 3) 体检
export BASE_URL="http://127.0.0.1:51985"
"$NODE" "$WALK"
CODE=$?

kill $SRV 2>/dev/null
echo "== stopped (sandbox: $SANDBOX) =="
exit $CODE
