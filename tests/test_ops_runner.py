"""ops 执行器集成测试: 用假 SSH 客户端跑完整任务流(不连真实主机)。

直接运行: python3 tests/test_ops_runner.py
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.config as cfg  # noqa: E402

_tmp = tempfile.mkdtemp()
cfg.DB_PATH = Path(_tmp) / "test.db"

from app.db import Host, OpsTask, get_session, init_db  # noqa: E402
from app.ops import runner  # noqa: E402


class FakeChan:
    def __init__(self, client):
        self.client = client
        self._out = b""
        self._code = 0

    def settimeout(self, _s):
        pass

    def exec_command(self, cmd):
        self.client.commands.append(cmd)
        if "sysctl -n net.ipv4.tcp_congestion_control" in cmd:
            self._out = b"bbr\n"  # 不会被这条复合脚本直接命中, 见下方整脚本输出
        self._out = b"fake-output\n"
        self._code = 1 if "FAILME" in cmd else 0

    def recv_ready(self):
        return bool(self._out)

    def recv(self, n):
        d, self._out = self._out[:n], self._out[n:]
        return d

    def recv_stderr_ready(self):
        return False

    def recv_stderr(self, n):
        return b""

    def exit_status_ready(self):
        return not self._out

    def recv_exit_status(self):
        return self._code

    def sendall(self, _d):
        pass

    def close(self):
        pass


class FakeTransport:
    def __init__(self, client):
        self.client = client

    def open_session(self):
        return FakeChan(self.client)


class FakeClient:
    def __init__(self):
        self.commands = []
        self.transport = FakeTransport(self)

    def get_transport(self):
        return self.transport

    def close(self):
        pass


def wait_done(task_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        with get_session() as s:
            t = s.get(OpsTask, task_id)
            if t.status != "running":
                return t
        time.sleep(0.1)
    raise AssertionError("任务未在超时内结束")


def main():
    init_db()
    fake = FakeClient()
    runner.connect_client = lambda host, pw, pk, timeout=15: fake
    runner.load_host_credentials = lambda hid: (
        {"hostname": "10.0.0.9", "port": 22, "username": "opc"}, None, "FAKEPKEY")
    with get_session() as s:
        h = Host(name="fake-host", hostname="10.0.0.9", port=22,
                 username="opc", auth_type="key", key_id=1)
        s.add(h)
        s.commit()
        s.refresh(h)
        hid = h.id

    # bbr: 整脚本一条命令, 假客户端返回 0 → done, 且日志含输出
    out = runner.start_task(hid, "bbr", {})
    t = wait_done(out["id"])
    assert t.status == "done", t.log
    assert "fake-output" in t.log
    assert any("tcp_congestion_control" in c for c in fake.commands), fake.commands
    assert any(c.startswith("sudo -n ") for c in fake.commands), \
        "opc 用户应走 sudo -n 前缀"

    # 未知主机 / 未知动作 / 非法参数都被拒
    for bad in (lambda: runner.start_task(9999, "bbr", {}),
                lambda: runner.start_task(hid, "nope", {}),
                lambda: runner.start_task(hid, "swap", {"size_gb": 99})):
        try:
            bad()
        except ValueError:
            pass
        else:
            raise AssertionError("非法任务未被拒绝")
    print("test_ops_runner: ALL PASS")


if __name__ == "__main__":
    main()
