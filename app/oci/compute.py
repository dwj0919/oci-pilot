"""Compute: 实例管理 / 开机(抢机) / 镜像 / Shape / AD。"""
import base64
import time

import oci

from .base import oci_clients, OCIError, CapacityError, QuotaError


def _norm(i) -> dict:
    sc = i.shape_config or {}
    ocpus = getattr(sc, "ocpus", None)
    mem = getattr(sc, "memory_in_gbs", None)
    return {
        "id": i.id,
        "name": i.display_name,
        "shape": i.shape,
        "ocpus": ocpus,
        "memory_gb": mem,
        "state": i.lifecycle_state,
        "ad": i.availability_domain,
        "compartment_id": i.compartment_id,
        "image_id": getattr(i, "image_id", None),
        "time_created": str(i.time_created),
    }


def _attach_ips(account_id: int, compartment_id: str, instances: list[dict]) -> list[dict]:
    """补上每台实例的公网/内网 IP。"""
    with oci_clients(account_id) as c:
        for inst in instances:
            try:
                atts = oci.pagination.list_call_get_all_results(
                    c.compute.list_vnic_attachments,
                    compartment_id, instance_id=inst["id"]).data
                ips = []
                for a in atts:
                    if a.lifecycle_state == "ATTACHED":
                        v = c.network.get_vnic(a.vnic_id).data
                        ips.append({"vnic_id": v.id, "public_ip": v.public_ip,
                                    "private_ip": v.private_ip,
                                    "ipv6": getattr(v, "ipv6_addresses", []) or []})
                inst["vnics"] = ips
                pub = next((x["public_ip"] for x in ips if x["public_ip"]), None)
                inst["public_ip"] = pub
                inst["private_ip"] = ips[0]["private_ip"] if ips else None
            except OCIError:
                inst["vnics"] = []
    return instances


def list_instances(account_id: int, compartment_id: str | None = None) -> list[dict]:
    with oci_clients(account_id) as c:
        comp = compartment_id or c.tenancy
        items = oci.pagination.list_call_get_all_results(
            c.compute.list_instances, comp).data
        result = [_norm(i) for i in items
                  if i.lifecycle_state not in ("TERMINATED", "TERMINATING")]
        return _attach_ips(account_id, comp, result)


def get_instance(account_id: int, instance_id: str) -> dict:
    with oci_clients(account_id) as c:
        i = c.compute.get_instance(instance_id).data
        d = _norm(i)
        _attach_ips(account_id, i.compartment_id, [d])
        return d


def list_ads(account_id: int, compartment_id: str | None = None) -> list[str]:
    with oci_clients(account_id) as c:
        comp = compartment_id or c.tenancy
        ads = c.identity.list_availability_domains(comp).data
        return [a.name for a in ads]


def list_images(account_id: int, compartment_id: str | None = None,
                shape: str | None = None, keyword: str = "") -> list[dict]:
    with oci_clients(account_id) as c:
        comp = compartment_id or c.tenancy
        kw = {"shape": shape} if shape else {}
        imgs = oci.pagination.list_call_get_all_results(
            c.compute.list_images, comp,
            operating_system_version=None, sort_by="TIMECREATED",
            sort_order="DESC", **kw).data
        out = []
        for im in imgs:
            if im.lifecycle_state != "AVAILABLE":
                continue
            if keyword and keyword.lower() not in (im.display_name or "").lower():
                continue
            out.append({"id": im.id, "name": im.display_name,
                        "os": im.operating_system,
                        "os_version": im.operating_system_version,
                        "time_created": str(im.time_created)})
        return out[:100]


