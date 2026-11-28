"""BUG-593: a fresh box could neither start AURA nor restore a backup. compose mounts the
aura-data volume at /data, the image had no /data, so Docker created the volume root-owned and
the `aura` user (uid 1000) could not create /data/state. Production only worked because its
volume had been chowned by hand. Found by the 2026-11-26 restore drill."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_the_image_owns_the_volume_mount_point():
    dockerfile = (ROOT / "aurabackend/Dockerfile").read_text(encoding="utf-8")
    base_os = dockerfile.split("AS base-os", 1)[1].split("FROM ", 1)[0]
    assert re.search(r"mkdir -p /data\b", base_os), "base-os must create /data"
    assert re.search(r"chown -R aura:aura [^\n]*/data\b", base_os), "/data must be owned by aura"
    # every runtime stage builds on base-os, so all three images carry it
    for stage in ("base-runtime", "causal-runtime", "streaming-runtime"):
        assert f"FROM base-os AS {stage}" in dockerfile


def _restore_block() -> str:
    script = (ROOT / "deploy/aws-free-tier/backup.sh").read_text(encoding="utf-8")
    return script.split('if [ "${1:-}" = "--restore" ]; then', 1)[1].split("\nfi\n", 1)[0]


def test_restore_can_write_a_root_owned_volume_and_hands_it_back_to_the_app_user():
    block = _restore_block()
    assert "--user root" in block
    assert "chown -R aura:aura /data" in block


def test_restore_never_swallows_a_failed_copy():
    block = _restore_block()
    assert "2>/dev/null || true" not in block
    assert "size mismatch" in block
