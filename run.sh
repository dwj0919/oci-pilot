#!/usr/bin/env bash
# oci-pilot 一键启动: venv 不存在则自动创建并装依赖, 然后 nohup 后台运行。
# 关掉终端进程也不会死。用法: bash run.sh [或 ./run.sh]
# 可选环境变量: HOST (默认 127.0.0.1) PORT (默认 9528)
set -e
cd "$(dirname "$0")"
mkdir -p logs data

if [ ! -x ".venv/bin/python" ]; then
  echo "[run] 未发现虚拟环境, 正在创建并安装依赖 (约需几分钟)..."
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
  echo "[run] 依赖安装完成"
fi

if [ -f "logs/uvicorn.pid" ] && kill -0 "$(cat logs/uvicorn.pid)" 2>/dev/null; then
  echo "[run] 已在运行 (pid $(cat logs/uvicorn.pid)), 无需重复启动"
  exit 0
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-9528}"
echo "[run] 后台启动: ${HOST}:${PORT} ..."
# shellcheck disable=SC2086
nohup .venv/bin/python -m uvicorn app.main:app --host "$HOST" --port "$PORT" \
  > logs/uvicorn.log 2>&1 &
echo $! > logs/uvicorn.pid
sleep 3
if kill -0 "$(cat logs/uvicorn.pid)" 2>/dev/null; then
  echo "[run] 启动成功 pid=$(cat logs/uvicorn.pid)  日志=logs/uvicorn.log"
  echo "[run] 健康检查: curl http://127.0.0.1:${PORT}/api/health"
else
  echo "[run] 启动失败, 末尾日志如下:"
  tail -20 logs/uvicorn.log
  rm -f logs/uvicorn.pid
  exit 1
fi
