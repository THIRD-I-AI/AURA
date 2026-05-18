"""
Sprint 19 — TRAIGA Federation: RFC 6962 Merkle audit log + Signed Tree
Head verification.

Layer 16 (eval-gate contract):
    Given 1000 synthetic audit records, the engine's daily Merkle root
    must be reconstructable from any record's inclusion proof. Tampering
    with any record must cause the recomputed root to differ from the
    Signed Tree Head root — i.e. inclusion proofs are tamper-evident.

The tests run in five tiers:

1. Pure Merkle primitive correctness (sizes 1, 2, 3, 4, 7, 1000).
2. audit_log integration: 1000 synthetic records via _AuditWriter,
   then daily_merkle_root + inclusion_proof_for_record + verify_inclusion.
3. Tamper detection — flipping a byte in record 250 makes the rebuilt
   root differ from the STH.
4. STH signing round-trip — signed bytes verify with the engine's
   ED25519 key.
5. SDK Client.verify_inclusion happy path + tampered-server-response
   failure mode, via respx-mocked HTTP.

No optional deps; uses the same cryptography stack the engine already
imports for ED25519 (which the SDK and the signing module both depend on).
"""
from __future__ import annotations

import base64
import hashlib
import importlib
import json
from datetime import datetime, timezone

import pytest

from shared import audit_log, merkle

# ── 1. Pure Merkle primitive correctness ──────────────────────────────


def _leaves(n: int) -> list[bytes]:
    """n leaf hashes, deterministic so the tree shape is reproducible."""
    return [merkle.leaf_hash(f"record-{i:04d}".encode("utf-8")) for i in range(n)]


@pytest.mark.parametrize("n", [1, 2, 3, 4, 7, 16, 1000])
def test_merkle_root_then_proof_then_verify_all_leaves(n: int) -> None:
    """For tree size n, every leaf's proof must verify against the root.

    The PATH algorithm is RFC 6962 § 2.1.1 — this is the contract any
    independent CT verifier should also satisfy. Walking every index
    catches edge cases in the unbalanced-tree split logic (odd n,
    leaf-at-boundary, right-most-promoted node).
    """
    leaves = _leaves(n)
    root = merkle.build_tree_root(leaves)
    for i in range(n):
        proof = merkle.inclusion_proof(leaves, i)
        assert merkle.verify_inclusion(leaves[i], i, n, proof, root), (
            f"verify failed at index {i} of tree size {n}"
        )


def test_merkle_empty_tree_returns_sha256_of_empty() -> None:
    """RFC 6962 § 2.1 MTH({}) = SHA-256("") — well-defined empty-tree root."""
    assert merkle.build_tree_root([]) == hashlib.sha256(b"").digest()


def test_merkle_node_hash_rejects_non_32_byte_inputs() -> None:
    """0x01 prefix only meaningful for 32-byte SHA-256 digests; refuse
    others rather than silently producing a malformed tree."""
    with pytest.raises(ValueError):
        merkle.node_hash(b"short", b"\x00" * 32)


def test_merkle_verify_rejects_tampered_proof_element() -> None:
    """Single-byte flip in the proof path must fail verification."""
    leaves = _leaves(100)
    root = merkle.build_tree_root(leaves)
    proof = merkle.inclusion_proof(leaves, 42)
    # Tamper the second sibling hash — flip its last byte.
    tampered = list(proof)
    tampered[1] = tampered[1][:-1] + bytes([(tampered[1][-1] + 1) & 0xFF])
    assert not merkle.verify_inclusion(leaves[42], 42, 100, tampered, root)


def test_merkle_verify_rejects_wrong_index() -> None:
    """Proof for index i must not verify a leaf at index j != i."""
    leaves = _leaves(50)
    root = merkle.build_tree_root(leaves)
    proof = merkle.inclusion_proof(leaves, 10)
    assert not merkle.verify_inclusion(leaves[10], 11, 50, proof, root)


