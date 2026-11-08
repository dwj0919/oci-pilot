"""OCI 封装层模型构造测试: mock 掉网络, 验证所有 SDK 模型参数正确。"""
import contextlib
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db, get_session, OCIAccount  # noqa: E402
from app.security import encrypt  # noqa: E402

init_db()
with get_session() as s:
    if not s.get(OCIAccount, 1):
        s.add(OCIAccount(id=1, name="test", tenancy_ocid="ocid1.tenancy.t",
                         user_ocid="ocid1.user.u", fingerprint="aa:bb",
                         region="us-ashburn-1",
                         private_key_enc=encrypt("fake")))
        s.commit()


class FakeResp:
    def __init__(self, data):
        self.data = data
        self.headers = {}
        self.next_page = None
        self.has_next_page = False
        self.status = 200
        self.request = None


def _vnic():
    return NS(id="ocid1.vnic.v1", compartment_id="ocid1.comp.c1",
              subnet_id="ocid1.subnet.s1", public_ip="1.1.1.1",
              private_ip="10.0.0.2", private_ip_id="ocid1.privateip.p1",
              ipv6_addresses=[], mac_address="00:00:00:00:00:01",
              lifecycle_state="AVAILABLE")


def _inst():
    return NS(id="ocid1.instance.i1", display_name="inst-1",
              shape="VM.Standard.A1.Flex",
              shape_config=NS(ocpus=2, memory_in_gbs=12),
              lifecycle_state="RUNNING",
              availability_domain="AD-1", compartment_id="ocid1.comp.c1",
              image_id="ocid1.image.img1", time_created="2026-01-01",
              metadata={"ssh_authorized_keys": "ssh-rsa AAAAB3 test"})


class FakeCompute:
    def launch_instance(self, details):
        assert details.shape == "VM.Standard.A1.Flex"
        src = details.source_details
        if getattr(src, "boot_volume_id", None):
            # 从启动盘开机: Flex 机型仍必须带 shape_config (生产代码有意为之,
            # 无 ocpus 参数时用默认 1C/1G)
            assert details.shape_config is not None
            assert details.shape_config.ocpus == 1
        else:
            assert details.shape_config.ocpus == 2
            assert "ssh_authorized_keys" in details.metadata
        return FakeResp(NS(id="ocid1.instance.new", display_name="n"))

    def get_instance(self, iid):
        return FakeResp(_inst())

    def list_instances(self, comp, **kw):
        return FakeResp([_inst()])

    def list_vnic_attachments(self, comp, **kw):
        return FakeResp([NS(vnic_id="ocid1.vnic.v1",
                            lifecycle_state="ATTACHED")])

    def instance_action(self, iid, action):
        assert action in ("START", "STOP", "SOFTSTOP", "RESET", "SOFTRESET")
        return FakeResp(None)

    def terminate_instance(self, iid, preserve_boot_volume=False):
        return FakeResp(None)

    def update_instance(self, iid, details):
        return FakeResp(None)

    def attach_volume(self, details):
        return FakeResp(NS(id="ocid1.attach.a1"))

    def detach_volume(self, aid):
        return FakeResp(None)

    def attach_boot_volume(self, details):
        return FakeResp(NS(id="ocid1.attach.b1"))

    def create_instance_console_connection(self, details):
        assert details.instance_id and details.public_key
        return FakeResp(NS(id="ocid1.consoleconn.cc1"))

    def delete_instance_console_connection(self, cid):
        return FakeResp(None)

    def capture_console_history(self, details):
        return FakeResp(NS(id="ocid1.history.h1",
                           lifecycle_state="SUCCEEDED"))

    def get_console_history(self, hid):
        import base64
        return FakeResp(NS(lifecycle_state="SUCCEEDED",
                           data=base64.b64encode(b"boot log line").decode()))

    def list_images(self, comp, **kw):
        return FakeResp([])

    def list_shapes(self, comp, **kw):
        return FakeResp([])


class FakeNetwork:
    def get_vnic(self, vid):
        return FakeResp(_vnic())

    def list_public_ips(self, scope, comp, **kw):
        return FakeResp([])

    def create_public_ip(self, details):
        assert details.private_ip_id
        return FakeResp(NS(id="ocid1.publicip.n1", ip_address="3.3.3.3"))

    def delete_public_ip(self, pid):
        return FakeResp(None)

    def update_public_ip(self, pid, details):
        return FakeResp(None)

    def create_ipv6(self, details):
        return FakeResp(NS(id="ocid1.ipv6.6", ip_address="2603::1"))

    def delete_ipv6(self, iid):
        return FakeResp(None)

    def list_vcns(self, comp, **kw):
        return FakeResp([])

    def list_subnets(self, comp, **kw):
        return FakeResp([])

    def list_security_lists(self, comp, **kw):
        return FakeResp([])

    def get_security_list(self, slid):
        return FakeResp(NS(ingress_security_rules=[]))

    def update_security_list(self, slid, details):
        assert len(details.ingress_security_rules) == 1
        return FakeResp(None)


class FakeBlock:
    def list_volumes(self, comp, **kw):
        return FakeResp([])

    def list_boot_volumes(self, comp, **kw):
        return FakeResp([])

    def list_volume_attachments(self, comp, **kw):
        return FakeResp([])

    def create_volume(self, details):
        assert details.size_in_gbs == 100
        return FakeResp(NS(id="ocid1.volume.v1", display_name="d1",
                           size_in_gbs=100, vpus_per_gb=10,
                           lifecycle_state="AVAILABLE",
                           availability_domain="AD-1"))

    def get_volume(self, vid):
        return FakeResp(NS(size_in_gbs=50))

    def update_volume(self, vid, details):
        assert details.size_in_gbs == 100
        return FakeResp(None)

    def delete_volume(self, vid):
        return FakeResp(None)


