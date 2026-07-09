"""Tests for the Phase-2 numeric heal controller (verified auto-commit).

Covers the state machine's three guarantees: sequential K-of-K verification
before commit, two-sided auto-revert once the raw stream recovers, and the
core safety property that a legitimate regime change is never healed.
"""

import numpy as np
import pytest

from uasr.numeric_heal_controller import (
    HealState,
    NumericHealController,
)
from uasr.numeric_semantics import NumericBaseline

_RNG = np.random.default_rng(1234)


def _healthy(n=200, mu=50.0, sigma=5.0):
    return list(_RNG.normal(mu, sigma, n))


def _baseline():
    # 12 healthy batches — a stable out-of-sample band. Fitting from only a
    # handful of batches leaves `healthy_z` noisy enough that even a correctly
    # healed batch occasionally clips the absolute band; production baselines
    # are fit from many batches.
    return NumericBaseline.fit([_healthy() for _ in range(12)])


def _scaled(factor, n=200, mu=50.0, sigma=5.0):
    return list(_RNG.normal(mu, sigma, n) * factor)


# ── sequential verification ──────────────────────────────────────────────

def test_no_commit_on_single_batch():
    ctrl = NumericHealController(k_confirm=3)
    ctrl.load_baseline("s", "c", _baseline())
    d = ctrl.observe("s", "c", _scaled(100))
    assert d.state is HealState.CANARY
    assert d.applied_transform == "none"      # never applies on the first sight
    assert d.confirmations == 1


def test_commits_after_k_consecutive_confirmations():
    ctrl = NumericHealController(k_confirm=3)
    ctrl.load_baseline("s", "c", _baseline())
    states = [ctrl.observe("s", "c", _scaled(100)).state for _ in range(3)]
    assert states == [HealState.CANARY, HealState.CANARY, HealState.COMMITTED]
    assert ctrl.state_of("s", "c") is HealState.COMMITTED


def test_higher_k_confirm_needs_more_batches():
    ctrl = NumericHealController(k_confirm=5)
    ctrl.load_baseline("s", "c", _baseline())
    for _ in range(4):
        assert ctrl.observe("s", "c", _scaled(100)).state is HealState.CANARY
    assert ctrl.observe("s", "c", _scaled(100)).state is HealState.COMMITTED


def test_canary_aborts_if_confirmation_breaks():
    ctrl = NumericHealController(k_confirm=3)
    ctrl.load_baseline("s", "c", _baseline())
    ctrl.observe("s", "c", _scaled(100))          # canary open, conf=1
    d = ctrl.observe("s", "c", _healthy())        # raw suddenly healthy: abort
    assert d.event == "abort"
    assert d.state is HealState.OBSERVING
    assert ctrl.state_of("s", "c") is HealState.OBSERVING


# ── applying the committed transform ───────────────────────────────────────

def test_apply_is_passthrough_until_committed():
    ctrl = NumericHealController(k_confirm=3)
    ctrl.load_baseline("s", "c", _baseline())
    vals = _scaled(100)
    ctrl.observe("s", "c", vals)
    out = ctrl.apply("s", "c", vals)
    np.testing.assert_allclose(out, np.asarray(vals, float))   # untouched in canary


def test_apply_transforms_once_committed():
    ctrl = NumericHealController(k_confirm=3)
    ctrl.load_baseline("s", "c", _baseline())
    for _ in range(3):
        ctrl.observe("s", "c", _scaled(100))
    bad = np.array([5000.0, 6000.0, 7000.0])
    out = ctrl.apply("s", "c", bad)
    np.testing.assert_allclose(out, bad * 0.01)                # div100 applied


# ── two-sided commit / auto-revert ─────────────────────────────────────────

def test_reverts_when_raw_stream_healthy_again():
    ctrl = NumericHealController(k_confirm=3, revert_patience=3)
    ctrl.load_baseline("s", "c", _baseline())
    for _ in range(3):
        ctrl.observe("s", "c", _scaled(100))
    assert ctrl.state_of("s", "c") is HealState.COMMITTED
    events = [ctrl.observe("s", "c", _healthy()).event for _ in range(3)]
    assert events[-1] == "revert"
    assert ctrl.state_of("s", "c") is HealState.OBSERVING