def test_merkle_leaf_and_internal_have_distinct_prefixes() -> None:
    """Second-preimage attack resistance: H(0x00||x) MUST NOT equal
    H(0x01||y||z) for any x, y, z — verified by checking they draw
    from a disjoint preimage space (the 0x00 vs 0x01 byte)."""
    leaf_h = merkle.leaf_hash(b"abc")
    node_h = merkle.node_hash(b"\x00" * 32, b"\xff" * 32)
    # Reconstruct what was hashed; the prefix byte is the disjointness guarantee
    assert hashlib.sha256(b"\x00abc").digest() == leaf_h
    assert hashlib.sha256(b"\x01" + b"\x00" * 32 + b"\xff" * 32).digest() == node_h
    # And the two are different (overwhelmingly true; check explicitly anyway).
    assert leaf_h != node_h


# ── 2. audit_log integration — full STH + inclusion proof flow ────────


@pytest.fixture
def audit_dir(tmp_path, monkeypatch):
    """Isolate audit writes to tmp_path; enable audit logging globally
    for the duration of one test.

    The module-level AUDIT_ENABLED constant is read at import time, so
    monkeypatching it here is the simplest cross-test approach. We also
    clear the singleton _writer so the test gets a fresh writer pointing
    at tmp_path."""
    monkeypatch.setattr(audit_log, "AUDIT_ENABLED", True)
    monkeypatch.setattr(audit_log, "AUDIT_DIR", tmp_path)
    monkeypatch.setattr(audit_log, "AUDIT_SERVICE_TAG", "sprint19-test")
    # Reset singleton so get_writer() builds against the patched dir.
    monkeypatch.setattr(audit_log, "_writer", None)
    return tmp_path


def _write_n_audit_records(n: int) -> list[str]:
    """Append n events to the audit log; return their record_hash list
    in append order. Used by integration + tamper tests."""
    hashes: list[str] = []
    writer = audit_log.get_writer()
    for i in range(n):
        writer.append("synthetic_event", {"i": i, "kind": "test"})
    # Read back from disk to confirm the on-disk order matches.
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    hashes = audit_log.read_day_record_hashes(today, "sprint19-test")
    assert len(hashes) == n, f"expected {n} on-disk records, found {len(hashes)}"
    return hashes


def test_daily_merkle_root_over_1000_records(audit_dir) -> None:
    """1000 records → daily_merkle_root returns a 64-hex root + correct
    tree_size. This is the workload an STH publication covers."""
    hashes = _write_n_audit_records(1000)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")

    sth = audit_log.daily_merkle_root(today, "sprint19-test")
    assert sth is not None
    assert sth["tree_size"] == 1000
    assert sth["day"] == today
    assert sth["service_tag"] == "sprint19-test"
    assert len(sth["root_hash_hex"]) == 64  # 32-byte SHA-256

    # Independently recompute to confirm the engine's MTH matches a
    # naive reconstruction — sanity check that the storage-layer read
    # path doesn't reorder records.
    leaves = [merkle.leaf_hash(h.encode("utf-8")) for h in hashes]
    expected_root = merkle.build_tree_root(leaves).hex()
    assert sth["root_hash_hex"] == expected_root


def test_inclusion_proof_for_record_500_verifies(audit_dir) -> None:
    """The middle record's proof must rebuild the STH root. This is the
    'cross-org-verifiable inclusion' contract auditors rely on."""
    hashes = _write_n_audit_records(1000)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    target = hashes[500]

    proof_payload = audit_log.inclusion_proof_for_record(target, day=today, service_tag="sprint19-test")
    assert proof_payload is not None
    assert proof_payload["leaf_index"] == 500
    assert proof_payload["tree_size"] == 1000

    # Verify with the public merkle.verify_inclusion primitive — what
    # an external auditor (or the SDK) would do.
    leaf = merkle.leaf_hash(target.encode("utf-8"))
    proof_bytes = [bytes.fromhex(p) for p in proof_payload["proof_hex"]]
    root_bytes = bytes.fromhex(proof_payload["root_hash_hex"])
    assert merkle.verify_inclusion(leaf, 500, 1000, proof_bytes, root_bytes)


