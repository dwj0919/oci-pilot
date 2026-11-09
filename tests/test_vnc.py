"""VNC 图形控制台测试: 会话登记/过期/占用 + WS 桥端到端(假 OCI、假隧道)。

直接运行: python3 tests/test_vnc.py
"""
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.config as cfg  # noqa: E402

_tmp = tempfile.mkdtemp()
cfg.DB_PATH = Path(_tmp) / "test.db"

from app.db import User, get_session, init_db  # noqa: E402
from app.security import create_token  # noqa: E402

init_db()
with get_session() as s:
    s.add(User(username="vnctester", telegram_user_id=42))
    s.add(User(username="other", telegram_user_id=43))
    s.commit()
TOKEN = create_token("vnctester")
TOKEN2 = create_token("other")

# ---- 假 OCI 层 ----
import app.oci.console as CC  # noqa: E402

_deleted = []


def _fake_create(account_id, instance_id):
    return {"connection_id": "ocid1.instanceconsoleconnection.test",
            "region": "ap-singapore-1", "private_key": "FAKEKEY",
            "vnc_connection_string": ""}


def _fake_delete(account_id, connection_id):
    _deleted.append(connection_id)
    return {"ok": True}


CC.create_connection_ephemeral = _fake_create
CC.delete_console_connection = _fake_delete
CC.wait_connection_active = lambda *a, **k: True

# ---- 假隧道: 一个会发 RFB banner 并回显的通道 ----
import app.vnc as V  # noqa: E402


class FakeVncChan:
    def __init__(self):
        self._q = [b"RFB 003.008\n"]
        self.closed = False
        self._cv = threading.Condition()

    def recv(self, n):
        with self._cv:
            while not self._q and not self.closed:
                self._cv.wait(timeout=5)
            if self._q:
                return self._q.pop(0)
            return b""

    def sendall(self, data):
        with self._cv:
            self._q.append(data)  # 回显
            self._cv.notify_all()

    def close(self):
        self.closed = True
        with self._cv:
            self._cv.notify_all()


class _FakeClient:
    def close(self):
        pass


_chan = FakeVncChan()
V.open_vnc_channel = lambda sess: (_FakeClient(), _FakeClient(), _chan)

# ---- 会话存储单元检查 ----
conn = _fake_create(1, "ocid1.instance.x")
s1 = V.register("vnctester", 1, "ocid1.instance.x", conn)
assert V.get(s1.id) is s1
assert V.claim(s1) and not V.claim(s1), "占用标记应只允许一次"
s1.created_at -= (V.SESSION_TTL + 10)
assert V.get(s1.id) is None, "过期会话不应被取到"
V.drop(s1.id)
print("session store: PASS")

# ---- HTTP + WS 端到端 ----
from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)
H = {"Authorization": "Bearer " + TOKEN}

r = client.post("/api/console/vnc-sessions",
                json={"account_id": 1, "instance_id": "ocid1.instance.x"})
assert r.status_code in (401, 403), f"未登录应被拒: {r.status_code}"
r = client.post("/api/console/vnc-sessions",
                json={"account_id": 1, "instance_id": "ocid1.instance.x"},
                headers=H)
assert r.status_code == 200, r.text
sid = r.json()["session_id"]

# 别人的 token 连不上这个会话
try:
    with client.websocket_connect(
            f"/api/console/vnc/ws?session={sid}&token={TOKEN2}"):
        raise AssertionError("他人会话应被拒绝")
except WebSocketDisconnect as e:
    assert e.code == 4401, e.code
# 坏 token
try:
    with client.websocket_connect(
            f"/api/console/vnc/ws?session={sid}&token=bad.token.here"):
        raise AssertionError("坏 token 应被拒绝")
except WebSocketDisconnect as e:
    assert e.code == 4401, e.code
print("ws auth guards: PASS")

# 正常连接: 先收 banner, 发什么收什么(回显)
with client.websocket_connect(
        f"/api/console/vnc/ws?session={sid}&token={TOKEN}") as ws:
    assert ws.receive_bytes() == b"RFB 003.008\n"
    ws.send_bytes(b"\x01\x02\x03client-init")
    assert ws.receive_bytes() == b"\x01\x02\x03client-init"

# 断开后: 会话被回收 + OCI 连接被删除
for _ in range(30):
    if _deleted:
        break
    time.sleep(0.1)
assert V.get(sid) is None, "WS 断开后会话应被回收"
assert _deleted == ["ocid1.instanceconsoleconnection.test"], _deleted
print("ws bridge + cleanup: PASS")

print("test_vnc: ALL PASS")
