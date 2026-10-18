"""串行控制台: 救机器通道 + 控制台历史。"""
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..config import DATA_DIR
from ..deps import get_current_user
from ..db import User
from ..oci import console as CC
from . import _util as U

router = APIRouter(prefix="/api/console")


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
