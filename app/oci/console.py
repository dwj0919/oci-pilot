"""串行控制台: SSH 连不上时的救命通道 + 控制台历史。"""
import base64
import logging
import time

import oci
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from .base import oci_clients, OCIError
from ..config import DATA_DIR

log = logging.getLogger("oci-pilot")


def _ephemeral_keypair() -> tuple[str, str]:
    """生成一次性 RSA 密钥对(控制台连接认证用)。"""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.OpenSSH,
        serialization.PublicFormat.OpenSSH).decode()
    return priv, pub


def _list_instance_connections(c, instance_id: str) -> list:
    try:
        return oci.pagination.list_call_get_all_results(
            c.compute.list_instance_console_connections,
            c.tenancy, instance_id=instance_id).data
    except Exception as e:
        log.warning("列出实例控制台连接失败: %s", e)
        return []


def _delete_instance_connections(c, instance_id: str) -> int:
    """删除某实例名下所有未删除的控制台连接, 返回数量(尽力而为)。"""
    n = 0
    for conn in _list_instance_connections(c, instance_id):
        if conn.lifecycle_state == "DELETED":
            continue
        try:
            c.compute.delete_instance_console_connection(conn.id)
            n += 1
        except Exception:
            pass
    return n


def _wait_connections_gone(c, instance_id: str, timeout: int = 20) -> None:
    """等旧连接终止干净(删除是异步的, 立刻重建可能仍撞 409)。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        left = [x for x in _list_instance_connections(c, instance_id)
                if x.lifecycle_state != "DELETED"]
        if not left:
            return
        time.sleep(2)


def _create_connection(c, instance_id: str, pub: str):
    """创建控制台连接; OCI 每实例只允许一个未终止连接, 遇到 409 先清理
    该实例的旧连接(多为之前没删的残留)再重试, 最多 3 轮。"""
    details = oci.core.models.CreateInstanceConsoleConnectionDetails(
        instance_id=instance_id, public_key=pub)
    last = None
    for _attempt in range(3):
        try:
            return c.compute.create_instance_console_connection(details).data
        except oci.exceptions.ServiceError as e:
            if e.status != 409:
                raise
            last = e
            n = _delete_instance_connections(c, instance_id)
            log.info("实例 %s 已有控制台连接(409), 已清理 %d 个旧连接后重试",
                     instance_id[-12:], n)
            _wait_connections_gone(c, instance_id)
    raise last


def create_connection_ephemeral(account_id: int, instance_id: str) -> dict:
    """创建控制台连接(浏览器 VNC 用): 一次性私钥只在内存中流转, 不落盘。

    返回 connection_id / region / private_key / 官方 VNC 连接串。
    """
    from ..db import get_session
    from ..db import OCIAccount
    with oci_clients(account_id) as c:
        with get_session() as s:
            region = s.get(OCIAccount, account_id).region
        priv, pub = _ephemeral_keypair()
        conn = _create_connection(c, instance_id, pub)
        return {"connection_id": conn.id, "region": region,
                "private_key": priv,
                "vnc_connection_string": conn.vnc_connection_string or ""}


def create_console_connection(account_id: int, instance_id: str) -> dict:
    """创建串行控制台连接, 返回可直接用的 SSH 命令和私钥(下载)。"""
    from ..db import get_session
    from ..db import OCIAccount
    with oci_clients(account_id) as c:
        with get_session() as s:
            region = s.get(OCIAccount, account_id).region
        priv, pub = _ephemeral_keypair()
        conn = _create_connection(c, instance_id, pub)
        key_path = DATA_DIR / f"console_{conn.id}.pem"
        key_path.write_text(priv)
        try:
            import os
            os.chmod(key_path, 0o600)
        except OSError:
            pass
        ssh_cmd = (
            f"ssh -i console_{conn.id}.pem "
            f"-o HostKeyAlgorithms=+ssh-rsa "
            f"-o PubkeyAcceptedAlgorithms=+ssh-rsa "
            f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
            f"-o ProxyCommand='ssh -W %h:%p -i console_{conn.id}.pem -p 443 "
            f"-o HostKeyAlgorithms=+ssh-rsa "
            f"-o PubkeyAcceptedAlgorithms=+ssh-rsa "
            f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
            f"{conn.id}@instance-console.{region}.oci.oraclecloud.com' "
            f"-p 443 {instance_id}"
        )
        return {"connection_id": conn.id, "ssh_command": ssh_cmd,
                "key_file": key_path.name,
                "note": "把私钥下载到本机后执行上面的 SSH 命令即可进入串行控制台"}


def delete_console_connection(account_id: int, connection_id: str) -> dict:
    with oci_clients(account_id) as c:
        c.compute.delete_instance_console_connection(connection_id)
        key_path = DATA_DIR / f"console_{connection_id}.pem"
        if key_path.exists():
            key_path.unlink()
        return {"ok": True}


def wait_connection_active(account_id: int, connection_id: str,
                           timeout: int = 90) -> bool:
    """等控制台连接进入 ACTIVE: 刚创建时是 CREATING, 此时建隧道会被拒
    (Authentication failed), 浏览器秒连必踩, 必须等就绪。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with oci_clients(account_id) as c:
                state = c.compute.get_instance_console_connection(
                    connection_id).data.lifecycle_state
            if state == "ACTIVE":
                return True
            if state in ("DELETED", "FAILED"):
                return False
        except Exception:
            pass
        time.sleep(2)
    return False


def capture_console_history(account_id: int, instance_id: str) -> dict:
    with oci_clients(account_id) as c:
        h = c.compute.capture_console_history(
            oci.core.models.CaptureConsoleHistoryDetails(
                instance_id=instance_id)).data
        return {"history_id": h.id, "state": h.lifecycle_state}


def get_console_history(account_id: int, history_id: str) -> dict:
    with oci_clients(account_id) as c:
        h = c.compute.get_console_history(history_id).data
        raw = h.data or ""
        try:
            text = base64.b64decode(raw).decode("utf-8", errors="replace")
        except Exception:
            text = raw
        return {"state": h.lifecycle_state, "content": text[-20000:]}
