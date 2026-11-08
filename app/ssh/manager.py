"""SSH 会话管理: paramiko 长连接 + 异步输出队列 + SFTP 复用。

设计说明:
- 每个会话 = 一个 SSHClient + 一个交互式 shell channel。
- 用 daemon 线程阻塞读 channel.recv, 通过 loop.call_soon_threadsafe
  把字节投递到 asyncio.Queue, WebSocket 再从队列取出发给浏览器。
- 所有阻塞调用都由上层用 asyncio.to_thread 包裹, 这里只管同步逻辑。
"""
import asyncio
import io
import threading
import uuid

import paramiko
from sqlmodel import select

from ..db import Host, SSHKey, get_session
from ..security import decrypt


def validate_private_key(pem: str) -> bool:
    """校验私钥能否被 paramiko 解析。"""
    try:
        _load_pkey(pem)
        return True
    except Exception:
        return False


# paramiko 5 已移除 DSSKey, 这里动态取值以兼容不同版本
_KEY_CLASSES = [c for c in (getattr(paramiko, "RSAKey", None),
                            getattr(paramiko, "Ed25519Key", None),
                            getattr(paramiko, "ECDSAKey", None)) if c]


def _load_pkey(pem: str):
    """逐个尝试常见私钥类型, 兼容 RSA / Ed25519 / ECDSA。"""
    for cls in _KEY_CLASSES:
        try:
            return cls.from_private_key(io.StringIO(pem))
        except Exception:
            continue
    raise ValueError("无法解析私钥(支持 RSA / Ed25519 / ECDSA 的 OpenSSH 或 PEM 格式, 加密私钥暂不支持)")