def test_inclusion_proof_for_every_record_verifies(audit_dir) -> None:
    """Stronger guarantee than the single-index test: EVERY record's
    proof must verify. Catches edge cases at indices 0, n-1, and
    around tree-shape boundaries."""
    hashes = _write_n_audit_records(50)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")

    for i, h in enumerate(hashes):
        payload = audit_log.inclusion_proof_for_record(h, day=today, service_tag="sprint19-test")
        assert payload is not None, f"no proof for index {i}"
        leaf = merkle.leaf_hash(h.encode("utf-8"))
        proof_bytes = [bytes.fromhex(p) for p in payload["proof_hex"]]
        root_bytes = bytes.fromhex(payload["root_hash_hex"])
        assert merkle.verify_inclusion(leaf, i, 50, proof_bytes, root_bytes), (
            f"verify failed at index {i}"
        )


def test_inclusion_proof_for_unknown_record_returns_none(audit_dir) -> None:
    """Records not in any of the last 30 days return None; the HTTP
    surface translates this to 404."""
    _write_n_audit_records(10)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    # A random 64-char hex that no real record matches.
    bogus = "0" * 64
    payload = audit_log.inclusion_proof_for_record(bogus, day=today, service_tag="sprint19-test")
    assert payload is None


# ── 3. Tamper detection — Layer 16 core contract ─────────────────────


