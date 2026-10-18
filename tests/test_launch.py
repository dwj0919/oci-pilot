"""抢机引擎逻辑测试(全 mock,不碰真实 OCI)。"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db, get_session, LaunchTask  # noqa: E402
from app.oci import launch  # noqa: E402
from app.oci.base import CapacityError, QuotaError  # noqa: E402

init_db()

notifies: list[str] = []
launch.set_notifier(notifies.append)


def _reset():
    notifies.clear()
    with get_session() as s:
        for t in s.query(LaunchTask).all():
            s.delete(t)
        s.commit()


def test_retry_until_success():
    """缺货两次 -> 第三次成功, attempts=3, 收到成功通知。"""
    _reset()
    calls = {"n": 0}

    def fake_launch(account_id, params):
        calls["n"] += 1
        if calls["n"] < 3:
            raise CapacityError("[500] Out of host capacity.")
        return {"instance_id": "ocid1.instance.test"}

    def fake_wait(account_id, instance_id, timeout=300):
        return {"name": "test-1", "public_ip": "1.2.3.4",
                "private_ip": "10.0.0.5"}

    tid = launch.create_task("测试抢机", 1, {"shape": "VM.Standard.A1.Flex"},
                             delay_min=0, delay_max=0)
    with patch.object(launch._compute, "launch_instance", fake_launch), \
         patch.object(launch._compute, "wait_for_ip", fake_wait):
        asyncio.run(launch._run_task(tid))

    with get_session() as s:
        t = s.get(LaunchTask, tid)
        assert t.status == "done", t.status
        assert t.attempts == 3, t.attempts
    assert any("抢机成功" in m and "1.2.3.4" in m for m in notifies), notifies
    print("test_retry_until_success PASS")


def test_quota_fails_fast():
    """配额不足 -> 直接失败, 不再重试, 收到终止通知。"""
    _reset()

    def fake_launch(account_id, params):
        raise QuotaError("[400] Limit exceeded.")

    tid = launch.create_task("测试配额", 1, {}, delay_min=0, delay_max=0)
    with patch.object(launch._compute, "launch_instance", fake_launch):
        asyncio.run(launch._run_task(tid))

    with get_session() as s:
        t = s.get(LaunchTask, tid)
        assert t.status == "failed", t.status
        assert t.attempts == 1, t.attempts
    assert any("配额不足" in m for m in notifies), notifies
    print("test_quota_fails_fast PASS")


def test_cancel():
    """取消任务 -> 循环退出。"""
    _reset()

    async def slow():
        tid = launch.create_task("测试取消", 1, {}, delay_min=30,
                                 delay_max=30)
        task = asyncio.create_task(launch._run_task(tid))
        await asyncio.sleep(0.2)
        launch.cancel_task(tid)
        try:
            await asyncio.wait_for(task, timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        with get_session() as s:
            t = s.get(LaunchTask, tid)
            assert t.status == "cancelled", t.status

    def fake_launch(account_id, params):
        raise CapacityError("[500] Out of host capacity.")

    with patch.object(launch._compute, "launch_instance", fake_launch):
        asyncio.run(slow())
    print("test_cancel PASS")


if __name__ == "__main__":
    test_retry_until_success()
    test_quota_fails_fast()
    test_cancel()
    print("ALL LAUNCH TESTS PASS")
