"""
Sprint 20a — Layer 17c contract tests for Akidau et al. Dataflow Model
primitives: composite watermark + trigger primitives + late-data policies.

These tests assert the FORMAL CONTRACT each primitive must satisfy:

  * WatermarkTracker.composite == min(per-upstream watermarks),
    monotonically non-decreasing.
  * WatermarkTrigger fires iff watermark >= window_end.
  * CountTrigger fires iff count >= threshold.
  * ProcessingTimeTrigger fires iff wall-clock elapsed >= interval.
  * CompositeTrigger correctly OR/ANDs children + propagates determinism.
  * Late-data policies enforce the Akidau § 5 contract:
        drop → silent drop
        side_output → diverted with reason
        remerge_within_allowed_lateness → accept iff lateness <= L

A change that breaks these invariants breaks the Dataflow Model
guarantee — Layer 17c blocks the regression.
"""
from __future__ import annotations

import math

import pytest

from pipeline.streaming.late_data import (
    LateDataDecision,
    drop_policy,
    remerge_within_allowed_lateness_policy,
    side_output_policy,
)
from pipeline.streaming.triggers import (
    CompositeTrigger,
    CountTrigger,
    ProcessingTimeTrigger,
    TriggerContext,
    WatermarkTrigger,
)
from pipeline.streaming.watermark_tracker import NEG_INF, WatermarkTracker

# ── WatermarkTracker: min-of-upstreams contract ────────────────────────


def test_composite_is_neg_inf_until_all_upstreams_report() -> None:
    """A downstream operator cannot close any window until EVERY
    upstream has guaranteed (via at least one watermark emission)
    that it's still producing — the strongest correctness signal."""
    tracker = WatermarkTracker(upstream_ids=["a", "b"])
    assert tracker.composite == NEG_INF
    tracker.receive("a", 100.0)
    # b still silent → composite remains -inf.
    assert tracker.composite == NEG_INF
    tracker.receive("b", 50.0)
    # Now composite = min(100, 50) = 50.
    assert tracker.composite == 50.0


def test_composite_is_min_across_upstreams() -> None:
    """The core Dataflow Model invariant — composite tracks the
    SLOWEST upstream, never the fastest. Window closure waits on the
    slowest because the fastest might still re-emit lower-timestamp
    events that the slowest path knows about."""
    tracker = WatermarkTracker(upstream_ids=["a", "b", "c"])
    tracker.receive("a", 500.0)
    tracker.receive("b", 300.0)
    tracker.receive("c", 400.0)
    assert tracker.composite == 300.0
    # b catches up to 350; a + c haven't moved. New composite = min(500, 350, 400) = 350.
    tracker.receive("b", 350.0)
    assert tracker.composite == 350.0


def test_composite_is_monotonically_non_decreasing() -> None:
    """Dataflow Model § 3.2: watermarks NEVER move backward. The
    tracker enforces this defensively — a non-monotone upstream
    is clamped to its previous max + warns. Composite stays sane."""
    tracker = WatermarkTracker(upstream_ids=["a"])
    tracker.receive("a", 100.0)
    tracker.receive("a", 50.0)  # MUST clamp to 100 (with warning).
    assert tracker.per_input["a"] == 100.0
    assert tracker.composite == 100.0


def test_slowest_upstream_diagnoses_the_bottleneck() -> None:
    """Useful for the operator UX 'which upstream is holding the
    pipeline back?' — points to the source that needs attention."""
    tracker = WatermarkTracker(upstream_ids=["fast", "slow"])
    tracker.receive("fast", 1000.0)
    tracker.receive("slow", 200.0)
    assert tracker.slowest_upstream == "slow"


def test_nan_watermark_is_rejected() -> None:
    """NaN comparisons are undefined in IEEE 754; a NaN watermark would
    silently produce NaN composite and downstream windows would never
    fire. Reject at the boundary."""
    tracker = WatermarkTracker(upstream_ids=["a"])
    with pytest.raises(ValueError, match="NaN"):
        tracker.receive("a", float("nan"))


def test_lag_reports_per_upstream_skew() -> None:
    """Lag = max(all) - per[upstream]. Telling the operator how far
    behind each upstream is relative to the fastest."""
    tracker = WatermarkTracker(upstream_ids=["a", "b", "c"])
    tracker.receive("a", 100.0)
    tracker.receive("b", 90.0)
    tracker.receive("c", 50.0)
    lag = tracker.lag()
    assert lag == {"a": 0.0, "b": 10.0, "c": 50.0}


