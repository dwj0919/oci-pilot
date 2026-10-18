"""硬盘: 数据卷 / 启动盘管理。"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..deps import get_current_user
from ..db import User
from ..oci import volumes as V
from . import _util as U

router = APIRouter(prefix="/api/volumes")


class AccountIn(BaseModel):
    account_id: int


@router.get("")
async def list_volumes(account_id: int,
                       user: User = Depends(get_current_user)):
    return await U.oci_call(V.list_volumes, account_id)


@router.get("/attachments")
async def attachments(account_id: int, instance_id: str = "",
                      user: User = Depends(get_current_user)):
    return await U.oci_call(V.list_attachments, account_id, None,
                            instance_id or None)


class CreateIn(BaseModel):
    account_id: int
    compartment_id: str
    ad: str
    size_gb: int
    name: str = ""
    vpus_per_gb: int = 10


@router.post("")
async def create(data: CreateIn, user: User = Depends(get_current_user)):
    return await U.oci_call(V.create_volume, data.account_id,
                            data.compartment_id, data.ad, data.size_gb,
                            data.name, data.vpus_per_gb)


class ResizeIn(BaseModel):
    account_id: int
    size_gb: int
    is_boot: bool = False


@router.put("/{volume_id}/resize")
async def resize(volume_id: str, data: ResizeIn,
                 user: User = Depends(get_current_user)):
    return await U.oci_call(V.resize_volume, data.account_id, volume_id,
                            data.size_gb, data.is_boot)


class PerfIn(BaseModel):
    account_id: int
    vpus_per_gb: int
    is_boot: bool = False


@router.put("/{volume_id}/performance")
async def performance(volume_id: str, data: PerfIn,
                      user: User = Depends(get_current_user)):
    return await U.oci_call(V.set_volume_performance, data.account_id,
                            volume_id, data.vpus_per_gb, data.is_boot)


@router.delete("/{volume_id}")
async def delete(volume_id: str, account_id: int, is_boot: bool = False,
                 user: User = Depends(get_current_user)):
    return await U.oci_call(V.delete_volume, account_id, volume_id, is_boot)


class AttachIn(BaseModel):
    account_id: int
    instance_id: str
    is_boot: bool = False


@router.post("/{volume_id}/attach")
async def attach(volume_id: str, data: AttachIn,
                 user: User = Depends(get_current_user)):
    return await U.oci_call(V.attach_volume, data.account_id,
                            data.instance_id, volume_id, data.is_boot)


@router.post("/attachments/{att_id}/detach")
async def detach(att_id: str, data: AccountIn,
                 user: User = Depends(get_current_user)):
    return await U.oci_call(V.detach_volume, data.account_id, att_id)
