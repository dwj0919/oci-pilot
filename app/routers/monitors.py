"""监控告警: 实例保活 / 流量守护 / 域名证书监控 / 每日报告。"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from ..deps import get_current_user
from ..db import (User, InstanceWatch, TrafficGuard, DomainMonitor,
                  KVStore, get_session)
from ..oci import compute as C
from ..oci import metrics as M
from .. import cloudflare as CF
from ..monitor.domains_check import get_domain_expiry, get_ssl_expiry
from . import _util as U

router = APIRouter(prefix="/api/monitors")


def _kv_get(key: str, default: str = "") -> str:
    with get_session() as s:
        kv = s.get(KVStore, key)
        return kv.value if kv else default


def _kv_set(key: str, value: str):
    with get_session() as s:
        kv = s.get(KVStore, key)
        if kv:
            kv.value = value
        else:
            kv = KVStore(key=key, value=value)
        s.add(kv)
        s.commit()


# ---------- 实例保活 ----------

class WatchIn(BaseModel):
    account_id: int
    instance_id: str
    display_name: str = ""
    auto_restart: bool = False


@router.get("/watch")
def list_watch(user: User = Depends(get_current_user)):
    with get_session() as s:
        return [w.model_dump() for w in s.exec(select(InstanceWatch)).all()]


@router.post("/watch")
def add_watch(data: WatchIn, user: User = Depends(get_current_user)):
    with get_session() as s:
        w = InstanceWatch(account_id=data.account_id,
                          instance_id=data.instance_id,
                          display_name=data.display_name,
                          auto_restart=data.auto_restart)
        s.add(w)
        s.commit()
        s.refresh(w)
        return w.model_dump()


class WatchUpdate(BaseModel):
    enabled: bool | None = None
    auto_restart: bool | None = None


@router.put("/watch/{wid}")
def update_watch(wid: int, data: WatchUpdate,
                 user: User = Depends(get_current_user)):
    with get_session() as s:
        w = s.get(InstanceWatch, wid)
        if not w:
            return {"ok": False}
        if data.enabled is not None:
            w.enabled = data.enabled
        if data.auto_restart is not None:
            w.auto_restart = data.auto_restart
        s.add(w)
        s.commit()
        return {"ok": True}


@router.delete("/watch/{wid}")
def del_watch(wid: int, user: User = Depends(get_current_user)):
    with get_session() as s:
        w = s.get(InstanceWatch, wid)
        if w:
            s.delete(w)
            s.commit()
        return {"ok": True}


# ---------- 流量守护 ----------

class TrafficIn(BaseModel):
    account_id: int
    instance_id: str
    display_name: str = ""
    vnic_id: str = ""
    threshold_gb: float = 9000.0
    action: str = "notify"  # notify | stop


@router.get("/traffic")
def list_traffic(user: User = Depends(get_current_user)):
    with get_session() as s:
        return [t.model_dump() for t in s.exec(select(TrafficGuard)).all()]


@router.post("/traffic")
def add_traffic(data: TrafficIn, user: User = Depends(get_current_user)):
    with get_session() as s:
        t = TrafficGuard(account_id=data.account_id,
                         instance_id=data.instance_id,
                         display_name=data.display_name,
                         vnic_id=data.vnic_id,
                         threshold_gb=data.threshold_gb,
                         action=data.action)
        s.add(t)
        s.commit()
        s.refresh(t)
        return t.model_dump()


@router.put("/traffic/{tid}")
def update_traffic(tid: int, data: WatchUpdate,
                   user: User = Depends(get_current_user)):
    with get_session() as s:
        t = s.get(TrafficGuard, tid)
        if not t:
            return {"ok": False}
        if data.enabled is not None:
            t.enabled = data.enabled
        s.add(t)
        s.commit()
        return {"ok": True}


@router.delete("/traffic/{tid}")
def del_traffic(tid: int, user: User = Depends(get_current_user)):
    with get_session() as s:
        t = s.get(TrafficGuard, tid)
        if t:
            s.delete(t)
            s.commit()
        return {"ok": True}


@router.get("/traffic/{tid}/usage")
async def traffic_usage(tid: int, account_id: int,
                        user: User = Depends(get_current_user)):
    with get_session() as s:
        t = s.get(TrafficGuard, tid)
        if not t:
            return {"ok": False}
        vnic_id, threshold = t.vnic_id, t.threshold_gb
    if not vnic_id:
        # 没有指定 vnic, 取实例第一个 vnic
        inst = await U.oci_call(C.get_instance, account_id, t.instance_id)
        vnics = inst.get("vnics") or []
        if not vnics:
            return {"used_gb": 0, "threshold_gb": threshold}
        vnic_id = vnics[0]["vnic_id"]
    comp = (await U.oci_call(C.get_instance, account_id,
                             t.instance_id))["compartment_id"]
    used = await U.oci_call(M.month_egress_gb, account_id, comp, vnic_id)
    from datetime import datetime
    return {"used_gb": used, "threshold_gb": threshold,
            "month": datetime.now().strftime("%Y-%m")}


# ---------- 域名 + SSL 证书监控 ----------

class DomainIn(BaseModel):
    domain: str
    check_domain: bool = True
    check_ssl: bool = True


@router.get("/domains")
def list_domains(user: User = Depends(get_current_user)):
    with get_session() as s:
        return [d.model_dump() for d in s.exec(select(DomainMonitor)).all()]


@router.post("/domains")
async def add_domain(data: DomainIn, user: User = Depends(get_current_user)):
    domain = data.domain.strip().lower()
    if not domain:
        raise HTTPException(400, "域名不能为空")
    with get_session() as s:
        exists = s.exec(select(DomainMonitor).where(
            DomainMonitor.domain == domain)).first()
        if exists:
            raise HTTPException(400, "该域名已在监控中")
        d = DomainMonitor(domain=domain,
                          check_domain=data.check_domain,
                          check_ssl=data.check_ssl)
        s.add(d)
        s.commit()
        s.refresh(d)
        did = d.id
    # 添加后立即检查一次, 不用等到第二天凌晨 3 点
    try:
        await asyncio.to_thread(_check_domain_now, did)
    except Exception:
        pass
    with get_session() as s:
        return s.get(DomainMonitor, did).model_dump()


def _check_domain_now(did: int) -> dict:
    """同步检查单个域名的到期时间并写库 (在线程里跑)。"""
    with get_session() as s:
        d = s.get(DomainMonitor, did)
        if not d:
            return {}
        domain, check_domain, check_ssl = d.domain, d.check_domain, d.check_ssl
    out = {}
    if check_domain:
        exp = get_domain_expiry(domain)
        if exp:
            out["domain_expiry"] = str(exp)
    if check_ssl:
        exp = get_ssl_expiry(domain)
        if exp:
            out["ssl_expiry"] = str(exp)
    if out:
        with get_session() as s:
            d = s.get(DomainMonitor, did)
            for k, v in out.items():
                setattr(d, k, v)
            s.add(d)
            s.commit()
    return {"domain": domain, **out}


@router.post("/domains/check-all")
async def check_all_domains(user: User = Depends(get_current_user)):
    """手动立即检查全部启用中的域名。"""
    with get_session() as s:
        ids = [d.id for d in s.exec(select(DomainMonitor)).all() if d.enabled]
    ok, failed = 0, 0
    for did in ids:
        try:
            await asyncio.to_thread(_check_domain_now, did)
            ok += 1
        except Exception:
            failed += 1
    return {"ok": True, "checked": ok, "failed": failed}


@router.delete("/domains/{did}")
def del_domain(did: int, user: User = Depends(get_current_user)):
    with get_session() as s:
        d = s.get(DomainMonitor, did)
        if d:
            s.delete(d)
            s.commit()
        return {"ok": True}


class ImportCfIn(BaseModel):
    cf_id: int


@router.post("/domains/import-cf")
async def import_cf(data: ImportCfIn, user: User = Depends(get_current_user)):
    zones = await U.oci_call(CF.list_zones, data.cf_id)
    added = 0
    with get_session() as s:
        for z in zones:
            exists = s.exec(select(DomainMonitor).where(
                DomainMonitor.domain == z["name"])).first()
            if not exists:
                s.add(DomainMonitor(domain=z["name"]))
                added += 1
        s.commit()
    return {"ok": True, "added": added, "total": len(zones),
            "imported": added}


# ---------- 每日报告 ----------

@router.get("/daily")
def get_daily(user: User = Depends(get_current_user)):
    return {"enabled": _kv_get("daily_report") == "1"}


class DailyIn(BaseModel):
    enabled: bool


@router.put("/daily")
def set_daily(data: DailyIn, user: User = Depends(get_current_user)):
    _kv_set("daily_report", "1" if data.enabled else "0")
    return {"ok": True}
