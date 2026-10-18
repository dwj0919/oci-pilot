"""Web SSH 终端 + SFTP 文件管理 API。

- 主机/密钥的增删改查
- SSH 会话的创建/关闭, WebSocket 终端透传
- 基于已建会话的 SFTP 文件管理(浏览/上传/下载/删除/改名/在线编辑)
"""
import asyncio
import json
import logging
import posixpath
import stat
from typing import Optional
from urllib.parse import quote

import paramiko
from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query,
                     UploadFile, WebSocket, WebSocketDisconnect)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import select

from ..deps import get_current_user, verify_ws_token
from ..db import Host, SSHKey, User, get_session
from ..security import encrypt, decrypt
from ..ssh.manager import SSHSessionManager, validate_private_key

router = APIRouter(prefix="/api/ssh", tags=["ssh"])
manager = SSHSessionManager()
log = logging.getLogger("oci-pilot")


# ---------- 请求/响应模型 ----------

class HostIn(BaseModel):
    name: str
    hostname: str
    port: int = 22
    username: str = "root"
    auth_type: str = "password"  # password | key
    password: Optional[str] = None
    key_id: Optional[int] = None
    tags: str = ""


class HostUpdate(BaseModel):
    name: Optional[str] = None
    hostname: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    auth_type: Optional[str] = None
    password: Optional[str] = None  # 传值则更新; 传空字符串则清空
    key_id: Optional[int] = None
    tags: Optional[str] = None


class KeyIn(BaseModel):
    name: str
    private_key: str
    public_key: str = ""


class SessionIn(BaseModel):
    host_id: Optional[int] = None
    # host_id 为空时走临时直连
    hostname: Optional[str] = None
    port: int = 22
    username: str = "root"
    auth_type: str = "password"
    password: Optional[str] = None
    key_id: Optional[int] = None


class SftpMkdirIn(BaseModel):
    session_id: str
    path: str


class SftpDeleteIn(BaseModel):
    session_id: str
    path: str


class SftpRenameIn(BaseModel):
    session_id: str
    old: str
    new: str


class SftpWriteIn(BaseModel):
    session_id: str
    path: str
    content: str


class SyncOciIn(BaseModel):
    account_id: int


def _host_out(h: Host) -> dict:
    return {"id": h.id, "name": h.name, "hostname": h.hostname,
            "port": h.port, "username": h.username,
            "auth_type": h.auth_type, "key_id": h.key_id, "tags": h.tags,
            "has_password": bool(h.password_enc),
            "oci_account_id": h.oci_account_id,
            "oci_instance_id": h.oci_instance_id}


def _safe_path(path: str) -> str:
    """SFTP 路径规范化: 折叠 .., 强制 rooted, 防止目录逃逸。"""
    if not path:
        raise HTTPException(400, "路径不能为空")
    p = posixpath.normpath(path if path.startswith("/") else "/" + path)
    if not p.startswith("/"):
        p = "/" + p
    return p


async def _get_sftp(session_id: str):
    sess = manager.get(session_id)
    if not sess:
        raise HTTPException(404, "SSH 会话不存在或已关闭, 请重新连接")
    try:
        return await asyncio.to_thread(manager.get_sftp, session_id)
    except KeyError:
        raise HTTPException(404, "SSH 会话不存在或已关闭, 请重新连接")


async def _sftp_call(fn, *args):
    """执行阻塞的 SFTP 调用, 并把常见异常翻译成 HTTP 错误。"""
    try:
        return await asyncio.to_thread(fn, *args)
    except FileNotFoundError:
        raise HTTPException(404, "文件或目录不存在")
    except PermissionError:
        raise HTTPException(403, "权限不足")
    except OSError as e:
        raise HTTPException(400, f"SFTP 操作失败: {e}")


# ---------- 主机管理 ----------

