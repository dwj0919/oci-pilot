"""日志脱敏: 自动打码日志里的私钥 / Bot Token, 防止密钥进日志文件。

用法: 在 app/main.py import 时和 lifespan 启动时各调一次
install_log_redaction() 即可, 幂等。
"""
import logging
import re

# PEM 私钥块 (跨行)
_PEM_RE = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----")
# Telegram Bot Token 形如 123456:AAH... (至少 30 位)。
# 注意: 日志里常以 https://api.telegram.org/bot<token>/ 形式出现,
# token 前的 "bot" 与数字连在一起, 所以数字前不能要求 \b。
_TG_TOKEN_RE = re.compile(r"\d{5,15}:[A-Za-z0-9_-]{30,}")

MASK_PEM = "[REDACTED PRIVATE KEY]"
MASK_TOKEN = "[REDACTED TG TOKEN]"


def redact(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    text = _PEM_RE.sub(MASK_PEM, text)
    text = _TG_TOKEN_RE.sub(MASK_TOKEN, text)
    return text


class RedactingFormatter(logging.Formatter):
    """包住原 formatter, 对格式化后的完整字符串 (含 traceback) 做脱敏。"""

    def __init__(self, wrapped: logging.Formatter):
        super().__init__()
        self.wrapped = wrapped

    def format(self, record: logging.LogRecord) -> str:
        return redact(self.wrapped.format(record))


def install_log_redaction() -> None:
    """给 root + uvicorn 相关 logger 的所有 handler 换上脱敏 formatter。"""
    targets = [logging.getLogger(), logging.getLogger("uvicorn"),
               logging.getLogger("uvicorn.error"),
               logging.getLogger("uvicorn.access")]
    for lg in targets:
        for h in lg.handlers:
            fmt = h.formatter or logging.Formatter("%(message)s")
            if not isinstance(fmt, RedactingFormatter):
                h.setFormatter(RedactingFormatter(fmt))
