"""开机向导 /launch: 预设/自定义/从启动盘开机, 缺货自动抢机。"""
import json
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, ConversationHandler, MessageHandler,
                          filters)

from ..oci import base as oci_base
from ..oci import compute, launch, volumes
from ..oci.base import OCIError
from .handlers import (cb_get, cb_put, check_bind_query,
                       get_current_account_id, need_bind, to_thread)

(L_PRESET, L_SHAPE, L_OCPU, L_MEM, L_AD, L_SUBNET, L_IMAGE,
 L_IMAGE_PICK, L_SSHKEY, L_DELAY, L_CONFIRM,
 L_BOOTVOL, L_BSHAPE) = range(13)

SKIP_WORDS = {"跳过", "skip", "跳", "."}


@need_bind
async def launch_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account_id = get_current_account_id(update.effective_user.id)
    if not account_id:
        await update.message.reply_text("请先用 /accounts 选择 OCI profile。")
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["account_id"] = account_id
    kb = [
        [InlineKeyboardButton("AMD 微型 1C/1G", callback_data="preset:amd_micro")],
        [InlineKeyboardButton("ARM A1 2C/12G", callback_data="preset:arm_a1")],
        [InlineKeyboardButton("ARM A1 满额 4C/24G", callback_data="preset:arm_a1_max")],
        [InlineKeyboardButton("🛠️ 自定义配置", callback_data="preset:custom")],
        [InlineKeyboardButton("💿 从现有启动盘开机", callback_data="preset:bootvol")],
    ]
    await update.message.reply_text("选择开机配置:",
                                    reply_markup=InlineKeyboardMarkup(kb))
    return L_PRESET


async def _ask_ad_query(query, account_id: int):
    ads = await to_thread(compute.list_ads, account_id)
    kb = [[InlineKeyboardButton(ad, callback_data=f"lad:{ad}")] for ad in ads]
    await query.edit_message_text("选择可用域(AD):",
                                  reply_markup=InlineKeyboardMarkup(kb))


async def cb_preset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_bind_query(query):
        return ConversationHandler.END
    ud = context.user_data
    account_id = ud.get("account_id") or get_current_account_id(query.from_user.id)
    ud["account_id"] = account_id
    preset = query.data.split(":")[1]

    if preset == "custom":
        await query.edit_message_text("请输入 Shape 名称\n"
                                      "如: VM.Standard.A1.Flex (ARM) / "
                                      "VM.Standard.E2.1.Micro (AMD)")
        return L_SHAPE

    if preset == "bootvol":
        await query.edit_message_text("正在查找未附加的启动盘…")
        try:
            vols = await to_thread(volumes.list_volumes, account_id)
            atts = await to_thread(volumes.list_attachments, account_id)
            attached_ids = {a["volume_id"] for a in atts}
            boots = [v for v in vols
                     if v["is_boot"] and v["id"] not in attached_ids
                     and v["state"] == "AVAILABLE"]
        except OCIError as e:
            await query.edit_message_text(f"❌ 查询失败: {e}")
            return ConversationHandler.END
        if not boots:
            await query.edit_message_text("没有可用的未附加启动盘。")
            return ConversationHandler.END
        kb = [[InlineKeyboardButton(
            f"{b['name'] or '(未命名)'} {b['size_gb']}G [{b['ad']}]",
            callback_data=f"bvol:{cb_put(json.dumps({'id': b['id'], 'ad': b['ad'], 'name': b['name'], 'size': b['size_gb']}))}")]
            for b in boots[:10]]
        await query.edit_message_text("选择启动盘(实例只能开在该盘所在 AD):",
                                      reply_markup=InlineKeyboardMarkup(kb))
        return L_BOOTVOL

    p = compute.QUICK_PRESETS[preset]
    ud.update({"shape": p["shape"], "ocpus": p["ocpus"],
               "mem": p["memory_gb"], "os_hint": p["os_hint"]})
    await _ask_ad_query(query, account_id)
    return L_AD


