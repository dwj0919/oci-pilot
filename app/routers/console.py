"""串行控制台: 救机器通道 + 控制台历史 + 浏览器图形控制台(VNC)。"""
import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, WebSocket
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import vnc as V
from ..config import DATA_DIR
from ..deps import get_current_user, verify_ws_token
from ..db import User
from ..oci import console as CC
from . import _util as U

router = APIRouter(prefix="/api/console")
log = logging.getLogger("oci-pilot")


class ConnIn(BaseModel):
    account_id: int
    instance_id: str


@router.post("/connections")
async def create(data: ConnIn, user: User = Depends(get_current_user)):
    return await U.oci_call(CC.create_console_connection,
                            data.account_id, data.instance_id)


@router.delete("/connections/{conn_id}")
async def delete(conn_id: str, account_id: int,
                 user: User = Depends(get_current_user)):
    return await U.oci_call(CC.delete_console_connection,
                            account_id, conn_id)


@router.get("/key/{filename}")
def download_key(filename: str, user: User = Depends(get_current_user)):
    p = DATA_DIR / Path(filename).name
    if not p.exists() or not filename.startswith("console_"):
        from fastapi import HTTPException
        raise HTTPException(404, "文件不存在")
    return FileResponse(p, filename=filename)


@router.post("/history")
async def capture(data: ConnIn, user: User = Depends(get_current_user)):
    return await U.oci_call(CC.capture_console_history,
                            data.account_id, data.instance_id)


@router.get("/history/{history_id}")
async def history(history_id: str, account_id: int,
                  user: User = Depends(get_current_user)):
    return await U.oci_call(CC.get_console_history, account_id, history_id)


# ---------------- 浏览器图形控制台 (VNC) ----------------

class VncIn(BaseModel):
    account_id: int
    instance_id: str


@router.post("/vnc-sessions")
async def vnc_create(data: VncIn, user: User = Depends(get_current_user)):
    """创建控制台连接并登记一个 VNC 会话, 返回会话 ID(新窗口用它连 WS)。"""
    conn = await U.oci_call(CC.create_connection_ephemeral,
                            data.account_id, data.instance_id)
    sess = V.register(user.username, data.account_id, data.instance_id, conn)
    return {"session_id": sess.id}


@router.delete("/vnc-sessions/{sid}")
async def vnc_delete(sid: str, user: User = Depends(get_current_user)):
    sess = V.drop(sid)
    if sess and sess.username == user.username:
        await asyncio.to_thread(V.close_session, sess)
    return {"ok": True}


@router.websocket("/vnc/ws")
async def vnc_ws(ws: WebSocket, session: str = "", token: str = ""):
    username = verify_ws_token(token)
    sess = V.get(session) if username else None
    if not sess or sess.username != username or not V.claim(sess):
        await ws.close(code=4401)
        return
    await ws.accept()
    try:
        outer, inner, chan = await asyncio.to_thread(V.open_vnc_channel, sess)
    except Exception as e:
        log.warning("VNC 隧道建立失败 (会话 %s): %s", sess.id, e)
        await ws.close(code=4502)
        V.drop(sess.id)
        await asyncio.to_thread(V.close_session, sess)
        return
    sess.attach(outer, inner, chan)
    log.info("VNC 图形控制台已连接: 实例 %s (会话 %s)",
             sess.instance_id[-12:], sess.id)

    async def ws_to_chan():
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            data = msg.get("bytes") or (msg.get("text") or "").encode()
            if data:
                await asyncio.to_thread(chan.sendall, data)

    async def chan_to_ws():
        while True:
            data = await asyncio.to_thread(chan.recv, 65536)
            if not data:
                return
            await ws.send_bytes(data)

    t1 = asyncio.ensure_future(ws_to_chan())
    t2 = asyncio.ensure_future(chan_to_ws())
    try:
        await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in (t1, t2):
            t.cancel()
        V.drop(sess.id)
        await asyncio.to_thread(V.close_session, sess)
        try:
            await ws.close()
        except Exception:
            pass
