"""
Sprint 19 — SDK Client.verify_inclusion (RFC 6962 Merkle inclusion +
ED25519 STH signature) via respx-mocked HTTP.

These tests live in the SDK lane (not the backend lane) because they
exercise the SDK's vendored Merkle reconstruction + signature
verification, and respx is an SDK-only test dep. The backend side of
the contract is covered by aurabackend/tests/test_audit_merkle.py.

Each test builds a real RFC 6962 inclusion proof (via the same
algorithm the engine uses, restated here so the SDK lane is
self-contained), serves it through respx, then drives Client.verify_inclusion
through the network surface and asserts the verifier's structured
result. Three modes:

1. Happy path (unsigned engine): root reconstruction succeeds against
   STH root → verified=True.
2. Tampered STH root: STH lies about the published root → SDK detects
   mismatch against rebuilt root → verified=False.
3. Tampered proof: proof endpoint serves a corrupted sibling hash →
   reconstruction diverges from STH root → verified=False.
"""
from __future__ import annotations

import hashlib

import httpx
import pytest
import respx

from aura_counterfactual import Client


BASE = "http://aura.test"


# ── Vendored RFC 6962 Merkle helpers ──────────────────────────────────
# Restated here so the SDK lane has zero dependency on aurabackend.
# Algorithm identical to aurabackend/shared/merkle.py.


def _leaf_hash(data: bytes) -> bytes:
    return hashlib.sha256(b"\x00" + data).digest()


def _node_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + left + right).digest()


def _largest_power_of_two_less_than(n: int) -> int:
    return 1 << ((n - 1).bit_length() - 1)


def _build_tree_root(leaves: list[bytes]) -> bytes:
    n = len(leaves)
    if n == 0:
        return hashlib.sha256(b"").digest()
    if n == 1:
        return leaves[0]
    k = _largest_power_of_two_less_than(n)
    return _node_hash(_build_tree_root(leaves[:k]), _build_tree_root(leaves[k:]))


def _inclusion_proof(leaves: list[bytes], index: int) -> list[bytes]:
    n = len(leaves)
    if n == 1:
        return []
    k = _largest_power_of_two_less_than(n)
    if index < k:
        return _inclusion_proof(leaves[:k], index) + [_build_tree_root(leaves[k:])]
    return _inclusion_proof(leaves[k:], index - k) + [_build_tree_root(leaves[:k])]


def _build_sdk_fixture(n: int, target_index: int) -> dict:
    """Engine-shaped STH + inclusion proof for one leaf in a tree of n."""
    record_hashes = [
        hashlib.sha256(f"sdk-rec-{i}".encode("utf-8")).hexdigest() for i in range(n)
    ]
    leaves = [_leaf_hash(h.encode("utf-8")) for h in record_hashes]
    root = _build_tree_root(leaves)
    proof = _inclusion_proof(leaves, target_index)
    return {
        "record_hash": record_hashes[target_index],
        "proof_payload": {
            "record_hash": record_hashes[target_index],
            "day": "20260517",
            "service_tag": "sprint19-test",
            "tree_size": n,
            "leaf_index": target_index,
            "proof_hex": [p.hex() for p in proof],
            "root_hash_hex": root.hex(),
        },
        "sth_payload": {
            "tree_size": n,
            "root_hash_hex": root.hex(),
            "timestamp_iso": "2026-05-17T00:00:00+00:00",
            "day": "20260517",
            "service_tag": "sprint19-test",
            "signature_b64": "",
            "signature_status": "unsigned",
            "signing_key_source": "none",
            "canonical_signed_bytes_b64": "",
        },
    }


# ── Tests ─────────────────────────────────────────────────────────────


@respx.mock
def test_verify_inclusion_happy_path_unsigned() -> None:
    """Unsigned engine: SDK rebuilds the root, matches the STH root,
    returns verified=True. The signed-engine path is covered indirectly
    by the backend STH signing round-trip test."""
    fixture = _build_sdk_fixture(n=100, target_index=42)
    h = fixture["record_hash"]

    respx.get(f"{BASE}/api/v1/counterfactual/audit/inclusion/{h}").mock(
        return_value=httpx.Response(200, json=fixture["proof_payload"]),
    )
    respx.get(f"{BASE}/api/v1/counterfactual/audit/sth?day=20260517").mock(
        return_value=httpx.Response(200, json=fixture["sth_payload"]),
    )

    with Client(base_url=BASE) as c:
        result = c.verify_inclusion(h, verify_signature=False)
    assert result["root_match"] is True
    assert result["verified"] is True
    assert result["leaf_index"] == 42
    assert result["tree_size"] == 100