async def msg_shape(update: Update, context: ContextTypes.DEFAULT_TYPE):
    shape = update.message.text.strip()
    if not shape:
        await update.message.reply_text("Shape 名称不能为空, 请重新输入")
        return L_SHAPE
    context.user_data["shape"] = shape
    await update.message.reply_text("请输入 OCPU 数(如 2, 非 Flex 机型可填 1):")
    return L_OCPU


async def msg_ocpu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        ocpu = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("请输入数字, 如 2")
        return L_OCPU
    context.user_data["ocpus"] = ocpu
    await update.message.reply_text("请输入内存 GB 数(如 12):")
    return L_MEM


async def msg_mem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        mem = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("请输入数字, 如 12")
        return L_MEM
    context.user_data["mem"] = mem
    context.user_data["os_hint"] = "Ubuntu"
    return await _ask_ad_msg(update, context)


async def cb_bootvol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_bind_query(query):
        return ConversationHandler.END
    raw = cb_get(query.data.split(":")[1])
    if not raw:
        await query.edit_message_text("按钮已过期, 请重新 /launch")
        return ConversationHandler.END
    bv = json.loads(raw)
    ud = context.user_data
    ud["boot_volume_id"] = bv["id"]
    ud["ad"] = bv["ad"]  # 启动盘开机只能用盘所在 AD
    kb = [
        [InlineKeyboardButton("ARM A1 Flex", callback_data="bshape:VM.Standard.A1.Flex")],
        [InlineKeyboardButton("AMD E2.1.Micro", callback_data="bshape:VM.Standard.E2.1.Micro")],
    ]
    await query.edit_message_text(
        f"已选启动盘: {bv['name'] or '(未命名)'} {bv['size']}G (AD: {bv['ad']})\n"
        "选择实例规格(或直接发送 Shape 名称):",
        reply_markup=InlineKeyboardMarkup(kb))
    return L_BSHAPE


async def cb_bshape(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_bind_query(query):
        return ConversationHandler.END
    context.user_data["shape"] = query.data.split(":", 1)[1]
    return await _ask_subnet(query, context)


async def msg_bshape(update: Update, context: ContextTypes.DEFAULT_TYPE):
    shape = update.message.text.strip()
    if not shape:
        await update.message.reply_text("Shape 名称不能为空")
        return L_BSHAPE
    context.user_data["shape"] = shape
    # 启动盘分支 AD 已固定, 直接选子网
    return await _ask_subnet_text(update, context)


def _subnet_kb(subs: list) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(f"{s['name']} ({s['vcn']})",
                               callback_data=f"lsub:{cb_put(s['id'])}")]
         for s in subs[:10]])


async def _ask_subnet_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """message 版本: 启动盘分支(AD 已固定)直接列子网。"""
    ud = context.user_data
    try:
        subs = await to_thread(compute.list_subnets, ud["account_id"])
    except OCIError as e:
        await update.message.reply_text(f"❌ 查询子网失败: {e}")
        return ConversationHandler.END
    subs = [s for s in subs if s["ad"] == ud["ad"] and s["public"]]
    if not subs:
        await update.message.reply_text(f"AD {ud['ad']} 下没有可用的公网子网")
        return ConversationHandler.END
    await update.message.reply_text("选择子网:", reply_markup=_subnet_kb(subs))
    return L_SUBNET


async def _ask_ad_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """message 版本: 自定义配置路径, 先选 AD。"""
    ud = context.user_data
    ads = await to_thread(compute.list_ads, ud["account_id"])
    kb = [[InlineKeyboardButton(ad, callback_data=f"lad:{ad}")] for ad in ads]
    await update.message.reply_text("选择可用域(AD):",
                                    reply_markup=InlineKeyboardMarkup(kb))
    return L_AD


async def _ask_subnet(query, context: ContextTypes.DEFAULT_TYPE):
    ud = context.user_data
    try:
        subs = await to_thread(compute.list_subnets, ud["account_id"])
    except OCIError as e:
        await query.edit_message_text(f"❌ 查询子网失败: {e}")
        return ConversationHandler.END
    subs = [s for s in subs if s["ad"] == ud["ad"] and s["public"]]
    if not subs:
        await query.edit_message_text(f"AD {ud['ad']} 下没有可用的公网子网")
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(f"{s['name']} ({s['vcn']})",
                                callback_data=f"lsub:{cb_put(s['id'])}")]
          for s in subs[:10]]
    await query.edit_message_text("选择子网:",
                                  reply_markup=InlineKeyboardMarkup(kb))
    return L_SUBNET


