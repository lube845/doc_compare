"""Guard against calls to addresses that must not be reachable.

The OCR channel may be any HTTP service - including one that runs in the
same network.  By default only public addresses are allowed, so a
(mistaken or malicious) configuration cannot make the server read the cloud
metadata of the host or a management port.  A deployment that talks to an
internal service sets ``DOC_COMPARE_OCR_ALLOW_PRIVATE_ENDPOINTS=1``.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from ..config import settings


class BlockedEndpoint(Exception):
    """The address must not be called."""


def _is_blocked_address(address: str) -> bool:
    """True for the addresses that are not reachable from the outside."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_endpoint(url: str, resolve: bool = True) -> None:
    """Raise :class:`BlockedEndpoint` when the URL must not be called.

    ``resolve`` is set to ``False`` when the caller provides its own
    transport (the tests): the address will not be resolved by this process,
    so there is nothing to check.
    """
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https"):
        raise BlockedEndpoint("接口地址必须以 http:// 或 https:// 开头")
    host = parts.hostname
    if not host:
        raise BlockedEndpoint("接口地址缺少主机名")
    if not resolve or settings.ocr_allow_private_endpoints:
        return

    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError):
        # The name cannot be resolved - the request would fail anyway.
        return
    for info in infos:
        address = info[4][0]
        if _is_blocked_address(address):
            raise BlockedEndpoint(
                f"接口地址指向内网地址（{address}）。如需访问内网自建服务，"
                "请设置 DOC_COMPARE_OCR_ALLOW_PRIVATE_ENDPOINTS=1"
            )