@router.get("/hosts")
def list_hosts(_u: User = Depends(get_current_user)):
    with get_session() as s:
        hosts = s.exec(select(Host).order_by(Host.id)).all()
        return [_host_out(h) for h in hosts]


@router.post("/hosts")
def create_host(body: HostIn, _u: User = Depends(get_current_user)):
    with get_session() as s:
        h = Host(name=body.name.strip(), hostname=body.hostname.strip(),
                 port=body.port, username=body.username,
                 auth_type=body.auth_type, key_id=body.key_id,
                 tags=body.tags or "")
        if body.auth_type == "password" and body.password:
            h.password_enc = encrypt(body.password)
        s.add(h)
        s.commit()
        s.refresh(h)
        return _host_out(h)


@router.put("/hosts/{host_id}")
def update_host(host_id: int, body: HostUpdate,
                _u: User = Depends(get_current_user)):
    with get_session() as s:
        h = s.get(Host, host_id)
        if not h:
            raise HTTPException(404, "主机不存在")
        data = body.model_dump(exclude_unset=True)
        password = data.pop("password", None)
        for k, v in data.items():
            if v is not None:
                setattr(h, k, v)
        if password is not None:  # 显式传了 password 字段
            h.password_enc = encrypt(password) if password else None
        s.add(h)
        s.commit()
        s.refresh(h)
        return _host_out(h)


@router.delete("/hosts/{host_id}")
def delete_host(host_id: int, _u: User = Depends(get_current_user)):
    with get_session() as s:
        h = s.get(Host, host_id)
        if not h:
            raise HTTPException(404, "主机不存在")
        s.delete(h)
        s.commit()
        return {"ok": True}


# ---------- 密钥管理 ----------

@router.get("/keys")
def list_keys(_u: User = Depends(get_current_user)):
    with get_session() as s:
        keys = s.exec(select(SSHKey).order_by(SSHKey.id)).all()
        return [{"id": k.id, "name": k.name, "public_key": k.public_key}
                for k in keys]


@router.post("/keys")
def create_key(body: KeyIn, _u: User = Depends(get_current_user)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "密钥名称不能为空")
    if not validate_private_key(body.private_key):
        raise HTTPException(400, "私钥格式无法识别(支持 RSA / Ed25519 / ECDSA 的 OpenSSH 或 PEM 格式)")
    with get_session() as s:
        if s.exec(select(SSHKey).where(SSHKey.name == name)).first():
            raise HTTPException(400, f"密钥名 '{name}' 已存在")
        k = SSHKey(name=name, private_key_enc=encrypt(body.private_key),
                   public_key=body.public_key or "")
        s.add(k)
        s.commit()
        s.refresh(k)
        return {"id": k.id, "name": k.name, "public_key": k.public_key}


@router.delete("/keys/{key_id}")
def delete_key(key_id: int, _u: User = Depends(get_current_user)):
    with get_session() as s:
        k = s.get(SSHKey, key_id)
        if not k:
            raise HTTPException(404, "密钥不存在")
        used = s.exec(select(Host).where(Host.key_id == key_id)).first()
        if used:
            raise HTTPException(400, f"密钥正被主机 '{used.name}' 使用, 请先解绑")
        s.delete(k)
        s.commit()
        return {"ok": True}


# ---------- SSH 会话 ----------

def _ssh_error(e: Exception) -> HTTPException:
    # 服务端记完整 traceback (日志已脱敏), 返回给前端带上异常类名便于定位
    log.exception("SSH 建会话失败")
    kind = type(e).__name__
    if isinstance(e, paramiko.AuthenticationException):
        return HTTPException(401, f"SSH 认证失败: 用户名/密码/密钥不正确 [{kind}]")
    if isinstance(e, ValueError):
        return HTTPException(400, str(e))
    return HTTPException(502, f"SSH 连接失败 [{kind}]: {e}")