class FakeIdentity:
    def list_users(self, comp, **kw):
        return FakeResp([])

    def create_user(self, details):
        return FakeResp(NS(id="ocid1.user.n1", name=details.name))

    def delete_user(self, uid):
        return FakeResp(None)

    def list_api_keys(self, uid, **kw):
        return FakeResp([])

    def delete_api_key(self, uid, kid):
        return FakeResp(None)

    def get_tenancy(self, tid):
        return FakeResp(NS(name="t", home_region_key="us-ashburn-1",
                           description=""))

    def list_availability_domains(self, comp):
        return FakeResp([NS(name="AD-1")])



class FakeObjects:
    def get_namespace(self):
        return FakeResp("testns")

    def list_buckets(self, ns, comp, **kw):
        return FakeResp([])

    def create_bucket(self, ns, details):
        assert details.name == "bkt1"
        return FakeResp(None)

    def delete_bucket(self, ns, name):
        return FakeResp(None)

    def list_objects(self, ns, bucket, **kw):
        return FakeResp(NS(objects=[NS(name="f.txt", size=10,
                                      time_modified="2026-01-01")]))

    def put_object(self, ns, bucket, name, data):
        return FakeResp(None)

    def get_object(self, ns, bucket, name):
        return FakeResp(NS(content=b"hello"))

    def delete_object(self, ns, bucket, name):
        return FakeResp(None)


class FakeMonitoring:
    def summarize_metrics_data(self, comp, details, **kw):
        assert details.namespace == "oci_vcn"
        return FakeResp([NS(aggregated_datapoints=[NS(value=5e9)])])


class FakeLimits:
    def list_limit_values(self, comp, **kw):
        return FakeResp([])

    def list_services(self, comp, **kw):
        return FakeResp([])


class FakeClients:
    def __init__(self):
        self.compute = FakeCompute()
        self.network = FakeNetwork()
        self.block = FakeBlock()
        self.identity = FakeIdentity()
        self.objects = FakeObjects()
        self.monitoring = FakeMonitoring()
        self.limits = FakeLimits()
        self.tenancy = "ocid1.tenancy.t"


@contextlib.contextmanager
def fake_oci_clients(account_id):
    yield FakeClients()


def _patch(*modules):
    return [patch(f"app.oci.{m}.oci_clients", fake_oci_clients)
            for m in modules]


def run():
    from app.oci import (compute, network, volumes, identity,
                         objectstore, metrics, console)

    mods = ["compute", "network", "volumes", "identity",
            "objectstore", "metrics", "console", "base"]
    patches = _patch(*mods)
    for p in patches:
        p.start()
    try:
        # 开机(镜像)
        r = compute.launch_instance(1, {
            "compartment_id": "ocid1.comp.c1", "ad": "AD-1",
            "shape": "VM.Standard.A1.Flex", "ocpus": 2, "memory_gb": 12,
            "image_id": "ocid1.image.img1", "subnet_id": "ocid1.subnet.s1",
            "ssh_key": "ssh-rsa AAAAB3", "display_name": "t1",
            "assign_public_ip": True})
        assert r["instance_id"] == "ocid1.instance.new"
        # 开机(启动盘)
        r = compute.launch_instance(1, {
            "compartment_id": "ocid1.comp.c1", "ad": "AD-1",
            "shape": "VM.Standard.A1.Flex",
            "subnet_id": "ocid1.subnet.s1", "ssh_key": "ssh-rsa AAAAB3",
            "boot_volume_id": "ocid1.bootvolume.bv1",
            "display_name": "t2"})
        assert r["instance_id"]
        # 实例列表(含 IP 补全)
        insts = compute.list_instances(1)
        assert insts[0]["public_ip"] == "1.1.1.1"
        # 电源操作
        compute.instance_action(1, "x", "START")
        compute.terminate_instance(1, "x", preserve_boot_volume=True)
        compute.update_instance(1, "x", display_name="new")
        # 换 IP
        nip = network.change_public_ip(1, "ocid1.vnic.v1")
        assert nip["new_ip"] == "3.3.3.3", nip
        network.attach_ipv6(1, "ocid1.vnic.v1")
        network.open_security_port(1, "ocid1.sl.sl1", 443, "tcp")
        # 硬盘
        v = volumes.create_volume(1, "ocid1.comp.c1", "AD-1", 100, "d1")
        assert v["size_gb"] == 100
        volumes.resize_volume(1, "ocid1.volume.v1", 100)
        volumes.attach_volume(1, "ocid1.instance.i1", "ocid1.volume.v1")
        # 身份
        identity.create_user(1, "bob", email="b@x.com")
        identity.get_quotas(1)
        # 对象存储
        objectstore.create_bucket(1, "ocid1.comp.c1", "bkt1")
        assert objectstore.list_objects(1, "bkt1")[0]["name"] == "f.txt"
        assert objectstore.download_object(1, "bkt1", "f") == b"hello"
        # 流量
        t = metrics.month_egress_gb(1, "ocid1.comp.c1", "ocid1.vnic.v1")
        assert t == 5.0, t
        # 串行控制台
        cc = console.create_console_connection(1, "ocid1.instance.i1")
        assert "instance-console.us-ashburn-1" in cc["ssh_command"]
        h = console.get_console_history(1, "ocid1.history.h1")
        assert "boot log" in h["content"]
        print("ALL OCI MODEL TESTS PASS")
    finally:
        for p in patches:
            p.stop()


if __name__ == "__main__":
    run()
