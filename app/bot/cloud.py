"""云服务命令: /quota 配额 /volumes 硬盘 /senders 邮件发件人 /mkdomain 发信域名 /dns CF 解析。"""
import ipaddress
import json

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, ConversationHandler, MessageHandler,
                          filters)

from .. import cloudflare as cf
from ..oci import base as oci_base
from ..oci import email as oci_email
from ..oci import identity, volumes
from ..oci.base import OCIError
from .handlers import (cb_get, cb_put, check_bind_query,
                       get_current_account_id, need_bind, to_thread)


@need_bind
async def cmd_quota(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """配额查询。"""
    account_id = get_current_account_id(update.effective_user.id)
    if not account_id:
        await update.message.reply_text("请先用 /accounts 选择 OCI profile。")
        return
    msg = await update.message.reply_text("正在查询配额…")
    try:
        quotas = await to_thread(identity.get_quotas, account_id)
    except OCIError as e:
        await msg.edit_text(f"❌ 查询失败: {e}")
        return
    if not quotas:
        await msg.edit_text("没有查到配额数据。")
        return
    lines = ["📊 配额(可用额度):"]
    for q in quotas[:40]:
        ad = f" [{q['ad']}]" if q.get("ad") else ""
        lines.append(f"{q['service']}{ad} | {q['name']}: {q['available']}")
    if len(quotas) > 40:
        lines.append(f"…还有 {len(quotas) - 40} 项, 请到 Web 面板查看完整列表")
    await msg.edit_text("\n".join(lines))


@need_bind
async def cmd_volumes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """硬盘列表(含启动盘标记)。"""
    account_id = get_current_account_id(update.effective_user.id)
    if not account_id:
        await update.message.reply_text("请先用 /accounts 选择 OCI profile。")
        return
    msg = await update.message.reply_text("正在查询硬盘…")
    try:
        vols = await to_thread(volumes.list_volumes, account_id)
    except OCIError as e:
        await msg.edit_text(f"❌ 查询失败: {e}")
        return
    if not vols:
        await msg.edit_text("没有硬盘。")
        return
    lines = ["💾 硬盘列表:"]
    for v in vols:
        tag = "【启动盘】" if v["is_boot"] else ""
        lines.append(f"{tag}{v['name'] or '(未命名)'} {v['size_gb']}G "
                     f"VPUs {v['vpus_per_gb']} [{v['state']}]")
    await msg.edit_text("\n".join(lines))


@need_bind
async def cmd_senders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """邮件发件人列表。"""
    account_id = get_current_account_id(update.effective_user.id)
    if not account_id:
        await update.message.reply_text("请先用 /accounts 选择 OCI profile。")
        return
    msg = await update.message.reply_text("正在查询发件人…")
    try:
        senders = await to_thread(oci_email.list_senders, account_id)
    except OCIError as e:
        await msg.edit_text(f"❌ 查询失败: {e}")
        return
    if not senders:
        await msg.edit_text("还没有批准的发件人, 用 /mkdomain 先创建发信域名。")
        return
    lines = ["📧 发件人列表:"] + [
        f"{s['email']} [{s['state']}]" for s in senders]
    await msg.edit_text("\n".join(lines))


MD_NAME = 0


@need_bind
async def mkdomain_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account_id = get_current_account_id(update.effective_user.id)
    if not account_id:
        await update.message.reply_text("请先用 /accounts 选择 OCI profile。")
        return ConversationHandler.END
    context.user_data["mk_account"] = account_id
    await update.message.reply_text("请输入发信域名(如 mail.example.com):")
    return MD_NAME


async def msg_mkdomain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dom = update.message.text.strip().lower()
    if not dom or " " in dom or "." not in dom:
        await update.message.reply_text("域名格式不对, 请重新发送")
        return MD_NAME
    account_id = context.user_data.get("mk_account")
    await update.message.reply_text("正在创建发信域名 + DKIM…")
    try:
        acc = await to_thread(oci_base.get_account, account_id)
        r = await to_thread(oci_email.create_email_domain, account_id,
                            acc.tenancy_ocid, dom)
    except OCIError as e:
        await update.message.reply_text(f"❌ 创建失败: {e}")
        return ConversationHandler.END
    lines = [f"✅ 发信域名已创建: {r['name']}",
             "请把以下 DKIM CNAME 记录添加到域名 DNS:"]
    for rec in r["dkim_records"]:
        lines.append(f"{rec['name']}  {rec['type']}  {rec['value']}")
    lines.append("DNS 生效后 DKIM 才会验证通过。")
    await update.message.reply_text("\n".join(lines))
    return ConversationHandler.END


async def mkdomain_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("已取消。")
    return ConversationHandler.END


DNS_IP = 0


@need_bind
async def cmd_dns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cloudflare 快捷操作入口: 列出 CF 账号, 后续 cf: 按钮由独立 handler 接。"""
    accs = await to_thread(cf.list_cf_accounts)
    if not accs:
        await update.message.reply_text("还没有 Cloudflare 账号, 请先在 Web 面板添加。")
        return
    kb = [[InlineKeyboardButton(a["name"], callback_data=f"cf:{a['id']}")]
          for a in accs]
    await update.message.reply_text("选择 Cloudflare 账号:",
                                    reply_markup=InlineKeyboardMarkup(kb))


async def cb_cf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_bind_query(query):
        return
    cfid = int(query.data.split(":")[1])
    try:
        zones = await to_thread(cf.list_zones, cfid)
    except Exception as e:  # noqa: BLE001
        await query.edit_message_text(f"❌ 查询域名失败: {e}")
        return
    if not zones:
        await query.edit_message_text("该账号下没有域名。")
        return
    kb = [[InlineKeyboardButton(z["name"], callback_data=f"cfz:{cfid}:{z['id']}")]
          for z in zones]
    await query.edit_message_text("选择域名:", reply_markup=InlineKeyboardMarkup(kb))


async def cb_zone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_bind_query(query):
        return ConversationHandler.END
    _, cfid, zone_id = query.data.split(":")
    cfid = int(cfid)
    try:
        recs = await to_thread(cf.list_dns, cfid, zone_id)
    except Exception as e:  # noqa: BLE001
        await query.edit_message_text(f"❌ 查询解析失败: {e}")
        return ConversationHandler.END
    a_recs = [r for r in recs if r["type"] == "A"]
    if not a_recs:
        await query.edit_message_text("该域名下没有 A 记录。")
        return ConversationHandler.END
    kb = []
    for r in a_recs:
        tok = cb_put(json.dumps({"cfid": cfid, "zone_id": zone_id,
                                 "record_id": r["id"], "name": r["name"],
                                 "content": r["content"]}))
        kb.append([InlineKeyboardButton(f"{r['name']} → {r['content']}",
                                        callback_data=f"cfd:{tok}")])
    await query.edit_message_text("选择要修改的 A 记录:",
                                  reply_markup=InlineKeyboardMarkup(kb))
    return DNS_IP


async def cb_dns_record(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_bind_query(query):
        return ConversationHandler.END
    raw = cb_get(query.data.split(":")[1])
    if not raw:
        await query.edit_message_text("按钮已过期, 请重新 /dns")
        return ConversationHandler.END
    info = json.loads(raw)
    context.user_data["dns_update"] = info
    await query.edit_message_text(
        f"记录: {info['name']}\n当前: {info['content']}\n请发送新的 IPv4 地址:")
    return DNS_IP


async def msg_dns_ip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ip = update.message.text.strip()
    try:
        ipaddress.IPv4Address(ip)
    except ipaddress.AddressValueError:
        await update.message.reply_text("IP 格式不对, 请重新发送")
        return DNS_IP
    info = context.user_data.get("dns_update")
    if not info:
        await update.message.reply_text("会话过期, 请重新 /dns")
        return ConversationHandler.END
    try:
        await to_thread(cf.update_dns, info["cfid"], info["zone_id"],
                        info["record_id"], ip)
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(f"❌ 更新失败: {e}")
        return ConversationHandler.END
    await update.message.reply_text(f"✅ 已更新: {info['name']} → {ip}")
    return ConversationHandler.END


async def dns_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("已取消。")
    return ConversationHandler.END


def register_cloud(app: Application):
    app.add_handler(CommandHandler("quota", cmd_quota))
    app.add_handler(CommandHandler("volumes", cmd_volumes))
    app.add_handler(CommandHandler("senders", cmd_senders))
    app.add_handler(CommandHandler("dns", cmd_dns))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("mkdomain", mkdomain_entry)],
        states={MD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND,
                                          msg_mkdomain)]},
        fallbacks=[CommandHandler("cancel", mkdomain_cancel)],
        per_user=True,
    ))
    app.add_handler(CallbackQueryHandler(cb_cf, pattern=r"^cf:\d+$"))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_zone, pattern=r"^cfz:\d+:[0-9a-f]+$")],
        states={DNS_IP: [
            CallbackQueryHandler(cb_dns_record, pattern=r"^cfd:[0-9a-f]+$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, msg_dns_ip),
        ]},
        fallbacks=[CommandHandler("cancel", dns_cancel)],
        per_user=True,
    ))