@respx.mock
def test_verify_inclusion_detects_tampered_sth_root() -> None:
    """If the engine's STH lies about its root, the SDK rebuilds the
    true root from (leaf, proof) and detects the mismatch. This is the
    cross-org-verifiable security signal — the STH is the published
    commitment, not the proof's self-attested root."""
    fixture = _build_sdk_fixture(n=100, target_index=42)
    bad_root = "ff" + fixture["sth_payload"]["root_hash_hex"][2:]
    tampered_sth = {**fixture["sth_payload"], "root_hash_hex": bad_root}
    h = fixture["record_hash"]

    respx.get(f"{BASE}/api/v1/counterfactual/audit/inclusion/{h}").mock(
        return_value=httpx.Response(200, json=fixture["proof_payload"]),
    )
    respx.get(f"{BASE}/api/v1/counterfactual/audit/sth?day=20260517").mock(
        return_value=httpx.Response(200, json=tampered_sth),
    )

    with Client(base_url=BASE) as c:
        result = c.verify_inclusion(h, verify_signature=False)
    assert result["root_match"] is False
    assert result["verified"] is False


@respx.mock
def test_verify_inclusion_detects_tampered_proof() -> None:
    """Corrupted sibling hash in the proof makes the reconstruction
    diverge from the STH root."""
    fixture = _build_sdk_fixture(n=100, target_index=42)
    tampered_proof = list(fixture["proof_payload"]["proof_hex"])
    tampered_proof[0] = "ff" + tampered_proof[0][2:]
    tampered_payload = {**fixture["proof_payload"], "proof_hex": tampered_proof}
    h = fixture["record_hash"]

    respx.get(f"{BASE}/api/v1/counterfactual/audit/inclusion/{h}").mock(
        return_value=httpx.Response(200, json=tampered_payload),
    )
    respx.get(f"{BASE}/api/v1/counterfactual/audit/sth?day=20260517").mock(
        return_value=httpx.Response(200, json=fixture["sth_payload"]),
    )

    with Client(base_url=BASE) as c:
        result = c.verify_inclusion(h, verify_signature=False)
    assert result["root_match"] is False
    assert result["verified"] is False


@respx.mock
def test_get_sth_returns_payload_as_dict() -> None:
    """Thin wrapper: get_sth(day) hits /audit/sth?day= and returns the
    raw payload. Auditors building custom verification flows need the
    raw shape, not a Pydantic model."""
    fixture = _build_sdk_fixture(n=5, target_index=0)
    respx.get(f"{BASE}/api/v1/counterfactual/audit/sth?day=20260517").mock(
        return_value=httpx.Response(200, json=fixture["sth_payload"]),
    )
    with Client(base_url=BASE) as c:
        sth = c.get_sth(day="20260517")
    assert sth["tree_size"] == 5
    assert sth["day"] == "20260517"


@respx.mock
@pytest.mark.asyncio
async def test_async_verify_inclusion_happy_path() -> None:
    """AsyncClient mirror — same algorithm, async I/O."""
    from aura_counterfactual import AsyncClient
    fixture = _build_sdk_fixture(n=50, target_index=17)
    h = fixture["record_hash"]
    respx.get(f"{BASE}/api/v1/counterfactual/audit/inclusion/{h}").mock(
        return_value=httpx.Response(200, json=fixture["proof_payload"]),
    )
    respx.get(f"{BASE}/api/v1/counterfactual/audit/sth?day=20260517").mock(
        return_value=httpx.Response(200, json=fixture["sth_payload"]),
    )
    async with AsyncClient(base_url=BASE) as c:
        result = await c.verify_inclusion(h, verify_signature=False)
    assert result["verified"] is True
