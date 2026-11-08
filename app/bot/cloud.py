"""云服务命令: /quota 配额 /volumes 硬盘。"""
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from ..oci import identity, volumes
from ..oci.base import OCIError
from .handlers import get_current_account_id, need_bind, to_thread


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


def register_cloud(app: Application):
    app.add_handler(CommandHandler("quota", cmd_quota))
    app.add_handler(CommandHandler("volumes", cmd_volumes))