async def cb_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_bind_query(query):
        return ConversationHandler.END
    context.user_data["ad"] = query.data.split(":", 1)[1]
    return await _ask_subnet(query, context)


async def cb_subnet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_bind_query(query):
        return ConversationHandler.END
    subnet_id = cb_get(query.data.split(":")[1])
    if not subnet_id:
        await query.edit_message_text("按钮已过期, 请重新 /launch")
        return ConversationHandler.END
    context.user_data["subnet_id"] = subnet_id
    ud = context.user_data
    if ud.get("boot_volume_id"):
        await query.edit_message_text("请粘贴 SSH 公钥(以 ssh- 开头):")
        return L_SSHKEY
    await query.edit_message_text(
        "发送镜像关键词搜索(如 Ubuntu / CentOS),\n"
        "发送「跳过」直接用默认最新 Ubuntu 镜像:")
    return L_IMAGE


async def msg_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud = context.user_data
    text = update.message.text.strip()
    kw = "" if text.lower() in SKIP_WORDS else text
    if not kw:
        kw = ud.get("os_hint") or "Ubuntu"
    await update.message.reply_text("正在搜索镜像…")
    try:
        imgs = await to_thread(compute.list_images, ud["account_id"],
                               shape=ud.get("shape"), keyword=kw)
    except OCIError as e:
        await update.message.reply_text(f"❌ 搜索失败: {e}, 请换个关键词")
        return L_IMAGE
    if not imgs:
        await update.message.reply_text("没找到镜像, 请换个关键词或发送「跳过」")
        return L_IMAGE
    if len(imgs) == 1:
        ud["image_id"] = imgs[0]["id"]
        ud["image_name"] = imgs[0]["name"]
        await update.message.reply_text(f"已选镜像: {imgs[0]['name']}\n请粘贴 SSH 公钥(以 ssh- 开头):")
        return L_SSHKEY
    kb = [[InlineKeyboardButton(im["name"][:60],
                                callback_data=f"limg:{cb_put(im['id'])}")]
          for im in imgs[:8]]
    await update.message.reply_text("找到多个镜像, 请选择:",
                                    reply_markup=InlineKeyboardMarkup(kb))
    return L_IMAGE_PICK


async def cb_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_bind_query(query):
        return ConversationHandler.END
    image_id = cb_get(query.data.split(":")[1])
    if not image_id:
        await query.edit_message_text("按钮已过期, 请重新 /launch")
        return ConversationHandler.END
    context.user_data["image_id"] = image_id
    await query.edit_message_text("请粘贴 SSH 公钥(以 ssh- 开头):")
    return L_SSHKEY


async def msg_sshkey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = update.message.text.strip()
    if not key.startswith("ssh-"):
        await update.message.reply_text("公钥格式不对, 应以 ssh- 开头, 请重新粘贴")
        return L_SSHKEY
    context.user_data["ssh_key"] = key
    await update.message.reply_text("抢机延迟区间(秒), 如 5-15\n发送「跳过」用默认 5-15:")
    return L_DELAY