def list_shapes(account_id: int, compartment_id: str | None = None,
                image_id: str | None = None) -> list[dict]:
    with oci_clients(account_id) as c:
        comp = compartment_id or c.tenancy
        kw = {"image_id": image_id} if image_id else {}
        shapes = oci.pagination.list_call_get_all_results(
            c.compute.list_shapes, comp, **kw).data
        out = []
        for s in shapes:
            cfg = s.shape_config_options or {}
            out.append({"shape": s.shape,
                        "ocpu_options": getattr(cfg, "ocpu_options", None) and {
                            "min": cfg.ocpu_options.min, "max": cfg.ocpu_options.max},
                        "memory_options": getattr(cfg, "memory_options", None) and {
                            "min": cfg.memory_options.min_in_gbs,
                            "max": cfg.memory_options.max_in_gbs}})
        return out


def list_subnets(account_id: int, compartment_id: str | None = None) -> list[dict]:
    """开机表单用: VCN -> 子网(含 AD 亲和性)。"""
    with oci_clients(account_id) as c:
        comp = compartment_id or c.tenancy
        vcns = oci.pagination.list_call_get_all_results(
            c.network.list_vcns, comp).data
        vcn_names = {v.id: v.display_name for v in vcns}
        out = []
        for v in vcns:
            subs = oci.pagination.list_call_get_all_results(
                c.network.list_subnets, comp, vcn_id=v.id).data
            for sn in subs:
                if sn.lifecycle_state != "AVAILABLE":
                    continue
                out.append({"id": sn.id, "name": sn.display_name,
                            "vcn": vcn_names.get(v.id, ""),
                            "ad": sn.availability_domain,
                            "public": not sn.prohibit_public_ip_on_vnic})
        return out


# ---------- 开机 ----------

QUICK_PRESETS = {
    "amd_micro": {"label": "AMD 微型 1C/1G", "shape": "VM.Standard.E2.1.Micro",
                  "ocpus": 1, "memory_gb": 1, "os_hint": "Ubuntu"},
    "arm_a1": {"label": "ARM A1 2C/12G", "shape": "VM.Standard.A1.Flex",
               "ocpus": 2, "memory_gb": 12, "os_hint": "Ubuntu"},
    "arm_a1_max": {"label": "ARM A1 4C/24G(满额)", "shape": "VM.Standard.A1.Flex",
                   "ocpus": 4, "memory_gb": 24, "os_hint": "Ubuntu"},
}


def launch_instance(account_id: int, params: dict) -> dict:
    """
    params: compartment_id, ad, shape, ocpus, memory_gb, image_id,
            subnet_id, ssh_key, boot_gb, display_name, assign_public_ip,
            user_data(明文脚本), hostname_label, boot_volume_id(从启动盘开机)
    成功返回 {"instance_id": ...}; 缺货抛 CapacityError; 配额抛 QuotaError。
    """
    if not params.get("ssh_key"):
        raise OCIError("SSH 公钥和 root 密码至少要给一个(当前实现要求 SSH 公钥)")

    metadata = {"ssh_authorized_keys": params["ssh_key"].strip()}
    if params.get("user_data"):
        metadata["user_data"] = base64.b64encode(
            params["user_data"].encode()).decode()

    if params.get("boot_volume_id"):
        source = oci.core.models.InstanceSourceViaBootVolumeDetails(
            boot_volume_id=params["boot_volume_id"])
    else:
        source = oci.core.models.InstanceSourceViaImageDetails(
            image_id=params["image_id"],
            boot_volume_size_in_gbs=int(params.get("boot_gb") or 0) or None)
    shape = (params.get("shape") or "").strip()
    if not shape:
        raise OCIError("形状(Shape)不能为空, 例如 VM.Standard.A1.Flex")
    # Flex 机型无论从镜像还是从启动盘开机, 都必须带 shape_config
    shape_cfg = None
    if "Flex" in shape:
        shape_cfg = oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=float(params.get("ocpus") or 1),
            memory_in_gbs=float(params.get("memory_gb") or 1))

    vnic = oci.core.models.CreateVnicDetails(
        subnet_id=params["subnet_id"],
        assign_public_ip=bool(params.get("assign_public_ip", True)),
        display_name=params.get("display_name") or "vnic",
        hostname_label=(params.get("hostname_label") or "").strip() or None,
    )
    with oci_clients(account_id) as c:
        comp = (params.get("compartment_id") or "").strip() or c.tenancy
        details = oci.core.models.LaunchInstanceDetails(
            compartment_id=comp,
            availability_domain=params["ad"],
            shape=shape,
            shape_config=shape_cfg,
            display_name=params.get("display_name") or f"oci-{int(time.time())}",
            source_details=source,
            create_vnic_details=vnic,
            metadata=metadata,
        )
        resp = c.compute.launch_instance(details)
        inst = resp.data
        return {"instance_id": inst.id, "name": inst.display_name}


