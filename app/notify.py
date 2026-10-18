"""统一通知出口: 调度器/抢机引擎 -> Telegram。

Bot 模块启动时调用 set_sender 注册真正的发送函数;
未配置 Bot 时 notify() 只是打日志, 不报错。
"""
import logging
from typing import Callable

log = logging.getLogger("oci-pilot")

_sender: Callable[[int | None, str], None] | None = None


def set_sender(fn: Callable[[int | None, str], None]):
    """fn(telegram_user_id | None, text): user_id 为 None 时广播给所有绑定用户。"""
    global _sender
    _sender = fn


def notify(text: str, telegram_user_id: int | None = None):
    if _sender is None:
        log.info("notify(no sender): %s", text[:120])
        return
    try:
        _sender(telegram_user_id, text)
    except Exception:  # noqa: BLE001
        log.exception("notify failed")
