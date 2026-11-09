"""浏览器图形控制台 (VNC): 会话管理 + OCI 控制台 SSH 隧道。

链路: 浏览器 noVNC <-> 面板 WebSocket <-> 本地 TCP socket <-> OpenSSH
子进程 (官方 vnc_connection_string 同款双跳 + -L 转发) <-> 实例 VNC (5900)。

为什么不用 paramiko: OCI 内层控制台端点是老式 sshd (主机密钥只有
ssh-rsa、加密 CBC、MAC/KEX 皆 SHA-1 系), 而 paramiko 5 已把 ssh-rsa
从算法注册表里整个移除, 无法协商; 系统 OpenSSH 仍实现这些算法,
用 -o 选项逐项加回即可, 且这正是官方连接串的目标客户端。

一次性私钥仅在会话期间以 0600 临时文件存在 (ssh -i 需要文件),
会话结束立即删除; 会话结束同时删除 OCI 侧控制台连接。
"""
import logging
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time

from .config import DATA_DIR

log = logging.getLogger("oci-pilot")

SESSION_TTL = 900        # 会话最长存活(秒), 到点强制断开
CONNECT_WINDOW = 180     # 创建后多久没建立隧道就回收(秒)

_sessions: dict[str, "VncSession"] = {}
_lock = threading.Lock()
_sweeper_started = False


class VncSession:
    def __init__(self, username: str, account_id: int, instance_id: str,
                 conn: dict):
        self.id = secrets.token_urlsafe(24)
        self.username = username
        self.account_id = account_id
        self.instance_id = instance_id
        self.connection_id = conn["connection_id"]
        self.region = conn["region"]
        self.private_key = conn["private_key"]
        self.created_at = time.time()
        self.connected = False
        self.claimed = False  # 是否已有 WS 在连接/使用(防并发占用)
        self.tunnel = None  # _SshTunnel

    def attach(self, tunnel):
        self.tunnel = tunnel
        self.connected = True

    def close_tunnel(self):
        if self.tunnel is not None:
            try:
                self.tunnel.close()
            except Exception:
                pass
            self.tunnel = None
        self.connected = False


def register(username: str, account_id: int, instance_id: str,
             conn: dict) -> VncSession:
    sess = VncSession(username, account_id, instance_id, conn)
    with _lock:
        _sessions[sess.id] = sess
    _ensure_sweeper()
    return sess


def get(session_id: str) -> VncSession | None:
    with _lock:
        sess = _sessions.get(session_id)
    if sess is None or _expired(sess):
        return None
    return sess


def drop(session_id: str) -> VncSession | None:
    with _lock:
        return _sessions.pop(session_id, None)


def claim(sess: VncSession) -> bool:
    """原子占用一个会话给某条 WS 连接用, 已被占用返回 False。"""
    with _lock:
        if sess.claimed:
            return False
        sess.claimed = True
        return True


def _expired(sess: VncSession) -> bool:
    age = time.time() - sess.created_at
    return age > SESSION_TTL or (not sess.connected and age > CONNECT_WINDOW)


def close_session(sess: VncSession) -> None:
    """关隧道 + 删除 OCI 侧控制台连接(尽力而为, 失败只记日志)。"""
    sess.close_tunnel()
    try:
        from .oci import console as CC
        CC.delete_console_connection(sess.account_id, sess.connection_id)
    except Exception as e:
        log.warning("VNC 会话 %s 删除 OCI 控制台连接失败: %s", sess.id, e)


def _ensure_sweeper():
    global _sweeper_started
    if _sweeper_started:
        return
    _sweeper_started = True

    def _loop():
        while True:
            time.sleep(60)
            with _lock:
                dead = [s for s in _sessions.values() if _expired(s)]
                for s in dead:
                    _sessions.pop(s.id, None)
            for s in dead:
                log.info("VNC 会话 %s 过期回收", s.id)
                close_session(s)

    threading.Thread(target=_loop, name="vnc-sweeper", daemon=True).start()


# OCI 内层控制台端点是老式 sshd, 现代客户端默认禁用的算法逐项加回
_LEGACY_OPTS = [
    "-o", "KexAlgorithms=+diffie-hellman-group14-sha1,"
          "diffie-hellman-group1-sha1,diffie-hellman-group-exchange-sha1",
    "-o", "HostKeyAlgorithms=+ssh-rsa",
    "-o", "PubkeyAcceptedAlgorithms=+ssh-rsa",
    "-o", "Ciphers=+aes128-cbc,aes192-cbc,aes256-cbc,3des-cbc",
    "-o", "MACs=+hmac-sha1,hmac-sha1-96,hmac-md5,hmac-md5-96",
]
_COMMON_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "ConnectTimeout=30",
    "-o", "LogLevel=ERROR",
    "-o", "BatchMode=yes",
]


class _SshTunnel:
    """OpenSSH 子进程 + 本地转发 socket 组成的数据通道。"""

    def __init__(self, proc, sock, key_path):
        self._proc = proc
        self._sock = sock
        self._key_path = key_path

    def recv(self, n: int) -> bytes:
        return self._sock.recv(n)

    def sendall(self, data: bytes) -> None:
        self._sock.sendall(data)

    def close(self) -> None:
        try:
            self._sock.close()
        except Exception:
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        try:
            self._key_path.unlink(missing_ok=True)
        except Exception:
            pass


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def open_vnc_tunnel(sess: VncSession) -> _SshTunnel:
    """起 OpenSSH 双跳隧道并连上本地转发口, 返回数据通道。

    阻塞调用 (等 ssh 就绪最长约 60 秒), 请在线程里执行。失败抛
    RuntimeError, 消息带 ssh 的 stderr 尾部便于定位。
    """
    if shutil.which("ssh") is None:
        raise RuntimeError("服务器未安装 OpenSSH 客户端 (ssh), 无法建隧道")
    key_path = DATA_DIR / f"vnc_{sess.id}.pem"
    key_path.write_text(sess.private_key)
    os.chmod(key_path, 0o600)
    host = f"instance-console.{sess.region}.oci.oraclecloud.com"
    proxy = " ".join(
        ["ssh", "-W", "%h:%p", "-i", str(key_path), "-p", "443",
         f"{sess.connection_id}@{host}"]
        + _COMMON_OPTS + _LEGACY_OPTS)
    port = _free_port()
    argv = ["ssh", "-N",
            "-L", f"127.0.0.1:{port}:{sess.instance_id}:5900",
            "-o", f"ProxyCommand={proxy}",
            "-i", str(key_path), "-p", "443",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            *_COMMON_OPTS, *_LEGACY_OPTS,
            sess.instance_id]
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE)
    deadline = time.time() + 60
    try:
        while time.time() < deadline:
            if proc.poll() is not None:
                err = ""
                if proc.stderr is not None:
                    err = proc.stderr.read().decode(
                        "utf-8", errors="replace")[-600:]
                raise RuntimeError(
                    f"OpenSSH 隧道进程退出 (rc={proc.returncode}): "
                    f"{err.strip()}")
            try:
                sock = socket.create_connection(("127.0.0.1", port),
                                                timeout=2)
                return _SshTunnel(proc, sock, key_path)
            except OSError:
                time.sleep(0.5)
        proc.kill()
        raise RuntimeError("OpenSSH 隧道 60 秒内未就绪")
    except Exception:
        try:
            key_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
