"""服务器运维动作模板库。

安全原则:
- 动作全部是固定模板, 用户只能填经过严格校验的参数(整数范围 / 白名单 /
  公钥正则), 参数进 shell 前一律再过一遍校验并用 shlex.quote 转义。
- 不提供任意命令执行入口。
- 模板里的 {SUDO} 占位符由执行器按主机实际情况替换( root 直用 /
  sudo -n / sudo -S ), 模板本身不关心提权方式。

每个动作:
  id, name, desc, danger(是否高危, 前端二次确认),
  params: 前端表单 schema [{key,label,type,default,min,max,options,help}]
  validate(params) -> 清洗后的 params(不合法抛 ValueError)
  build(params, ctx) -> steps: [{cmd, timeout, allow_fail}]
  ctx: {"username": 登录用户, "ssh_port": 当前 SSH 端口}
"""
import re
import shlex

PUBKEY_RE = re.compile(
    r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|"
    r"ecdsa-sha2-nistp521) ([A-Za-z0-9+/=]+)( .*)?$")

ALLOWED_TZ = [
    "Asia/Shanghai", "Asia/Hong_Kong", "Asia/Taipei", "Asia/Tokyo",
    "Asia/Singapore", "Asia/Seoul", "UTC", "Europe/London", "Europe/Berlin",
    "America/New_York", "America/Chicago", "America/Denver",
    "America/Los_Angeles", "America/Sao_Paulo", "Australia/Sydney",
]


def _int(params, key, default, lo, hi, label):
    v = params.get(key, default)
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{label}必须是整数")
    if v < lo or v > hi:
        raise ValueError(f"{label}必须在 {lo} 到 {hi} 之间")
    return v


def _bool(params, key, default=False):
    return bool(params.get(key, default))


def _tee_lines(lines, path):
    """把多行内容经 sudo tee 写到远端文件, 返回 shell 命令。"""
    args = " ".join(shlex.quote(line) for line in lines)
    return f"printf '%s\\n' {args} | {{SUDO}} tee {shlex.quote(path)} > /dev/null"


# ---------------- fail2ban ----------------

def _fail2ban_validate(p):
    return {
        "bantime": _int(p, "bantime", 3600, 60, 31536000, "封禁时长(秒)"),
        "findtime": _int(p, "findtime", 600, 60, 86400, "统计窗口(秒)"),
        "maxretry": _int(p, "maxretry", 5, 1, 100, "最大重试次数"),
    }


def _fail2ban_build(p, ctx):
    jail = [
        "[sshd]",
        "enabled = true",
        f"port = {ctx['ssh_port']}",
        "filter = sshd",
        "backend = systemd",
        f"bantime = {p['bantime']}",
        f"findtime = {p['findtime']}",
        f"maxretry = {p['maxretry']}",
    ]
    return [
        {"cmd": "if command -v apt-get > /dev/null 2>&1; then "
                "{SUDO} env DEBIAN_FRONTEND=noninteractive apt-get update -qq && "
                "{SUDO} env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fail2ban; "
                "elif command -v dnf > /dev/null 2>&1; then {SUDO} dnf install -y fail2ban; "
                "elif command -v yum > /dev/null 2>&1; then {SUDO} yum install -y fail2ban; "
                "else echo '不支持的包管理器(仅支持 apt / dnf / yum)'; exit 1; fi",
         "timeout": 600},
        {"cmd": "{SUDO} mkdir -p /etc/fail2ban/jail.d && "
                + _tee_lines(jail, "/etc/fail2ban/jail.d/oci-pilot-sshd.local")},
        {"cmd": "{SUDO} fail2ban-client -t || "
                "(echo 'systemd backend 校验失败, 回退 backend=auto'; "
                "{SUDO} sed -i 's/^backend = systemd/backend = auto/' "
                "/etc/fail2ban/jail.d/oci-pilot-sshd.local && "
                "{SUDO} fail2ban-client -t)"},
        {"cmd": "{SUDO} systemctl enable --now fail2ban 2>/dev/null || "
                "{SUDO} service fail2ban restart"},
        {"cmd": "sleep 2; {SUDO} fail2ban-client status sshd",
         "allow_fail": True},
    ]


# ---------------- 密钥登录 ----------------

def _key_login_validate(p):
    source = p.get("source", "paste")
    if source not in ("paste", "stored"):
        raise ValueError("密钥来源不合法")
    out = {"source": source, "disable_password": _bool(p, "disable_password")}
    if source == "stored":
        kid = p.get("key_id")
        try:
            out["key_id"] = int(kid)
        except (TypeError, ValueError):
            raise ValueError("请选择一个已保存的密钥")
    else:
        pub = (p.get("public_key") or "").strip()
        if not PUBKEY_RE.match(pub):
            raise ValueError("公钥格式不正确(应为 ssh-ed25519 / ssh-rsa / ecdsa 开头的一行)")
        out["public_key"] = pub
    if out["disable_password"] and source != "stored":
        raise ValueError("关闭密码登录只允许使用已保存密钥(需要先用该密钥实测能登录, 粘贴的公钥无法验证)")
    return out