# ── Trigger primitives ────────────────────────────────────────────────


def _ctx(watermark=0.0, count=0, proc=0.0, first_proc=float("nan")) -> TriggerContext:
    return TriggerContext(
        watermark_ts=watermark,
        event_count=count,
        processing_ts=proc,
        first_event_processing_ts=first_proc,
    )


def test_watermark_trigger_fires_when_watermark_crosses_window_end() -> None:
    """The Dataflow Model's default fire-on-watermark contract."""
    trig = WatermarkTrigger(window_end_ts=100.0)
    assert trig.should_fire(_ctx(watermark=99.0)) is False
    assert trig.should_fire(_ctx(watermark=100.0)) is True
    assert trig.should_fire(_ctx(watermark=150.0)) is True


def test_watermark_trigger_is_deterministic() -> None:
    """Watermark-driven triggers are causal; same stream → same fires.
    Audit-engine relies on this for byte-identical replay."""
    trig = WatermarkTrigger(window_end_ts=100.0)
    assert trig.is_deterministic is True


def test_count_trigger_fires_at_threshold() -> None:
    """Early speculative results: emit something after N events."""
    trig = CountTrigger(threshold=100)
    assert trig.should_fire(_ctx(count=99)) is False
    assert trig.should_fire(_ctx(count=100)) is True


def test_count_trigger_rejects_invalid_threshold() -> None:
    with pytest.raises(ValueError, match="threshold must be >= 1"):
        CountTrigger(threshold=0)


def test_processing_time_trigger_is_non_deterministic() -> None:
    """Wall-clock based → not stable across replays. Audit-engine
    integration in S20.1 will refuse this trigger for hash-stable
    artifacts."""
    trig = ProcessingTimeTrigger(interval_seconds=5.0)
    assert trig.is_deterministic is False


def test_processing_time_trigger_fires_after_interval() -> None:
    trig = ProcessingTimeTrigger(interval_seconds=5.0)
    # No events yet → NaN first_proc → must not fire.
    assert trig.should_fire(_ctx(proc=999.0)) is False
    # First event at processing_ts=100; interval=5. Fires at proc >= 105.
    assert trig.should_fire(_ctx(proc=104.9, first_proc=100.0)) is False
    assert trig.should_fire(_ctx(proc=105.0, first_proc=100.0)) is True


def test_composite_any_fires_when_one_child_fires() -> None:
    """OR-semantics: 'fire early on count OR finally on watermark'."""
    trig = CompositeTrigger(
        children=[CountTrigger(threshold=100), WatermarkTrigger(window_end_ts=200.0)],
        mode="any",
    )
    # Neither fires.
    assert trig.should_fire(_ctx(watermark=150.0, count=50)) is False
    # Count fires.
    assert trig.should_fire(_ctx(watermark=150.0, count=100)) is True
    # Watermark fires.
    assert trig.should_fire(_ctx(watermark=200.0, count=10)) is True


def test_composite_all_requires_every_child_to_fire() -> None:
    """AND-semantics: 'fire only when 100 events AND 5 seconds elapsed'."""
    trig = CompositeTrigger(
        children=[
            CountTrigger(threshold=100),
            ProcessingTimeTrigger(interval_seconds=5.0),
        ],
        mode="all",
    )
    # Count satisfied, processing-time not yet (no events).
    assert trig.should_fire(_ctx(count=100)) is False
    # Both satisfied.
    ok = trig.should_fire(_ctx(count=100, proc=10.0, first_proc=4.0))
    assert ok is True


def test_composite_determinism_propagates_from_children() -> None:
    """A composite containing a ProcessingTimeTrigger is non-deterministic;
    a composite containing only Watermark + Count triggers is deterministic."""
    det = CompositeTrigger(
        children=[CountTrigger(threshold=10), WatermarkTrigger(window_end_ts=1.0)],
        mode="any",
    )
    assert det.is_deterministic is True
    non_det = CompositeTrigger(
        children=[CountTrigger(threshold=10), ProcessingTimeTrigger(interval_seconds=1.0)],
        mode="any",
    )
    assert non_det.is_deterministic is False


