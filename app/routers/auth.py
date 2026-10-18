"""登录: 仅 Telegram 验证码登录(无密码体系)。

首次部署: 在 Telegram 里给机器人发 /bind 创建管理员账号,
再发 /logincode 获取验证码, 在 Web 页输入即登录。
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import select, delete

from ..db import User, LoginCode, get_session
from ..deps import get_current_user, check_login_rate_limit
from ..security import create_token

router = APIRouter(prefix="/api/auth")


class LoginIn(BaseModel):
    login_code: str = ""


@router.get("/setup-needed")
def setup_needed():
    """是否还没有任何账号(公开接口, 用于前端显示首次部署指引)。"""
    with get_session() as s:
        return {"needed": s.exec(select(User)).first() is None}


@router.post("/login")
def login(data: LoginIn, request: Request):
    check_login_rate_limit(request)
    code_str = data.login_code.strip()
    if not code_str:
        raise HTTPException(400, "请输入 6 位验证码")
    now = datetime.now(timezone.utc)
    with get_session() as s:
        # 原子消费验证码: DELETE ... RETURNING 保证并发下只有一个请求能拿到它,
        # 读到即删除, 杜绝一码多用
        stmt = (delete(LoginCode)
                .where(LoginCode.code == code_str)
                .returning(LoginCode.telegram_user_id,
                           LoginCode.chat_id, LoginCode.message_id,
                           LoginCode.expires_at))
        row = s.exec(stmt).first()
        s.commit()
        if not row:
            raise HTTPException(400, "验证码无效或已过期")
        tgid, chat_id, message_id, expires_at = row
        if expires_at.tzinfo is None:  # SQLite 可能返回 naive 时间
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < now:
            raise HTTPException(400, "验证码无效或已过期")
        u = s.exec(select(User).where(User.telegram_user_id == tgid)).first()
        if not u:
            raise HTTPException(400, "该 Telegram 用户尚未绑定面板账号, "
                                     "请先在 Bot 里用 /bind 绑定")
        # 登录成功: 自动删除 Telegram 上的验证码消息, 不留痕迹
        try:
            from ..bot.bot import delete_login_code_message_sync
            delete_login_code_message_sync(chat_id, message_id)
        except Exception:
            pass
        return {"token": create_token(u.username), "username": u.username}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"username": user.username,
            "telegram_bound": bool(user.telegram_user_id)}
