"""后台定时任务: 实例保活 / 流量守护 / 域名证书到期 / 每日报告。"""
import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import select

from ..db import (InstanceWatch, TrafficGuard, DomainMonitor,
                  KVStore, get_session)
from ..notify import notify
from ..oci import compute as C
from ..oci import metrics as M
from ..oci.base import OCIError
from .domains_check import (get_domain_expiry, get_ssl_expiry, days_left)

log = logging.getLogger("oci-pilot")
scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

TIERS = [30, 14, 7, 1]


def kv_get(key: str, default: str = "") -> str:
    with get_session() as s:
        kv = s.get(KVStore, key)
        return kv.value if kv else default


def kv_set(key: str, value: str):
    with get_session() as s:
        kv = s.get(KVStore, key)
        if kv:
            kv.value = value
            s.add(kv)
        else:
            s.add(KVStore(key=key, value=value))
        s.commit()


async def _watchdog():
    """实例保活: 意外停机通知 / 自动拉起。"""
    with get_session() as s:
        watches = s.exec(select(InstanceWatch)).all()
        watches = [w for w in watches if w.enabled]
        items = [(w.id, w.account_id, w.instance_id,
                  w.display_name, w.auto_restart) for w in watches]
    for wid, account_id, instance_id, name, auto_restart in items:
        try:
            inst = await asyncio.to_thread(C.get_instance, account_id,
                                           instance_id)
            state = inst["state"]
            last = kv_get(f"watch:{wid}:last")
            kv_set(f"watch:{wid}:last", state)
            if last == "RUNNING" and state == "STOPPED":
                if auto_restart:
                    await asyncio.to_thread(C.instance_action, account_id,
                                            instance_id, "START")
                    notify(f"[{name}] 意外停机, 已自动拉起")
                else:
                    notify(f"[{name}] 意外停机 (RUNNING→STOPPED)")
        except OCIError as e:
            log.warning("watchdog %s: %s", name, e)
        except Exception:
            log.exception("watchdog %s failed", name)


async def _traffic_guard():
    """流量守护: 本月流出超阈值 -> 通知 / 自动关机。"""
    month = datetime.now().strftime("%Y-%m")
    with get_session() as s:
        guards = s.exec(select(TrafficGuard)).all()
        guards = [g for g in guards if g.enabled]
        items = [(g.id, g.account_id, g.instance_id, g.display_name,
                  g.vnic_id, g.threshold_gb, g.action) for g in guards]
    for gid, account_id, instance_id, name, vnic_id, threshold, action in items:
        try:
            inst = await asyncio.to_thread(C.get_instance, account_id,
                                           instance_id)
            comp = inst["compartment_id"]
            if not vnic_id:
                vnics = inst.get("vnics") or []
                if not vnics:
                    continue
                vnic_id = vnics[0]["vnic_id"]
            used = await asyncio.to_thread(M.month_egress_gb, account_id,
                                           comp, vnic_id)
            with get_session() as s:
                g = s.get(TrafficGuard, gid)
                if g:
                    g.month = month
                    g.used_gb = used
                    s.add(g)
                    s.commit()
            if used >= threshold and not kv_get(f"traffic:{gid}:{month}"):
                kv_set(f"traffic:{gid}:{month}", "1")
                if action == "stop":
                    await asyncio.to_thread(C.instance_action, account_id,
                                            instance_id, "STOP")
                    notify(f"[流量守护] [{name}] 本月已用 {used}GB "
                           f"(阈值 {threshold}GB), 已自动关机")
                else:
                    notify(f"[流量守护] [{name}] 本月已用 {used}GB "
                           f"(阈值 {threshold}GB), 请注意")
        except OCIError as e:
            log.warning("traffic guard %s: %s", name, e)
        except Exception:
            log.exception("traffic guard %s failed", name)


async def _domain_check():
    """域名 + SSL 证书到期检查, 30/14/7/1 天分档提醒。"""
    with get_session() as s:
        domains = s.exec(select(DomainMonitor)).all()
        domains = [d for d in domains if d.enabled]
        items = [(d.id, d.domain, d.check_domain, d.check_ssl,
                  d.last_notified_tier) for d in domains]
    for did, domain, check_domain, check_ssl, last_tiers in items:
        try:
            notified = set(last_tiers.split(",")) if last_tiers else set()
            msgs = []
            if check_domain:
                exp = await asyncio.to_thread(get_domain_expiry, domain)
                dl = days_left(exp)
                if exp:
                    with get_session() as s:
                        d = s.get(DomainMonitor, did)
                        d.domain_expiry = str(exp)
                        s.add(d)
                        s.commit()
                if dl is not None and dl >= 0:
                    for tier in TIERS:
                        if dl <= tier and str(tier) not in notified:
                            msgs.append(f"域名 {domain} 还有 {dl} 天到期")
                            notified.add(str(tier))
            if check_ssl:
                exp = await asyncio.to_thread(get_ssl_expiry, domain)
                dl = days_left(exp)
                if exp:
                    with get_session() as s:
                        d = s.get(DomainMonitor, did)
                        d.ssl_expiry = str(exp)
                        s.add(d)
                        s.commit()
                if dl is not None and dl >= 0:
                    for tier in TIERS:
                        if dl <= tier and f"ssl{tier}" not in notified:
                            msgs.append(
                                f"域名 {domain} SSL 证书还有 {dl} 天到期")
                            notified.add(f"ssl{tier}")
            if msgs:
                notify("[到期提醒]\n" + "\n".join(msgs))
            with get_session() as s:
                d = s.get(DomainMonitor, did)
                d.last_notified_tier = ",".join(sorted(notified))
                s.add(d)
                s.commit()
        except Exception:
            log.exception("domain check %s failed", domain)


async def _daily_report():
    """每日 08:00 推送实例日报。"""
    if kv_get("daily_report") != "1":
        return
    from ..oci import base as B
    lines = ["[每日报告]"]
    for acc in B.list_accounts():
        try:
            insts = await asyncio.to_thread(C.list_instances, acc["id"])
            running = sum(1 for i in insts if i["state"] == "RUNNING")
            lines.append(f"[{acc['name']}] 实例 {len(insts)} 台, "
                         f"运行中 {running} 台")
        except OCIError as e:
            lines.append(f"[{acc['name']}] 查询失败: {e}")
    notify("\n".join(lines))


def start_scheduler():
    scheduler.add_job(_watchdog, "interval", minutes=5, id="watchdog",
                      max_instances=1, coalesce=True)
    scheduler.add_job(_traffic_guard, "interval", minutes=30,
                      id="traffic_guard", max_instances=1, coalesce=True)
    scheduler.add_job(_domain_check, "cron", hour=3, minute=0,
                      id="domain_check")
    scheduler.add_job(_daily_report, "cron", hour=8, minute=0,
                      id="daily_report")
    scheduler.start()
    log.info("scheduler started")