def test_composite_rejects_empty_children() -> None:
    with pytest.raises(ValueError, match="at least one child"):
        CompositeTrigger(children=[], mode="any")


def test_composite_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="must be 'any' or 'all'"):
        CompositeTrigger(children=[CountTrigger(1)], mode="xor")  # type: ignore[arg-type]


# ── Late-data policies ────────────────────────────────────────────────


def test_drop_policy_silently_drops() -> None:
    """No window merge, no side output — the highest-throughput,
    lowest-completeness policy."""
    d = drop_policy({"v": 1}, event_ts=50.0, watermark_ts=100.0)
    assert d.accept_to_window is False
    assert d.side_output is None
    assert d.is_within_lateness is False


def test_side_output_policy_diverts_with_diagnostic() -> None:
    """Side-output for offline reconciliation — main path stays clean,
    every late event is preserved with diagnostic fields."""
    evt = {"v": "abc"}
    d = side_output_policy(evt, event_ts=50.0, watermark_ts=100.0)
    assert d.accept_to_window is False
    assert d.side_output is not None
    assert d.side_output["event"] == evt
    assert d.side_output["lateness_seconds"] == pytest.approx(50.0)
    assert d.side_output["reason"] == "late_event_side_output"


def test_remerge_within_lateness_accepts_when_inside_window() -> None:
    """The Akidau § 5 'allowed lateness' contract: accept if
    watermark - event_ts <= L. Inside the window the event merges
    back into the operator state and a refinement is emitted."""
    policy = remerge_within_allowed_lateness_policy(allowed_lateness_seconds=30.0)
    # Watermark = 100, event = 80 → lateness 20s ≤ 30s ⇒ accept.
    d = policy({"v": 1}, event_ts=80.0, watermark_ts=100.0)
    assert d.accept_to_window is True
    assert d.is_within_lateness is True
    assert d.side_output is None


def test_remerge_within_lateness_drops_beyond_window_by_default() -> None:
    """Lateness > allowed → on_expiry='drop' default → silent drop."""
    policy = remerge_within_allowed_lateness_policy(allowed_lateness_seconds=30.0)
    d = policy({"v": 1}, event_ts=50.0, watermark_ts=100.0)  # 50s late > 30s
    assert d.accept_to_window is False
    assert d.is_within_lateness is False
    assert d.side_output is None


def test_remerge_within_lateness_side_outputs_beyond_window_when_requested() -> None:
    """on_expiry='side_output' redirects expired events to side
    channel instead of dropping — useful for audit + replay."""
    policy = remerge_within_allowed_lateness_policy(
        allowed_lateness_seconds=30.0, on_expiry="side_output",
    )
    d = policy({"v": 1}, event_ts=50.0, watermark_ts=100.0)
    assert d.accept_to_window is False
    assert d.side_output is not None
    assert d.side_output["reason"] == "late_beyond_allowed_lateness"
    assert d.side_output["allowed_lateness_seconds"] == 30.0


def test_remerge_rejects_negative_lateness_param() -> None:
    with pytest.raises(ValueError, match=">= 0"):
        remerge_within_allowed_lateness_policy(allowed_lateness_seconds=-1.0)


def test_remerge_rejects_invalid_on_expiry() -> None:
    with pytest.raises(ValueError, match="drop' or 'side_output"):
        remerge_within_allowed_lateness_policy(
            allowed_lateness_seconds=10.0, on_expiry="invalid",  # type: ignore[arg-type]
        )


# ── Decision dataclass shape (consumer contract) ─────────────────────


def test_late_data_decision_is_a_complete_contract() -> None:
    """The operator should be able to dispatch ALL late-data behaviour
    off the three fields of LateDataDecision — no operator branch
    should need to inspect the policy itself."""
    d = LateDataDecision(accept_to_window=True, side_output=None, is_within_lateness=True)
    assert d.accept_to_window is True
    assert d.side_output is None
    assert d.is_within_lateness is True


def test_math_isnan_check_works_for_first_proc_sentinel() -> None:
    """Sanity check: NaN comparisons used by ProcessingTimeTrigger
    must return False the way we rely on them. Belt-and-braces — if
    Python ever changes NaN semantics we want to catch it here."""
    assert math.isnan(float("nan"))
    assert (float("nan") < 100.0) is False
    assert (float("nan") >= 100.0) is False
