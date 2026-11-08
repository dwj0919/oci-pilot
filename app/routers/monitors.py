"""监控告警: 实例保活 / 流量守护 / 每日报告。"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import select

from ..deps import get_current_user
from ..db import (User, InstanceWatch, TrafficGuard, KVStore, get_session)
from ..oci import compute as C
from ..oci import metrics as M
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
