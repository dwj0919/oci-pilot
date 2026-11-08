"""OCI SDK 客户端工厂 + 统一异常。所有公有函数第一个参数都是 account_id。"""
import contextlib
import os
import threading
from typing import Iterator

import oci
from sqlmodel import select

from ..config import DATA_DIR
from ..db import OCIAccount, get_session
from ..security import decrypt


class OCIError(Exception):
    """可直接展示给用户的错误。"""


class CapacityError(OCIError):
    """Out of host capacity: 缺货, 可重试。"""


class QuotaError(OCIError):
    """配额不足: 重试无用。"""


_proxy_lock = threading.Lock()


@contextlib.contextmanager
def _proxy_env(proxy: str | None) -> Iterator[None]:
    """requests 按每次请求读取环境变量做代理, 加锁保证并发账号互不干扰。"""
    if not proxy:
        yield
        return
    with _proxy_lock:
        old = {k: os.environ.get(k) for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")}
        os.environ["HTTP_PROXY"] = proxy
        os.environ["HTTPS_PROXY"] = proxy
        os.environ["http_proxy"] = proxy
        os.environ["https_proxy"] = proxy
        try:
            yield
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def get_account(account_id: int) -> OCIAccount:
    with get_session() as s:
        acc = s.get(OCIAccount, account_id)
        if not acc:
            raise OCIError(f"OCI 账号 #{account_id} 不存在")
        s.expunge(acc)
        return acc


def _translate(e: oci.exceptions.ServiceError) -> OCIError:
    msg = f"[{e.status}] {e.message or e.code}".strip()
    low = (e.message or "").lower()
    if e.status == 500 and "out of host capacity" in low:
        return CapacityError(msg)
    if e.status in (400, 429) and ("exceed" in low and "limit" in low or "quota" in low):
        return QuotaError(msg)
    return OCIError(msg)


class Clients:
    """按需懒加载各服务客户端。"""

    def __init__(self, account: OCIAccount):
        key = decrypt(account.private_key_enc)
        self.signer = oci.signer.Signer(
            tenancy=account.tenancy_ocid,
            user=account.user_ocid,
            fingerprint=account.fingerprint,
            private_key_file_location=None,
            private_key_content=key,
            pass_phrase=None,
        )
        self.config = {
            # 新版 oci SDK 的 validate_config 即使传了 signer 也要求这几个字段齐全
            # (key_file 可用 key_content 代替), 否则抛 InvalidConfig
            "region": account.region,
            "tenancy": account.tenancy_ocid,
            "user": account.user_ocid,
            "fingerprint": (account.fingerprint or "").strip().lower(),
            "key_content": key,
        }
        self.tenancy = account.tenancy_ocid
        self._cache = {}

    def _get(self, name: str, cls):
        if name not in self._cache:
            self._cache[name] = cls(self.config, signer=self.signer)
        return self._cache[name]

    @property
    def compute(self):
        return self._get("compute", oci.core.ComputeClient)

    @property
    def network(self):
        return self._get("network", oci.core.VirtualNetworkClient)

    @property
    def block(self):
        return self._get("block", oci.core.BlockstorageClient)

    @property
    def identity(self):
        return self._get("identity", oci.identity.IdentityClient)

    @property
    def objects(self):
        return self._get("objects", oci.object_storage.ObjectStorageClient)

    @property
    def monitoring(self):
        return self._get("monitoring", oci.monitoring.MonitoringClient)

    @property
    def limits(self):
        return self._get("limits", oci.limits.LimitsClient)


@contextlib.contextmanager
def oci_clients(account_id: int) -> Iterator[Clients]:
    """用法: with oci_clients(account_id) as c: c.compute.list_instances(...)"""
    account = get_account(account_id)
    with _proxy_env(account.proxy):
        try:
            yield Clients(account)
        except oci.exceptions.ServiceError as e:
            raise _translate(e) from e


def list_accounts() -> list[dict]:
    with get_session() as s:
        accs = s.exec(select(OCIAccount)).all()
        return [
            {"id": a.id, "name": a.name, "region": a.region,
             "tenancy": a.tenancy_ocid[:20] + "...", "proxy": a.proxy or ""}
            for a in accs
        ]


def create_account(name: str, tenancy_ocid: str, user_ocid: str, fingerprint: str,
                   region: str, private_key_pem: str, proxy: str = "") -> int:
    from ..security import encrypt
    with get_session() as s:
        acc = OCIAccount(name=name, tenancy_ocid=tenancy_ocid.strip(),
                         user_ocid=user_ocid.strip(), fingerprint=fingerprint.strip(),
                         region=region.strip(), private_key_enc=encrypt(private_key_pem),
                         proxy=proxy.strip() or None)
        s.add(acc)
        s.commit()
        s.refresh(acc)
        return acc.id


def delete_account(account_id: int):
    with get_session() as s:
        acc = s.get(OCIAccount, account_id)
        if acc:
            s.delete(acc)
            s.commit()


def test_account(account_id: int) -> dict:
    """一键测活: 调 identity 查租户信息。"""
    try:
        with oci_clients(account_id) as c:
            t = c.identity.get_tenancy(c.tenancy).data
            return {"ok": True, "tenancy_name": t.name, "home_region": t.home_region_key}
    except OCIError as e:
        return {"ok": False, "error": str(e)}
