"""SQLite 数据层 (SQLModel)。所有用户数据只存在本地 data/ 目录。"""
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel, Session, create_engine

from .config import DB_PATH

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    password_hash: str = ""  # 已废弃: 仅 Telegram 登录, 保留字段兼容旧库
    telegram_user_id: Optional[int] = None
    totp_secret: Optional[str] = None  # 已废弃: 随密码体系下线
    totp_enabled: bool = False  # 已废弃: 随密码体系下线
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OCIAccount(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    tenancy_ocid: str
    user_ocid: str
    fingerprint: str
    region: str
    private_key_enc: str  # Fernet 加密后的 PEM
    proxy: Optional[str] = None  # http://host:port, 可选
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Host(SQLModel, table=True):
    """SSH 主机(手动添加 / 从 OCI 一键同步)。"""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    hostname: str
    port: int = 22
    username: str = "root"
    auth_type: str = "password"  # password | key
    password_enc: Optional[str] = None
    key_id: Optional[int] = None
    tags: str = ""
    oci_account_id: Optional[int] = None
    oci_instance_id: Optional[str] = None


class SSHKey(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    private_key_enc: str
    public_key: str = ""


class LaunchTask(SQLModel, table=True):
    """抢机任务: 后台循环重试直到开出来 / 取消 / 配额失败。"""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    account_id: int
    params_json: str
    delay_min: int = 5
    delay_max: int = 15
    status: str = "running"  # running | done | cancelled | failed
    attempts: int = 0
    last_error: str = ""
    result_json: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None


class InstanceWatch(SQLModel, table=True):
    """实例保活: 状态监控 + 掉线通知/自动拉起。"""
    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int
    instance_id: str = Field(index=True)
    display_name: str = ""
    auto_restart: bool = False
    enabled: bool = True


class TrafficGuard(SQLModel, table=True):
    """流量守护: 按实例设月流出阈值, 超了通知或自动关机。"""
    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int
    instance_id: str = Field(index=True)
    display_name: str = ""
    vnic_id: str = ""
    threshold_gb: float = 9000.0
    action: str = "notify"  # notify | stop
    enabled: bool = True
    month: str = ""  # 已统计的月份 YYYY-MM, 用于重置
    used_gb: float = 0.0




class OpsTask(SQLModel, table=True):
    """服务器运维任务: 对一台主机执行一个固定动作模板, 保存全程日志。"""
    id: Optional[int] = Field(default=None, primary_key=True)
    host_id: int
    host_name: str = ""
    action: str
    params_json: str = "{}"
    status: str = "running"  # running | done | failed
    log: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None


class LoginCode(SQLModel, table=True):
    """Telegram 验证码登录: Bot 发 6 位码, Web 端验证。"""
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True)
    telegram_user_id: int
    expires_at: datetime
    chat_id: Optional[int] = None  # 验证码消息所在聊天, 用于登录后自动删除
    message_id: Optional[int] = None


class KVStore(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str = ""


def init_db():
    SQLModel.metadata.create_all(engine)
    # 轻量迁移: 给已存在的 user 表补 totp 字段 (SQLite ADD COLUMN)
    with engine.connect() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(user)").fetchall()}
        if "totp_secret" not in cols:
            conn.exec_driver_sql("ALTER TABLE user ADD COLUMN totp_secret VARCHAR")
        if "totp_enabled" not in cols:
            conn.exec_driver_sql("ALTER TABLE user ADD COLUMN totp_enabled BOOLEAN DEFAULT 0")
        conn.commit()
    # login_code 表补消息定位字段
    with engine.connect() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(logincode)").fetchall()}
        if "chat_id" not in cols:
            conn.exec_driver_sql("ALTER TABLE logincode ADD COLUMN chat_id INTEGER")
        if "message_id" not in cols:
            conn.exec_driver_sql("ALTER TABLE logincode ADD COLUMN message_id INTEGER")
        conn.commit()


def get_session() -> Session:
    return Session(engine)
