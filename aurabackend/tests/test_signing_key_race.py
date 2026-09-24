"""BUG-160: the persisted ED25519 signing key must be created exactly once,
even when several PROCESSES race the first use.

signing._resolve_key_pair()'s threading.Lock only serialises callers inside one
process. Two replicas (or workers) hitting a cold key directory could each see
"no key yet", each generate one, and each write signing_ed25519.pem -- last
writer wins on disk, so anything the losing process signed can never verify
again. The write was also non-atomic (truncate-then-write), so a reader could
observe a half-written PEM and fail to parse it -- which in production raised,
and elsewhere silently signed with an ephemeral key until the next restart.

These tests use real OS processes: the existing thread-based test in
test_counterfactual_sprint9.py cannot see this class of bug.
"""
import os
import subprocess
import sys
import textwrap
import time

import pytest

pytest.importorskip("cryptography")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Each child waits for a shared wall-clock instant so they enter the resolver
# together, and generate() is slowed so the check-then-write window is wide.
DRIVER = textwrap.dedent(
    """
    import sys, time
    sys.path.insert(0, {root!r})
    start_at = float(sys.argv[1])
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    import counterfactual_service.signing as s

    real = ed25519.Ed25519PrivateKey.generate
    def slow():
        time.sleep(0.3)
        return real()
    ed25519.Ed25519PrivateKey.generate = staticmethod(slow)

    while time.time() < start_at:
        pass
    pair = s._resolve_key_pair()
    pub = pair[1].public_bytes(encoding=serialization.Encoding.Raw,
                               format=serialization.PublicFormat.Raw).hex()
    print(pub, s._KEY_SOURCE)
    """
).format(root=ROOT)


def _env(key_dir):
    env = {k: v for k, v in os.environ.items()
           if k not in ("AURA_SIGNING_PRIVATE_KEY_HEX", "AURA_SIGNING_PRIVATE_KEY_PATH")}
    env["AURA_SIGNING_KEY_DIR"] = str(key_dir)
    env["PYTHONPATH"] = ROOT
    return env


def _pub_of_key_file(path):
    from cryptography.hazmat.primitives import serialization

    sk = serialization.load_pem_private_key(path.read_bytes(), password=None)
    return sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    ).hex()


def test_concurrent_first_use_across_processes_agree_on_one_key(tmp_path):
    n = 6
    start_at = time.time() + 6.0  # leaves time for every child to finish importing
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", DRIVER, str(start_at)],
            env=_env(tmp_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for _ in range(n)
    ]
    outs = []
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err
        # Imports/loggers may print above it; the driver's result is the last line.
        outs.append(out.strip().splitlines()[-1].split())

    pubs = {pub for pub, _src in outs}
    assert {src for _pub, src in outs} == {"persisted_file"}
    on_disk = _pub_of_key_file(tmp_path / "signing_ed25519.pem")
    assert pubs == {on_disk}, (
        f"{len(pubs)} different signing keys were in use across {n} processes; "
        "the losers' signatures can never verify against the persisted key"
    )
    assert not [p for p in os.listdir(tmp_path) if p.endswith(".tmp")], "temp key file leaked"


def test_an_unparseable_existing_key_file_is_never_overwritten(monkeypatch, tmp_path):
    """Guard for the new creation path: a key file that exists but cannot be
    parsed must be left exactly as found and reported, never replaced -- replacing
    it could destroy the key another process just published. (The old code also
    left it alone, degrading to an ephemeral key; this pins that the fix keeps
    that property.)"""
    import counterfactual_service.signing as s

    monkeypatch.setattr(s, "_KEY_PAIR", None)
    monkeypatch.setattr(s, "_KEY_SOURCE", "uninitialized")
    monkeypatch.delenv("AURA_SIGNING_PRIVATE_KEY_HEX", raising=False)
    monkeypatch.delenv("AURA_SIGNING_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.setenv("AURA_SIGNING_KEY_DIR", str(tmp_path))

    key_file = tmp_path / "signing_ed25519.pem"
    partial = b"-----BEGIN PRIVATE KEY-----\nMC4CAQAwBQYDK2Vw"  # cut off mid-write
    key_file.write_bytes(partial)

    pair = s._resolve_key_pair()

    assert pair is not None
    assert key_file.read_bytes() == partial, "the existing key file was overwritten"
    assert s._KEY_SOURCE == "ephemeral"


def test_loser_of_the_creation_race_adopts_the_winners_key(monkeypatch, tmp_path):
    """Deterministic single-process version of the race: another process
    publishes its key between our exists() check and our write. We must adopt
    that key, not replace it."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    import counterfactual_service.signing as s

    monkeypatch.setattr(s, "_KEY_PAIR", None)
    monkeypatch.setattr(s, "_KEY_SOURCE", "uninitialized")
    monkeypatch.delenv("AURA_SIGNING_PRIVATE_KEY_HEX", raising=False)
    monkeypatch.delenv("AURA_SIGNING_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.setenv("AURA_SIGNING_KEY_DIR", str(tmp_path))

    winner = ed25519.Ed25519PrivateKey.generate()
    winner_pem = winner.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_file = tmp_path / "signing_ed25519.pem"
    real_generate = ed25519.Ed25519PrivateKey.generate

    def generate_then_lose_the_race():
        loser = real_generate()
        key_file.write_bytes(winner_pem)  # the other process publishes first
        return loser

    monkeypatch.setattr(ed25519.Ed25519PrivateKey, "generate", staticmethod(generate_then_lose_the_race))

    pair = s._resolve_key_pair()

    raw = lambda pub: pub.public_bytes(  # noqa: E731
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    assert raw(pair[1]) == raw(winner.public_key()), "adopted our own key instead of the winner's"
    assert key_file.read_bytes() == winner_pem, "the winner's key file was replaced"
    assert s._KEY_SOURCE == "persisted_file"
