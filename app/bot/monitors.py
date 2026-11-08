"""监控命令: /watch 保活 /traffic 流量守护 /daily 日报开关。"""
from sqlmodel import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, ConversationHandler, MessageHandler,
                          filters)

from ..db import InstanceWatch, TrafficGuard, get_session
from ..oci import compute, metrics
from ..oci.base import OCIError
from .handlers import (cb_get, cb_put, check_bind_query,
                       get_current_account_id, get_kv, need_bind, set_kv,
                       to_thread)

# ---------- /watch 实例保活 ----------

@need_bind
async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with get_session() as s:
        watches = s.exec(select(InstanceWatch)).all()
        rows = [(w.id, w.account_id, w.instance_id, w.display_name,
                 w.auto_restart, w.enabled) for w in watches]
    if not rows:
        text = "还没有保活任务。"
    else:
        text = "📡 实例保活列表:\n" + "\n".join(
            f"{'✅' if en else '⏸️'} {name or iid[:20]}… "
            f"{'自动重启开' if ar else '仅通知'}"
            for _, _, iid, name, ar, en in rows)
    kb = [[InlineKeyboardButton(f"🗑️ 删除 {name or iid[:12]}…",
                                callback_data=f"wdel:{wid}")]
          for wid, _, iid, name, _, _ in rows]
    kb.append([InlineKeyboardButton("➕ 添加保活", callback_data="wadd")])
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))


async def cb_watch_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_bind_query(query):
        return
    account_id = get_current_account_id(query.from_user.id)
    if not account_id:
        await query.edit_message_text("请先用 /accounts 选择 OCI profile。")
        return
    try:
        insts = await to_thread(compute.list_instances, account_id)
    except OCIError as e:
        await query.edit_message_text(f"❌ {e}")
        return
    if not insts:
        await query.edit_message_text("没有实例。")
        return
    context.user_data["watch_account"] = account_id
    kb = [[InlineKeyboardButton(f"{i['name']} [{i['state']}]",
                                callback_data=f"waddi:{cb_put(i['id'])}")]
          for i in insts]
    await query.edit_message_text("选择要保活的实例:",
                                  reply_markup=InlineKeyboardMarkup(kb))


async def cb_watch_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_bind_query(query):
        return
    tok = query.data.split(":")[1]
    inst_id = cb_get(tok)
    if not inst_id:
        await query.edit_message_text("按钮已过期")
        return
    context.user_data["watch_instance"] = inst_id
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 掉线自动重启", callback_data=f"wauto:{tok}:1"),
        InlineKeyboardButton("🔔 仅通知我", callback_data=f"wauto:{tok}:0"),
    ]])
    await query.edit_message_text("实例掉线时:", reply_markup=kb)


async def cb_watch_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_bind_query(query):
        return
    _, tok, auto = query.data.split(":")
    inst_id = cb_get(tok)
    account_id = context.user_data.get("watch_account") or \
        get_current_account_id(query.from_user.id)
    try:
        inst = await to_thread(compute.get_instance, account_id, inst_id)
        name = inst["name"]
    except OCIError:
        name = inst_id[:20]
    with get_session() as s:
        exists = s.exec(select(InstanceWatch).where(
            InstanceWatch.instance_id == inst_id)).first()
        if exists:
            exists.auto_restart = auto == "1"
            exists.enabled = True
            s.add(exists)
        else:
            s.add(InstanceWatch(account_id=account_id, instance_id=inst_id,
                                display_name=name, auto_restart=auto == "1"))
        s.commit()
    await query.edit_message_text(
        f"✅ 已添加保活: {name}\n模式: {'掉线自动重启' if auto == '1' else '仅通知'}")


async def cb_watch_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_bind_query(query):
        return
    wid = int(query.data.split(":")[1])
    with get_session() as s:
        w = s.get(InstanceWatch, wid)
        if w:
            s.delete(w)
            s.commit()
    await query.edit_message_text("✅ 保活已删除, 用 /watch 查看最新列表。")


