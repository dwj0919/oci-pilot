"""Telegram Bot 启动/停止: 把 notify 与抢机引擎的推送接到 Telegram。"""
import asyncio
import logging
import os

from telegram.ext import Application, ApplicationBuilder

from .. import config
from .. import notify
from ..db import User, get_session
from ..oci import launch
from .handlers import register_basic
from .instances import register_instances
from .launch_wizard import register_launch
from .monitors import register_monitors
from .cloud import register_cloud

log = logging.getLogger("oci-pilot")

_app: Application | None = None
_loop: asyncio.AbstractEventLoop | None = None
TG_MSG_LIMIT = 4000


def _chunks(text: str):
    for i in range(0, len(text), TG_MSG_LIMIT):
        yield text[i:i + TG_MSG_LIMIT]


async def _send_one(user_id: int, text: str):
    for part in _chunks(text):
        try:
            await _app.bot.send_message(chat_id=user_id, text=part)
        except Exception as e:  # noqa: BLE001
            log.warning("tg send failed to %s: %s", user_id, e)


async def _broadcast(text: str):
    with get_session() as s:
        from sqlmodel import select
        users = s.exec(select(User).where(User.telegram_user_id.is_not(None))).all()
        ids = [u.telegram_user_id for u in users if u.telegram_user_id]
    for uid in ids:
        await _send_one(uid, text)


def _sender(user_id: int | None, text: str):
    """notify 注册的发送函数: 可能在调度线程被调用, 用 run_coroutine_threadsafe 跨线程投递。"""
    coro = _send_one(user_id, text) if user_id else _broadcast(text)
    try:
        asyncio.run_coroutine_threadsafe(coro, _loop)
    except Exception:  # noqa: BLE001
        log.exception("schedule tg send failed")


def _sanitize_proxy_env():
    """httpx 会把 no_proxy 条目拼成 all://*host 这类 key 再做 URL 解析;
    畸形条目(如截断的 IPv6、带方括号的 [::1])会让 Application 构建直接炸。
    这里照抄 httpx 的拼装逻辑, 只剔除拼完仍非法的条目, 保留正常代理。"""
    try:
        from httpx._utils import URLPattern, is_ipv4_hostname, is_ipv6_hostname
    except Exception:  # noqa: BLE001
        return
    for var in ("no_proxy", "NO_PROXY"):
        val = os.environ.get(var)
        if not val:
            continue
        keep = []
        for hostname in val.split(","):
            hostname = hostname.strip()
            if not hostname:
                continue
            if hostname == "*" or "://" in hostname:
                keep.append(hostname)
                continue
            if is_ipv4_hostname(hostname):
                key = f"all://{hostname}"
            elif is_ipv6_hostname(hostname):
                key = f"all://[{hostname}]"
            elif hostname.lower() == "localhost":
                key = f"all://{hostname}"
            else:
                key = f"all://*{hostname}"
            try:
                URLPattern(key)
                keep.append(hostname)
            except Exception:  # noqa: BLE001
                log.warning("drop malformed %s entry: %r", var, hostname)
        os.environ[var] = ",".join(keep)


async def start_bot() -> Application | None:
    """启动 Bot。未配置 TELEGRAM_BOT_TOKEN 时返回 None。"""
    global _app, _loop
    token = (config.TELEGRAM_BOT_TOKEN or "").strip()
    if not token:
        log.info("TELEGRAM_BOT_TOKEN 未配置, Bot 不启动")
        return None
    _sanitize_proxy_env()
    _loop = asyncio.get_running_loop()
    _app = ApplicationBuilder().token(token).build()

    register_basic(_app)
    register_instances(_app)
    register_launch(_app)
    register_monitors(_app)
    register_cloud(_app)

    notify.set_sender(_sender)
    launch.set_notifier(lambda text: notify.notify(text))

    await _app.initialize()
    await _app.start()
    await _app.updater.start_polling()
    log.info("telegram bot started")
    return _app


async def stop_bot():
    global _app
    if _app is not None:
        try:
            await _app.updater.stop()
            await _app.stop()
            await _app.shutdown()
        finally:
            _app = None
        log.info("telegram bot stopped")


async def delete_login_code_message(chat_id: int, message_id: int) -> bool:
    """删除登录验证码消息(登录成功后调用), 失败不抛异常。"""
    if _app is None or not chat_id or not message_id:
        return False
    try:
        await _app.bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except Exception:  # noqa: BLE001
        log.warning("删除验证码消息失败 chat=%s msg=%s", chat_id, message_id)
        return False


def delete_login_code_message_sync(chat_id: int | None, message_id: int | None):
    """给 Web 登录接口用的同步版本: fire-and-forget, 不阻塞登录。"""
    if _app is None or _loop is None or not chat_id or not message_id:
        return
    try:
        asyncio.run_coroutine_threadsafe(
            delete_login_code_message(chat_id, message_id), _loop)
    except Exception:  # noqa: BLE001
        log.warning("提交删除验证码消息任务失败")
