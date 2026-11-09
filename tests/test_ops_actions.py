"""ops 动作模板单测: 参数校验 + 命令渲染(不连真实主机)。

直接运行: python3 tests/test_ops_actions.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ops import actions as A  # noqa: E402

CTX = {"username": "opc", "ssh_port": 22}
ED = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyMaterialForTestOnly1234567890 test@host"


def raises(fn, *args):
    try:
        fn(*args)
    except ValueError:
        return True
    return False


def test_fail2ban():
    p = A.BY_ID["fail2ban"]["validate"]({})
    assert p == {"bantime": 3600, "findtime": 600, "maxretry": 5}
    steps = A.BY_ID["fail2ban"]["build"](p, CTX)
    joined = "\n".join(s["cmd"] for s in steps)
    assert "apt-get install -y -qq fail2ban" in joined
    assert "port = 22" in joined and "bantime = 3600" in joined
    assert "fail2ban-client status sshd" in joined
    assert raises(A.BY_ID["fail2ban"]["validate"], {"bantime": 10})
    assert raises(A.BY_ID["fail2ban"]["validate"], {"maxretry": 0})
    assert raises(A.BY_ID["fail2ban"]["validate"], {"bantime": "abc"})


def test_key_login():
    v = A.BY_ID["key_login"]["validate"]
    assert raises(v, {"source": "paste", "public_key": "not-a-key"})
    assert raises(v, {"source": "paste", "public_key": ED, "disable_password": True}), \
        "粘贴公钥不许关密码登录"
    assert raises(v, {"source": "stored"})  # 缺 key_id
    p = v({"source": "stored", "key_id": 3, "disable_password": True})
    assert p["key_id"] == 3 and p["disable_password"] is True
    # build: 先补齐 public_key(执行器负责), 断言去重与关密码步骤
    p2 = v({"source": "paste", "public_key": ED})
    p2["public_key"] = ED
    steps = A.BY_ID["key_login"]["build"](p2, CTX)
    joined = "\n".join(s["cmd"] for s in steps)
    assert "authorized_keys" in joined and "grep -qxF" in joined
    assert "PasswordAuthentication no" not in joined  # 未要求关密码
    p3 = v({"source": "stored", "key_id": 1, "disable_password": True})
    p3["public_key"] = ED
    joined3 = "\n".join(s["cmd"] for s in A.BY_ID["key_login"]["build"](p3, CTX))
    assert "PasswordAuthentication no" in joined3 and "sshd -t" in joined3
    full, blob = A.split_pubkey(ED)
    assert blob.startswith("AAAAC3") and full.endswith("test@host")


def test_ssh_port():
    v = A.BY_ID["ssh_port"]["validate"]
    assert raises(v, {"new_port": 0}) and raises(v, {"new_port": 70000})
    p = v({"new_port": 22222})
    a = "\n".join(s["cmd"] for s in A.ssh_port_phase_a(p, CTX))
    assert "Port 22" in a and "Port 22222" in a and "sshd -t" in a
    c = "\n".join(s["cmd"] for s in A.ssh_port_phase_c(p, CTX))
    assert "Port 22222" in c and "Port 22\n" not in c.replace("Port 22222", "")


def test_swap_bbr_tz_update():
    sp = A.BY_ID["swap"]["validate"]({"size_gb": 4})
    cmd = A.BY_ID["swap"]["build"](sp, CTX)[0]["cmd"]
    assert "fallocate -l 4G" in cmd and "/etc/fstab" in cmd and "swapon" in cmd
    assert raises(A.BY_ID["swap"]["validate"], {"size_gb": 64})
    bbr = A.BY_ID["bbr"]["build"]({}, CTX)[0]["cmd"]
    assert "tcp_congestion_control = bbr" in bbr and "default_qdisc = fq" in bbr
    tz = A.BY_ID["timezone"]["validate"]
    assert tz({"tz": "Asia/Shanghai"}) == {"tz": "Asia/Shanghai"}
    assert raises(tz, {"tz": "Mars/Olympus"})
    assert raises(tz, {"tz": "Asia/Shanghai; rm -rf /"})
    upd = "\n".join(s["cmd"] for s in A.BY_ID["sysupdate"]["build"]({}, CTX))
    assert "apt-get" in upd and "reboot-required" in upd


def test_hostname():
    v = A.BY_ID["hostname"]["validate"]
    assert v({"hostname": "web-1"}) == {"hostname": "web-1"}
    assert v({"hostname": " Web-1.Example.COM "}) == {"hostname": "web-1.example.com"}
    assert v({"hostname": "a"}) == {"hostname": "a"}
    for bad in ("", "-bad", "bad-", "bad_name", "a..b", "ho st", "a" * 64,
                "web_1", ".web", "web.", "web-.x"):
        assert raises(v, {"hostname": bad}), bad
    cmd = A.BY_ID["hostname"]["build"]({"hostname": "web-1"}, CTX)[0]["cmd"]
    assert "hostnamectl set-hostname web-1" in cmd
    assert "127.0.1.1 web-1" in cmd and "{SUDO}" in cmd
    assert "/etc/hostname" in cmd  # 无 systemd 回退路径


def test_public_actions_shape():
    acts = A.public_actions()
    assert len(acts) == 8
    for a in acts:
        assert set(a) == {"id", "name", "desc", "danger", "params"}
        assert not callable(a.get("validate"))


if __name__ == "__main__":
    test_fail2ban()
    test_key_login()
    test_ssh_port()
    test_swap_bbr_tz_update()
    test_hostname()
    test_public_actions_shape()
    print("test_ops_actions: ALL PASS")
