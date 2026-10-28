"""Audit round 10 — DAR execute sandboxing and ingestion publish integrity.

BUG-528: DAR execute_node ran LLM-written SQL with DuckDB external file access
         enabled and fetched every row.
BUG-529: the ingestion Kafka singleton ignored KAFKA_BOOTSTRAP_SERVERS.
BUG-530: the batch dedup key was the bare client batch_id, shared by tenants.
BUG-531: DLQ-routed / lost publishes were audited as 'ingestion_batch_processed'
         and a failed DLQ logged the whole ledger payload.

No Kafka broker exists locally (Tier B external dependency), so a fake class
with the AIOKafkaProducer start/stop/send_and_wait surface stands in for it,
as in test_ingestion_resilience.py. DuckDB is real.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys

import duckdb
import pytest
from aiokafka.errors import MessageSizeTooLargeError

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND)

from dar_service.graph import execute_node  # noqa: E402
from dar_service.schemas import DARState, ResearchQuestion  # noqa: E402
from ingestion_service import kafka_client as kc  # noqa: E402

# ── BUG-528 ───────────────────────────────────────────────────────────


def _dar_state(db_path: str, *sqls: str) -> DARState:
    return DARState(
        run_id="r", source_id="s", table_name="sales", duckdb_path=db_path,
        questions=[ResearchQuestion(question=f"q{i}", sql=s) for i, s in enumerate(sqls)],
    )


@pytest.fixture()
def lake(tmp_path):
    db = str(tmp_path / "lake.duckdb")
    con = duckdb.connect(db)
    con.execute("CREATE TABLE sales AS SELECT range AS id FROM range(10)")
    con.close()
    return db


def test_dar_sql_cannot_read_host_files(lake, tmp_path):
    secret = tmp_path / "secret.env"
    secret.write_text("OPENAI_API_KEY=sk-should-never-leak\n")
    path = secret.as_posix()

    out = execute_node(_dar_state(
        lake,
        # An injected question first tries to re-enable access; it must not stick.
        "SET enable_external_access=true",
        f"SELECT * FROM read_text('{path}')",
        f"SELECT * FROM read_csv('{path}')",
        "SELECT count(*) FROM sales",
    ))
    results = out["query_results"]

    for r in results[1:3]:
        assert r.error is not None, f"host file read succeeded: {r.rows}"
        assert r.rows == []
        assert "sk-should-never-leak" not in repr(r.rows)
    assert results[3].error is None and results[3].rows == [[10]]


def test_dar_result_rows_are_capped(lake):
    out = execute_node(_dar_state(lake, "SELECT * FROM range(200000)"))
    r = out["query_results"][0]
    assert r.error is None
    assert 0 < r.row_count <= 1000, r.row_count


# ── BUG-529 ───────────────────────────────────────────────────────────


def test_singleton_reads_kafka_bootstrap_servers_env():
    env = dict(os.environ, KAFKA_BOOTSTRAP_SERVERS="aura-kafka.svc:9093")
    out = subprocess.run(
        [sys.executable, "-c",
         "from ingestion_service.kafka_client import kafka_producer as p;"
         "print(p.bootstrap_servers)"],
        cwd=_BACKEND, env=env, capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "aura-kafka.svc:9093"


# ── BUG-530 / BUG-531 shared fixtures ─────────────────────────────────


class _Producer:
    fail_topics: set = set()
    error: Exception = MessageSizeTooLargeError("The message is too large")

    def __init__(self, **kwargs):
        self.sent = []

    async def start(self):
        pass

    async def stop(self):
        pass

    async def send_and_wait(self, topic, value=None, key=None):
        if topic in self.fail_topics:
            raise self.error
        self.sent.append((topic, value, key))


@pytest.fixture()
def ingest(monkeypatch):
    import ingestion_service.main as im

    _Producer.fail_topics = set()
    monkeypatch.setattr(kc, "AIOKafkaProducer", _Producer)
    producer = kc.ResilientKafkaProducer(bootstrap_servers="fake:9092")
    monkeypatch.setattr(im, "kafka_producer", producer)
    events = []
    monkeypatch.setattr(im, "audit_event", lambda kind, payload: events.append((kind, payload)))
    return im, producer, events


def _batch(im, tenant: str, batch_id: str, memo: str = "rent"):
    return im.RawIngestionPayload(
        tenant_id=tenant, batch_id=batch_id,
        entries=[{"internalId": "1", "debit": 10.0, "tranDate": "2026-10-21",
                  "memo": memo}],
    )


def _ledger_sends(producer):
    return [s for s in producer.producer.sent if s[0] == "aura.ledger.ingested"]


# ── BUG-530 ───────────────────────────────────────────────────────────


def test_same_batch_id_from_two_tenants_both_publish(ingest):
    im, producer, events = ingest
    asyncio.run(im.process_raw_batch_async(_batch(im, "tenant-a", "2026-10-21"), "NetSuite"))
    asyncio.run(im.process_raw_batch_async(_batch(im, "tenant-b", "2026-10-21"), "NetSuite"))

    assert [s[2] for s in _ledger_sends(producer)] == ["tenant-a", "tenant-b"]
    assert [k for k, _ in events] == ["ingestion_batch_processed"] * 2


def test_same_tenant_retry_is_still_deduped_and_not_audited_as_processed(ingest):
    im, producer, events = ingest
    asyncio.run(im.process_raw_batch_async(_batch(im, "tenant-a", "b1"), "NetSuite"))
    asyncio.run(im.process_raw_batch_async(_batch(im, "tenant-a", "b1"), "NetSuite"))

    assert len(_ledger_sends(producer)) == 1
    assert [k for k, _ in events] == [
        "ingestion_batch_processed", "ingestion_batch_duplicate_skipped"]


# ── BUG-531 ───────────────────────────────────────────────────────────


def test_dead_lettered_batch_is_audited_as_failed(ingest):
    im, producer, events = ingest
    _Producer.fail_topics = {"aura.ledger.ingested"}
    asyncio.run(im.process_raw_batch_async(_batch(im, "tenant-a", "b1"), "NetSuite"))

    assert [s[0] for s in producer.producer.sent] == ["aura.dlq.ledger"]
    kinds = [k for k, _ in events]
    assert kinds == ["ingestion_publish_failed"], kinds
    assert events[0][1]["dead_lettered"] is True


def test_oversized_batch_lost_to_dlq_too_is_failed_and_payload_not_logged(ingest, caplog):
    im, producer, events = ingest
    _Producer.fail_topics = {"aura.ledger.ingested", "aura.dlq.ledger"}
    with caplog.at_level(logging.DEBUG):
        asyncio.run(im.process_raw_batch_async(
            _batch(im, "tenant-a", "b1", memo="SENSITIVE-MEMO-7731"), "NetSuite"))

    assert producer.producer.sent == []
    kinds = [k for k, _ in events]
    assert kinds == ["ingestion_publish_failed"], kinds
    assert events[0][1]["dead_lettered"] is False
    assert "SENSITIVE-MEMO-7731" not in caplog.text
