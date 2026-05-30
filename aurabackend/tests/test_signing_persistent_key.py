"""S31b — persistent ED25519 signing key (4th source)."""
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("cryptography")


def _fresh_signing(monkeypatch, key_dir):
    monkeypatch.delenv("AURA_SIGNING_PRIVATE_KEY_HEX", raising=False)
    monkeypatch.delenv("AURA_SIGNING_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.setenv("AURA_SIGNING_KEY_DIR", str(key_dir))
    import counterfactual_service.signing as s
    importlib.reload(s)
    return s


def test_generates_and_persists_then_reloads_same_key(monkeypatch, tmp_path):
    s = _fresh_signing(monkeypatch, tmp_path)
    pem1 = s.public_key_pem()
    assert s.signing_key_source() == "persisted_file"
    assert (tmp_path / "signing_ed25519.pem").exists()
    # Reload from scratch — must read the same persisted key.
    s2 = _fresh_signing(monkeypatch, tmp_path)
    assert s2.public_key_pem() == pem1
    assert s2.signing_key_source() == "persisted_file"


def test_falls_back_to_ephemeral_when_dir_unwritable(monkeypatch, tmp_path):
    bad = tmp_path / "nope.pem"
    bad.write_text("not-a-dir-parent")  # key dir path is actually a file
    s = _fresh_signing(monkeypatch, bad)
    assert s.public_key_pem() is not None
    assert s.signing_key_source() == "ephemeral"
