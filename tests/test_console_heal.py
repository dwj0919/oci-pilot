"""控制台连接 409 自愈测试: 创建遇到已存在连接时先删旧连接再重试。

用法: .venv/bin/python -m tests.test_console_heal
"""
import contextlib
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import oci  # noqa: E402

from app import config as cfg  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="oci-pilot-heal-")
cfg.DB_PATH = Path(_TMP) / "test.db"

from app import db as dbm  # noqa: E402
from app.db import OCIAccount  # noqa: E402
from app.oci import console as cons  # noqa: E402

dbm.init_db()
with dbm.get_session() as s:
    s.add(OCIAccount(name="t", tenancy_ocid="ocid1.tenancy.oc1..t",
                     user_ocid="ocid1.user.oc1..t", fingerprint="aa:bb",
                     region="ap-singapore-1", private_key_enc="x"))
    s.commit()

cons.DATA_DIR = Path(_TMP)


class FakeCompute:
    def __init__(self, fail_status=None):
        self.fail_status = fail_status  # 409=先失败一次; 400=一直失败; None=直接成功
        self.create_calls = 0
        self.deleted = []
        self.stale_alive = True

    def create_instance_console_connection(self, details):
        self.create_calls += 1
        if self.fail_status == 400:
            raise oci.exceptions.ServiceError(
                status=400, code="BadRequest", headers={},
                message="some other error")
        if self.fail_status == 409 and self.stale_alive:
            raise oci.exceptions.ServiceError(
                status=409, code="Conflict", headers={},
                message="Console connection for instance X already exists "
                        "or has not been terminated.")
        return SimpleNamespace(data=SimpleNamespace(
            id="ocid1.instanceconsoleconnection.new",
            vnc_connection_string="ssh -N -L localhost:5900:x:5900"))

    def list_instance_console_connections(self, compartment_id, **kwargs):
        assert kwargs.get("instance_id"), "list 必须带 instance_id 过滤"
        data = []
        if self.stale_alive:
            data = [SimpleNamespace(
                id="ocid1.instanceconsoleconnection.old",
                lifecycle_state="ACTIVE")]
        return oci.response.Response(200, {}, data, None)

    def delete_instance_console_connection(self, conn_id):
        self.deleted.append(conn_id)
        self.stale_alive = False
        return SimpleNamespace(status=204, headers={})


def run_with(fake):
    @contextlib.contextmanager
    def fake_clients(account_id):
        yield SimpleNamespace(compute=fake, tenancy="ocid1.tenancy.oc1..t")
    old = cons.oci_clients
    cons.oci_clients = fake_clients
    try:
        return cons.create_connection_ephemeral(1, "ocid1.instance.oc1..i")
    finally:
        cons.oci_clients = old


def main():
    # 1) 有旧连接 → 409 → 自动删除 → 重试成功
    f = FakeCompute(fail_status=409)
    r = run_with(f)
    assert r["connection_id"] == "ocid1.instanceconsoleconnection.new", r
    assert f.create_calls == 2, f.create_calls
    assert f.deleted == ["ocid1.instanceconsoleconnection.old"], f.deleted
    print("409 自愈: 旧连接已删, 重试创建成功 ✓")

    # 2) 没有旧连接 → 一次成功, 不删任何东西
    f2 = FakeCompute()
    r2 = run_with(f2)
    assert r2["connection_id"].endswith(".new") and f2.create_calls == 1
    assert f2.deleted == []
    print("无冲突: 一次创建成功 ✓")

    # 3) 非 409 错误 → 原样抛出, 不删连接
    f3 = FakeCompute(fail_status=400)
    try:
        run_with(f3)
        raise AssertionError("400 错误应该抛出")
    except oci.exceptions.ServiceError as e:
        assert e.status == 400
    assert f3.deleted == []
    print("非 409 错误: 不误删, 原样抛出 ✓")

    print("ALL CONSOLE HEAL TESTS PASSED")


if __name__ == "__main__":
    main()
