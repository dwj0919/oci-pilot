"""对象存储: Bucket 与文件管理。"""
import oci

from .base import oci_clients, OCIError

_ns_cache: dict[int, str] = {}


def get_namespace(account_id: int) -> str:
    if account_id not in _ns_cache:
        with oci_clients(account_id) as c:
            _ns_cache[account_id] = c.objects.get_namespace().data
    return _ns_cache[account_id]


def list_buckets(account_id: int, compartment_id: str | None = None) -> list[dict]:
    with oci_clients(account_id) as c:
        ns = get_namespace(account_id)
        comp = compartment_id or c.tenancy
        buckets = oci.pagination.list_call_get_all_results(
            c.objects.list_buckets, ns, comp).data
        return [{"name": b.name, "time_created": str(b.time_created)} for b in buckets]


def create_bucket(account_id: int, compartment_id: str, name: str) -> dict:
    with oci_clients(account_id) as c:
        ns = get_namespace(account_id)
        c.objects.create_bucket(
            ns, oci.object_storage.models.CreateBucketDetails(
                name=name, compartment_id=compartment_id or c.tenancy))
        return {"ok": True, "name": name}


def delete_bucket(account_id: int, name: str) -> dict:
    with oci_clients(account_id) as c:
        c.objects.delete_bucket(get_namespace(account_id), name)
        return {"ok": True}


def list_objects(account_id: int, bucket: str, prefix: str = "",
                 limit: int = 200) -> list[dict]:
    with oci_clients(account_id) as c:
        ns = get_namespace(account_id)
        resp = c.objects.list_objects(ns, bucket, prefix=prefix or None,
                                      limit=limit)
        return [{"name": o.name, "size": o.size,
                 "time_modified": str(o.time_modified)}
                for o in (resp.data.objects or [])]


def upload_object(account_id: int, bucket: str, object_name: str,
                  data: bytes) -> dict:
    with oci_clients(account_id) as c:
        c.objects.put_object(get_namespace(account_id), bucket,
                             object_name, data)
        return {"ok": True, "size": len(data)}


def download_object(account_id: int, bucket: str, object_name: str) -> bytes:
    with oci_clients(account_id) as c:
        resp = c.objects.get_object(get_namespace(account_id), bucket,
                                    object_name)
        return resp.data.content


def delete_object(account_id: int, bucket: str, object_name: str) -> dict:
    with oci_clients(account_id) as c:
        c.objects.delete_object(get_namespace(account_id), bucket, object_name)
        return {"ok": True}
