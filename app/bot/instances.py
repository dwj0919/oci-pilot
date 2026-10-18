"""实例管理: /instances 列表 + 电源/换IP/终止/重装(含二次确认)。"""
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes)

from ..oci import base as oci_base
from ..oci import compute, launch, network
from ..oci.base import OCIError, oci_clients
from .handlers import (cb_get, cb_put, check_bind_query, get_current_account_id,
                       need_bind, reply_long, to_thread)

ACTIONS = [
    ("start", "▶️ 开机"), ("stop", "⏹️ 关机"), ("reboot", "🔄 重启"),
    ("changeip", "🌐 换IP"), ("detail", "📋 详情"),
    ("reinstall", "💿 重装系统"),
    ("term_keep", "🗑️ 终止(保留启动盘)"), ("term_del", "☠️ 终止(删除启动盘)"),
]


def _inst_line(i: dict) -> str:
    ip = i.get("public_ip") or "无公网IP"
    return f"{i['name']} [{i['state']}] {ip}"


def _inst_kb(i: dict) -> InlineKeyboardMarkup:
    tok = cb_put(i["id"])
    rows, row = [], []
    for act, label in ACTIONS:
        row.append(InlineKeyboardButton(label, callback_data=f"iact:{act}:{tok}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ 返回列表", callback_data="instlist")])
    return InlineKeyboardMarkup(rows)


def _detail_text(i: dict) -> str:
    vnics = i.get("vnics") or []
    vinfo = "\n".join(
        f"  网卡: 公网 {v.get('public_ip') or '-'} / 内网 {v.get('private_ip')}"
        for v in vnics) or "  (无网卡信息)"
    return (f"📋 {i['name']}\n"
            f"状态: {i['state']}\n"
            f"规格: {i['shape']} {i.get('ocpus') or ''}C/{i.get('memory_gb') or ''}G\n"
            f"可用域: {i['ad']}\n"
            f"创建时间: {i['time_created']}\n{vinfo}")


@need_bind
async def cmd_instances(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account_id = get_current_account_id(update.effective_user.id)
    if not account_id:
        await update.message.reply_text("请先用 /accounts 选择 OCI profile。")
        return
    msg = await update.message.reply_text("正在拉取实例列表…")
    try:
        insts = await to_thread(compute.list_instances, account_id)
    except OCIError as e:
        await msg.edit_text(f"❌ 拉取失败: {e}")
        return
    if not insts:
        await msg.edit_text("该 profile 下没有实例。")
        return
    kb = [[InlineKeyboardButton(_inst_line(i), callback_data=f"inst:{cb_put(i['id'])}")]
          for i in insts]
    await msg.edit_text(f"共 {len(insts)} 台实例, 点一台进行操作:",
                        reply_markup=InlineKeyboardMarkup(kb))


async def cb_inst_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        await query.edit_message_text(f"❌ 拉取失败: {e}")
        return
    kb = [[InlineKeyboardButton(_inst_line(i), callback_data=f"inst:{cb_put(i['id'])}")]
          for i in insts]
    await query.edit_message_text(f"共 {len(insts)} 台实例:",
                                  reply_markup=InlineKeyboardMarkup(kb))


async def cb_instance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_bind_query(query):
        return
    inst_id = cb_get(query.data.split(":")[1])
    if not inst_id:
        await query.edit_message_text("按钮已过期, 请重新 /instances")
        return
    account_id = get_current_account_id(query.from_user.id)
    try:
        i = await to_thread(compute.get_instance, account_id, inst_id)
    except OCIError as e:
        await query.edit_message_text(f"❌ {e}")
        return
    await query.edit_message_text(_detail_text(i), reply_markup=_inst_kb(i))


async def cb_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_bind_query(query):
        return
    _, act, tok = query.data.split(":")
    inst_id = cb_get(tok)
    if not inst_id:
        await query.edit_message_text("按钮已过期, 请重新 /instances")
        return
    account_id = get_current_account_id(query.from_user.id)

    if act == "detail":
        try:
            i = await to_thread(compute.get_instance, account_id, inst_id)
        except OCIError as e:
            await query.edit_message_text(f"❌ {e}")
            return
        await query.edit_message_text(_detail_text(i), reply_markup=_inst_kb(i))
        return

    if act == "reinstall":
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("⚠️ 确认重装", callback_data=f"rzy:{tok}"),
            InlineKeyboardButton("取消", callback_data=f"rzn:{tok}"),
        ]])
        await query.edit_message_text(
            "⚠️ 重装系统 = 终止当前实例(删除启动盘) + 按相同配置自动重开。\n"
            "系统盘数据会丢失! 确认继续吗?", reply_markup=kb)
        return

    if act == "changeip":
        await query.edit_message_text("正在换 IP…")
        try:
            i = await to_thread(compute.get_instance, account_id, inst_id)
            vnics = i.get("vnics") or []
            if not vnics:
                raise OCIError("实例没有网卡")
            r = await to_thread(network.change_public_ip, account_id,
                                vnics[0]["vnic_id"])
            await query.edit_message_text(f"✅ 换 IP 成功, 新 IP: {r['new_ip']}")
        except OCIError as e:
            await query.edit_message_text(f"❌ 换 IP 失败: {e}")
        return

    if act in ("start", "stop", "reboot"):
        oci_act = {"start": "START", "stop": "STOP", "reboot": "RESET"}[act]
        await query.edit_message_text("执行中…")
        try:
            await to_thread(compute.instance_action, account_id, inst_id, oci_act)
            await query.edit_message_text(f"✅ 已发送指令: {oci_act}")
        except OCIError as e:
            await query.edit_message_text(f"❌ 执行失败: {e}")
        return

    if act in ("term_keep", "term_del"):
        preserve = act == "term_keep"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("⚠️ 确认终止",
                                 callback_data=f"tmcy:{tok}:{1 if preserve else 0}"),
            InlineKeyboardButton("取消", callback_data=f"inst:{tok}"),
        ]])
        note = "启动盘会被保留, 之后可从启动盘重新开机" if preserve else "启动盘将一并删除, 数据不可恢复"
        await query.edit_message_text(f"⚠️ 确认终止这台实例吗?\n{note}",
                                      reply_markup=kb)
        return