def test_no_revert_while_raw_still_drifted():
    ctrl = NumericHealController(k_confirm=3, revert_patience=3)
    ctrl.load_baseline("s", "c", _baseline())
    for _ in range(3):
        ctrl.observe("s", "c", _scaled(100))
    for _ in range(5):
        d = ctrl.observe("s", "c", _scaled(100))
        assert d.state is HealState.COMMITTED          # stays committed
        assert d.event is None


# ── core safety property: regime change never heals ────────────────────────

def test_regime_change_never_commits():
    ctrl = NumericHealController(k_confirm=3)
    ctrl.load_baseline("s", "c", _baseline())
    states = [ctrl.observe("s", "c", _healthy(mu=90.0)).state for _ in range(10)]
    assert HealState.COMMITTED not in states
    assert all(s is HealState.OBSERVING for s in states)


def test_variance_change_never_commits():
    ctrl = NumericHealController(k_confirm=3)
    ctrl.load_baseline("s", "c", _baseline())
    states = [ctrl.observe("s", "c", _healthy(sigma=25.0)).state for _ in range(10)]
    assert HealState.COMMITTED not in states


# ── no baseline / passthrough ──────────────────────────────────────────────

def test_no_baseline_is_passthrough():
    ctrl = NumericHealController()
    d = ctrl.observe("unknown", "c", _scaled(100))
    assert d.state is HealState.OBSERVING
    assert d.applied_transform == "none"
    out = ctrl.apply("unknown", "c", [1.0, 2.0])
    np.testing.assert_allclose(out, [1.0, 2.0])


def test_healthy_stream_stays_observing():
    ctrl = NumericHealController(k_confirm=3)
    ctrl.load_baseline("s", "c", _baseline())
    for _ in range(6):
        d = ctrl.observe("s", "c", _healthy())
        assert d.state is HealState.OBSERVING
        assert d.raw_drifted is False


# ── audit trail ─────────────────────────────────────────────────────────────

def test_audit_records_transitions_with_unique_hashes():
    ctrl = NumericHealController(k_confirm=3, revert_patience=3)
    ctrl.load_baseline("s", "c", _baseline())
    for _ in range(3):
        ctrl.observe("s", "c", _scaled(100))
    for _ in range(3):
        ctrl.observe("s", "c", _healthy())
    events = [a.event for a in ctrl.audit_log]
    assert events == ["canary_open", "commit", "revert"]
    hashes = [a.audit_record_hash for a in ctrl.audit_log]
    assert len(set(hashes)) == len(hashes)
    assert all(len(h) == 64 for h in hashes)          # sha256 hex


def test_audit_hash_is_deterministic_over_payload():
    from counterfactual_service.canonical import sha256_canonical
    ctrl = NumericHealController(k_confirm=1)
    ctrl.load_baseline("s", "c", _baseline())
    a = ctrl.observe("s", "c", _scaled(100)).audit    # k_confirm=1 ⇒ commit immediately
    assert a is not None and a.event == "commit"
    expected = sha256_canonical({
        "schema_version": "uasr.numeric_heal.v1",
        "source_id": "s", "column": "c", "event": "commit",
        "transform": a.transform, "factor": a.factor, "detail": a.detail,
    })
    assert a.audit_record_hash == expected


# ── independence across columns / sources ───────────────────────────────────

def test_columns_are_independent():
    ctrl = NumericHealController(k_confirm=3)
    ctrl.load_baseline("s", "good", _baseline())
    ctrl.load_baseline("s", "bad", _baseline())
    for _ in range(3):
        ctrl.observe("s", "good", _healthy())
        ctrl.observe("s", "bad", _scaled(100))
    assert ctrl.state_of("s", "good") is HealState.OBSERVING
    assert ctrl.state_of("s", "bad") is HealState.COMMITTED
