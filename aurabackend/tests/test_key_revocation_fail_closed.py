"""BUG-209: an unreadable revocation file must not silently un-revoke the signing key."""
import json

import pytest

pytest.importorskip("econml", reason="counterfactual_service package imports need the causal stack")

from counterfactual_service import cryptography  # noqa: E402


@pytest.fixture
def key_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_SIGNING_KEY_DIR", str(tmp_path))
    return tmp_path


def test_missing_file_means_not_revoked(key_dir):
    assert cryptography.is_revoked() is False


def test_revoke_then_read_back(key_dir):
    cryptography.soft_revoke_key()
    assert cryptography.is_revoked() is True
    assert json.loads((key_dir / "revoked_kids.json").read_text()) == ["aura-ed25519"]
    assert not (key_dir / "revoked_kids.json.tmp").exists()


@pytest.mark.parametrize("content", ['{"truncated": ', "", "not json", '{"a": 1}'])
def test_corrupt_file_fails_closed(key_dir, content):
    (key_dir / "revoked_kids.json").write_text(content, encoding="utf-8")
    assert cryptography.is_revoked() is True
    assert cryptography.get_jwks is not None


def test_unreadable_path_fails_closed(key_dir):
    # a directory where the file should be -> OSError on read (not "missing")
    (key_dir / "revoked_kids.json").mkdir()
    assert cryptography.is_revoked() is True


def test_revoking_over_a_corrupt_file_repairs_it(key_dir):
    (key_dir / "revoked_kids.json").write_text("{corrupt", encoding="utf-8")
    cryptography.soft_revoke_key("other-kid")
    assert set(json.loads((key_dir / "revoked_kids.json").read_text())) == {"other-kid"}


def test_other_kids_are_not_reported_revoked_by_a_valid_file(key_dir):
    cryptography.soft_revoke_key("k1")
    assert cryptography.is_revoked("k1") is True
    assert cryptography.is_revoked("k2") is False
