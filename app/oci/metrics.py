"""监控指标: VNIC 出/入流量(流量守护用)。"""
from datetime import datetime, timezone, timedelta

import oci

from .base import oci_clients


def _summarize(account_id: int, compartment_id: str, vnic_id: str,
               metric: str, start: datetime, end: datetime) -> float:
    with oci_clients(account_id) as c:
        details = oci.monitoring.models.SummarizeMetricsDataDetails(
            namespace="oci_vcn",
            query=f'{metric}[1d]{{resourceId="{vnic_id}"}}.sum()',
            start_time=start, end_time=end)
        items = oci.pagination.list_call_get_all_results(
            c.monitoring.summarize_metrics_data,
            compartment_id, details).data
        total = 0.0
        for it in items:
            for dp in (it.aggregated_datapoints or []):
                total += dp.value or 0
        return total


def vnic_traffic(account_id: int, compartment_id: str, vnic_id: str,
                 days: int = 30) -> dict:
    """近 N 天流量, 返回 GB。"""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    egress = _summarize(account_id, compartment_id, vnic_id,
                        "VnicEgressBytes", start, end)
    ingress = _summarize(account_id, compartment_id, vnic_id,
                         "VnicIngressBytes", start, end)
    return {"egress_gb": round(egress / 1e9, 2),
            "ingress_gb": round(ingress / 1e9, 2), "days": days}


def month_egress_gb(account_id: int, compartment_id: str, vnic_id: str) -> float:
    """本月 1 号至今的流出流量(GB), 免费额度 10TB/月对照用。"""
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = _summarize(account_id, compartment_id, vnic_id,
                       "VnicEgressBytes", start, now)
    return round(total / 1e9, 2)
