"""Network validation shared by local components."""

from __future__ import annotations

import ipaddress


def is_loopback_host(host: str) -> bool:
    """Accept only names and addresses that represent the local host."""
    normalized = host.strip().lower()
    if normalized == "localhost":
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped.is_loopback
    return address.is_loopback


def format_url_host(host: str) -> str:
    """Format IPv6 addresses with the brackets required by HTTP URLs."""
    normalized = host.strip()
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return normalized
    if isinstance(address, ipaddress.IPv6Address):
        return f"[{normalized}]"
    return normalized
