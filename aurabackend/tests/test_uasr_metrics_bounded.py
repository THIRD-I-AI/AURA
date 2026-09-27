"""BUG-210: the UASR metrics tracker must not grow without bound."""
from uasr.metrics import HealingMetricTracker, RecoveryEvent
from uasr.models import DriftSeverity, DriftType, RecoveryStatus


def _ev(i):
    return RecoveryEvent(
        source_id=f"src-{i}", drift_type=DriftType.STATISTICAL, severity=DriftSeverity.LOW,
        status=RecoveryStatus.DEPLOYED, latency_seconds=0.1,
    )


def test_event_list_is_capped_and_keeps_the_newest():
    t = HealingMetricTracker(max_events=100)
    for i in range(350):
        t.record(_ev(i))
    assert len(t._events) == 100
    assert t._events[0].source_id == "src-250" and t._events[-1].source_id == "src-349"
    assert t.compute().total_events == 100


def test_default_cap_is_finite(monkeypatch):
    monkeypatch.delenv("AURA_UASR_MAX_EVENTS", raising=False)
    assert HealingMetricTracker()._max_events == 10000


def test_cap_is_configurable_by_env(monkeypatch):
    monkeypatch.setenv("AURA_UASR_MAX_EVENTS", "7")
    assert HealingMetricTracker()._max_events == 7
    monkeypatch.setenv("AURA_UASR_MAX_EVENTS", "not-a-number")
    assert HealingMetricTracker()._max_events == 10000


def test_trend_histories_are_capped_and_the_trend_is_unchanged():
    t = HealingMetricTracker(trend_window=5, trend_min_interval_seconds=0.0)
    for i in range(1200):
        t.record(_ev(i))
        t.compute()
    assert len(t._hu_history) <= 1000 and len(t._composite_history) <= 1000
    assert len(t._hu_history) == len(t._composite_history)
    assert len(t.compute().trend) == 5
