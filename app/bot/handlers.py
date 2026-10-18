"""Bot 公共 helpers + 基础命令(/start /bind /logincode /accounts /test)。"""
import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from sqlmodel import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from ..db import KVStore, LoginCode, User, get_session
from ..config import TELEGRAM_ALLOWED_IDS
from ..oci import base as oci_base

# 回调按钮携带的长 OCID 用短 token 代替(Telegram callback_data 上限 64 字节)
_CB_MAP: dict[str, str] = {}


def cb_put(value: str) -> str:
    """存一个长 ID, 返回可放进 callback_data 的短 token。"""
    token = secrets.token_hex(4)
    _CB_MAP[token] = value
    return token


def cb_get(token: str) -> str | None:
    return _CB_MAP.get(token)


async def reply_long(target, text: str):
    """超长消息分段回复, target 是 message 或 callback query。"""
    for i in range(0, max(len(text), 1), 4000):
        part = text[i:i + 4000]
        if hasattr(target, "edit_message_text"):
            await target.edit_message_text(part)
            break  # query 只编辑一次, 剩余部分用 message 发
        else:
            await target.reply_text(part)


def get_bound_user(tgid: int) -> User | None:
    with get_session() as s:
        return s.exec(select(User).where(User.telegram_user_id == tgid)).first()


def is_allowed(tgid: int) -> bool:
    """是否在 TELEGRAM_ALLOWED_IDS 白名单中。为空 = 拒绝所有(安全默认)。"""
    return tgid in TELEGRAM_ALLOWED_IDS


_UNAUTHORIZED_MSG = ("⛔ 未授权。\n请在面板项目根目录的 .env 里配置\n"
                    "TELEGRAM_ALLOWED_IDS=你的 Telegram 数字 ID\n"
                    "(给 @userinfobot 发任意消息可查到)，然后重启面板。")


def need_bind(fn):
    """命令 handler 装饰器: 白名单 + 已绑定面板账号才放行。"""

    @wraps(fn)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        tgid = update.effective_user.id
        if not is_allowed(tgid):
            await update.message.reply_text(_UNAUTHORIZED_MSG)
            return
        if get_bound_user(tgid) is None:
            await update.message.reply_text(
                "⚠️ 请先绑定面板账号:\n/bind\n\n"
                "绑定后才能使用管理命令。")
            return
        return await fn(update, context)

    return wrapper


async def check_bind_query(query) -> bool:
    """callback query 的绑定检查, 未绑定时 answer 提示并返回 False。"""
    if not is_allowed(query.from_user.id):
        await query.answer("⛔ 未授权用户", show_alert=True)
        return False
    if get_bound_user(query.from_user.id) is None:
        await query.answer("请先用 /bind 绑定", show_alert=True)
        return False
    return True


def get_current_account_id(tgid: int) -> int | None:
    """当前 TG 用户选中的 OCI profile; 未选择且只有一个账号时自动用它。"""
    with get_session() as s:
        kv = s.get(KVStore, f"tg:{tgid}:account_id")
        if kv and kv.value:
            return int(kv.value)
    accs = oci_base.list_accounts()
    if len(accs) == 1:
        return accs[0]["id"]
    return None


def set_current_account_id(tgid: int, account_id: int):
    with get_session() as s:
        kv = s.get(KVStore, f"tg:{tgid}:account_id")
        if kv:
            kv.value = str(account_id)
        else:
            kv = KVStore(key=f"tg:{tgid}:account_id", value=str(account_id))
        s.add(kv)
        s.commit()


def get_kv(key: str, default: str = "") -> str:
    with get_session() as s:
        kv = s.get(KVStore, key)
        return kv.value if kv else default


def set_kv(key: str, value: str):
    with get_session() as s:
        kv = s.get(KVStore, key)
        if kv:
            kv.value = value
        else:
            kv = KVStore(key=key, value=value)
        s.add(kv)
        s.commit()


