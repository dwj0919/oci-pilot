#!/usr/bin/env bash
# 停止 run.sh 启动的后台进程。用法: bash stop.sh [或 ./stop.sh]
cd "$(dirname "$0")"
if [ -f "logs/uvicorn.pid" ]; then
  pid="$(cat logs/uvicorn.pid)"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" && echo "[stop] 已停止 pid $pid"
  else
    echo "[stop] pid $pid 的进程已不在"
  fi
  rm -f logs/uvicorn.pid
else
  if pkill -f "uvicorn app.main:app"; then
    echo "[stop] 已按进程特征停止"
  else
    echo "[stop] 没有运行中的 oci-pilot 进程"
  fi
fi
