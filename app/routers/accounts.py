"""OCI 账号(profile)管理: 多 profile 增删改查 + 测活 + 配额。"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..deps import get_current_user
from ..db import User
from ..oci import base as oci_base
from . import _util as U

router = APIRouter(prefix="/api/accounts")


class AccountIn(BaseModel):
    name: str
    tenancy_ocid: str
    user_ocid: str
    fingerprint: str
    region: str
    private_key_pem: str
    proxy: str = ""


@router.get("")
def list_accounts(user: User = Depends(get_current_user)):
    return oci_base.list_accounts()


@router.post("")
def create_account(data: AccountIn, user: User = Depends(get_current_user)):
    return {"id": oci_base.create_account(
        data.name.strip(), data.tenancy_ocid, data.user_ocid,
        data.fingerprint, data.region, data.private_key_pem, data.proxy)}


@router.delete("/{account_id}")
def delete_account(account_id: int, user: User = Depends(get_current_user)):
    oci_base.delete_account(account_id)
    return {"ok": True}


@router.post("/{account_id}/test")
async def test_account(account_id: int, user: User = Depends(get_current_user)):
    return await U.oci_call(oci_base.test_account, account_id)


@router.get("/{account_id}/quotas")
async def quotas(account_id: int, user: User = Depends(get_current_user)):
    from ..oci import identity
    return await U.oci_call(identity.get_quotas, account_id)


@router.get("/{account_id}/tenancy")
async def tenancy(account_id: int, user: User = Depends(get_current_user)):
    from ..oci import identity
    return await U.oci_call(identity.get_tenancy_info, account_id)
