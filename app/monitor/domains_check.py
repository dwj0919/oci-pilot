"""域名到期 + SSL 证书到期检查。"""
import socket
import ssl
from datetime import datetime, date


def get_domain_expiry(domain: str) -> date | None:
    """whois 查域名到期日, 查不到返回 None。"""
    try:
        import whois
        w = whois.whois(domain)
        exp = w.expiration_date
        if isinstance(exp, list):
            exp = exp[0]
        if isinstance(exp, datetime):
            return exp.date()
        return None
    except Exception:  # noqa: BLE001
        return None


def get_ssl_expiry(domain: str, port: int = 443) -> date | None:
    """连 443 拿证书 notAfter。"""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((domain, port), timeout=15) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert()
        not_after = cert.get("notAfter")
        if not_after:
            dt = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
            return dt.date()
        return None
    except Exception:  # noqa: BLE001
        return None


def days_left(expiry: date | None) -> int | None:
    if not expiry:
        return None
    return (expiry - date.today()).days