@router.post("/sessions")
async def create_ssh_session(body: SessionIn,
                             _u: User = Depends(get_current_user)):
    """建会话: 传 host_id 用保存的主机; 否则用临时直连参数。"""
    try:
        if body.host_id:
            sid = await manager.create_from_host_id(body.host_id)
        else:
            if not body.hostname:
                raise HTTPException(400, "缺少 hostname")
            pkey = None
            if body.auth_type == "key" and body.key_id:
                with get_session() as s:
                    k = s.get(SSHKey, body.key_id)
                    if not k:
                        raise HTTPException(404, "密钥不存在")
                    pkey = decrypt(k.private_key_enc)
            sid = await manager.create_session(
                {"hostname": body.hostname, "port": body.port,
                 "username": body.username},
                body.password, pkey)
    except HTTPException:
        raise
    except Exception as e:
        raise _ssh_error(e)
    return {"session_id": sid}


@router.delete("/sessions/{session_id}")
async def close_ssh_session(session_id: str,
                            _u: User = Depends(get_current_user)):
    await asyncio.to_thread(manager.close, session_id)
    return {"ok": True}


@router.post("/sync-oci")
async def sync_oci(body: SyncOciIn, _u: User = Depends(get_current_user)):
    """一键从 OCI 同步实例到主机列表。"""
    try:
        result = await asyncio.to_thread(manager.sync_oci_hosts,
                                         body.account_id)
    except Exception as e:
        raise HTTPException(502, f"同步失败: {e}")
    return result


# ---------- SFTP 文件管理 ----------

@router.get("/sftp/list")
async def sftp_list(session_id: str = Query(...), path: str = Query("/"),
                    _u: User = Depends(get_current_user)):
    sftp = await _get_sftp(session_id)
    p = _safe_path(path)
    entries = await _sftp_call(sftp.listdir_attr, p)
    out = []
    for e in sorted(entries, key=lambda x: (not stat.S_ISDIR(x.st_mode), x.filename)):
        full = posixpath.join(p, e.filename)
        out.append({"name": e.filename, "path": full,
                    "is_dir": stat.S_ISDIR(e.st_mode),
                    "size": e.st_size, "mtime": int(e.st_mtime or 0)})
    return out


@router.post("/sftp/mkdir")
async def sftp_mkdir(body: SftpMkdirIn, _u: User = Depends(get_current_user)):
    sftp = await _get_sftp(body.session_id)
    await _sftp_call(sftp.mkdir, _safe_path(body.path))
    return {"ok": True}


@router.post("/sftp/upload")
async def sftp_upload(session_id: str = Form(...), dir: str = Form("/"),
                      file: UploadFile = File(...),
                      _u: User = Depends(get_current_user)):
    sftp = await _get_sftp(session_id)
    d = _safe_path(dir)
    filename = posixpath.basename(file.filename or "upload.bin")
    if not filename or filename in (".", ".."):
        raise HTTPException(400, "非法文件名")
    remote = posixpath.join(d, filename)
    await _sftp_call(sftp.putfo, file.file, remote)
    return {"ok": True, "path": remote}


@router.get("/sftp/download")
async def sftp_download(session_id: str = Query(...), path: str = Query(...),
                        _u: User = Depends(get_current_user)):
    sftp = await _get_sftp(session_id)
    p = _safe_path(path)
    st = await _sftp_call(sftp.stat, p)
    if stat.S_ISDIR(st.st_mode):
        raise HTTPException(400, "不能下载目录")
    filename = posixpath.basename(p)

    async def gen():
        f = await _sftp_call(sftp.open, p, "rb")
        try:
            while True:
                chunk = await asyncio.to_thread(f.read, 65536)
                if not chunk:
                    break
                yield chunk
        finally:
            await asyncio.to_thread(f.close)

    return StreamingResponse(
        gen(), media_type="application/octet-stream",
        headers={"Content-Disposition":
                 f"attachment; filename*=UTF-8''{quote(filename)}"})


