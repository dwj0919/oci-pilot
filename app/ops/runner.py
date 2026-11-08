"""运维任务执行器: 在后台线程里连上主机, 按动作模板逐步执行命令,
全程输出写入 OpsTask.log 供前端轮询展示。

提权策略: root 直用; 否则先试 sudo -n(免密 sudo, OCI 官方镜像默认如此);
再不行用主机保存的登录密码走 sudo -S; 都不行就裸跑并在日志里预警。
"""
import json
import threading
import time
from datetime import datetime, timezone

from ..db import Host, OpsTask, SSHKey, get_session
from ..security import decrypt
from ..ssh.manager import _load_pkey, connect_client, load_host_credentials
from . import actions as A

_MAX_LOG = 128 * 1024


def _now():
    return datetime.now(timezone.utc)


def _append(task_id: int, text: str, secrets: tuple = ()):
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    if not text:
        return
    with get_session() as s:
        t = s.get(OpsTask, task_id)
        if not t:
            return
        t.log = (t.log + text)[-_MAX_LOG:]
        s.add(t)
        s.commit()


def _finish(task_id: int, status: str):
    with get_session() as s:
        t = s.get(OpsTask, task_id)
        if t:
            t.status = status
            t.finished_at = _now()
            s.add(t)
            s.commit()


def task_out(t: OpsTask, with_log: bool = False) -> dict:
    d = {"id": t.id, "host_id": t.host_id, "host_name": t.host_name,
         "action": t.action, "status": t.status,
         "params": json.loads(t.params_json or "{}"),
         "created_at": t.created_at.isoformat() if t.created_at else None,
         "finished_at": t.finished_at.isoformat() if t.finished_at else None}
    if with_log:
        d["log"] = t.log
    return d


def start_task(host_id: int, action_id: str, raw_params: dict) -> dict:
    action = A.BY_ID.get(action_id)
    if not action:
        raise ValueError(f"未知动作: {action_id}")
    params = action["validate"](raw_params or {})
    if action_id == "ssh_port":
        pass  # new_port 与当前端口相同在线程里结合主机现状校验
    with get_session() as s:
        h = s.get(Host, host_id)
        if not h:
            raise ValueError("主机不存在")
        if action_id == "ssh_port" and params["new_port"] == h.port:
            raise ValueError("新端口与当前端口相同")
        t = OpsTask(host_id=host_id, host_name=h.name, action=action_id,
                    params_json=json.dumps(params, ensure_ascii=False))
        s.add(t)
        s.commit()
        s.refresh(t)
        out = task_out(t)
    th = threading.Thread(target=_run, args=(out["id"],),
                          name=f"ops-task-{out['id']}", daemon=True)
    th.start()
    return out


class _Exec:
    """一条连接上的命令执行上下文: 处理 sudo 前缀替换与日志流。"""

    def __init__(self, task_id, client, sudo_prefix, password):
        self.task_id = task_id
        self.client = client
        self.sudo = sudo_prefix
        self.password = password

    def log(self, text):
        _append(self.task_id, text, secrets=(self.password,))

    def run(self, cmd: str, timeout: int = 300, allow_fail: bool = False,
            quiet: bool = False) -> int:
        real = cmd.replace("{SUDO}", self.sudo)
        shown = real if len(real) < 400 else real[:400] + " …"
        if not quiet:
            self.log(f"\n$ {shown}\n")
        chan = self.client.get_transport().open_session()
        chan.settimeout(5)
        chan.exec_command(real)
        if self.sudo.startswith("sudo -S") and "sudo -S" in real:
            try:
                chan.sendall((self.password or "") + "\n")
            except Exception:
                pass
        buf = b""
        deadline = time.time() + timeout
        code = -1
        try:
            while True:
                got = False
                while chan.recv_ready():
                    data = chan.recv(8192)
                    if data:
                        buf += data
                        got = True
                while chan.recv_stderr_ready():
                    data = chan.recv_stderr(8192)
                    if data:
                        buf += data
                        got = True
                if buf:
                    text = buf.decode("utf-8", errors="replace")
                    buf = b""
                    if not quiet:
                        self.log(text)
                if chan.exit_status_ready():
                    code = chan.recv_exit_status()
                    break
                if time.time() > deadline:
                    self.log(f"\n[超时 {timeout}s, 已中断该步骤]\n")
                    try:
                        chan.close()
                    except Exception:
                        pass
                    code = -1
                    break
                if not got:
                    time.sleep(0.15)
        finally:
            try:
                chan.close()
            except Exception:
                pass
        if code != 0 and not allow_fail:
            self.log(f"[步骤失败, 退出码 {code}]\n")
        return code

    def run_steps(self, steps) -> bool:
        for st in steps:
            code = self.run(st["cmd"], timeout=st.get("timeout", 300),
                            allow_fail=st.get("allow_fail", False))
            if code != 0 and not st.get("allow_fail", False):
                return False
        return True


def _resolve_sudo(ex: _Exec, username: str) -> str:
    if username == "root":
        return ""
    if ex.run("sudo -n true", timeout=15, quiet=True) == 0:
        ex.log("[sudo] 当前用户可免密 sudo\n")
        return "sudo -n "
    if ex.password:
        ex.log("[sudo] 使用登录密码走 sudo -S\n")
        return "sudo -S "
    ex.log("[sudo] ⚠️ 非 root 且无可用 sudo, 需要管理员权限的步骤可能失败\n")
    return ""