async def cb_terminate_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_bind_query(query):
        return
    _, tok, preserve = query.data.split(":")
    inst_id = cb_get(tok)
    if not inst_id:
        await query.edit_message_text("按钮已过期, 请重新 /instances")
        return
    account_id = get_current_account_id(query.from_user.id)
    await query.edit_message_text("正在终止…")
    try:
        await to_thread(compute.terminate_instance, account_id, inst_id,
                        preserve_boot_volume=preserve == "1")
        await query.edit_message_text("✅ 终止指令已发送")
    except OCIError as e:
        await query.edit_message_text(f"❌ 终止失败: {e}")


def _reinstall_params(account_id: int, instance_id: str) -> dict:
    """读出现有实例的全部开机参数, 用于重装。"""
    with oci_clients(account_id) as c:
        inst = c.compute.get_instance(instance_id).data
        comp = inst.compartment_id
        atts = c.compute.list_vnic_attachments(comp, instance_id=instance_id).data
        attached = [a for a in atts if a.lifecycle_state == "ATTACHED"]
        if not attached:
            raise OCIError("实例没有已附加的网卡, 无法读取子网")
        vnic = c.network.get_vnic(attached[0].vnic_id).data
        boot_gb = 50
        try:
            bvas = c.compute.list_boot_volume_attachments(
                comp, instance_id=instance_id).data
            bvas = [b for b in bvas if b.lifecycle_state == "ATTACHED"]
            if bvas:
                bv = c.block.get_boot_volume(bvas[0].boot_volume_id).data
                boot_gb = bv.size_in_gbs
        except OCIError:
            pass
        sc = inst.shape_config
        return {
            "compartment_id": comp,
            "ad": inst.availability_domain,
            "shape": inst.shape,
            "ocpus": getattr(sc, "ocpus", None),
            "memory_gb": getattr(sc, "memory_in_gbs", None),
            "image_id": inst.image_id,
            "subnet_id": vnic.subnet_id,
            "ssh_key": (inst.metadata or {}).get("ssh_authorized_keys", ""),
            "boot_gb": boot_gb,
            "display_name": inst.display_name,
            "assign_public_ip": bool(vnic.public_ip),
            "user_data": "",
            "hostname_label": "",
        }


async def cb_reinstall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_bind_query(query):
        return
    confirm = query.data.startswith("rzy:")
    tok = query.data.split(":")[1]
    inst_id = cb_get(tok)
    if not inst_id:
        await query.edit_message_text("按钮已过期, 请重新 /instances")
        return
    if not confirm:
        await query.edit_message_text("已取消重装。")
        return
    account_id = get_current_account_id(query.from_user.id)
    await query.edit_message_text("正在读取实例配置…")
    try:
        params = await to_thread(_reinstall_params, account_id, inst_id)
        if not params["ssh_key"]:
            await query.edit_message_text("❌ 实例没有 SSH 公钥元数据, 无法自动重装")
            return
        await to_thread(compute.terminate_instance, account_id, inst_id,
                        preserve_boot_volume=False)
        name = f"重装-{params['display_name']}-{time.strftime('%m%d%H%M')}"
        task_id = await to_thread(launch.create_task, name, account_id,
                                  params, 5, 15)
        await to_thread(launch.submit, task_id)
        await query.edit_message_text(
            f"✅ 旧实例已终止, 重装抢机任务已提交 (ID: {task_id})\n"
            f"缺货会自动重试, 成功后会推送新 IP。用 /tasks 查看进度。")
    except OCIError as e:
        await query.edit_message_text(f"❌ 重装失败: {e}")


def register_instances(app: Application):
    app.add_handler(CommandHandler("instances", cmd_instances))
    app.add_handler(CallbackQueryHandler(cb_inst_list, pattern=r"^instlist$"))
    app.add_handler(CallbackQueryHandler(cb_instance, pattern=r"^inst:[0-9a-f]+$"))
    app.add_handler(CallbackQueryHandler(cb_action, pattern=r"^iact:[a-z_]+:[0-9a-f]+$"))
    app.add_handler(CallbackQueryHandler(cb_terminate_confirm, pattern=r"^tmcy:[0-9a-f]+:[01]$"))
    app.add_handler(CallbackQueryHandler(cb_reinstall, pattern=r"^rz[yn]:[0-9a-f]+$"))
