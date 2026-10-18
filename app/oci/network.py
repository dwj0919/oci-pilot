"""网络: 换公网 IP / 保留 IP / IPv6 / 安全组开端口。"""
import oci

from .base import oci_clients, OCIError


def _norm_vnic(v) -> dict:
    return {"id": v.id, "public_ip": v.public_ip, "private_ip": v.private_ip,
            "private_ip_id": getattr(v, "private_ip_id", None),
            "ipv6": getattr(v, "ipv6_addresses", []) or [],
            "mac": v.mac_address, "subnet_id": v.subnet_id}


def get_vnic(account_id: int, vnic_id: str) -> dict:
    with oci_clients(account_id) as c:
        v = c.network.get_vnic(vnic_id).data
        d = _norm_vnic(v)
        # IPv6 删除需要 OCID, 这里把地址和 OCID 一起返回
        try:
            ip6s = oci.pagination.list_call_get_all_results(
                c.network.list_ipv6s, compartment_id=v.compartment_id,
                vnic_id=vnic_id).data
            d["ipv6"] = [{"id": x.id, "ip": x.ip_address} for x in ip6s
                         if x.lifecycle_state == "AVAILABLE"]
        except OCIError:
            pass
        return d


def change_public_ip(account_id: int, vnic_id: str) -> dict:
    """换 IP: 建一个新的临时公网 IP 绑定到同一内网 IP, 再删旧的。"""
    with oci_clients(account_id) as c:
        v = c.network.get_vnic(vnic_id).data
        priv_id = getattr(v, "private_ip_id", None)
        if not priv_id:
            raise OCIError("该 VNIC 没有内网 IP, 无法换 IP")
        old_ips = oci.pagination.list_call_get_all_results(
            c.network.list_public_ips, "REGION", c.tenancy,
            private_ip_id=priv_id).data
        new_ip = c.network.create_public_ip(
            oci.core.models.CreatePublicIpDetails(
                compartment_id=v.compartment_id,
                lifetime="EPHEMERAL",
                private_ip_id=priv_id)).data
        for o in old_ips:
            if o.id != new_ip.id and o.lifetime == "EPHEMERAL":
                try:
                    c.network.delete_public_ip(o.id)
                except OCIError:
                    pass
        return {"new_ip": new_ip.ip_address, "id": new_ip.id}


def list_public_ips(account_id: int, compartment_id: str | None = None) -> list[dict]:
    with oci_clients(account_id) as c:
        comp = compartment_id or c.tenancy
        ips = oci.pagination.list_call_get_all_results(
            c.network.list_public_ips, "REGION", comp).data
        return [{"id": p.id, "ip": p.ip_address, "lifetime": p.lifetime,
                 "state": p.lifecycle_state,
                 "assigned": bool(p.private_ip_id)} for p in ips]


def create_reserved_ip(account_id: int, compartment_id: str | None = None,
                       name: str = "") -> dict:
    with oci_clients(account_id) as c:
        comp = compartment_id or c.tenancy
        p = c.network.create_public_ip(
            oci.core.models.CreatePublicIpDetails(
                compartment_id=comp, lifetime="RESERVED",
                display_name=name or None)).data
        return {"id": p.id, "ip": p.ip_address}


def assign_public_ip(account_id: int, public_ip_id: str, private_ip_id: str) -> dict:
    with oci_clients(account_id) as c:
        c.network.update_public_ip(
            public_ip_id,
            oci.core.models.UpdatePublicIpDetails(private_ip_id=private_ip_id))
        return {"ok": True}


def unassign_public_ip(account_id: int, public_ip_id: str) -> dict:
    return assign_public_ip(account_id, public_ip_id, "")


def delete_public_ip(account_id: int, public_ip_id: str) -> dict:
    with oci_clients(account_id) as c:
        c.network.delete_public_ip(public_ip_id)
        return {"ok": True}


def attach_ipv6(account_id: int, vnic_id: str) -> dict:
    with oci_clients(account_id) as c:
        ip6 = c.network.create_ipv6(
            oci.core.models.CreateIpv6Details(vnic_id=vnic_id)).data
        return {"id": ip6.id, "ip": ip6.ip_address}


def detach_ipv6(account_id: int, ipv6_id: str) -> dict:
    with oci_clients(account_id) as c:
        c.network.delete_ipv6(ipv6_id)
        return {"ok": True}


def list_vcns(account_id: int, compartment_id: str | None = None) -> list[dict]:
    with oci_clients(account_id) as c:
        comp = compartment_id or c.tenancy
        vcns = oci.pagination.list_call_get_all_results(
            c.network.list_vcns, comp).data
        return [{"id": v.id, "name": v.display_name, "cidr": v.cidr_block} for v in vcns]


def list_security_lists(account_id: int, compartment_id: str | None = None,
                        vcn_id: str | None = None) -> list[dict]:
    with oci_clients(account_id) as c:
        comp = compartment_id or c.tenancy
        kw = {"vcn_id": vcn_id} if vcn_id else {}
        sls = oci.pagination.list_call_get_all_results(
            c.network.list_security_lists, comp, **kw).data
        return [{"id": s.id, "name": s.display_name} for s in sls]


def open_security_port(account_id: int, security_list_id: str, port: int,
                       protocol: str = "tcp", source: str = "0.0.0.0/0") -> dict:
    """一键开放安全组端口。protocol: tcp | udp | icmp。"""
    proto_num = {"tcp": "6", "udp": "17", "icmp": "1"}.get(protocol.lower(), "6")
    with oci_clients(account_id) as c:
        sl = c.network.get_security_list(security_list_id).data
        rules = list(sl.ingress_security_rules or [])
        for r in rules:
            if getattr(r, "protocol", "") != proto_num:
                continue
            if proto_num == "1":
                return {"ok": True, "note": "ICMP 规则已存在"}
            opts = getattr(r, "tcp_options", None) or getattr(r, "udp_options", None)
            pr = getattr(opts, "destination_port_range", None) if opts else None
            if pr and pr.min == port and pr.max == port and r.source == source:
                return {"ok": True, "note": "规则已存在"}
        if proto_num == "1":
            opt_kw = {"icmp_options": oci.core.models.IcmpOptions(type=8)}
        elif proto_num == "6":
            opt_kw = {"tcp_options": oci.core.models.TcpOptions(
                destination_port_range=oci.core.models.PortRange(
                    min=port, max=port))}
        else:
            opt_kw = {"udp_options": oci.core.models.UdpOptions(
                destination_port_range=oci.core.models.PortRange(
                    min=port, max=port))}
        rules.append(oci.core.models.IngressSecurityRule(
            protocol=proto_num, source=source, source_type="CIDR_BLOCK",
            **opt_kw))
        c.network.update_security_list(
            security_list_id,
            oci.core.models.UpdateSecurityListDetails(
                ingress_security_rules=rules))
        return {"ok": True}
