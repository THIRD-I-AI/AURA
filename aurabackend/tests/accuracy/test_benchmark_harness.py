"""The ADR-002 benchmark's own ground truth and scoring, checked without the healer.

A benchmark that scored a missed fault as a pass, or whose "clean" data already held
duplicates, would report precision it never measured. These pin the generator, the
injectors and the classifier on hand-built responses.
"""
from __future__ import annotations

import pytest

from .datasets import make_dataset, strict_equal
from .faults import FAULT_TYPES, INJECTORS, build_clean, build_trial
from .harness import CORRECT, QUARANTINED, WRONG, applied, classify, classify_clean, metrics, scores


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


def test_applied_fix_precision_counts_every_flagged_batch_that_returned_rows():
    """(a): a batch the healer flagged and returned rows from counts, right or wrong;
    a silent miss and a full quarantine do not -- (b) and (c) carry those."""
    t = build_trial("decoy_x7", 5)
    ok = classify(t, _response([e for e in t.expected if e is not None],
                               quarantined=[(i, t.sent[i]) for i, e in enumerate(t.expected) if e is None]))
    flagged_but_passed = classify(t, _response(t.sent, healed=False))
    silent = classify(t, _response(t.sent, drift=False))
    held = classify(t, _response([], quarantined=list(enumerate(t.sent)), healed=False))
    a = applied([ok, flagged_but_passed, silent, held])
    assert (a.batches, a.correct, a.wrong) == (2, 1, 1) and a.precision == 0.5
    assert flagged_but_passed.wrong_kind == "applied" and silent.wrong_kind == "silent miss"
    assert held.wrong_kind is None and not held.applied


def test_a_flagged_batch_whose_rows_are_the_truth_but_misses_an_alert_is_strictly_wrong_only():
    t = build_trial("null_spike", 4)
    r = classify(t, _response(t.sent, healed=True, alerts=[], drift=True))
    assert r.outcome == WRONG and r.wrong_kind == "missed alert"
    assert r.applied_ok and applied([r]).precision == 1.0
    s = scores([r], [])
    assert s["b_strict_outcome_accuracy"]["wrong_by_kind"]["missed alert"] == 1
    assert s["b_strict_outcome_accuracy"]["accuracy"] == 0.0
    assert s["a_applied_fix_precision"]["precision"] == 1.0


# ── BUG-577: real sources ─────────────────────────────────────────────────────────


def test_a_real_trial_is_rebuilt_exactly_from_its_seed_and_its_batch_avoids_the_baseline():
    from .realdata import load, make_real_dataset

    a, b = build_trial("scale_up", 4, "compas"), build_trial("scale_up", 4, "compas")
    assert strict_equal(a.sent, b.sent) and a.expected == b.expected and a.detail == b.detail
    assert a.dataset.shape == "real compas"
    ds = make_real_dataset("compas", 11)
    assert len(ds.baseline_positions) == len(ds.baseline)
    assert len(ds.baseline) + len(ds.truth) <= len(load("compas")[1])


def test_a_real_clean_batch_is_cut_to_the_rows_the_source_has():
    from .realdata import load

    for seed in range(20):
        t = build_clean(seed, "compas")
        assert len(t.dataset.baseline) + len(t.sent) <= len(load("compas")[1])


def test_a_decoy_leaves_a_numerically_unchanged_value_expected():
    # x2.5 sends an int 0 as 0.0: the lossless cast back is the truth, so that row is
    # not voided -- and 0.0 returned for it is still WRONG (strict types).
    from .faults import Trial

    t = build_trial("decoy_x7", 0)
    probe = Trial(fault="probe", seed=0, dataset=t.dataset, sent=[{"c": 0.0}, {"c": 7}],
                  expected=[{"c": 0}, {"c": 1}], snapshot=None, tenant=None)
    probe.void_changed("c", [0, 1])
    assert probe.expected == [{"c": 0}, None]
    assert classify(probe, _response([{"c": 0.0}], quarantined=[(1, {"c": 7})])).outcome == WRONG
    assert classify(probe, _response([{"c": 0}], quarantined=[(1, {"c": 7})])).outcome == CORRECT


# ── BUG-577: bounded memory -- the realism lanes once grew to 7.4 GB ──────────────


def test_a_scored_result_keeps_no_rows_of_its_trial_or_response():
    # A Result held the whole Trial and the handler's response: ~3.8 MB per trial on a
    # 65-column source, kept for every trial of a lane. It keeps what the reports read.
    t = build_trial("rename", 1)
    r = classify(t, _response([dict(x) for x in t.expected]))
    assert (r.trial.fault, r.trial.seed, r.trial.detail, r.trial.case_class) == (
        t.fault, t.seed, t.detail, t.case_class)
    assert r.trial.dataset.shape == t.dataset.shape and r.trial.dataset.name == t.dataset.name
    assert not hasattr(r, "response")
    assert not any(hasattr(r.trial, a) for a in ("sent", "expected", "baseline", "truth"))
    assert not hasattr(r.trial.dataset, "baseline")


def test_a_cold_start_trial_leaves_no_state_of_its_source_behind():
    # Each trial is its own source, and the healer keeps every source's baseline,
    # locks and canary state for the life of the process (BUG-578, ~240 KB for a
    # 65-column source): 10,000 trials would carry 10,000 dead sources. heal()
    # forgets the source once its batch is scored.
    from uasr import service

    from .harness import _PER_SOURCE_HOLDERS, heal, run_isolated

    def held():
        n = len(service._detector._store) if hasattr(service._detector._store, "__len__") else 0
        for name in _PER_SOURCE_HOLDERS:
            n += sum(len(v) for v in vars(getattr(service, name)).values() if isinstance(v, (dict, set)))
        return n

    async def go():
        before = held()
        for seed in range(4):
            for fault in ("scale_up", "rename"):
                t = build_trial(fault, seed)
                classify(t, await heal(t))
            t = build_clean(seed)
            classify_clean(t, await heal(t))
        return before, held()

    (before, after), _ = run_isolated(go)
    assert after == before
