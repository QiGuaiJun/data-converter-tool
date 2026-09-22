#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 腾讯云轻量应用服务器 · 一键部署（Ubuntu 24.04 / 26.04 实测）
#
# 作用：把 data-converter-tool 部署到 127.0.0.1:51978，并由 Nginx 以
#       HTTPS(443, 自签证书) 对外提供服务，systemd 常驻 + 开机自启。
#
# 幂等：可重复执行；已存在的代码 / venv / .env / 证书不会覆盖。
#
# 前置（需人工完成，脚本不处理）：
#   1) 已在 MySQL 建库建账号（见 docs/腾讯云部署准备-20260922.md 8.1）
#   2) 证书已存在于 /opt/dc/certs/（无则本脚本会生成一张含 IP SAN 的自签证书）
#   3) 云防火墙已放通 443
#
# 用法（root 执行）：
#   bash tencent-cloud-setup.sh
# 可选环境变量：
#   REPO_URL   源码地址，默认 GitHub 官方仓库
#   PUBLIC_IP  证书 SAN 用的公网 IP，默认自动探测
# ---------------------------------------------------------------------------
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/dc/app}"
VENV_DIR="${VENV_DIR:-/opt/dc/venv}"
RUNTIME_DIR="${RUNTIME_DIR:-/opt/dc/runtime}"
CERT_DIR="${CERT_DIR:-/opt/dc/certs}"
PORT="${PORT:-51978}"
REPO_URL="${REPO_URL:-https://github.com/QiGuaiJun/data-converter-tool.git}"
PIP_INDEX="${PIP_INDEX:-https://mirrors.cloud.tencent.com/pypi/simple}"
SERVICE_NAME="data-converter"

