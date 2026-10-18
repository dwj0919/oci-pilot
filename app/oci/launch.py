"""抢机引擎: 后台循环重试开机, 直到成功 / 取消 / 配额失败。

甲骨文免费 ARM 长期缺货, 提交后按随机延迟间隔一直重试。
结果通过 notifier 推送(由 Bot 模块注册)。
"""
import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from typing import Callable

from sqlmodel import select

from ..db import LaunchTask, get_session
from . import compute as _compute
from .base import CapacityError, QuotaError, OCIError

log = logging.getLogger("oci-pilot")

_notifier: Callable[[str], None] | None = None
_tasks: dict[int, asyncio.Task] = {}


def set_notifier(fn: Callable[[str], None]):
    global _notifier
    _notifier = fn


def _notify(text: str):
    if _notifier:
        try:
            _notifier(text)
        except Exception:
            pass


def create_task(name: str, account_id: int, params: dict,
                delay_min: int = 5, delay_max: int = 15) -> int:
    with get_session() as s:
        t = LaunchTask(name=name, account_id=account_id,
                       params_json=json.dumps(params),
                       delay_min=delay_min, delay_max=delay_max)
        s.add(t)
        s.commit()
        s.refresh(t)
        return t.id


def list_tasks() -> list[dict]:
    with get_session() as s:
        tasks = s.exec(select(LaunchTask).order_by(
            LaunchTask.created_at.desc())).all()
        return [{"id": t.id, "name": t.name, "account_id": t.account_id,
                 "status": t.status, "attempts": t.attempts,
                 "last_error": t.last_error,
                 "created_at": str(t.created_at)} for t in tasks]


def cancel_task(task_id: int):
    with get_session() as s:
        t = s.get(LaunchTask, task_id)
        if t and t.status == "running":
            t.status = "cancelled"
            t.finished_at = datetime.now(timezone.utc)
            s.add(t)
            s.commit()
    task = _tasks.pop(task_id, None)
    if task:
        task.cancel()


async def _run_task(task_id: int):
    with get_session() as s:
        t = s.get(LaunchTask, task_id)
        if not t:
            return
        params = json.loads(t.params_json)
        account_id = t.account_id
        delay_min, delay_max = t.delay_min, t.delay_max
        name = t.name
    log.info("抢机任务 #%d [%s] 启动 (shape=%s)", task_id, name,
             params.get("shape"))

    attempts = 0
    while True:
        attempts += 1
        with get_session() as s:
            t = s.get(LaunchTask, task_id)
            if not t or t.status != "running":
                log.info("抢机任务 #%d 已停止(取消/完成)", task_id)
                return
            t.attempts = attempts
            s.add(t)
            s.commit()
        try:
            result = await asyncio.to_thread(
                _compute.launch_instance, account_id, params)
            inst_id = result["instance_id"]
            with get_session() as s:
                t = s.get(LaunchTask, task_id)
                t.status = "done"
                t.finished_at = datetime.now(timezone.utc)
                t.result_json = json.dumps(result)
                s.add(t)
                s.commit()
            info = await asyncio.to_thread(
                _compute.wait_for_ip, account_id, inst_id, 300)
            log.info("抢机任务 #%d [%s] 成功: 实例 %s, 共尝试 %d 次",
                     task_id, name, info.get("name"), attempts)
            _notify(f"🎉 抢机成功 [{name}]\n"
                    f"实例: {info.get('name')}\n"
                    f"公网 IP: {info.get('public_ip')}\n"
                    f"内网 IP: {info.get('private_ip')}\n"
                    f"尝试 {attempts} 次")
            return
        except CapacityError as e:
            err = f"缺货({attempts}次): {e}"
        except QuotaError as e:
            with get_session() as s:
                t = s.get(LaunchTask, task_id)
                t.status = "failed"
                t.last_error = str(e)
                t.finished_at = datetime.now(timezone.utc)
                s.add(t)
                s.commit()
            log.warning("抢机任务 #%d [%s] 终止: %s", task_id, name, e)
            _notify(f"❌ 抢机终止 [{name}]: 配额不足, 重试无用\n{e}")
            return
        except OCIError as e:
            err = f"API 错误({attempts}次): {e}"
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            err = f"未知错误({attempts}次): {e}"

        with get_session() as s:
            t = s.get(LaunchTask, task_id)
            if t:
                t.last_error = err
                s.add(t)
                s.commit()
        delay = random.randint(delay_min, delay_max)
        log.info("抢机任务 #%d [%s] %s, %d 秒后重试",
                 task_id, name, err, delay)
        await asyncio.sleep(delay)


async def resume_all():
    """启动时恢复未完成的抢机任务。"""
    with get_session() as s:
        tasks = s.exec(select(LaunchTask).where(
            LaunchTask.status == "running")).all()
        ids = [t.id for t in tasks]
    for tid in ids:
        _tasks[tid] = asyncio.create_task(_run_task(tid))


def submit(task_id: int):
    _tasks[task_id] = asyncio.create_task(_run_task(task_id))
