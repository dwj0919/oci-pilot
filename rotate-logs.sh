#!/usr/bin/env bash
# 日志切割: 把 logs/uvicorn.log 按天归档并压缩, 防止文件过大。
# 采用 copytruncate (复制后原地清空), 运行中的进程不受影响, 无需重启。
# 用法: 每日 0 点跑一次 ——
#   systemd 用户: 由 install-service.sh 自动装好 timer, 无需手动操作
#   run.sh 用户: 加一条 cron -> 0 0 * * * /root/rbot-test/oci-pilot/rotate-logs.sh
set -e
cd "$(dirname "$0")"
LOG="logs/uvicorn.log"
[ -f "$LOG" ] || exit 0
[ -s "$LOG" ] || exit 0  # 空文件不切

# 此时切的是昨天一整天的日志, 用昨天日期命名
STAMP=$(date -d "yesterday" +%Y%m%d 2>/dev/null || date +%Y%m%d)
ARCHIVE="logs/uvicorn-${STAMP}.log"

cp -- "$LOG" "$ARCHIVE"
: > "$LOG"
gzip -f "$ARCHIVE"

# 只保留最近 30 天的归档
ls -t logs/uvicorn-*.log.gz 2>/dev/null | tail -n +31 | xargs -r rm -f

echo "[rotate] $(date '+%F %T') 已归档 -> ${ARCHIVE}.gz"
