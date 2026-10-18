"""总览: 账号 + 实例状态汇总。"""
from fastapi import APIRouter, Depends

from ..deps import get_current_user
from ..db import User
from ..oci import base as B
from ..oci import compute as C
from ..oci import launch as L
from . import _util as U

router = APIRouter(prefix="/api/overview")


@router.get("")
async def overview(account_id: int = 0,
                   user: User = Depends(get_current_user)):
    accounts = B.list_accounts()
    data = {"accounts": accounts, "instances_total": 0, "running": 0,
            "stopped": 0, "tasks_running": 0}
    tasks = L.list_tasks()
    data["tasks_running"] = sum(1 for t in tasks if t["status"] == "running")
    target = account_id or (accounts[0]["id"] if accounts else 0)
    if target:
        try:
            insts = await U.oci_call(C.list_instances, target)
            data["instances_total"] = len(insts)
            data["running"] = sum(1 for i in insts if i["state"] == "RUNNING")
            data["stopped"] = sum(1 for i in insts if i["state"] == "STOPPED")
            data["instances"] = [
                {"id": i["id"], "name": i["name"], "state": i["state"],
                 "shape": i["shape"], "public_ip": i.get("public_ip")}
                for i in insts]
        except Exception:  # noqa: BLE001
            pass
    return data
