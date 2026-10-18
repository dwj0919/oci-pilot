"""实例管理 + 开机(抢机) + 重装系统。"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..deps import get_current_user
from ..db import User
from ..oci import compute as C
from ..oci import launch as L
from ..oci.base import oci_clients, OCIError
from . import _util as U

router = APIRouter()


# ---------- 实例 ----------

@router.get("/api/instances")
async def list_instances(account_id: int,
                         user: User = Depends(get_current_user)):
    return await U.oci_call(C.list_instances, account_id)


@router.get("/api/instances/{instance_id}")
async def get_instance(instance_id: str, account_id: int,
                       user: User = Depends(get_current_user)):
    return await U.oci_call(C.get_instance, account_id, instance_id)


class ActionIn(BaseModel):
    account_id: int
    action: str  # START | STOP | SOFTSTOP | RESET | SOFTRESET


@router.post("/api/instances/{instance_id}/action")
async def instance_action(instance_id: str, data: ActionIn,
                          user: User = Depends(get_current_user)):
    return await U.oci_call(C.instance_action, data.account_id,
                            instance_id, data.action)


class TerminateIn(BaseModel):
    account_id: int
    preserve_boot_volume: bool = False


@router.post("/api/instances/{instance_id}/terminate")
async def terminate(instance_id: str, data: TerminateIn,
                    user: User = Depends(get_current_user)):
    return await U.oci_call(C.terminate_instance, data.account_id,
                            instance_id, data.preserve_boot_volume)


class UpdateIn(BaseModel):
    account_id: int
    display_name: str | None = None
    ocpus: float | None = None
    memory_gb: float | None = None


@router.put("/api/instances/{instance_id}")
async def update_instance(instance_id: str, data: UpdateIn,
                          user: User = Depends(get_current_user)):
    return await U.oci_call(C.update_instance, data.account_id, instance_id,
                            data.display_name, data.ocpus, data.memory_gb)


class ReinstallIn(BaseModel):
    account_id: int
    delay_min: int = 5
    delay_max: int = 15


@router.post("/api/instances/{instance_id}/reinstall")
async def reinstall(instance_id: str, data: ReinstallIn,
                    user: User = Depends(get_current_user)):
    """重置系统: 收集原实例参数 -> 删除实例(含启动盘) -> 提交抢机任务自动重开。"""
    def _do():
        with oci_clients(data.account_id) as c:
            inst = c.compute.get_instance(instance_id).data
            if not inst.image_id:
                raise OCIError("该实例无镜像信息(如从启动盘开的机), 无法重装")
            atts = c.compute.list_vnic_attachments(
                inst.compartment_id, instance_id=instance_id).data
            vnic = c.network.get_vnic(atts[0].vnic_id).data
            sc = inst.shape_config
            params = {
                "compartment_id": inst.compartment_id,
                "ad": inst.availability_domain,
                "shape": inst.shape,
                "ocpus": getattr(sc, "ocpus", 1),
                "memory_gb": getattr(sc, "memory_in_gbs", 1),
                "image_id": inst.image_id,
                "subnet_id": vnic.subnet_id,
                "ssh_key": (inst.metadata or {}).get("ssh_authorized_keys", ""),
                "display_name": inst.display_name,
                "assign_public_ip": True,
            }
            c.compute.terminate_instance(instance_id, preserve_boot_volume=False)
        task_id = L.create_task(f"重装-{inst.display_name}", data.account_id,
                                params, data.delay_min, data.delay_max)
        L.submit(task_id)
        return {"task_id": task_id,
                "note": "原实例已删除, 后台正在抢机重开, 成功后会推送通知"}
    return await U.oci_call(_do)


# ---------- 开机(抢机) ----------

class LaunchIn(BaseModel):
    account_id: int
    name: str = ""
    params: dict
    delay_min: int = 5
    delay_max: int = 15


@router.post("/api/launch")
async def launch(data: LaunchIn, user: User = Depends(get_current_user)):
    task_id = L.create_task(data.name or "手动开机", data.account_id,
                            data.params, data.delay_min, data.delay_max)
    L.submit(task_id)
    return {"task_id": task_id}


@router.get("/api/launch/tasks")
def list_tasks(user: User = Depends(get_current_user)):
    return L.list_tasks()


@router.post("/api/launch/tasks/{task_id}/cancel")
def cancel_task(task_id: int, user: User = Depends(get_current_user)):
    L.cancel_task(task_id)
    return {"ok": True}


# ---------- 开机表单辅助 ----------

@router.get("/api/compute/presets")
def presets(user: User = Depends(get_current_user)):
    return C.QUICK_PRESETS


@router.get("/api/compute/ads")
async def ads(account_id: int, user: User = Depends(get_current_user)):
    return await U.oci_call(C.list_ads, account_id)


@router.get("/api/compute/subnets")
async def subnets(account_id: int, user: User = Depends(get_current_user)):
    return await U.oci_call(C.list_subnets, account_id)


@router.get("/api/compute/images")
async def images(account_id: int, shape: str = "", keyword: str = "",
                 user: User = Depends(get_current_user)):
    return await U.oci_call(C.list_images, account_id, None,
                            shape or None, keyword)


@router.get("/api/compute/shapes")
async def shapes(account_id: int, image_id: str = "",
                 user: User = Depends(get_current_user)):
    return await U.oci_call(C.list_shapes, account_id, None,
                            image_id or None)


@router.get("/api/compute/a1-audit")
async def a1_audit(account_id: int, user: User = Depends(get_current_user)):
    return await U.oci_call(C.a1_audit, account_id)
