"""浏览器图形控制台 (VNC): 会话管理 + OCI 控制台双跳 SSH 隧道。

链路: 浏览器 noVNC <-> 面板 WebSocket <-> paramiko 通道 <-> OCI 控制台
服务 <-> 实例的 VNC 服务器 (5900)。拓扑与官方 vnc_connection_string 一致:
  外层: SSH 到 <connection_id>@instance-console.<region>:443 (认证)
  中转: 在外层上开 direct-tcpip 到 (instance_id, 443)   # 即 -W %h:%p
  内层: 在中转通道上再建 SSH 会话 (认证用同一把一次性密钥)
  VNC:  在内层上开 direct-tcpip 到 (instance_id, 5900)  # 即 -L 转发目标

一次性私钥只存在于本进程内存, 不落盘; 会话结束即删除 OCI 侧控制台连接。
"""
import io
import logging
import secrets
import threading
import time

import paramiko

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
        self.outer = None   # paramiko.SSHClient
        self.inner = None   # paramiko.SSHClient
        self.channel = None  # paramiko.Channel (VNC 数据通道)

    def attach(self, outer, inner, channel):
        self.outer, self.inner, self.channel = outer, inner, channel
        self.connected = True

    def close_tunnel(self):
        for obj in (self.channel, self.inner, self.outer):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        self.channel = self.inner = self.outer = None
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


def open_vnc_channel(sess: VncSession):
    """按官方连接串拓扑建双跳隧道, 返回 (outer, inner, vnc_channel)。

    阻塞调用(两次 SSH 握手), 请在线程里执行。失败时抛异常且不留半开连接。
    """
    key = paramiko.RSAKey.from_private_key(io.StringIO(sess.private_key))
    host = f"instance-console.{sess.region}.oci.oraclecloud.com"
    outer = paramiko.SSHClient()
    outer.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    inner = paramiko.SSHClient()
    inner.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        try:
            outer.connect(host, port=443, username=sess.connection_id,
                          pkey=key, timeout=30, banner_timeout=30,
                          auth_timeout=30, look_for_keys=False,
                          allow_agent=False)
        except Exception as e:
            raise RuntimeError(f"外层 SSH (instance-console) 失败: {e}") from e
        outer.get_transport().set_keepalive(30)
        try:
            hop = outer.get_transport().open_channel(
                "direct-tcpip", (sess.instance_id, 443), ("127.0.0.1", 0),
                timeout=30)
        except Exception as e:
            raise RuntimeError(f"中转通道 (-W %h:%p) 失败: {e}") from e
        try:
            inner.connect(sess.instance_id, port=443,
                          username=sess.connection_id, pkey=key, sock=hop,
                          timeout=30, banner_timeout=30, auth_timeout=30,
                          look_for_keys=False, allow_agent=False)
        except Exception as e:
            raise RuntimeError(f"内层 SSH (实例控制台) 失败: {e}") from e
        inner.get_transport().set_keepalive(30)
        try:
            chan = inner.get_transport().open_channel(
                "direct-tcpip", (sess.instance_id, 5900), ("127.0.0.1", 0),
                timeout=30)
        except Exception as e:
            raise RuntimeError(f"VNC 通道 (5900) 失败: {e}") from e
        return outer, inner, chan
    except Exception:
        for obj in (inner, outer):
            try:
                obj.close()
            except Exception:
                pass
        raise
