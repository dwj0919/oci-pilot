"""邮件服务 (OCI Email Delivery): 发信域名 + DKIM + SMTP 账号 + 测试发信。"""
import smtplib
from email.mime.text import MIMEText

import oci

from .base import oci_clients, OCIError

SMTP_ENDPOINT_TMPL = "smtp.email.{region}.oci.oraclecloud.com"


def list_email_domains(account_id: int, compartment_id: str | None = None) -> list[dict]:
    with oci_clients(account_id) as c:
        comp = compartment_id or c.tenancy
        domains = oci.pagination.list_call_get_all_results(
            c.email.list_email_domains, comp).data
        return [{"id": d.id, "name": d.name, "state": d.lifecycle_state} for d in domains]


def create_email_domain(account_id: int, compartment_id: str, name: str) -> dict:
    """创建发信域名, 返回 DKIM 的 CNAME 记录(去 DNS 商添加后生效)。"""
    with oci_clients(account_id) as c:
        comp = compartment_id or c.tenancy
        d = c.email.create_email_domain(
            oci.email.models.CreateEmailDomainDetails(
                compartment_id=comp, name=name)).data
        dkim = c.email.create_dkim(
            oci.email.models.CreateDkimDetails(
                name=f"{name}-dkim", email_domain_id=d.id)).data
        records = [{"name": r.name, "type": r.record_type, "value": r.value}
                   for r in (dkim.dns_records or [])]
        return {"id": d.id, "name": d.name, "dkim_records": records,
                "note": "把上面的 CNAME 记录添加到域名 DNS 后, DKIM 才会生效"}


def delete_email_domain(account_id: int, domain_id: str) -> dict:
    with oci_clients(account_id) as c:
        c.email.delete_email_domain(domain_id)
        return {"ok": True}


def list_senders(account_id: int, compartment_id: str | None = None) -> list[dict]:
    with oci_clients(account_id) as c:
        comp = compartment_id or c.tenancy
        senders = oci.pagination.list_call_get_all_results(
            c.email.list_senders, comp).data
        return [{"id": s.id, "email": s.email_address,
                 "state": s.lifecycle_state} for s in senders]


def create_sender(account_id: int, compartment_id: str, email_address: str) -> dict:
    with oci_clients(account_id) as c:
        comp = compartment_id or c.tenancy
        s = c.email.create_sender(
            oci.email.models.CreateSenderDetails(
                compartment_id=comp, email_address=email_address)).data
        return {"id": s.id, "email": s.email_address}


def delete_sender(account_id: int, sender_id: str) -> dict:
    with oci_clients(account_id) as c:
        c.email.delete_sender(sender_id)
        return {"ok": True}


def create_smtp_credential(account_id: int, user_id: str,
                           description: str = "oci-pilot") -> dict:
    """创建 SMTP 账号, 密码只返回一次, 请立即保存。"""
    with oci_clients(account_id) as c:
        cred = c.identity.create_smtp_credential(
            user_id,
            oci.identity.models.CreateSmtpCredentialDetails(
                description=description)).data
        from ..db import get_session, OCIAccount
        with get_session() as s:
            region = s.get(OCIAccount, account_id).region
        return {"id": cred.id, "username": cred.username,
                "password": cred.password,
                "smtp_host": SMTP_ENDPOINT_TMPL.format(region=region),
                "smtp_port": 587,
                "warning": "密码只显示一次, 请立即保存"}


def list_smtp_credentials(account_id: int, user_id: str) -> list[dict]:
    with oci_clients(account_id) as c:
        creds = oci.pagination.list_call_get_all_results(
            c.identity.list_smtp_credentials, user_id).data
        return [{"id": x.id, "username": x.username,
                 "state": x.lifecycle_state,
                 "time_created": str(x.time_created)} for x in creds
                if x.lifecycle_state != "DELETED"]


def delete_smtp_credential(account_id: int, user_id: str, cred_id: str) -> dict:
    with oci_clients(account_id) as c:
        c.identity.delete_smtp_credential(user_id, cred_id)
        return {"ok": True}


def test_send(account_id: int, smtp_host: str, smtp_port: int,
              smtp_user: str, smtp_password: str,
              from_addr: str, to_addr: str) -> dict:
    """测试发信。"""
    msg = MIMEText("这是一封来自 oci-pilot 的测试邮件。", "plain", "utf-8")
    msg["Subject"] = "oci-pilot 发信测试"
    msg["From"] = from_addr
    msg["To"] = to_addr
    try:
        with smtplib.SMTP(smtp_host, int(smtp_port), timeout=20) as s:
            s.starttls()
            s.login(smtp_user, smtp_password)
            s.sendmail(from_addr, [to_addr], msg.as_string())
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        raise OCIError(f"发信失败: {e}") from e