@router.post("/sftp/delete")
async def sftp_delete(body: SftpDeleteIn, _u: User = Depends(get_current_user)):
    sftp = await _get_sftp(body.session_id)
    p = _safe_path(body.path)
    if p == "/":
        raise HTTPException(400, "不能删除根目录")
    st = await _sftp_call(sftp.stat, p)
    if stat.S_ISDIR(st.st_mode):
        try:
            await asyncio.to_thread(sftp.rmdir, p)
        except OSError:
            raise HTTPException(400, "目录非空, 请先清空后再删除")
    else:
        await _sftp_call(sftp.remove, p)
    return {"ok": True}


@router.post("/sftp/rename")
async def sftp_rename(body: SftpRenameIn, _u: User = Depends(get_current_user)):
    sftp = await _get_sftp(body.session_id)
    await _sftp_call(sftp.rename, _safe_path(body.old), _safe_path(body.new))
    return {"ok": True}


@router.get("/sftp/read")
async def sftp_read(session_id: str = Query(...), path: str = Query(...),
                    _u: User = Depends(get_current_user)):
    """在线编辑用: 读取文本文件, 限 1MB。"""
    sftp = await _get_sftp(session_id)
    p = _safe_path(path)
    st = await _sftp_call(sftp.stat, p)
    if stat.S_ISDIR(st.st_mode):
        raise HTTPException(400, "不能读取目录")
    if (st.st_size or 0) > 1024 * 1024:
        raise HTTPException(400, "文件超过 1MB, 不支持在线读取, 请下载查看")

    def _read() -> bytes:
        f = sftp.open(p, "rb")
        try:
            return f.read()
        finally:
            f.close()

    data = await _sftp_call(_read)
    return {"content": data.decode("utf-8", errors="replace")}


@router.post("/sftp/write")
async def sftp_write(body: SftpWriteIn, _u: User = Depends(get_current_user)):
    """在线编辑用: 覆盖写文本文件。"""
    sftp = await _get_sftp(body.session_id)
    p = _safe_path(body.path)

    def _write():
        f = sftp.open(p, "w")
        try:
            f.write(body.content)
        finally:
            f.close()

    await _sftp_call(_write)
    return {"ok": True}


# ---------- WebSocket 终端 ----------

@router.websocket("/ws/ssh")
async def ws_ssh(ws: WebSocket):
    """浏览器终端透传。

    query: token=<JWT> & session_id=<id>。
    client -> server JSON: {"t":"input","d":"..."} / {"t":"resize","cols":c,"rows":r}
    server -> client: 二进制字节流(远端输出)。
    """
    token = ws.query_params.get("token", "")
    session_id = ws.query_params.get("session_id", "")
    username = verify_ws_token(token)
    sess = manager.get(session_id) if username else None
    if not username or not sess:
        await ws.close(code=4401)
        return
    await ws.accept()
    queue = sess["queue"]

    async def _pump_out():
        # 远端输出 -> 浏览器; 收到 None 表示通道已关闭, 顺手关掉 ws
        try:
            while True:
                data = await queue.get()
                if data is None:
                    break
                await ws.send_bytes(data)
        except Exception:
            pass
        finally:
            try:
                await ws.close()
            except Exception:
                pass

    pump = asyncio.create_task(_pump_out())
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            t = msg.get("t")
            if t == "input":
                d = msg.get("d", "")
                if isinstance(d, str):
                    d = d.encode("utf-8", errors="replace")
                try:
                    await manager.write(session_id, d)
                except Exception:
                    break
            elif t == "resize":
                try:
                    manager.resize(session_id,
                                   int(msg.get("cols", 120)),
                                   int(msg.get("rows", 30)))
                except Exception:
                    pass
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        # ws 断开不杀 SSH 会话, 允许浏览器重连回来; 会话由 DELETE /sessions 关闭
        if not pump.done():
            pump.cancel()
