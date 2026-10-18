"""对象存储: Bucket 与文件管理。"""
from fastapi import APIRouter, Depends, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..deps import get_current_user
from ..db import User
from ..oci import objectstore as O
from . import _util as U

router = APIRouter(prefix="/api/objectstore")


@router.get("/buckets")
async def buckets(account_id: int, user: User = Depends(get_current_user)):
    return await U.oci_call(O.list_buckets, account_id)


class BucketIn(BaseModel):
    account_id: int
    compartment_id: str
    name: str


@router.post("/buckets")
async def create_bucket(data: BucketIn, user: User = Depends(get_current_user)):
    return await U.oci_call(O.create_bucket, data.account_id,
                            data.compartment_id, data.name)


@router.delete("/buckets/{name}")
async def delete_bucket(name: str, account_id: int,
                        user: User = Depends(get_current_user)):
    return await U.oci_call(O.delete_bucket, account_id, name)


@router.get("/objects")
async def objects(account_id: int, bucket: str, prefix: str = "",
                  user: User = Depends(get_current_user)):
    return await U.oci_call(O.list_objects, account_id, bucket, prefix)


@router.post("/upload")
async def upload(account_id: int = Form(...), bucket: str = Form(...),
                 prefix: str = Form(""), file: UploadFile = File(...),
                 user: User = Depends(get_current_user)):
    data = await file.read()
    name = (prefix or "") + file.filename
    return await U.oci_call(O.upload_object, account_id, bucket, name, data)


@router.get("/download")
async def download(account_id: int, bucket: str, name: str,
                   user: User = Depends(get_current_user)):
    data = await U.oci_call(O.download_object, account_id, bucket, name)

    def _gen():
        yield data

    return StreamingResponse(_gen(),
                             media_type="application/octet-stream",
                             headers={"Content-Disposition":
                                      f"attachment; filename={name.split('/')[-1]}"})


@router.delete("/objects")
async def delete_object(account_id: int, bucket: str, name: str,
                        user: User = Depends(get_current_user)):
    return await U.oci_call(O.delete_object, account_id, bucket, name)
