from dataclasses import dataclass
from ipaddress import ip_address

from fastapi import Request

from app.core.config import get_settings


@dataclass(frozen=True, slots=True)
class RequestSecurityContext:
    ip_address: str | None
    user_agent: str | None


def request_security_context(request: Request) -> RequestSecurityContext:
    client = request.client
    user_agent = request.headers.get("user-agent")
    client_ip = client.host if client is not None else None
    trusted_proxies = set(get_settings().trusted_proxy_ips)
    if client_ip in trusted_proxies:
        forwarded_for = request.headers.get("x-forwarded-for")
        candidate = (
            forwarded_for.split(",", 1)[0].strip()
            if forwarded_for
            else request.headers.get("x-real-ip")
        )
        if candidate:
            try:
                client_ip = str(ip_address(candidate))
            except ValueError:
                pass
    return RequestSecurityContext(
        ip_address=client_ip,
        user_agent=user_agent[:512] if user_agent else None,
    )
