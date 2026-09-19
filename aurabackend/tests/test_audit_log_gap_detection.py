"""BUG-118 — a failed audit-log write must not vanish from the chain.

Before the fix, ``_AuditWriter.append`` caught ``OSError`` and only logged
it: ``self._prev_hash`` stayed unchanged, so the next successful record
chained cleanly from the last one that *did* land on disk, and
``verify_chain`` reported ``ok: True`` even though a compliance record was
lost. The fix stamps the next successful record with ``gap_before`` (how
many writes were dropped immediately before it) and has ``verify_chain``
surface those as ``gaps``, distinct from tamper ``failures``.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import audit_log


@pytest.fixture()
def enabled(monkeypatch):
    monkeypatch.setattr(audit_log, "AUDIT_ENABLED", True)


def _flaky_open(monkeypatch, fail_on_calls: set[int]):
    """Make ``Path.open`` raise OSError on the given 1-indexed call numbers,
    delegating to the real implementation otherwise."""
    orig_open = pathlib.Path.open
    calls = {"n": 0}

    def patched(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] in fail_on_calls:
            raise OSError("simulated disk write failure")
        return orig_open(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "open", patched)
    return calls


def _today_path(tmp_path, tag: str) -> pathlib.Path:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return tmp_path / f"audit-{tag}-{day}.jsonl"


def test_dropped_write_does_not_reach_disk(tmp_path, monkeypatch, enabled):
    _flaky_open(monkeypatch, fail_on_calls={1})
    writer = audit_log._AuditWriter(tmp_path, "gap-test")

    writer.append("event", {"i": 1})  # this write fails

    path = _today_path(tmp_path, "gap-test")
    assert not path.exists() or path.read_text() == ""


def test_next_successful_record_carries_gap_before(tmp_path, monkeypatch, enabled):
    _flaky_open(monkeypatch, fail_on_calls={1})
    writer = audit_log._AuditWriter(tmp_path, "gap-test")

    writer.append("event", {"i": 1})  # dropped
    writer.append("event", {"i": 2})  # succeeds

    path = _today_path(tmp_path, "gap-test")
    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert len(lines) == 1, "the dropped write must not have landed on disk"
    assert lines[0]["payload"]["i"] == 2
    assert lines[0]["gap_before"] == 1


def test_gap_counter_resets_after_being_reported(tmp_path, monkeypatch, enabled):
    # Two drops back to back, then a success — gap_before must reflect the
    # full run of 2 drops, and a subsequent success must not repeat it.
    _flaky_open(monkeypatch, fail_on_calls={1, 2})
    writer = audit_log._AuditWriter(tmp_path, "gap-test")

    writer.append("event", {"i": 1})  # dropped
    writer.append("event", {"i": 2})  # dropped
    writer.append("event", {"i": 3})  # succeeds, gap_before == 2
    writer.append("event", {"i": 4})  # succeeds, no gap_before

    path = _today_path(tmp_path, "gap-test")
    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert [rec["payload"]["i"] for rec in lines] == [3, 4]
    assert lines[0]["gap_before"] == 2
    assert "gap_before" not in lines[1]


def test_clean_run_has_no_gap_before_and_unchanged_hash_format(tmp_path, monkeypatch, enabled):
    writer = audit_log._AuditWriter(tmp_path, "gap-test")
    writer.append("event", {"i": 1})

    path = _today_path(tmp_path, "gap-test")
    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert "gap_before" not in lines[0]

    report = audit_log.verify_chain(path)
    assert report["ok"] is True
    assert report["gaps"] == []


def test_verify_chain_surfaces_the_gap_without_flagging_tamper(tmp_path, monkeypatch, enabled):
    _flaky_open(monkeypatch, fail_on_calls={1})
    writer = audit_log._AuditWriter(tmp_path, "gap-test")
    writer.append("event", {"i": 1})  # dropped
    writer.append("event", {"i": 2})  # succeeds, carries gap_before=1

    path = _today_path(tmp_path, "gap-test")
    report = audit_log.verify_chain(path)
    assert report["ok"] is True, "a reported gap is not tampering; the chain itself is intact"
    assert report["failures"] == []
    assert report["gaps"] == [{"line": 1, "dropped_records": 1}]
