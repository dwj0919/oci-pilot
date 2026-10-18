"""邮件服务: 发信域名/DKIM/发件人/SMTP 账号/测试发信。"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..deps import get_current_user
from ..db import User
from ..oci import email as E
from ..oci import identity as I
from . import _util as U

router = APIRouter(prefix="/api/email")


@router.get("/domains")
async def domains(account_id: int, user: User = Depends(get_current_user)):
    return await U.oci_call(E.list_email_domains, account_id)


class DomainIn(BaseModel):
    account_id: int
    compartment_id: str
    name: str


@router.post("/domains")
async def create_domain(data: DomainIn, user: User = Depends(get_current_user)):
    return await U.oci_call(E.create_email_domain, data.account_id,
                            data.compartment_id, data.name)


@router.delete("/domains/{domain_id}")
async def delete_domain(domain_id: str, account_id: int,
                        user: User = Depends(get_current_user)):
    return await U.oci_call(E.delete_email_domain, account_id, domain_id)


@router.get("/senders")
async def senders(account_id: int, user: User = Depends(get_current_user)):
    return await U.oci_call(E.list_senders, account_id)


class SenderIn(BaseModel):
    account_id: int
    compartment_id: str
    email: str


@router.post("/senders")
async def create_sender(data: SenderIn, user: User = Depends(get_current_user)):
    return await U.oci_call(E.create_sender, data.account_id,
                            data.compartment_id, data.email)


@router.delete("/senders/{sender_id}")
async def delete_sender(sender_id: str, account_id: int,
                        user: User = Depends(get_current_user)):
    return await U.oci_call(E.delete_sender, account_id, sender_id)


@router.get("/users")
async def iam_users(account_id: int, user: User = Depends(get_current_user)):
    return await U.oci_call(I.list_users, account_id)


class SmtpIn(BaseModel):
    account_id: int
    user_id: str
    description: str = "oci-pilot"


@router.post("/smtp")
async def create_smtp(data: SmtpIn, user: User = Depends(get_current_user)):
    return await U.oci_call(E.create_smtp_credential, data.account_id,
                            data.user_id, data.description)


@router.get("/smtp")
async def list_smtp(account_id: int, user_id: str,
                    user: User = Depends(get_current_user)):
    return await U.oci_call(E.list_smtp_credentials, account_id, user_id)


@router.delete("/smtp")
async def delete_smtp(account_id: int, user_id: str, cred_id: str,
                      user: User = Depends(get_current_user)):
    return await U.oci_call(E.delete_smtp_credential, account_id,
                            user_id, cred_id)


class TestSendIn(BaseModel):
    account_id: int
    smtp_host: str
    smtp_port: int = 587
    smtp_user: str
    smtp_password: str
    from_addr: str
    to_addr: str


@router.post("/test")
async def test_send(data: TestSendIn, user: User = Depends(get_current_user)):
    return await U.oci_call(E.test_send, data.account_id, data.smtp_host,
                            data.smtp_port, data.smtp_user,
                            data.smtp_password, data.from_addr, data.to_addr)