def split_pubkey(pub: str):
    """返回 (完整行, 密钥指纹段)。公钥须先过 PUBKEY_RE。"""
    parts = pub.strip().split()
    return " ".join(parts), parts[1]


def _key_login_build(p, ctx):
    # p["public_key"] 由执行器在运行前统一补齐(stored 模式从私钥推导)
    full, blob = split_pubkey(p["public_key"])
    steps = [
        {"cmd": "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
                "touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"},
        {"cmd": f"awk '{{print $2}}' ~/.ssh/authorized_keys | grep -qxF {shlex.quote(blob)} "
                f"&& echo '该密钥已存在, 未重复添加' || "
                f"{{ echo {shlex.quote(full)} >> ~/.ssh/authorized_keys; "
                "echo '密钥已添加到 authorized_keys'; }"},
        {"cmd": "echo -n 'authorized_keys 当前公钥数量: '; grep -c . ~/.ssh/authorized_keys"},
    ]
    if p.get("disable_password"):
        dropin = [
            "# Managed by oci-pilot: key-only login",
            "PubkeyAuthentication yes",
            "PasswordAuthentication no",
            "KbdInteractiveAuthentication no",
            "ChallengeResponseAuthentication no",
        ]
        steps += [
            {"cmd": "{SUDO} mkdir -p /etc/ssh/sshd_config.d && "
                    + _tee_lines(dropin,
                                 "/etc/ssh/sshd_config.d/99-oci-pilot-keyonly.conf")},
            {"cmd": "{SUDO} mkdir -p /run/sshd && {SUDO} sshd -t && "
                    "({SUDO} systemctl restart sshd 2>/dev/null || "
                    "{SUDO} systemctl restart ssh) && "
                    "echo 'sshd 已重载: 密码登录已关闭(当前会话不受影响)'"},
            {"cmd": "{SUDO} sshd -T 2>/dev/null | grep -i '^passwordauthentication' || true",
             "allow_fail": True},
        ]
    return steps


# ---------------- 修改 SSH 端口 ----------------

def _ssh_port_validate(p):
    return {
        "new_port": _int(p, "new_port", 0, 1, 65535, "新端口"),
        "close_old": _bool(p, "close_old", True),
    }


def ssh_port_phase_a(p, ctx):
    """第一阶段: 新旧端口同时监听 + 主机侧防火墙放行 + 重启 sshd。"""
    old, new = ctx["ssh_port"], p["new_port"]
    lines = ["# Managed by oci-pilot: ssh ports",
             f"Port {old}", f"Port {new}"]
    return [
        {"cmd": "{SUDO} mkdir -p /etc/ssh/sshd_config.d && "
                + _tee_lines(lines, "/etc/ssh/sshd_config.d/99-oci-pilot-port.conf")},
        {"cmd": f"if {{SUDO}} ufw status 2>/dev/null | grep -q 'Status: active'; then "
                f"{{SUDO}} ufw allow {new}/tcp; fi; "
                f"if systemctl is-active firewalld > /dev/null 2>&1; then "
                f"{{SUDO}} firewall-cmd --permanent --add-port={new}/tcp && "
                "{SUDO} firewall-cmd --reload; fi; echo '主机防火墙处理完毕'",
         "allow_fail": True},
        {"cmd": "{SUDO} mkdir -p /run/sshd && {SUDO} sshd -t && "
                "({SUDO} systemctl restart sshd 2>/dev/null || "
                "{SUDO} systemctl restart ssh) && "
                f"echo 'sshd 已重启, 当前同时监听 {old} 与 {new}'"},
    ]


def ssh_port_phase_c(p, ctx):
    """第三阶段: 新端口实测通过后, 只保留新端口。"""
    return [
        {"cmd": "{SUDO} mkdir -p /etc/ssh/sshd_config.d && "
                + _tee_lines(["# Managed by oci-pilot: ssh ports",
                              f"Port {p['new_port']}"],
                             "/etc/ssh/sshd_config.d/99-oci-pilot-port.conf")},
        {"cmd": "{SUDO} mkdir -p /run/sshd && {SUDO} sshd -t && "
                "({SUDO} systemctl restart sshd 2>/dev/null || "
                "{SUDO} systemctl restart ssh) && "
                f"echo '已切换: sshd 现在只监听 {p['new_port']}'"},
    ]


# ---------------- Swap ----------------