log() { printf '\n[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

[[ $EUID -eq 0 ]] || { echo "请用 root 执行（sudo bash $0）"; exit 1; }

PUBLIC_IP="${PUBLIC_IP:-$(curl -s --max-time 5 ifconfig.me || true)}"
[[ -n "${PUBLIC_IP:-}" ]] || PUBLIC_IP="$(hostname -I | awk '{print $1}')"

log "目标：应用 $APP_DIR ｜ venv $VENV_DIR ｜ 端口 $PORT ｜ 公网 IP $PUBLIC_IP"

# ---------------------------------------------------------------- 1. 系统依赖
log "安装系统依赖（nginx / git / python3-venv / unixodbc）"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx git python3-venv python3-pip unixodbc-dev curl >/dev/null

# ---------------------------------------------------------------- 2. 目录
mkdir -p "$RUNTIME_DIR"/{data,uploads,exports,logs} "$CERT_DIR"

# ---------------------------------------------------------------- 3. 代码
if [[ -d "$APP_DIR/.git" ]]; then
  log "代码已存在，执行 git pull"
  git -C "$APP_DIR" pull --ff-only || log "git pull 失败（离线环境可忽略，继续用现有代码）"
elif [[ -e "$APP_DIR" ]]; then
  # 目录存在但不是 git 仓库：不擅自删除，交回人工确认（避免误删数据）
  log "错误：$APP_DIR 已存在且不是 git 仓库，请先人工处理（备份或移走）后重跑"
  exit 1
else
  log "克隆代码 $REPO_URL"
  git clone --depth 1 "$REPO_URL" "$APP_DIR"
fi

# ---------------------------------------------------------------- 4. venv + 依赖
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  log "创建虚拟环境"
  python3 -m venv "$VENV_DIR"
fi
log "安装 Python 依赖（镜像：$PIP_INDEX）"
"$VENV_DIR/bin/pip" install -q --upgrade pip -i "$PIP_INDEX"
"$VENV_DIR/bin/pip" install -q -r "$APP_DIR/requirements.txt" -i "$PIP_INDEX"

# ---------------------------------------------------------------- 5. .env
ENV_FILE="$APP_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  log "生成 $ENV_FILE（含随机 DC_MASTER_KEY）"
  MASTER_KEY="$("$VENV_DIR/bin/python" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
  cat > "$ENV_FILE" <<EOF
# data-converter-tool 运行配置（由 tencent-cloud-setup.sh 生成）
HOST=127.0.0.1
PORT=$PORT
APP_AUTH_ENABLED=true
DC_MASTER_KEY=$MASTER_KEY
DATA_DIR=$RUNTIME_DIR/data
UPLOADS_DIR=$RUNTIME_DIR/uploads
EXPORTS_DIR=$RUNTIME_DIR/exports
PYTHONIOENCODING=utf-8
PYTHONUTF8=1
EOF
  chmod 600 "$ENV_FILE"
else
  log "$ENV_FILE 已存在，跳过（如需重建请先手动备份删除）"
fi

# ---------------------------------------------------------------- 6. 自签证书
if [[ ! -f "$CERT_DIR/dc.crt" || ! -f "$CERT_DIR/dc.key" ]]; then
  log "生成自签证书（CN/SAN = $PUBLIC_IP，10 年）"
  openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
    -keyout "$CERT_DIR/dc.key" -out "$CERT_DIR/dc.crt" \
    -subj "/C=CN/ST=Beijing/O=DataConverter/CN=$PUBLIC_IP" \
    -addext "subjectAltName=IP:$PUBLIC_IP" >/dev/null 2>&1
  chmod 600 "$CERT_DIR/dc.key"
else
  log "证书已存在，跳过"
fi

# ---------------------------------------------------------------- 7. systemd
log "写入 systemd 服务 $SERVICE_NAME"
cat > "/etc/systemd/system/$SERVICE_NAME.service" <<EOF
[Unit]
Description=Data Converter Tool
After=network-online.target mysql.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$VENV_DIR/bin/python server.py
Restart=always
RestartSec=5
StandardOutput=append:$RUNTIME_DIR/logs/service.log
StandardError=append:$RUNTIME_DIR/logs/service.log

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || log "enable 失败（不阻断后续步骤）"
systemctl restart "$SERVICE_NAME" || log "服务启动失败，稍后自检会打印日志"

# ---------------------------------------------------------------- 8. Nginx
log "写入 Nginx 配置（80 → 443 跳转，443 → 127.0.0.1:$PORT）"
cat > /etc/nginx/sites-available/data-converter <<EOF
server {
    listen 80;
    server_name $PUBLIC_IP;
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl;
    http2 on;
    server_name $PUBLIC_IP;

    ssl_certificate     $CERT_DIR/dc.crt;
    ssl_certificate_key $CERT_DIR/dc.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # 导入大文件与长耗时任务
    client_max_body_size 1024m;
    proxy_read_timeout   1800s;
    proxy_send_timeout   1800s;

    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_http_version 1.1;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/data-converter /etc/nginx/sites-enabled/data-converter
nginx -t
systemctl reload nginx

# ---------------------------------------------------------------- 9. 自检
log "自检"
sleep 3
systemctl is-active --quiet "$SERVICE_NAME" && echo "  systemd: 运行中" || { echo "  systemd: 未运行，日志见 $RUNTIME_DIR/logs/service.log"; journalctl -u "$SERVICE_NAME" -n 20 --no-pager || true; }
curl -sk --max-time 8 "http://127.0.0.1:$PORT/api/ping" >/dev/null && echo "  应用: 51978 可达" || echo "  应用: 51978 不可达"
curl -sk --max-time 8 "https://127.0.0.1/api/ping" >/dev/null && echo "  Nginx: HTTPS 可达" || echo "  Nginx: HTTPS 不可达"
"$VENV_DIR/bin/python" - <<'PY'
import json, urllib.request, ssl
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
try:
    with urllib.request.urlopen("https://127.0.0.1/api/meta", context=ctx, timeout=8) as r:
        print("  应用版本:", json.load(r).get("appVersion"))
except Exception as exc:
    print("  版本探测失败:", exc)
PY

log "完成。访问地址： https://$PUBLIC_IP"
echo "  提示：自签证书首次访问需在浏览器点「高级 → 继续前往」"
echo "  后续步骤：登录页创建首个管理员账号 → 在「新建连接」里连 127.0.0.1 / dc_cloud"
