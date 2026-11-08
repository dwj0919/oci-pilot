"""服务器运维 API: 固定动作模板 + 任务执行/日志查询。

动作模板见 app/ops/actions.py; 执行器见 app/ops/runner.py。
所有接口都需要登录; 不提供任意命令执行。
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import desc, select

from ..db import OpsTask, User, get_session
from ..deps import get_current_user
from ..ops import actions as A
from ..ops.runner import start_task, task_out

router = APIRouter(prefix="/api/ops", tags=["ops"])


class TaskIn(BaseModel):
    host_id: int
    action: str
    params: dict = {}


@router.get("/actions")
def list_actions(_u: User = Depends(get_current_user)):
    return A.public_actions()


@router.post("/tasks")
def create_task(body: TaskIn, _u: User = Depends(get_current_user)):
    try:
        return start_task(body.host_id, body.action, body.params)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/tasks")
def list_tasks(limit: int = 30, _u: User = Depends(get_current_user)):
    with get_session() as s:
        rows = s.exec(select(OpsTask).order_by(desc(OpsTask.id))
                      .limit(min(limit, 100))).all()
        return [task_out(t) for t in rows]


@router.get("/tasks/{task_id}")
def get_task(task_id: int, _u: User = Depends(get_current_user)):
    with get_session() as s:
        t = s.get(OpsTask, task_id)
        if not t:
            raise HTTPException(404, "任务不存在")
        return task_out(t, with_log=True)
