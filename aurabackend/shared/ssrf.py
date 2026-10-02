"""The one check for "may the backend connect to this caller-supplied URL".

Moved here from api_gateway/routers/webhooks.py (BUG-054) so the streaming webhook
sink and websocket source can use it too (BUG-287): they connected to whatever URL a
pipeline definition named, which made the gateway a proxy into its own network.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Sequence
from urllib.parse import urlparse


def is_public_url(url: str, schemes: Sequence[str] = ("http", "https")) -> bool:
    """False for a URL whose host is, or resolves to, a loopback, link-local, private,
    reserved, multicast or unspecified address -- or does not resolve at all.

    Resolves DNS, so call it through ``asyncio.to_thread`` from async code. It is a
    check at the time of the call: a caller that connects later, or repeatedly, must
    re-check before each connection.
    """
    parsed = urlparse(url)
    if parsed.scheme not in schemes:
        return False
    host = parsed.hostname
    if not host:
        return False

    try:
        candidates = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return False
        candidates = [ipaddress.ip_address(info[4][0]) for info in infos]

    return all(_is_public_address(ip) for ip in candidates)


def _is_public_address(ip) -> bool:
    """True only for a globally routable unicast address.

    BUG-310: the check used to be a list of things an address must not be (private,
    loopback, link-local, reserved, multicast, unspecified). Whatever that list did
    not name counted as public: carrier-grade NAT space 100.64.0.0/10 (pod networks,
    VPN peers, one cloud's metadata address) and IPv6 site-local fec0::/10. Asking
    whether the address is global turns it into an allow-rule.
    """
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:  # ::ffff:a.b.c.d reaches the IPv4 address
        ip = mapped
    # Python still reports the deprecated IPv6 site-local range as global.
    return ip.is_global and not ip.is_multicast and not getattr(ip, "is_site_local", False)


async def is_public_url_async(url: str, schemes: Sequence[str] = ("http", "https")) -> bool:
    return await asyncio.to_thread(is_public_url, url, schemes)