def _key_login_prepare(task_id, params, host_dict, password):
    """stored 模式: 推导公钥; 若要关闭密码登录, 先用该密钥实测登录。

    返回清洗后的 params(带 public_key), 失败抛 ValueError。
    """
    with get_session() as s:
        k = s.get(SSHKey, params["key_id"])
        if not k:
            raise ValueError("所选密钥不存在")
        pem = decrypt(k.private_key_enc)
        stored_pub = (k.public_key or "").strip()
    pkey = _load_pkey(pem)
    params["public_key"] = stored_pub or f"{pkey.get_name()} {pkey.get_base64()}"
    if params.get("disable_password"):
        _append(task_id, "[预检] 正在用所选密钥实测登录…\n")
        test = connect_client(host_dict, None, pem)
        try:
            chan = test.get_transport().open_session()
            chan.exec_command("echo oci-pilot-key-ok")
            code = chan.recv_exit_status()
            if code != 0:
                raise ValueError("密钥实测登录执行命令失败")
        finally:
            test.close()
        _append(task_id, "[预检] 密钥实测登录成功, 允许关闭密码登录\n")
    return params


def _ssh_port_oci_open(task_id, host_row, new_port):
    """OCI 主机: 在实例子网关联的安全列表里放行新端口(失败只警告)。"""
    if not (host_row.oci_account_id and host_row.oci_instance_id):
        return
    try:
        from ..oci import compute as oci_compute
        from ..oci import network as oci_network
        inst = oci_compute.get_instance(host_row.oci_account_id,
                                        host_row.oci_instance_id)
        vnics = inst.get("vnics") or []
        subnet_id = None
        if vnics and vnics[0].get("vnic_id"):
            subnet_id = oci_network.get_vnic_subnet_id(
                host_row.oci_account_id, vnics[0]["vnic_id"])
        if not subnet_id:
            _append(task_id, "[OCI] 未取到子网信息, 跳过安全列表放行, 请手动确认\n")
            return
        sls = oci_network.get_subnet_security_lists(
            host_row.oci_account_id, subnet_id)
        for sl_id in sls:
            r = oci_network.open_security_port(
                host_row.oci_account_id, sl_id, new_port, "tcp")
            _append(task_id, f"[OCI] 安全列表已放行 {new_port}/tcp"
                             f"{'(' + r.get('note') + ')' if r.get('note') else ''}\n")
    except Exception as e:
        _append(task_id, f"[OCI] ⚠️ 安全列表放行失败({e}), "
                         "若新端口连不上请到网络页手动放行\n")


def _run_ssh_port(task_id, ex, params, host_dict, host_row):
    from .actions import ssh_port_phase_a, ssh_port_phase_c
    ctx = {"username": host_dict["username"], "ssh_port": host_dict["port"]}
    new_port = params["new_port"]
    _ssh_port_oci_open(task_id, host_row, new_port)
    if not ex.run_steps(ssh_port_phase_a(params, ctx)):
        _append(task_id, "\n第一阶段失败: sshd 仍按原配置运行(或双端口未生效), "
                         "未做进一步变更。\n")
        return False
    ex.log("\n[验证] 等待 3 秒后用新端口实测登录…\n")
    time.sleep(3)
    password, pkey = ex.password, ex._pkey
    ok = False
    try:
        probe_host = dict(host_dict, port=new_port)
        probe = connect_client(probe_host, password, pkey)
        try:
            chan = probe.get_transport().open_session()
            chan.exec_command("echo oci-pilot-port-ok")
            ok = chan.recv_exit_status() == 0
        finally:
            probe.close()
    except Exception as e:
        ex.log(f"[验证] 新端口登录失败: {e}\n")
    if not ok:
        _append(task_id, "\n新端口实测未通过: 已停在双端口监听状态"
                         f"(旧 {host_dict['port']} 与新 {new_port} 同时可用), "
                         "不会锁死; 请检查云安全列表/防火墙后重试。\n")
        return False
    ex.log("[验证] 新端口登录成功\n")
    if not params.get("close_old", True):
        _append(task_id, f"\n完成: {host_dict['port']} 与 {new_port} 双端口监听中, "
                         "面板仍按旧端口连接。\n")
        return True
    if not ex.run_steps(ssh_port_phase_c(params, ctx)):
        _append(task_id, "\n关闭旧端口阶段失败: 当前仍是双端口监听, 可正常使用。\n")
        return False
    with get_session() as s:
        h = s.get(Host, host_row.id)
        if h:
            h.port = new_port
            s.add(h)
            s.commit()
    _append(task_id, f"\n完成: 主机记录的端口已更新为 {new_port}, "
                     "之后请用新端口连接。\n")
    return True


def _run(task_id: int):
    try:
        with get_session() as s:
            t = s.get(OpsTask, task_id)
            host_row = s.get(Host, t.host_id)
            params = json.loads(t.params_json or "{}")
            action_id = t.action
            host_name = t.host_name
        action = A.BY_ID[action_id]
        _append(task_id, f"=== {action['name']} → {host_name} "
                         f"({host_row.hostname}:{host_row.port}) ===\n")
        host_dict, password, pkey = load_host_credentials(host_row.id)

        if action_id == "key_login" and params.get("source") == "stored":
            params = _key_login_prepare(task_id, params, host_dict, password)

        client = connect_client(host_dict, password, pkey)
        try:
            ex = _Exec(task_id, client, "", password)
            ex._pkey = pkey  # 供改端口后的探测连接复用
            ex.sudo = _resolve_sudo(ex, host_dict["username"])
            ctx = {"username": host_dict["username"],
                   "ssh_port": host_dict["port"]}
            if action_id == "ssh_port":
                ok = _run_ssh_port(task_id, ex, params, host_dict, host_row)
            else:
                ok = ex.run_steps(action["build"](params, ctx))
        finally:
            client.close()
        _finish(task_id, "done" if ok else "failed")
    except Exception as e:
        _append(task_id, f"\n[执行出错] {type(e).__name__}: {e}\n")
        _finish(task_id, "failed")
