"""BUG-443: the production compose override must not publish internal services on the host."""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
# The only services meant to be reachable from outside the compose network.
_PUBLIC = {"api_gateway", "frontend"}


def _service_blocks(text: str) -> dict[str, str]:
    services = text.split("\nservices:\n", 1)[1]
    services = re.split(r"\n(?=\S)", services, maxsplit=1)[0]  # stop at the next top-level key
    blocks: dict[str, str] = {}
    name = None
    for line in services.splitlines():
        header = re.match(r"^  ([a-z_]+):\s*$", line)
        if header:
            name = header.group(1)
            blocks[name] = ""
        elif name:
            blocks[name] += line + "\n"
    return blocks


def test_every_internal_service_has_its_dev_port_mapping_removed():
    base = _service_blocks((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    prod = _service_blocks((_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))

    published = {name for name, body in base.items() if re.search(r"^    ports:", body, re.M)}
    assert published - _PUBLIC, "expected the base file to publish internal services for local dev"

    still_published = sorted(
        name for name in published - _PUBLIC
        if "    ports: !reset []" not in prod.get(name, "")
    )
    assert still_published == []