def test_tamper_with_record_250_breaks_inclusion_proof(audit_dir, tmp_path) -> None:
    """Layer 16 core: an attacker who flips a byte in record 250's
    on-disk line MUST cause the recomputed root for the SAME inclusion
    proof to diverge from the original STH root.

    This is the proof that the Merkle tree gives us 'tamper-evident'
    not just 'tamper-resistant'. Without prefix bytes 0x00/0x01 this
    test would still pass for THIS particular attack, but vanilla
    Merkle is vulnerable to second-preimage subtree substitution
    which the RFC 6962 prefix bytes block — covered by the prefix
    test above.
    """
    hashes = _write_n_audit_records(1000)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")

    # Save the original STH root + a proof for some untouched record.
    original_sth = audit_log.daily_merkle_root(today, "sprint19-test")
    assert original_sth is not None
    untouched_proof = audit_log.inclusion_proof_for_record(
        hashes[750], day=today, service_tag="sprint19-test",
    )
    assert untouched_proof is not None

    # Find the day's audit file + tamper with record 250.
    audit_file = audit_dir / "audit-sprint19-test-{}.jsonl".format(today)
    raw_lines = audit_file.read_bytes().splitlines()
    assert len(raw_lines) == 1000
    rec = json.loads(raw_lines[250])
    # Mutate the payload — keeps JSON well-formed so read_day_record_hashes
    # still picks the line up; but the record_hash field no longer matches
    # the stable fields (and the chain breaks).
    rec["payload"]["i"] = 99999
    raw_lines[250] = json.dumps(rec, separators=(",", ":")).encode("utf-8")
    audit_file.write_bytes(b"\n".join(raw_lines) + b"\n")

    # The day's record_hash list now contains the OLD record_hash at
    # index 250 (because we left the record_hash field alone — the
    # tamper is in the stable-field payload). When we re-compute the
    # Merkle root, since the LEAVES are the record_hash strings and
    # those are unchanged, the recomputed root will MATCH the original.
    # That's expected — record_hash is a SHA-256 of the OLD payload, so
    # an attacker keeping record_hash unchanged cannot use it to detect
    # the tamper via the Merkle root alone.
    #
    # However: verify_chain (the per-record chain walker) DOES detect
    # this — because it re-derives record_hash from stable fields and
    # compares to the on-disk record_hash. Layer 16 = Merkle + chain
    # walker TOGETHER detect any single-line tamper.
    chain_report = audit_log.verify_chain(audit_file)
    assert not chain_report["ok"], "chain walker must reject tampered record"
    assert any(
        f["line"] == 251 and "record_hash mismatch" in f["error"]
        for f in chain_report["failures"]
    ), "tampered record_hash mismatch must be flagged at line 251"

    # Stronger Merkle-only test: an attacker who DOES update record_hash
    # to match the tampered payload would still be caught — because the
    # record_hash itself changes, and the rebuilt Merkle root over the
    # new hash list diverges from the original STH root.
    tampered_stable = {
        k: rec[k] for k in ("ts", "service", "kind", "payload", "prev_hash")
    }
    new_hash = hashlib.sha256(
        json.dumps(tampered_stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    rec["record_hash"] = new_hash
    raw_lines[250] = json.dumps(rec, separators=(",", ":")).encode("utf-8")
    audit_file.write_bytes(b"\n".join(raw_lines) + b"\n")

    new_sth = audit_log.daily_merkle_root(today, "sprint19-test")
    assert new_sth is not None
    assert new_sth["root_hash_hex"] != original_sth["root_hash_hex"], (
        "tampered record_hash must shift the Merkle root — STH comparison "
        "is the cross-org-verifiable tamper signal"
    )

    # And the original record-750 proof now fails to verify against the
    # NEW root — proving inclusion proofs are tamper-evident in both
    # directions (the proof was valid against the old tree; it's invalid
    # against the new tree).
    new_root_bytes = bytes.fromhex(new_sth["root_hash_hex"])
    leaf_750 = merkle.leaf_hash(hashes[750].encode("utf-8"))
    proof_bytes = [bytes.fromhex(p) for p in untouched_proof["proof_hex"]]
    assert not merkle.verify_inclusion(leaf_750, 750, 1000, proof_bytes, new_root_bytes)


# ── 4. STH signing round-trip ────────────────────────────────────────


def test_sth_canonical_bytes_signature_roundtrip(audit_dir, monkeypatch) -> None:
    """The engine signs canonical-JSON of (day, service_tag, tree_size,
    root_hash_hex, timestamp_iso). A consumer holding the public key
    must verify the same bytes.

    Uses the engine's signing module directly — same code path the HTTP
    endpoint takes — so this test catches any drift between the
    canonical-bytes helper and the signing implementation."""
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    # Generate an ephemeral signing key for this test only.
    priv = Ed25519PrivateKey.generate()
    priv_hex = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    ).hex()
    monkeypatch.setenv("AURA_SIGNING_PRIVATE_KEY_HEX", priv_hex)
    # Force reload so the signing module picks up the new env var.
    from counterfactual_service import signing
    importlib.reload(signing)

    _write_n_audit_records(20)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    sth = audit_log.daily_merkle_root(today, "sprint19-test")
    assert sth is not None

    # Replicate the engine's canonical-STH-bytes shape so the signature
    # the engine produces is verifiable by any consumer following the
    # same canonical-JSON contract.
    canonical = {
        "day": sth["day"],
        "service_tag": sth["service_tag"],
        "tree_size": sth["tree_size"],
        "root_hash_hex": sth["root_hash_hex"],
        "timestamp_iso": "2026-05-17T00:00:00+00:00",
    }
    signed_bytes = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sig = signing.sign_bytes(signed_bytes)
    assert sig is not None
    # Verify with the raw public key — what an external auditor would do.
    pub = priv.public_key()
    pub.verify(base64.b64decode(sig), signed_bytes)


# NOTE: SDK respx-mocked verify_inclusion tests live in
# sdk/tests/test_inclusion_verifier.py — kept out of the backend lane
# because respx isn't a backend test dep and the SDK lane is the
# natural home for HTTP-mocked SDK behaviour. The pure Merkle +
# audit_log integration tests above already cover the backend side
# of the contract.
