"""The ADR-002 benchmark's own ground truth and scoring, checked without the healer.

A benchmark that scored a missed fault as a pass, or whose "clean" data already held
duplicates, would report precision it never measured. These pin the generator, the
injectors and the classifier on hand-built responses.
"""
from __future__ import annotations

import pytest

from .datasets import make_dataset, strict_equal
from .faults import FAULT_TYPES, INJECTORS, build_clean, build_trial
from .harness import CORRECT, QUARANTINED, WRONG, classify, classify_clean, metrics


def _response(rows, quarantined=(), healed=True, alerts=(), drift=True):
    return {"rows": rows, "healed": healed, "drift_detected": drift, "degraded": bool(quarantined or alerts),
            "quarantined_rows": [{"index": i, "row": r, "reason": "held"} for i, r in quarantined],
            "alerts": [{"type": a} for a in alerts], "reason": None if healed else "held"}


@pytest.mark.parametrize("fault", FAULT_TYPES)
def test_a_trial_is_rebuilt_exactly_from_its_seed(fault):
    a, b = build_trial(fault, 7), build_trial(fault, 7)
    assert strict_equal(a.sent, b.sent) and a.expected == b.expected and a.detail == b.detail
    assert len(a.sent) == len(a.expected)


def test_clean_datasets_hold_no_duplicate_rows_and_unique_identifiers():
    for seed in range(30):
        ds = make_dataset(seed)
        rows = ds.baseline + ds.truth
        assert len({repr(sorted(r.items())) for r in rows}) == len(rows)
        ident = ds.columns[0].name
        assert len({r[ident] for r in rows}) == len(rows)


@pytest.mark.parametrize("fault", sorted(INJECTORS))
def test_every_injected_fault_changes_what_upstream_sends(fault):
    for seed in range(10):
        t = build_trial(fault, seed)
        assert not strict_equal(t.sent, t.dataset.truth), t.detail


def test_scale_down_and_int_as_float_keep_the_clean_truth_as_expected():
    for fault in ("scale_down", "int_as_float", "rename", "retype_lossless"):
        t = build_trial(fault, 3)
        assert t.expected == t.dataset.truth and t.expect_healed


def test_a_missed_fault_returned_as_healed_is_wrong():
    t = build_trial("scale_down", 0)
    r = classify(t, _response([dict(x) for x in t.sent], drift=False))
    assert r.outcome == WRONG and "expected" in r.why


def test_an_int_returned_as_float_is_wrong_even_though_it_compares_equal():
    t = build_trial("int_as_float", 0)
    assert t.sent[0] == t.expected[0]  # plain == cannot see BUG-572
    assert classify(t, _response(t.sent, drift=False)).outcome == WRONG


def test_exact_restore_is_correct_and_full_quarantine_is_a_safe_abstention():
    t = build_trial("rename", 1)
    assert classify(t, _response([dict(x) for x in t.expected])).outcome == CORRECT
    held = classify(t, _response([], quarantined=list(enumerate(t.sent)), healed=False))
    assert held.outcome == QUARANTINED and held.accounting_error is None


def test_rows_with_no_correct_value_must_be_quarantined():
    t = build_trial("retype_lossy", 2)
    bad = [i for i, e in enumerate(t.expected) if e is None]
    assert bad
    good_rows = [e for e in t.expected if e is not None]
    assert classify(t, _response(good_rows, quarantined=[(i, t.sent[i]) for i in bad])).outcome == CORRECT
    leaked = classify(t, _response([e if e is not None else t.sent[i] for i, e in enumerate(t.expected)]))
    assert leaked.outcome == WRONG


def test_detection_only_needs_the_alert_and_healed_false():
    t = build_trial("null_spike", 4)
    assert classify(t, _response(t.sent, healed=False, alerts=["null_rate"])).outcome == CORRECT
    assert classify(t, _response(t.sent, healed=True)).outcome == WRONG


def test_wrong_values_in_rows_are_wrong_even_when_healed_is_false():
    # rows reach the sink whatever `healed` says (ADR-002 phase 2b); only quarantine holds back.
    t = build_trial("decoy_x7", 5)
    assert classify(t, _response(t.sent, healed=False)).outcome == WRONG


def test_accounting_catches_a_lost_row_and_an_edited_quarantined_row():
    t = build_trial("removed_column", 6)
    lost = classify(t, _response([], quarantined=list(enumerate(t.sent))[1:], healed=False))
    assert lost.accounting_error and lost.outcome == WRONG
    edited = [(0, {**t.sent[0], "x": 1})] + list(enumerate(t.sent))[1:]
    assert classify(t, _response([], quarantined=edited, healed=False)).accounting_error


def test_a_clean_batch_is_a_false_alarm_on_any_finding():
    t = build_clean(0)
    assert classify_clean(t, _response(t.sent, drift=False)).false_alarm is None
    assert classify_clean(t, _response(t.sent, drift=True)).false_alarm
    assert classify_clean(t, _response(t.sent, healed=False, alerts=["duplicate"], drift=False)).false_alarm


def test_precision_counts_a_miss_and_never_an_abstention():
    t = build_trial("rename", 1)
    ok = classify(t, _response([dict(x) for x in t.expected]))
    held = classify(t, _response([], quarantined=list(enumerate(t.sent)), healed=False))
    miss = classify(t, _response(t.sent, drift=False))
    m = metrics([ok, held, miss])
    assert (m.correct, m.quarantined, m.wrong) == (1, 1, 1)
    assert m.precision == 0.5 and m.recall == pytest.approx(1 / 3)