# ---------- /traffic 流量守护 ----------

T_THRESHOLD, T_ACTION = range(2)


@need_bind
async def cmd_traffic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with get_session() as s:
        guards = s.exec(select(TrafficGuard)).all()
        rows = [(g.id, g.account_id, g.instance_id, g.display_name,
                 g.vnic_id, g.threshold_gb, g.action, g.enabled) for g in guards]
    lines = ["🚦 流量守护列表:"]
    for gid, aid, iid, name, vnic, th, act, en in rows:
        used = "?"
        if vnic and en:
            try:
                inst = await to_thread(compute.get_instance, aid, iid)
                used = await to_thread(metrics.month_egress_gb, aid,
                                       inst["compartment_id"], vnic)
                used = f"{used}GB"
            except OCIError:
                used = "查询失败"
        act_cn = "超阈值关机" if act == "stop" else "仅通知"
        lines.append(f"{'✅' if en else '⏸️'} {name or iid[:20]}… "
                     f"本月已用 {used} / 阈值 {th}GB ({act_cn})")
    if not rows:
        lines.append("(空)")
    kb = [[InlineKeyboardButton(f"🗑️ 删除 {name or iid[:12]}…",
                                callback_data=f"tdel:{gid}")]
          for gid, _, iid, name, _, _, _, _ in rows]
    kb.append([InlineKeyboardButton("➕ 添加守护", callback_data="tadd")])
    await update.message.reply_text("\n".join(lines),
                                    reply_markup=InlineKeyboardMarkup(kb))


async def cb_traffic_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_bind_query(query):
        return ConversationHandler.END
    account_id = get_current_account_id(query.from_user.id)
    if not account_id:
        await query.edit_message_text("请先用 /accounts 选择 OCI profile。")
        return ConversationHandler.END
    try:
        insts = await to_thread(compute.list_instances, account_id)
    except OCIError as e:
        await query.edit_message_text(f"❌ {e}")
        return ConversationHandler.END
    insts = [i for i in insts if i.get("vnics")]
    if not insts:
        await query.edit_message_text("没有带网卡的实例。")
        return ConversationHandler.END
    context.user_data["traffic_account"] = account_id
    kb = [[InlineKeyboardButton(f"{i['name']} [{i['state']}]",
                                callback_data=f"taddi:{cb_put(i['id'])}")]
          for i in insts]
    await query.edit_message_text("选择要守护的实例:",
                                  reply_markup=InlineKeyboardMarkup(kb))
    return T_THRESHOLD


async def cb_traffic_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_bind_query(query):
        return ConversationHandler.END
    inst_id = cb_get(query.data.split(":")[1])
    if not inst_id:
        await query.edit_message_text("按钮已过期")
        return ConversationHandler.END
    account_id = context.user_data.get("traffic_account")
    try:
        inst = await to_thread(compute.get_instance, account_id, inst_id)
    except OCIError as e:
        await query.edit_message_text(f"❌ {e}")
        return ConversationHandler.END
    vnics = inst.get("vnics") or []
    if not vnics:
        await query.edit_message_text("该实例没有网卡")
        return ConversationHandler.END
    context.user_data["traffic_instance"] = {
        "id": inst_id, "name": inst["name"],
        "vnic_id": vnics[0]["vnic_id"]}
    await query.edit_message_text(
        f"已选: {inst['name']}\n请输入月流量阈值(GB, 如 9000, 免费额度约 10000GB/月):")
    return T_ACTION


async def msg_traffic_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        th = float(update.message.text.strip())
        if th <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("请输入大于 0 的数字")
        return T_ACTION
    context.user_data["traffic_threshold"] = th
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔔 超阈值只通知", callback_data="tact:notify"),
        InlineKeyboardButton("⏹️ 超阈值自动关机", callback_data="tact:stop"),
    ]])
    await update.message.reply_text("超过阈值时:", reply_markup=kb)
    return T_ACTION