async def msg_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    dmin, dmax = 5, 15
    if text.lower() not in SKIP_WORDS:
        try:
            if "-" in text:
                a, b = text.split("-", 1)
                dmin, dmax = int(a.strip()), int(b.strip())
            else:
                dmin = dmax = int(text)
            if dmin < 1 or dmax < dmin:
                raise ValueError
        except ValueError:
            await update.message.reply_text("格式不对, 如 5-15, 请重新输入")
            return L_DELAY
    ud = context.user_data
    ud["delay_min"], ud["delay_max"] = dmin, dmax
    acc = await to_thread(oci_base.get_account, ud["account_id"])
    lines = [
        "📝 开机配置汇总:",
        f"账号: {acc.name} ({acc.region})",
        f"规格: {ud['shape']}",
    ]
    if ud.get("ocpus"):
        lines.append(f"OCPU/内存: {ud['ocpus']}C / {ud['mem']}G")
    if ud.get("boot_volume_id"):
        lines.append(f"启动盘: {ud['boot_volume_id'][:30]}…")
    else:
        lines.append(f"镜像ID: {ud['image_id'][:40]}…")
    lines += [f"可用域: {ud['ad']}",
              f"延迟区间: {dmin}-{dmax} 秒",
              "缺货会自动重试, 成功后推送通知。"]
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ 确认开机", callback_data="lok"),
        InlineKeyboardButton("❌ 取消", callback_data="lno"),
    ]])
    await update.message.reply_text("\n".join(lines), reply_markup=kb)
    return L_CONFIRM


async def cb_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_bind_query(query):
        return ConversationHandler.END
    if query.data == "lno":
        await query.edit_message_text("已取消开机。")
        return ConversationHandler.END
    ud = context.user_data
    account_id = ud["account_id"]
    try:
        acc = await to_thread(oci_base.get_account, account_id)
    except OCIError as e:
        await query.edit_message_text(f"❌ {e}")
        return ConversationHandler.END
    params = {
        "compartment_id": acc.tenancy_ocid,
        "ad": ud["ad"],
        "shape": ud["shape"],
        "ocpus": ud.get("ocpus"),
        "memory_gb": ud.get("mem"),
        "image_id": ud.get("image_id"),
        "subnet_id": ud["subnet_id"],
        "ssh_key": ud["ssh_key"],
        "boot_gb": 50,
        "display_name": f"tg-{ud['shape'].split('.')[-1]}-{time.strftime('%m%d%H%M')}",
        "assign_public_ip": True,
        "user_data": "",
        "hostname_label": "",
    }
    if ud.get("boot_volume_id"):
        params["boot_volume_id"] = ud["boot_volume_id"]
    name = f"TG抢机-{ud['shape'].split('.')[-1]}-{time.strftime('%m%d%H%M')}"
    try:
        task_id = await to_thread(launch.create_task, name, account_id,
                                  params, ud["delay_min"], ud["delay_max"])
        await to_thread(launch.submit, task_id)
    except Exception as e:  # noqa: BLE001
        await query.edit_message_text(f"❌ 提交失败: {e}")
        return ConversationHandler.END
    await query.edit_message_text(
        f"✅ 抢机任务已提交 (ID: {task_id})\n"
        f"缺货会自动重试, 成功后会推送新 IP。\n用 /tasks 查看进度/取消。")
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("已取消。")
    return ConversationHandler.END


def register_launch(app: Application):
    conv = ConversationHandler(
        entry_points=[CommandHandler("launch", launch_entry)],
        states={
            L_PRESET: [CallbackQueryHandler(cb_preset, pattern=r"^preset:")],
            L_SHAPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, msg_shape)],
            L_OCPU: [MessageHandler(filters.TEXT & ~filters.COMMAND, msg_ocpu)],
            L_MEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, msg_mem)],
            L_BOOTVOL: [CallbackQueryHandler(cb_bootvol, pattern=r"^bvol:")],
            L_BSHAPE: [CallbackQueryHandler(cb_bshape, pattern=r"^bshape:"),
                       MessageHandler(filters.TEXT & ~filters.COMMAND, msg_bshape)],
            L_AD: [CallbackQueryHandler(cb_ad, pattern=r"^lad:")],
            L_SUBNET: [CallbackQueryHandler(cb_subnet, pattern=r"^lsub:")],
            L_IMAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, msg_image)],
            L_IMAGE_PICK: [CallbackQueryHandler(cb_image, pattern=r"^limg:")],
            L_SSHKEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, msg_sshkey)],
            L_DELAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, msg_delay)],
            L_CONFIRM: [CallbackQueryHandler(cb_confirm, pattern=r"^l(ok|no)$")],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_user=True,
    )
    app.add_handler(conv)
