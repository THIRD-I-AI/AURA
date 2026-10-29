"""The one check for "may the backend connect to this caller-supplied URL".

Moved here from api_gateway/routers/webhooks.py (BUG-054) so the streaming webhook
sink and websocket source can use it too (BUG-287): they connected to whatever URL a
pipeline definition named, which made the gateway a proxy into its own network.
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from typing import Optional, Sequence
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


def _pin_public_address(name: str) -> Optional[str]:
    try:
        candidates = [ipaddress.ip_address(name)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(name, None)
        except (socket.gaierror, UnicodeError):
            return None
        candidates = [ipaddress.ip_address(info[4][0]) for info in infos]
    if candidates and all(_is_public_address(ip) for ip in candidates):
        return str(candidates[0])
    return None


async def resolve_allowed_db_host(host: object) -> Optional[str]:
    """The address the backend may open a database connection to for this
    caller-supplied host, or None if it may not.

    The one rule for every place that connects to a caller-named database (connector
    routes, /execute/query, pipeline sources and sinks, the streaming database sink):
    BUG-367, 377 and 404 were the same gap found in three places because each route
    carried its own copy, or none. None for an empty host (drivers turn that into
    the gateway's own localhost) and for a host with any non-public address. If the
    operator has set AURA_CONNECTORS_ALLOW_PRIVATE_HOSTS for a private-network
    deployment, the host is returned as given.

    BUG-547: a host name comes back as the IP address that was checked. A caller
    that connects must connect to that address: handing the driver the name again
    lets it resolve a second time, and a rebinding DNS server answers that second
    lookup with a loopback or private address.
    """
    name = str(host or "").strip()
    if os.getenv("AURA_CONNECTORS_ALLOW_PRIVATE_HOSTS", "").strip().lower() in ("1", "true", "yes"):
        return name
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    if not name:
        return None
    return await asyncio.to_thread(_pin_public_address, name)


async def is_allowed_db_host(host: object) -> bool:
    """May the backend open a database connection to this caller-supplied host?
    See ``resolve_allowed_db_host``, whose pinned address a caller should prefer."""
    return await resolve_allowed_db_host(host) is not None
