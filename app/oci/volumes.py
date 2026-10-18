"""块存储: 数据卷 / 启动盘的创建、扩容、调性能、挂载、删除。"""
import oci

from .base import oci_clients, OCIError


def _norm_vol(v) -> dict:
    return {"id": v.id, "name": v.display_name, "size_gb": v.size_in_gbs,
            "vpus_per_gb": getattr(v, "vpus_per_gb", None),
            "state": v.lifecycle_state, "ad": v.availability_domain,
            "is_boot": False}


def list_volumes(account_id: int, compartment_id: str | None = None) -> list[dict]:
    with oci_clients(account_id) as c:
        comp = compartment_id or c.tenancy
        vols = oci.pagination.list_call_get_all_results(
            c.block.list_volumes, compartment_id=comp).data
        out = [_norm_vol(v) for v in vols
               if v.lifecycle_state not in ("TERMINATED", "TERMINATING")]
        boots = oci.pagination.list_call_get_all_results(
            c.block.list_boot_volumes, compartment_id=comp).data
        for b in boots:
            if b.lifecycle_state in ("TERMINATED", "TERMINATING"):
                continue
            d = _norm_vol(b)
            d["is_boot"] = True
            out.append(d)
        return out


def list_attachments(account_id: int, compartment_id: str | None = None,
                     instance_id: str | None = None) -> list[dict]:
    with oci_clients(account_id) as c:
        comp = compartment_id or c.tenancy
        kw = {"instance_id": instance_id} if instance_id else {}
        atts = oci.pagination.list_call_get_all_results(
            c.compute.list_volume_attachments, comp, **kw).data
        return [{"id": a.id, "volume_id": a.volume_id,
                 "instance_id": a.instance_id, "state": a.lifecycle_state,
                 "type": a.attachment_type} for a in atts
                if a.lifecycle_state not in ("DETACHED", "DETACHING")]


def create_volume(account_id: int, compartment_id: str, ad: str, size_gb: int,
                  name: str = "", vpus_per_gb: int = 10) -> dict:
    with oci_clients(account_id) as c:
        v = c.block.create_volume(
            oci.core.models.CreateVolumeDetails(
                compartment_id=compartment_id, availability_domain=ad,
                size_in_gbs=int(size_gb), display_name=name or None,
                vpus_per_gb=int(vpus_per_gb))).data
        return _norm_vol(v)


def resize_volume(account_id: int, volume_id: str, size_gb: int,
                  is_boot: bool = False) -> dict:
    """扩容(只能调大)。"""
    with oci_clients(account_id) as c:
        if is_boot:
            cur = c.block.get_boot_volume(volume_id).data.size_in_gbs
            if int(size_gb) <= cur:
                raise OCIError(f"新大小必须大于当前 {cur}GB")
            c.block.update_boot_volume(
                volume_id,
                oci.core.models.UpdateBootVolumeDetails(size_in_gbs=int(size_gb)))
        else:
            cur = c.block.get_volume(volume_id).data.size_in_gbs
            if int(size_gb) <= cur:
                raise OCIError(f"新大小必须大于当前 {cur}GB")
            c.block.update_volume(
                volume_id,
                oci.core.models.UpdateVolumeDetails(size_in_gbs=int(size_gb)))
        return {"ok": True}


def set_volume_performance(account_id: int, volume_id: str, vpus_per_gb: int,
                           is_boot: bool = False) -> dict:
    """提升 IO: vpus_per_gb 取值 0(更低成本)/10(均衡)/20(更高性能)。"""
    with oci_clients(account_id) as c:
        if is_boot:
            c.block.update_boot_volume(
                volume_id, oci.core.models.UpdateBootVolumeDetails(
                    vpus_per_gb=int(vpus_per_gb)))
        else:
            c.block.update_volume(
                volume_id, oci.core.models.UpdateVolumeDetails(
                    vpus_per_gb=int(vpus_per_gb)))
        return {"ok": True}


def delete_volume(account_id: int, volume_id: str, is_boot: bool = False) -> dict:
    with oci_clients(account_id) as c:
        if is_boot:
            c.block.delete_boot_volume(volume_id)
        else:
            c.block.delete_volume(volume_id)
        return {"ok": True}


def attach_volume(account_id: int, instance_id: str, volume_id: str,
                  is_boot: bool = False) -> dict:
    """把卸下来的(启动)盘挂回实例。"""
    with oci_clients(account_id) as c:
        if is_boot:
            a = c.compute.attach_boot_volume(
                oci.core.models.AttachBootVolumeDetails(
                    instance_id=instance_id, boot_volume_id=volume_id)).data
        else:
            a = c.compute.attach_volume(
                oci.core.models.AttachVolumeDetails(
                    instance_id=instance_id, volume_id=volume_id,
                    type="paravirtualized")).data
        return {"attachment_id": a.id}


def detach_volume(account_id: int, attachment_id: str) -> dict:
    with oci_clients(account_id) as c:
        c.compute.detach_volume(attachment_id)
        return {"ok": True}
