"""Cloudflare DNS 管理。"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..deps import get_current_user
from ..db import User
from .. import cloudflare as CF
from . import _util as U

router = APIRouter(prefix="/api/cf")


class CFAccountIn(BaseModel):
    name: str
    token: str


@router.get("/accounts")
def list_accounts(user: User = Depends(get_current_user)):
    return CF.list_cf_accounts()


@router.post("/accounts")
def create_account(data: CFAccountIn, user: User = Depends(get_current_user)):
    return {"id": CF.create_cf_account(data.name.strip(), data.token.strip())}


@router.delete("/accounts/{cf_id}")
def delete_account(cf_id: int, user: User = Depends(get_current_user)):
    CF.delete_cf_account(cf_id)
    return {"ok": True}


@router.get("/zones")
async def zones(cf_id: int, user: User = Depends(get_current_user)):
    return await U.oci_call(CF.list_zones, cf_id)


@router.get("/dns")
async def dns(cf_id: int, zone_id: str, name: str = "",
              user: User = Depends(get_current_user)):
    return await U.oci_call(CF.list_dns, cf_id, zone_id, name)


class DnsIn(BaseModel):
    cf_id: int
    zone_id: str
    type: str = "A"
    name: str
    content: str
    ttl: int = 1
    proxied: bool = False


@router.post("/dns")
async def create_dns(data: DnsIn, user: User = Depends(get_current_user)):
    return await U.oci_call(CF.create_dns, data.cf_id, data.zone_id,
                            data.type, data.name, data.content,
                            data.ttl, data.proxied)


class DnsUpdateIn(BaseModel):
    cf_id: int
    zone_id: str
    content: str


@router.patch("/dns/{record_id}")
async def update_dns(record_id: str, data: DnsUpdateIn,
                     user: User = Depends(get_current_user)):
    return await U.oci_call(CF.update_dns, data.cf_id, data.zone_id,
                            record_id, data.content)


@router.delete("/dns/{record_id}")
async def delete_dns(record_id: str, cf_id: int, zone_id: str,
                     user: User = Depends(get_current_user)):
    return await U.oci_call(CF.delete_dns, cf_id, zone_id, record_id)


class AutoUpdateIn(BaseModel):
    cf_id: int
    zone_id: str
    record_name: str
    new_ip: str


@router.post("/dns/auto-update")
async def auto_update(data: AutoUpdateIn,
                      user: User = Depends(get_current_user)):
    return await U.oci_call(CF.auto_update_dns, data.cf_id, data.zone_id,
                            data.record_name, data.new_ip)
