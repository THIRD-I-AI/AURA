"""BUG-378: webhook subscriptions and inbound hooks were stored under the package
directory (inside the container image, not on the /data volume), so every redeploy
deleted them. AURA_WEBHOOK_DIR moves them; the deploy compose files set it."""
from __future__ import annotations

import importlib
import os
from pathlib import Path


def _reloaded(module_name: str, monkeypatch, tmp_path):
    monkeypatch.setenv("AURA_WEBHOOK_DIR", str(tmp_path / "hooks"))
    mod = importlib.import_module(module_name)
    try:
        return importlib.reload(mod)
    finally:
        monkeypatch.delenv("AURA_WEBHOOK_DIR")


def test_both_stores_follow_aura_webhook_dir(monkeypatch, tmp_path):
    for name in ("shared.webhook_dispatcher", "shared.inbound_hooks"):
        mod = _reloaded(name, monkeypatch, tmp_path)
        assert Path(mod._STORE_PATH).parent == tmp_path / "hooks", name
    for name in ("shared.webhook_dispatcher", "shared.inbound_hooks"):
        importlib.reload(importlib.import_module(name))  # back to the default for other tests


def test_the_deploy_compose_files_put_the_store_on_the_volume():
    root = Path(__file__).resolve().parents[2]
    free_tier = (root / "deploy" / "aws-free-tier" / "docker-compose.yml").read_text(encoding="utf-8")
    prod = (root / "docker-compose.prod.yml").read_text(encoding="utf-8")
    assert "AURA_WEBHOOK_DIR: /data/webhooks" in free_tier
    assert "AURA_WEBHOOK_DIR: /data/webhooks" in prod and "aura-webhooks:/data/webhooks" in prod



def test_a_failed_store_write_keeps_the_previous_subscriptions(monkeypatch, tmp_path):
    # BUG-384: the store was rewritten in place (open "w" truncates first) by several
    # worker threads at once with no lock, so a write that failed or raced part-way
    # left a truncated file -- and every subscription was gone on the next load. It is
    # now written to a temp file and swapped in, under a lock.
    from shared import webhook_dispatcher as wd

    monkeypatch.setattr(wd, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(wd, "_STORE_PATH", str(tmp_path / "subscriptions.json"))
    disp = wd.WebhookDispatcher()
    disp.register("ws", "https://example.com/a", ["*"])

    def _boom(obj, fp, **kw):
        fp.write("[{")
        raise OSError("disk full")

    monkeypatch.setattr(wd.json, "dump", _boom)
    disp.register("ws", "https://example.com/b", ["*"])
    monkeypatch.undo()
    monkeypatch.setattr(wd, "_STORE_PATH", str(tmp_path / "subscriptions.json"))

    reloaded = wd.WebhookDispatcher()
    assert [s.url for s in reloaded.list("ws")] == ["https://example.com/a"]