async def to_thread(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


HELP_TEXT = """🤖 oci-pilot Bot 命令一览

👤 账号
/bind - 绑定面板账号(白名单用户自动开户, 首次部署用它)
/logincode - 生成 Web 登录验证码(免密码登录)
/accounts - 查看/切换 OCI profile
/test - 测活当前 OCI profile

🖥️ 实例
/instances - 实例列表与电源管理
/launch - 开机向导(含抢机重试)
/tasks - 抢机任务列表/取消

💾 存储网络
/volumes - 硬盘列表(含启动盘)
/quota - 配额查询
/dns - Cloudflare 解析快捷操作

📧 邮件
/senders - 邮件发件人列表
/mkdomain - 创建发信域名(含 DKIM)

📊 监控
/watch - 实例保活(掉线通知/自动重启)
/traffic - 流量守护(超阈值通知/关机)
/domains - 域名与证书到期监控
/daily - 每日报告开关

除 /start /bind /logincode 外, 其他命令需先绑定。
"""


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text(_UNAUTHORIZED_MSG)
        return
    await update.message.reply_text(HELP_TEXT)


def get_or_create_user(tgid: int, tg_username: str | None) -> str:
    """按 Telegram ID 查找或创建面板账号, 返回用户名。"""
    with get_session() as s:
        u = s.exec(select(User).where(User.telegram_user_id == tgid)).first()
        if u:
            return u.username
        base = (tg_username or "").strip() or f"tg_{tgid}"
        username, i = base, 1
        while s.exec(select(User).where(User.username == username)).first():
            i += 1
            username = f"{base}{i}"
        s.add(User(username=username, password_hash="",
                   telegram_user_id=tgid))
        s.commit()
        return username


async def cmd_bind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tgid = update.effective_user.id
    if not is_allowed(tgid):
        await update.message.reply_text(_UNAUTHORIZED_MSG)
        return
    username = get_or_create_user(tgid, update.effective_user.username)
    await update.message.reply_text(
        f"✅ 已绑定面板账号 {username}\n发 /logincode 获取验证码登录 Web 面板。")


async def cmd_logincode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tgid = update.effective_user.id
    if not is_allowed(tgid):
        await update.message.reply_text(_UNAUTHORIZED_MSG)
        return
    get_or_create_user(tgid, update.effective_user.username)
    code = f"{secrets.randbelow(900000) + 100000}"
    msg = await update.message.reply_text(
        f"🔑 Web 登录验证码: {code}\n5 分钟内有效, 在 Web 登录页输入即可登录。\n"
        f"登录成功后这条消息会自动删除。")
    with get_session() as s:
        # 清掉该用户旧码
        olds = s.exec(select(LoginCode).where(
            LoginCode.telegram_user_id == tgid)).all()
        for o in olds:
            s.delete(o)
        s.add(LoginCode(code=code, telegram_user_id=tgid,
                        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                        chat_id=msg.chat_id, message_id=msg.message_id))
        s.commit()


@need_bind
async def cmd_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    accs = await to_thread(oci_base.list_accounts)
    if not accs:
        await update.message.reply_text("还没有 OCI profile, 请先在 Web 面板添加。")
        return
    cur = get_current_account_id(update.effective_user.id)
    kb = [[InlineKeyboardButton(
        f"{'✅ ' if a['id'] == cur else ''}{a['name']} ({a['region']})",
        callback_data=f"acc:{a['id']}")] for a in accs]
    await update.message.reply_text("选择当前使用的 OCI profile:",
                                    reply_markup=InlineKeyboardMarkup(kb))


async def cb_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_bind_query(query):
        return
    account_id = int(query.data.split(":")[1])
    set_current_account_id(query.from_user.id, account_id)
    accs = await to_thread(oci_base.list_accounts)
    name = next((a["name"] for a in accs if a["id"] == account_id), str(account_id))
    await query.edit_message_text(f"✅ 已切换到 profile: {name}")


@need_bind
async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account_id = get_current_account_id(update.effective_user.id)
    if not account_id:
        await update.message.reply_text("请先用 /accounts 选择 OCI profile。")
        return
    await update.message.reply_text("正在测活…")
    r = await to_thread(oci_base.test_account, account_id)
    if r["ok"]:
        await update.message.reply_text(
            f"✅ 测活成功\n租户: {r['tenancy_name']}\n主区域: {r['home_region']}")
    else:
        await update.message.reply_text(f"❌ 测活失败\n{r['error']}")


def register_basic(app: Application):
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("bind", cmd_bind))
    app.add_handler(CommandHandler("logincode", cmd_logincode))
    app.add_handler(CommandHandler("accounts", cmd_accounts))
    app.add_handler(CommandHandler("test", cmd_test))
    app.add_handler(CallbackQueryHandler(cb_account, pattern=r"^acc:\d+$"))
