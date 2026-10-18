"""Cloudflare: 域名解析管理, 换 IP 自动更新解析。"""
import httpx
from sqlmodel import select

from .db import CloudflareAccount, get_session
from .security import encrypt, decrypt

API = "https://api.cloudflare.com/client/v4"


def _token(cf_id: int) -> str:
    with get_session() as s:
        acc = s.get(CloudflareAccount, cf_id)
        if not acc:
            raise ValueError(f"Cloudflare 账号 #{cf_id} 不存在")
        return decrypt(acc.token_enc)


def _req(cf_id: int, method: str, path: str, **kw):
    token = _token(cf_id)
    with httpx.Client(timeout=30) as cli:
        r = cli.request(method, API + path,
                        headers={"Authorization": f"Bearer {token}",
                                 "Content-Type": "application/json"}, **kw)
    data = r.json()
    if not data.get("success"):
        errs = "; ".join(e.get("message", "") for e in data.get("errors", []))
        raise ValueError(f"Cloudflare API 错误: {errs or r.text[:200]}")
    return data["result"]


def create_cf_account(name: str, token: str) -> int:
    with get_session() as s:
        acc = CloudflareAccount(name=name, token_enc=encrypt(token))
        s.add(acc)
        s.commit()
        s.refresh(acc)
        return acc.id


def list_cf_accounts() -> list[dict]:
    with get_session() as s:
        accs = s.exec(select(CloudflareAccount)).all()
        return [{"id": a.id, "name": a.name} for a in accs]


def delete_cf_account(cf_id: int):
    with get_session() as s:
        acc = s.get(CloudflareAccount, cf_id)
        if acc:
            s.delete(acc)
            s.commit()


def list_zones(cf_id: int) -> list[dict]:
    zones = _req(cf_id, "GET", "/zones", params={"per_page": 50})
    return [{"id": z["id"], "name": z["name"], "status": z["status"]} for z in zones]


def list_dns(cf_id: int, zone_id: str, name: str = "") -> list[dict]:
    params = {"per_page": 100}
    if name:
        params["name"] = name
    recs = _req(cf_id, "GET", f"/zones/{zone_id}/dns_records", params=params)
    return [{"id": r["id"], "type": r["type"], "name": r["name"],
             "content": r["content"], "ttl": r["ttl"],
             "proxied": r.get("proxied")} for r in recs]


def create_dns(cf_id: int, zone_id: str, type_: str, name: str,
               content: str, ttl: int = 1, proxied: bool = False) -> dict:
    r = _req(cf_id, "POST", f"/zones/{zone_id}/dns_records", json={
        "type": type_, "name": name, "content": content,
        "ttl": ttl, "proxied": proxied})
    return {"id": r["id"]}


def update_dns(cf_id: int, zone_id: str, record_id: str,
               content: str) -> dict:
    _req(cf_id, "PATCH", f"/zones/{zone_id}/dns_records/{record_id}",
         json={"content": content})
    return {"ok": True}


def delete_dns(cf_id: int, zone_id: str, record_id: str) -> dict:
    _req(cf_id, "DELETE", f"/zones/{zone_id}/dns_records/{record_id}")
    return {"ok": True}


def auto_update_dns(cf_id: int, zone_id: str, record_name: str,
                    new_ip: str) -> dict:
    """换 IP 后自动更新 A 记录(只更新完全匹配的 A 记录)。"""
    recs = [r for r in list_dns(cf_id, zone_id, record_name)
            if r["type"] == "A" and r["name"] == record_name]
    updated = []
    for r in recs:
        if r["content"] != new_ip:
            update_dns(cf_id, zone_id, r["id"], new_ip)
            updated.append(r["id"])
    return {"ok": True, "updated": updated, "new_ip": new_ip}
