"""网络: 换 IP / 保留 IP / IPv6 / 安全组开端口。"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..deps import get_current_user
from ..db import User
from ..oci import network as N
from . import _util as U

router = APIRouter(prefix="/api/network")


class AccountIn(BaseModel):
    account_id: int


@router.get("/vnic/{vnic_id}")
async def get_vnic(vnic_id: str, account_id: int,
                   user: User = Depends(get_current_user)):
    return await U.oci_call(N.get_vnic, account_id, vnic_id)


@router.post("/vnic/{vnic_id}/change-ip")
async def change_ip(vnic_id: str, data: AccountIn,
                    user: User = Depends(get_current_user)):
    return await U.oci_call(N.change_public_ip, data.account_id, vnic_id)


@router.get("/public-ips")
async def list_public_ips(account_id: int,
                          user: User = Depends(get_current_user)):
    return await U.oci_call(N.list_public_ips, account_id)


class CreateReservedIn(BaseModel):
    account_id: int
    name: str = ""


@router.post("/public-ips")
async def create_reserved(data: CreateReservedIn,
                         user: User = Depends(get_current_user)):
    return await U.oci_call(N.create_reserved_ip, data.account_id,
                            None, data.name)


class AssignIn(BaseModel):
    account_id: int
    private_ip_id: str


@router.post("/public-ips/{ip_id}/assign")
async def assign(ip_id: str, data: AssignIn,
                 user: User = Depends(get_current_user)):
    return await U.oci_call(N.assign_public_ip, data.account_id, ip_id,
                            data.private_ip_id)


@router.post("/public-ips/{ip_id}/unassign")
async def unassign(ip_id: str, data: AccountIn,
                   user: User = Depends(get_current_user)):
    return await U.oci_call(N.unassign_public_ip, data.account_id, ip_id)


@router.delete("/public-ips/{ip_id}")
async def delete_ip(ip_id: str, account_id: int,
                    user: User = Depends(get_current_user)):
    return await U.oci_call(N.delete_public_ip, account_id, ip_id)


@router.post("/vnic/{vnic_id}/ipv6")
async def attach_ipv6(vnic_id: str, data: AccountIn,
                      user: User = Depends(get_current_user)):
    return await U.oci_call(N.attach_ipv6, data.account_id, vnic_id)


@router.delete("/ipv6/{ipv6_id}")
async def detach_ipv6(ipv6_id: str, account_id: int,
                      user: User = Depends(get_current_user)):
    return await U.oci_call(N.detach_ipv6, account_id, ipv6_id)


@router.get("/vcns")
async def vcns(account_id: int, user: User = Depends(get_current_user)):
    return await U.oci_call(N.list_vcns, account_id)


@router.get("/security-lists")
async def security_lists(account_id: int, vcn_id: str = "",
                         user: User = Depends(get_current_user)):
    return await U.oci_call(N.list_security_lists, account_id, None,
                            vcn_id or None)


class OpenPortIn(BaseModel):
    account_id: int
    port: int
    protocol: str = "tcp"


@router.post("/security-lists/{sl_id}/open-port")
async def open_port(sl_id: str, data: OpenPortIn,
                    user: User = Depends(get_current_user)):
    return await U.oci_call(N.open_security_port, data.account_id, sl_id,
                            data.port, data.protocol)