def _swap_validate(p):
    return {"size_gb": _int(p, "size_gb", 2, 1, 32, "Swap 大小(GB)")}


def _swap_build(p, ctx):
    n = p["size_gb"]
    script = f"""set -e
if {{SUDO}} swapon --show=NAME --noheadings 2>/dev/null | grep -q .; then
  echo '检测到已有 swap, 未做任何更改:'
  {{SUDO}} swapon --show
  exit 0
fi
{{SUDO}} fallocate -l {n}G /swapfile || {{SUDO}} dd if=/dev/zero of=/swapfile bs=1M count={n * 1024} status=none
{{SUDO}} chmod 600 /swapfile
{{SUDO}} mkswap /swapfile
{{SUDO}} swapon /swapfile
grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | {{SUDO}} tee -a /etc/fstab > /dev/null
echo 'swap 已创建并写入 fstab:'
{{SUDO}} swapon --show
free -h"""
    return [{"cmd": script, "timeout": 300}]


# ---------------- BBR ----------------

def _bbr_build(p, ctx):
    lines = ["net.core.default_qdisc = fq",
             "net.ipv4.tcp_congestion_control = bbr"]
    script = ("{SUDO} modprobe tcp_bbr 2>/dev/null || true\n"
              + _tee_lines(lines, "/etc/sysctl.d/99-oci-pilot-bbr.conf") + "\n"
              "{SUDO} sysctl --system > /dev/null\n"
              "cur=$({SUDO} sysctl -n net.ipv4.tcp_congestion_control)\n"
              "echo \"当前 TCP 拥塞控制: $cur\"\n"
              "echo \"可用: $({SUDO} sysctl -n net.ipv4.tcp_available_congestion_control)\"\n"
              "if [ \"$cur\" != \"bbr\" ]; then echo 'BBR 未生效(内核可能不支持)'; exit 1; fi\n"
              "echo 'BBR 已开启'")
    return [{"cmd": script}]


# ---------------- 时区 ----------------

def _tz_validate(p):
    tz = (p.get("tz") or "").strip()
    if tz not in ALLOWED_TZ:
        raise ValueError("时区不在支持列表中")
    return {"tz": tz}


def _tz_build(p, ctx):
    return [
        {"cmd": f"{{SUDO}} timedatectl set-timezone {shlex.quote(p['tz'])} && "
                f"echo '时区已设置为 {p['tz']}'"},
        {"cmd": "timedatectl | head -4; date", "allow_fail": True},
    ]


# ---------------- 主机名 ----------------

HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
    r"(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")


def _hostname_validate(p):
    name = (p.get("hostname") or "").strip().lower()
    if not HOSTNAME_RE.match(name):
        raise ValueError(
            "主机名不合法: 只能用小写字母/数字/连字符/点, 每段 1-63 字符, "
            "段首段尾不能是连字符, 总长不超过 253")
    return {"hostname": name}


def _hostname_build(p, ctx):
    name = p["hostname"]
    q = shlex.quote(name)
    script = (
        "set -e\n"
        f"if command -v hostnamectl > /dev/null 2>&1; then "
        f"{{SUDO}} hostnamectl set-hostname {q}; "
        f"else echo {q} | {{SUDO}} tee /etc/hostname > /dev/null && "
        f"{{SUDO}} hostname {q}; fi\n"
        f"if grep -q '^127\\.0\\.1\\.1' /etc/hosts; then "
        f"{{SUDO}} sed -i 's/^127\\.0\\.1\\.1.*/127.0.1.1 {name}/' /etc/hosts; "
        f"else echo '127.0.1.1 {name}' | {{SUDO}} tee -a /etc/hosts > /dev/null; fi\n"
        "echo '主机名已设置(当前 shell 的提示符要重新登录才刷新):'\n"
        "hostname\n"
        "grep '^127\\.0\\.1\\.1' /etc/hosts || true"
    )
    return [{"cmd": script}]


# ---------------- 系统更新 ----------------

def _sysupdate_build(p, ctx):
    return [
        {"cmd": "if command -v apt-get > /dev/null 2>&1; then "
                "{SUDO} env DEBIAN_FRONTEND=noninteractive apt-get update && "
                "{SUDO} env DEBIAN_FRONTEND=noninteractive apt-get -y "
                "-o Dpkg::Options::='--force-confold' upgrade; "
                "elif command -v dnf > /dev/null 2>&1; then {SUDO} dnf -y upgrade; "
                "elif command -v yum > /dev/null 2>&1; then {SUDO} yum -y update; "
                "else echo '不支持的包管理器(仅支持 apt / dnf / yum)'; exit 1; fi",
         "timeout": 1800},
        {"cmd": "if [ -f /var/run/reboot-required ]; then "
                "echo '⚠️ 更新后需要重启服务器才能完全生效'; "
                "else echo '本次更新无需重启'; fi", "allow_fail": True},
    ]


