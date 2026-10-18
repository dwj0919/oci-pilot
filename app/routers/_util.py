"""路由层公共: 把阻塞的 OCI/SDK 调用丢进线程池, 统一翻译异常。"""
import asyncio

from fastapi import HTTPException

from ..oci.base import OCIError


async def oci_call(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except OCIError as e:
        raise HTTPException(400, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