class SSHSessionManager:
    def __init__(self):
        self._sessions: dict[str, dict] = {}
        self._lock = threading.Lock()

    # ---------- 会话生命周期 ----------

    async def create_session(self, host: dict, password: str | None = None,
                             private_key: str | None = None) -> str:
        """建立 SSH 连接并打开交互式 shell, 返回 session_id。

        host: {"hostname", "port", "username"}。
        必须在 async 上下文调用(内部用 to_thread 做阻塞 connect)。
        """
        loop = asyncio.get_running_loop()
        hostname = host["hostname"]
        port = int(host.get("port") or 22)
        username = host.get("username") or "root"
        if not private_key and not password:
            raise ValueError("该主机未配置登录凭证, 请先编辑主机填写密码或关联密钥")

        def _connect() -> paramiko.SSHClient:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            kwargs: dict = {
                "hostname": hostname, "port": port, "username": username,
                "timeout": 15, "banner_timeout": 15, "auth_timeout": 15,
                "allow_agent": False, "look_for_keys": False,
            }
            if private_key:
                kwargs["pkey"] = _load_pkey(private_key)
            elif password:
                kwargs["password"] = password
            client.connect(**kwargs)
            return client

        client = await asyncio.to_thread(_connect)
        try:
            channel = client.get_transport().open_session()
            channel.get_pty(term="xterm-256color", width=120, height=30)
            channel.invoke_shell()
        except Exception:
            client.close()  # 建连成功但开通道失败时回收连接, 避免泄漏
            raise

        queue: asyncio.Queue = asyncio.Queue()

        def _reader():
            # 阻塞读远端输出, 投递到事件循环队列; 收到空字节表示通道已关闭
            try:
                while True:
                    data = channel.recv(8192)
                    if not data:
                        break
                    loop.call_soon_threadsafe(queue.put_nowait, data)
            except Exception:
                pass
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=_reader, daemon=True,
                         name=f"ssh-reader-{hostname}").start()

        sid = uuid.uuid4().hex
        with self._lock:
            self._sessions[sid] = {
                "client": client, "channel": channel, "queue": queue,
                "sftp": None, "hostname": hostname,
            }
        return sid

    async def create_from_host_id(self, host_id: int) -> str:
        """从 DB 的 Host 记录创建会话(自动解密密码 / 关联密钥)。"""
        host, password, pkey = await asyncio.to_thread(
            load_host_credentials, host_id)
        return await self.create_session(host, password, pkey)

    def get(self, session_id: str) -> dict | None:
        with self._lock:
            return self._sessions.get(session_id)

    async def write(self, session_id: str, data: bytes):
        """向远端 shell 发送输入字节。"""
        sess = self.get(session_id)
        if not sess:
            raise KeyError("会话不存在或已关闭")
        channel = sess["channel"]
        if channel.closed:
            raise ConnectionError("SSH 通道已关闭")

        def _send_all():
            buf = data
            while buf:
                n = channel.send(buf)
                if n <= 0:
                    raise ConnectionError("发送失败, 通道可能已断开")
                buf = buf[n:]

        await asyncio.to_thread(_send_all)

    def resize(self, session_id: str, cols: int, rows: int):
        """终端尺寸变化时同步 pty 大小。"""
        sess = self.get(session_id)
        if sess and not sess["channel"].closed:
            try:
                sess["channel"].resize_pty(width=max(1, cols),
                                           height=max(1, rows))
            except Exception:
                pass

    def close(self, session_id: str):
        """关闭会话: SFTP -> channel -> client。"""
        with self._lock:
            sess = self._sessions.pop(session_id, None)
        if not sess:
            return
        for key in ("sftp", "channel", "client"):
            try:
                obj = sess.get(key)
                if obj is not None:
                    obj.close()
            except Exception:
                pass

    def get_sftp(self, session_id: str):
        """获取(并缓存)该会话的 SFTPClient。调用方用 to_thread 包裹。"""
        sess = self.get(session_id)
        if not sess:
            raise KeyError("会话不存在或已关闭")
        if sess["sftp"] is None:
            sess["sftp"] = sess["client"].get_transport().open_sftp_client()
        return sess["sftp"]

    # ---------- OCI 一键同步 ----------

    def sync_oci_hosts(self, account_id: int) -> dict:
        """从 OCI 拉取实例列表, 按 oci_instance_id upsert 到 Host 表。

        username 默认 opc(甲骨文官方镜像默认用户); 已有记录只更新 IP。
        没有任何 IP 的实例跳过(连不上, 同步了也没意义)。
        """
        from ..oci import compute as oci_compute

        instances = oci_compute.list_instances(account_id)
        created, updated = 0, 0
        with get_session() as s:
            for inst in instances:
                if inst.get("state") in ("TERMINATED", "TERMINATING"):
                    continue
                ip = inst.get("public_ip") or inst.get("private_ip")
                if not ip:
                    continue
                existing = s.exec(
                    select(Host).where(
                        Host.oci_instance_id == inst["id"],
                        Host.oci_account_id == account_id)).first()
                if existing:
                    existing.hostname = ip
                    if inst.get("name"):
                        existing.name = inst["name"]
                    s.add(existing)
                    updated += 1
                else:
                    s.add(Host(
                        name=inst.get("name") or inst["id"][:16],
                        hostname=ip, port=22, username="opc",
                        auth_type="password", tags="oci",
                        oci_account_id=account_id,
                        oci_instance_id=inst["id"]))
                    created += 1
            s.commit()
        return {"created": created, "updated": updated,
                "synced": created + updated}


def load_host_credentials(host_id: int):
    """同步读取一台主机的连接信息与凭证, 返回 (host_dict, password, pkey_pem)。

    供交互式会话与运维任务执行器共用; 在线程里调用。
    """
    with get_session() as s:
        h = s.get(Host, host_id)
        if not h:
            raise ValueError(f"主机 #{host_id} 不存在")
        password = decrypt(h.password_enc) if h.password_enc else None
        pkey = None
        if h.auth_type == "key" and h.key_id:
            k = s.get(SSHKey, h.key_id)
            if not k:
                raise ValueError("该主机关联的密钥已被删除")
            pkey = decrypt(k.private_key_enc)
        return {"hostname": h.hostname, "port": h.port,
                "username": h.username}, password, pkey


def connect_client(host: dict, password: str | None = None,
                   private_key: str | None = None,
                   timeout: int = 15) -> paramiko.SSHClient:
    """建立一条非交互 SSH 连接(不开 shell), 供命令执行器使用。"""
    if not private_key and not password:
        raise ValueError("该主机未配置登录凭证, 请先编辑主机填写密码或关联密钥")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs: dict = {
        "hostname": host["hostname"], "port": int(host.get("port") or 22),
        "username": host.get("username") or "root",
        "timeout": timeout, "banner_timeout": timeout,
        "auth_timeout": timeout, "allow_agent": False, "look_for_keys": False,
    }
    if private_key:
        kwargs["pkey"] = _load_pkey(private_key)
    elif password:
        kwargs["password"] = password
    client.connect(**kwargs)
    return client
