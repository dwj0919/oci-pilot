"""身份: 用户管理 / API 密钥 / 配额查询。"""
import oci

from .base import oci_clients, OCIError


def list_users(account_id: int) -> list[dict]:
    with oci_clients(account_id) as c:
        users = oci.pagination.list_call_get_all_results(
            c.identity.list_users, c.tenancy).data
        return [{"id": u.id, "name": u.name, "email": u.email,
                 "state": u.lifecycle_state,
                 "time_created": str(u.time_created)} for u in users
                if u.lifecycle_state != "DELETED"]


def create_user(account_id: int, name: str, email: str = "",
                description: str = "") -> dict:
    with oci_clients(account_id) as c:
        u = c.identity.create_user(
            oci.identity.models.CreateUserDetails(
                compartment_id=c.tenancy, name=name,
                description=description or None, email=email or None)).data
        return {"id": u.id, "name": u.name}


def delete_user(account_id: int, user_id: str) -> dict:
    with oci_clients(account_id) as c:
        c.identity.delete_user(user_id)
        return {"ok": True}


def list_api_keys(account_id: int, user_id: str) -> list[dict]:
    with oci_clients(account_id) as c:
        keys = oci.pagination.list_call_get_all_results(
            c.identity.list_api_keys, user_id).data
        return [{"id": k.key_id, "fingerprint": k.fingerprint,
                 "state": k.lifecycle_state,
                 "time_created": str(k.time_created)} for k in keys
                if k.lifecycle_state != "DELETED"]


def delete_api_key(account_id: int, user_id: str, key_id: str) -> dict:
    with oci_clients(account_id) as c:
        c.identity.delete_api_key(user_id, key_id)
        return {"ok": True}


def get_quotas(account_id: int, compartment_id: str | None = None) -> list[dict]:
    """配额查询: 返回 compute / network / storage 各服务的可用额度。"""
    out = []
    with oci_clients(account_id) as c:
        comp = compartment_id or c.tenancy
        for service in ("compute", "network", "block-storage", "object-storage"):
            try:
                vals = oci.pagination.list_call_get_all_results(
                    c.limits.list_limit_values, comp, service_name=service).data
                for v in vals:
                    avail = getattr(v, "available", None)
                    if avail is None:
                        continue
                    out.append({"service": service, "name": v.name,
                                "scope": v.scope_type,
                                "ad": getattr(v, "availability_domain", None),
                                "available": avail})
            except OCIError:
                continue
    return out


def get_tenancy_info(account_id: int) -> dict:
    with oci_clients(account_id) as c:
        t = c.identity.get_tenancy(c.tenancy).data
        return {"name": t.name, "home_region": t.home_region_key,
                "description": t.description}
