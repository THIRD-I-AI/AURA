"""BUG-310: is_public_url listed what an address must not be, so address space the
list did not name -- carrier-grade NAT 100.64.0.0/10, IPv6 site-local -- counted as
public and could be reached through webhooks and streaming sources."""
from __future__ import annotations

import pytest

from shared.ssrf import is_public_url


@pytest.mark.parametrize("url", [
    "http://100.100.100.200/latest/meta-data/",   # a cloud metadata address in CGNAT space
    "http://100.64.0.1:8080/",
    "http://100.127.255.254/",
    "http://[fec0::1]/",                           # IPv6 site-local
    "http://[::ffff:127.0.0.1]/",                  # IPv4-mapped loopback
    "http://[::ffff:10.0.0.5]/",
    "http://[::ffff:169.254.169.254]/",
    "http://192.0.2.10/",                          # documentation range
    "http://198.18.0.1/",                          # benchmarking range
    "http://[fc00::1]/",                           # unique local
    "http://[2001:db8::1]/",                       # documentation
])
def test_non_global_addresses_are_not_public(url):
    assert is_public_url(url) is False


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/", "http://127.0.0.1/", "http://10.0.0.5/", "http://192.168.1.1/",
    "http://0.0.0.0/", "http://[::1]/", "http://224.0.0.1/", "http://[ff02::1]/",
])
def test_addresses_refused_before_are_still_refused(url):
    assert is_public_url(url) is False


@pytest.mark.parametrize("url", [
    "https://8.8.8.8/hook", "http://93.184.216.34/", "https://[2606:4700:4700::1111]/",
])
def test_global_addresses_are_public(url):
    assert is_public_url(url) is True
