"""串行控制台: SSH 连不上时的救命通道 + 控制台历史。"""
import base64

import oci
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from .base import oci_clients, OCIError
from ..config import DATA_DIR


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


def create_console_connection(account_id: int, instance_id: str) -> dict:
    """创建串行控制台连接, 返回可直接用的 SSH 命令和私钥(下载)。"""
    from ..db import get_session
    from ..db import OCIAccount
    with oci_clients(account_id) as c:
        with get_session() as s:
            region = s.get(OCIAccount, account_id).region
        priv, pub = _ephemeral_keypair()
        conn = c.compute.create_instance_console_connection(
            oci.core.models.CreateInstanceConsoleConnectionDetails(
                instance_id=instance_id, public_key=pub)).data
        key_path = DATA_DIR / f"console_{conn.id}.pem"
        key_path.write_text(priv)
        try:
            import os
            os.chmod(key_path, 0o600)
        except OSError:
            pass
        ssh_cmd = (
            f"ssh -i console_{conn.id}.pem "
            f"-o ProxyCommand='ssh -W %h:%p -p 443 "
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
