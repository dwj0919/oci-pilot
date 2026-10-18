#!/usr/bin/env bash
# 安装为 systemd 服务: 开机自启 + 崩溃自动重启 (5 秒后拉起)。
# 必须用 root 运行: sudo bash install-service.sh
# 可选环境变量: HOST (默认 127.0.0.1) PORT (默认 9528)
set -e
[ "$(id -u)" = "0" ] || { echo "请用 root 运行: sudo bash install-service.sh"; exit 1; }
DIR="$(cd "$(dirname "$0")" && pwd)"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-9528}"

if [ ! -x "$DIR/.venv/bin/python" ]; then
  echo "[svc] 未发现虚拟环境, 正在创建并安装依赖 (约需几分钟)..."
  python3 -m venv "$DIR/.venv"
  "$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"
fi
mkdir -p "$DIR/logs"

cat > /etc/systemd/system/oci-pilot.service <<EOF
[Unit]
Description=oci-pilot (Oracle Cloud 管理面板)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python -m uvicorn app.main:app --host $HOST --port $PORT
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
StandardOutput=append:$DIR/logs/uvicorn.log
StandardError=append:$DIR/logs/uvicorn.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now oci-pilot
sleep 2
echo "---- 服务状态 ----"
systemctl --no-pager --lines=8 status oci-pilot || true

# ---- 日志切割: 每日 0 点自动归档 logs/uvicorn.log ----
cat > /etc/systemd/system/oci-pilot-logrotate.service <<EOF
[Unit]
Description=oci-pilot 日志切割

[Service]
Type=oneshot
ExecStart=$DIR/rotate-logs.sh
EOF
cat > /etc/systemd/system/oci-pilot-logrotate.timer <<EOF
[Unit]
Description=每日 0 点切割 oci-pilot 日志

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now oci-pilot-logrotate.timer
echo "[svc] 日志切割 timer 已安装 (每日 0 点): systemctl list-timers oci-pilot-logrotate.timer"
echo "[svc] 日志: $DIR/logs/uvicorn.log ；卸载: sudo systemctl disable --now oci-pilot oci-pilot-logrotate.timer"
