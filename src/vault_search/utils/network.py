"""Validações de rede compartilhadas pelos componentes locais."""

from __future__ import annotations

import ipaddress


def is_loopback_host(host: str) -> bool:
    """Aceita apenas nomes e endereços que representam o host local."""
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
    """Formata endereços IPv6 com os colchetes exigidos por URLs HTTP."""
    normalized = host.strip()
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return normalized
    if isinstance(address, ipaddress.IPv6Address):
        return f"[{normalized}]"
    return normalized