async def cb_traffic_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_bind_query(query):
        return ConversationHandler.END
    if query.data == "tact:cancel":
        await query.edit_message_text("已取消。")
        return ConversationHandler.END
    action = query.data.split(":")[1]
    ud = context.user_data
    info = ud.get("traffic_instance") or {}
    account_id = ud.get("traffic_account")
    if not info or not account_id:
        await query.edit_message_text("会话过期, 请重新 /traffic")
        return ConversationHandler.END
    with get_session() as s:
        exists = s.exec(select(TrafficGuard).where(
            TrafficGuard.instance_id == info["id"])).first()
        if exists:
            exists.threshold_gb = ud["traffic_threshold"]
            exists.action = action
            exists.vnic_id = info["vnic_id"]
            exists.enabled = True
            s.add(exists)
        else:
            s.add(TrafficGuard(account_id=account_id, instance_id=info["id"],
                               display_name=info["name"],
                               vnic_id=info["vnic_id"],
                               threshold_gb=ud["traffic_threshold"],
                               action=action))
        s.commit()
    act_cn = "自动关机" if action == "stop" else "仅通知"
    await query.edit_message_text(
        f"✅ 流量守护已添加: {info['name']}\n"
        f"阈值 {ud['traffic_threshold']}GB/月, 超阈值{act_cn}")
    return ConversationHandler.END


async def cb_traffic_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_bind_query(query):
        return
    gid = int(query.data.split(":")[1])
    with get_session() as s:
        g = s.get(TrafficGuard, gid)
        if g:
            s.delete(g)
            s.commit()
    await query.edit_message_text("✅ 流量守护已删除, 用 /traffic 查看最新列表。")


# ---------- /daily 每日报告 ----------

@need_bind
async def cmd_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cur = get_kv("daily_report", "0") == "1"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ 开启", callback_data="don:1"),
        InlineKeyboardButton("❌ 关闭", callback_data="don:0"),
    ]])
    await update.message.reply_text(
        f"📊 每日报告当前: {'开启' if cur else '关闭'}\n"
        "开启后每天推送实例数/流量/花费摘要。",
        reply_markup=kb)


async def cb_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_bind_query(query):
        return
    on = query.data.split(":")[1] == "1"
    set_kv("daily_report", "1" if on else "0")
    await query.edit_message_text(f"📊 每日报告已{'开启' if on else '关闭'}。")


def register_monitors(app: Application):
    app.add_handler(CommandHandler("watch", cmd_watch))
    app.add_handler(CallbackQueryHandler(cb_watch_add, pattern=r"^wadd$"))
    app.add_handler(CallbackQueryHandler(cb_watch_pick, pattern=r"^waddi:[0-9a-f]+$"))
    app.add_handler(CallbackQueryHandler(cb_watch_auto, pattern=r"^wauto:[0-9a-f]+:[01]$"))
    app.add_handler(CallbackQueryHandler(cb_watch_del, pattern=r"^wdel:\d+$"))

    traffic_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_traffic_add, pattern=r"^tadd$")],
        states={
            T_THRESHOLD: [CallbackQueryHandler(cb_traffic_pick, pattern=r"^taddi:[0-9a-f]+$")],
            T_ACTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, msg_traffic_threshold),
                       CallbackQueryHandler(cb_traffic_action, pattern=r"^tact:")],
        },
        fallbacks=[],
        per_user=True,
    )
    app.add_handler(traffic_conv)
    app.add_handler(CommandHandler("traffic", cmd_traffic))
    app.add_handler(CallbackQueryHandler(cb_traffic_del, pattern=r"^tdel:\d+$"))

    app.add_handler(CommandHandler("daily", cmd_daily))
    app.add_handler(CallbackQueryHandler(cb_daily, pattern=r"^don:[01]$"))
