"""FastAPI 鉴权依赖。"""
import time

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlmodel import select

from .config import LOGIN_RATE_LIMIT, LOGIN_RATE_WINDOW
from .db import User, get_session
from .security import verify_token

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> User:
    if not creds:
        raise HTTPException(401, "未登录")
    username = verify_token(creds.credentials)
    if not username:
        raise HTTPException(401, "登录已过期, 请重新登录")
    with get_session() as s:
        u = s.exec(select(User).where(User.username == username)).first()
        if not u:
            raise HTTPException(401, "用户不存在")
        s.expunge(u)
        return u


def verify_ws_token(token: str) -> str | None:
    """WebSocket 用: query 参数里的 token。"""
    return verify_token(token)


# ---------------- 登录限流 ----------------
# 内存滑动窗口: 每 IP 每窗口期最多 N 次登录/初始化尝试, 防暴力破解。
# 成功失败都计数; 面板是单机自托管, 内存记录足够, 重启后清零。
_login_hits: dict[str, list[float]] = {}


def check_login_rate_limit(request: Request):
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    hits = [t for t in _login_hits.get(ip, []) if now - t < LOGIN_RATE_WINDOW]
    if len(hits) >= LOGIN_RATE_LIMIT:
        raise HTTPException(429, f"登录尝试过于频繁, 请 {LOGIN_RATE_WINDOW} 秒后再试")
    hits.append(now)
    _login_hits[ip] = hits
    # 顺手清理过期条目, 防内存膨胀
    if len(_login_hits) > 2000:
        for k in [k for k, v in _login_hits.items()
                  if not v or now - v[-1] > LOGIN_RATE_WINDOW]:
            _login_hits.pop(k, None)
