"""全局配置: 项目根目录 .env 文件 + 环境变量(环境变量优先), 开箱即用。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("OCI_PILOT_DATA", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_dotenv():
    """极简 .env 解析: KEY=VALUE, 忽略空行/#注释, 已存在的环境变量不覆盖。"""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv()

DB_PATH = DATA_DIR / "oci_pilot.db"
SECRET_FILE = DATA_DIR / ".secret"

PORT = int(os.environ.get("OCI_PILOT_PORT", "9528"))
HOST = os.environ.get("OCI_PILOT_HOST", "0.0.0.0")

# Telegram Bot Token (可选, 不填则 Bot 不启动)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")


def _parse_ids(s: str) -> set[int]:
    out = set()
    for part in (s or "").replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


# 允许使用 Bot 的 Telegram 数字 ID 白名单(逗号分隔)。
# 为空 = 拒绝所有绑定/命令(安全默认); 去 @userinfobot 发任意消息可查到自己的 ID。
TELEGRAM_ALLOWED_IDS = _parse_ids(os.environ.get("TELEGRAM_ALLOWED_IDS", ""))

JWT_EXPIRE_HOURS = int(os.environ.get("JWT_EXPIRE_HOURS", "72"))

# 登录限流: 每 IP 每窗口期最多尝试次数
LOGIN_RATE_LIMIT = int(os.environ.get("LOGIN_RATE_LIMIT", "10"))
LOGIN_RATE_WINDOW = int(os.environ.get("LOGIN_RATE_WINDOW", "60"))

WEB_DIR = BASE_DIR / "web"


def get_secret_key() -> str:
    """本地持久化的 Fernet key, 用于加密存储 API 私钥等敏感信息。"""
    if SECRET_FILE.exists():
        return SECRET_FILE.read_text().strip()
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    SECRET_FILE.write_text(key)
    try:
        os.chmod(SECRET_FILE, 0o600)
    except OSError:
        pass
    return key