def wait_for_ip(account_id: int, instance_id: str, timeout: int = 300) -> dict:
    """等实例 RUNNING 并拿到公网 IP, 抢机成功通知用。"""
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        try:
            last = get_instance(account_id, instance_id)
        except OCIError:
            time.sleep(5)
            continue
        if last.get("state") == "RUNNING" and last.get("public_ip"):
            return last
        time.sleep(10)
    return last


# ---------- 电源 / 生命周期 ----------

def instance_action(account_id: int, instance_id: str, action: str) -> dict:
    """action: START | STOP | SOFTSTOP | RESET | SOFTRESET"""
    action = action.upper()
    if action not in ("START", "STOP", "SOFTSTOP", "RESET", "SOFTRESET"):
        raise OCIError(f"未知动作: {action}")
    with oci_clients(account_id) as c:
        c.compute.instance_action(instance_id, action)
        return {"ok": True, "action": action}


def terminate_instance(account_id: int, instance_id: str,
                       preserve_boot_volume: bool = False) -> dict:
    with oci_clients(account_id) as c:
        c.compute.terminate_instance(instance_id,
                                     preserve_boot_volume=preserve_boot_volume)
        return {"ok": True}


def update_instance(account_id: int, instance_id: str, display_name: str | None = None,
                    ocpus: float | None = None, memory_gb: float | None = None) -> dict:
    """改名 / 弹性升降配(实例需先停止)。"""
    with oci_clients(account_id) as c:
        inst = c.compute.get_instance(instance_id).data
        if (ocpus or memory_gb) and inst.lifecycle_state != "STOPPED":
            raise OCIError("升降配前请先停止实例")
        shape_cfg = None
        if ocpus or memory_gb:
            shape_cfg = oci.core.models.UpdateInstanceShapeConfigDetails(
                ocpus=float(ocpus) if ocpus else None,
                memory_in_gbs=float(memory_gb) if memory_gb else None)
        details = oci.core.models.UpdateInstanceDetails(
            display_name=display_name or None, shape_config=shape_cfg)
        c.compute.update_instance(instance_id, details)
        return {"ok": True}


# ---------- A1 免费额度体检 ----------

FREE_A1_OCPU = 4
FREE_A1_MEM = 24


def a1_audit(account_id: int, compartment_id: str | None = None) -> dict:
    """批量检查 A1 实例是否超出免费额度(4C/24G), 返回可一键降配的清单。"""
    with oci_clients(account_id) as c:
        comp = compartment_id or c.tenancy
    instances = [i for i in list_instances(account_id, comp)
                 if i["shape"] == "VM.Standard.A1.Flex"
                 and i["state"] not in ("TERMINATED", "TERMINATING")]
    total_ocpu = sum(i["ocpus"] or 0 for i in instances)
    total_mem = sum(i["memory_gb"] or 0 for i in instances)
    over = total_ocpu > FREE_A1_OCPU or total_mem > FREE_A1_MEM
    return {"instances": instances, "total_ocpu": total_ocpu,
            "total_mem": total_mem, "over_quota": over,
            "free_ocpu": FREE_A1_OCPU, "free_mem": FREE_A1_MEM}