ACTIONS = [
    {
        "id": "fail2ban", "name": "安装 fail2ban", "danger": False,
        "desc": "安装并启用 fail2ban 的 sshd 防爆破 jail, 自动封禁反复输错密码的 IP。",
        "params": [
            {"key": "bantime", "label": "封禁时长(秒)", "type": "int",
             "default": 3600, "min": 60, "max": 31536000},
            {"key": "findtime", "label": "统计窗口(秒)", "type": "int",
             "default": 600, "min": 60, "max": 86400},
            {"key": "maxretry", "label": "窗口内最大失败次数", "type": "int",
             "default": 5, "min": 1, "max": 100},
        ],
        "validate": _fail2ban_validate, "build": _fail2ban_build,
    },
    {
        "id": "key_login", "name": "设置密钥登录", "danger": True,
        "desc": "把公钥加入登录用户的 authorized_keys。可选关闭密码登录——仅限使用面板已保存密钥, 且执行器会先用该密钥实测能登录成功才关闭, 防止把自己锁在外面。",
        "params": [
            {"key": "source", "label": "公钥来源", "type": "select",
             "default": "stored",
             "options": [{"v": "stored", "t": "面板已保存的密钥"},
                         {"v": "paste", "t": "粘贴公钥(不能关闭密码登录)"}]},
            {"key": "key_id", "label": "已保存密钥", "type": "key_id"},
            {"key": "public_key", "label": "公钥(一行)", "type": "textarea",
             "help": "形如 ssh-ed25519 AAAA... 备注"},
            {"key": "disable_password", "label": "同时关闭密码登录(仅密钥可登录)",
             "type": "bool", "default": False},
        ],
        "validate": _key_login_validate, "build": _key_login_build,
    },
    {
        "id": "ssh_port", "name": "修改 SSH 端口", "danger": True,
        "desc": "把 sshd 改到新端口。流程是先双端口监听, 执行器实测新端口能登录后才关闭旧端口; OCI 主机会同步在安全列表放行新端口。中途失败会停在双端口状态, 不会锁死。",
        "params": [
            {"key": "new_port", "label": "新端口", "type": "int",
             "default": 22222, "min": 1, "max": 65535},
            {"key": "close_old", "label": "实测通过后关闭旧端口", "type": "bool",
             "default": True},
        ],
        "validate": _ssh_port_validate, "build": None,  # 执行器分阶段处理
    },
    {
        "id": "swap", "name": "添加 Swap", "danger": False,
        "desc": "创建 swap 文件并写入 fstab 持久化。已有 swap 时自动跳过, 不覆盖。",
        "params": [
            {"key": "size_gb", "label": "Swap 大小(GB)", "type": "int",
             "default": 2, "min": 1, "max": 32},
        ],
        "validate": _swap_validate, "build": _swap_build,
    },
    {
        "id": "bbr", "name": "开启 BBR", "danger": False,
        "desc": "启用 TCP BBR 拥塞控制(fq + bbr 并持久化), 改善跨国/高延迟链路吞吐。",
        "params": [],
        "validate": lambda p: {}, "build": _bbr_build,
    },
    {
        "id": "timezone", "name": "设置时区", "danger": False,
        "desc": "用 timedatectl 设置系统时区。",
        "params": [
            {"key": "tz", "label": "时区", "type": "select", "default": "Asia/Shanghai",
             "options": [{"v": tz, "t": tz} for tz in ALLOWED_TZ]},
        ],
        "validate": _tz_validate, "build": _tz_build,
    },
    {
        "id": "hostname", "name": "设置主机名", "danger": False,
        "desc": "修改操作系统的主机名(hostnamectl 设置, 并同步 /etc/hostname 与 /etc/hosts 的 127.0.1.1 记录)。注意: 这是系统内部的主机名, 与实例管理里改的 OCI 显示名是两回事, 互不影响。",
        "params": [
            {"key": "hostname", "label": "新主机名(小写字母/数字/-/., 如 web-1)",
             "type": "text", "default": ""},
        ],
        "validate": _hostname_validate, "build": _hostname_build,
    },
    {
        "id": "sysupdate", "name": "系统更新", "danger": True,
        "desc": "执行系统软件包全量更新(apt / dnf / yum)。耗时较长; 如需重启会在日志里明确提示, 不会自动重启。",
        "params": [],
        "validate": lambda p: {}, "build": _sysupdate_build,
    },
]

BY_ID = {a["id"]: a for a in ACTIONS}


def public_actions():
    """给前端的动作清单(不含函数)。"""
    return [{k: a[k] for k in ("id", "name", "desc", "danger", "params")}
            for a in ACTIONS]
