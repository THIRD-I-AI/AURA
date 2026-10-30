"""ADR-002 phase 2a item 4: freshness for scheduled work.

The gateway's only in-process scheduler is the saved-query ticker
(api_gateway/routers/queries.py); scheduler_service is not deployed in free tier and
Pipeline.schedule is never executed. So freshness is enforced on saved-query schedules:

- every scheduled run that succeeds records last_success_at;
- a periodic check marks a slot late once now > last_success + interval + grace
  (grace = max(50% of the interval, 5 minutes)), publishes a ``late`` event that the
  webhook dispatcher maps to ``query.late``, and re-runs the query once for that slot;
- the slot is persisted, so a restart (or a second worker) never re-runs it again;
- a paused query, or one with no schedule, never alerts;
- the event carries the owning workspace, so only that tenant's webhooks receive it.

The clock is injected by replacing ``queries._utcnow``; queries run for real against
uploaded DuckDB files and the database is a per-test SQLite file.
"""
from __future__ import annotations

import asyncio
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from starlette.requests import Request

from api_gateway import persistence
from api_gateway.routers import queries
from shared.streaming_manager import streaming_manager

T0 = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)
HOURLY = {"interval": "hourly", "hour": 9, "minute": 0, "day_of_week": None, "enabled": True}
GOOD_SQL = "SELECT * FROM sales"
BAD_SQL = "SELECT * FROM table_that_does_not_exist"


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path / "uploads"))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    monkeypatch.setenv("GATEWAY_DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'gw.db').as_posix()}")
    from shared.storage import get_storage_backend, reset_storage_backend

    reset_storage_backend()
    get_storage_backend().write("acme", "sales.csv", b"region,amount\nEU,10\nUS,20\n")
    _reset_process_state()
    clock = _Clock(T0)
    monkeypatch.setattr(queries, "_utcnow", clock)
    yield clock
    _reset_process_state()
    reset_storage_backend()


def _reset_process_state() -> None:
    """What a gateway restart loses: the engine and the in-memory run history."""
    persistence._engine = None
    persistence._session_factory = None
    persistence._schema_initialized = False
    with queries._saved_query_runs_lock:
        queries._saved_query_runs_store.clear()


async def _restart() -> None:
    await persistence.close_database()
    _reset_process_state()


async def _insert(workspace_id: str, sql: str, schedule, **extra) -> str:
    qid = f"sq_{uuid.uuid4().hex[:10]}"
    await persistence.insert_saved_query({
        "id": qid, "workspace_id": workspace_id, "name": f"q-{qid}", "sql": sql,
        "created_at": T0.isoformat(), "created_ts": T0.timestamp(), "updated_at": T0.isoformat(),
        "schedule": schedule, **extra,
    })
    return qid


def _runs(qid: str):
    with queries._saved_query_runs_lock:
        return list(queries._saved_query_runs_store.get(qid, []))


def _reruns(qid: str):
    return [r for r in _runs(qid) if r.get("trigger") == "freshness_rerun"]


def _late_marks(qid: str):
    return [r for r in _runs(qid) if r.get("status") == "late"]


def _drain(queue) -> list:
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


def _run(scenario):
    async def _wrapped():
        try:
            await scenario()
        finally:
            await persistence.close_database()

    asyncio.run(_wrapped())


def test_an_on_time_query_never_alerts(env):
    async def scenario():
        sub_id, events = streaming_manager.subscribe("query:*")
        try:
            qid = await _insert("acme", GOOD_SQL, HOURLY,
                                schedule_set_at=T0.isoformat(),
                                next_run_at=(T0 + timedelta(hours=1)).isoformat())
            # A real tick every 30 s for five hours, as _scheduler_loop runs it.
            while env.now < T0 + timedelta(hours=5):
                env.now += timedelta(seconds=30)
                await queries._fire_due_saved_queries()
                await queries._check_saved_query_freshness()
            record = await persistence.get_saved_query(qid, "acme")
            assert record["last_success_at"] == (T0 + timedelta(hours=5)).isoformat()
            # Exactly at the next deadline (last success + 1h + 30m grace) is still on time.
            env.now = T0 + timedelta(hours=6, minutes=30)
            await queries._check_saved_query_freshness()
            assert [e for e in _drain(events) if e.event_type == "late"] == []
            assert _late_marks(qid) == [] and _reruns(qid) == []
            assert len([r for r in _runs(qid) if r["status"] == "success"]) == 5
        finally:
            streaming_manager.unsubscribe(sub_id)

    _run(scenario)


def test_a_late_query_alerts_once_and_reruns_once_per_missed_slot(env):
    async def scenario():
        sub_id, events = streaming_manager.subscribe("query:*")
        try:
            qid = await _insert("acme", BAD_SQL, HOURLY, last_success_at=T0.isoformat())

            # Deadline is last_success + 1h + 30m grace: exactly at it is not late yet.
            env.now = T0 + timedelta(hours=1, minutes=30)
            await queries._check_saved_query_freshness()
            assert _drain(events) == [] and _runs(qid) == []

            env.now = T0 + timedelta(hours=1, minutes=30, seconds=1)
            await queries._check_saved_query_freshness()
            env.now = T0 + timedelta(hours=1, minutes=45)
            await queries._check_saved_query_freshness()
            await queries._check_saved_query_freshness()

            late = [e for e in _drain(events) if e.event_type == "late"]
            assert len(late) == 1
            assert late[0].topic == f"query:{qid}" and late[0].workspace_id == "acme"
            assert late[0].payload["slot"] == (T0 + timedelta(hours=1)).isoformat()
            assert late[0].payload["last_success_at"] == T0.isoformat()
            assert len(_late_marks(qid)) == 1
            reruns = _reruns(qid)
            assert len(reruns) == 1 and reruns[0]["status"] == "failed"

            # The next slot is missed too: it is its own slot, alerted and re-run once.
            env.now = T0 + timedelta(hours=2, minutes=31)
            await queries._check_saved_query_freshness()
            await queries._check_saved_query_freshness()
            late = [e for e in _drain(events) if e.event_type == "late"]
            assert [e.payload["slot"] for e in late] == [(T0 + timedelta(hours=2)).isoformat()]
            assert len(_reruns(qid)) == 2
        finally:
            streaming_manager.unsubscribe(sub_id)

    _run(scenario)


def test_a_successful_rerun_makes_the_query_fresh_again(env):
    async def scenario():
        qid = await _insert("acme", GOOD_SQL, HOURLY, last_success_at=T0.isoformat())
        env.now = T0 + timedelta(hours=1, minutes=31)
        await queries._check_saved_query_freshness()
        assert [r["status"] for r in _reruns(qid)] == ["success"]
        record = await persistence.get_saved_query(qid, "acme")
        assert record["last_success_at"] == env.now.isoformat()

        env.now = T0 + timedelta(hours=2, minutes=40)
        await queries._check_saved_query_freshness()
        assert len(_late_marks(qid)) == 1 and len(_reruns(qid)) == 1

    _run(scenario)


def test_a_restart_never_reruns_the_same_slot_twice(env):
    async def scenario():
        qid = await _insert("acme", BAD_SQL, HOURLY, last_success_at=T0.isoformat())
        env.now = T0 + timedelta(hours=1, minutes=31)
        await queries._check_saved_query_freshness()
        first = len(_reruns(qid))

        await _restart()
        sub_id, events = streaming_manager.subscribe("query:*")
        try:
            await queries._check_saved_query_freshness()
            env.now = T0 + timedelta(hours=1, minutes=59)
            await queries._check_saved_query_freshness()
            assert first == 1
            assert _reruns(qid) == [] and _late_marks(qid) == []
            assert [e for e in _drain(events) if e.event_type == "late"] == []
            record = await persistence.get_saved_query(qid, "acme")
            assert record["late_slot_at"] == (T0 + timedelta(hours=1)).isoformat()
        finally:
            streaming_manager.unsubscribe(sub_id)

    _run(scenario)


def test_two_workers_checking_at_once_rerun_a_slot_once(env):
    async def scenario():
        qid = await _insert("acme", BAD_SQL, HOURLY, last_success_at=T0.isoformat())
        env.now = T0 + timedelta(hours=1, minutes=31)
        await asyncio.gather(*(queries._check_saved_query_freshness() for _ in range(4)))
        assert len(_reruns(qid)) == 1

    _run(scenario)


def test_a_paused_or_unscheduled_query_never_alerts(env):
    async def scenario():
        sub_id, events = streaming_manager.subscribe("query:*")
        try:
            paused = await _insert("acme", BAD_SQL, {**HOURLY, "enabled": False},
                                   last_success_at=T0.isoformat())
            unscheduled = await _insert("acme", BAD_SQL, None, last_success_at=T0.isoformat())
            for days in (1, 3, 10):
                env.now = T0 + timedelta(days=days)
                await queries._check_saved_query_freshness()
            assert [e for e in _drain(events) if e.event_type == "late"] == []
            assert _runs(paused) == [] and _runs(unscheduled) == []
        finally:
            streaming_manager.unsubscribe(sub_id)

    _run(scenario)


def test_re_enabling_a_long_paused_query_restarts_the_freshness_clock(env):
    async def scenario():
        sub_id, events = streaming_manager.subscribe("query:*")
        try:
            qid = await _insert("acme", BAD_SQL, {**HOURLY, "enabled": False},
                                last_success_at=T0.isoformat())
            env.now = T0 + timedelta(days=3)
            request = Request({"type": "http", "headers": [], "state": {"user": {"org_id": "acme"}}})
            await queries.set_saved_query_schedule(qid, queries.SavedQuerySchedule(**HOURLY), request)

            await queries._check_saved_query_freshness()
            env.now += timedelta(hours=1, minutes=30)
            await queries._check_saved_query_freshness()
            assert [e for e in _drain(events) if e.event_type == "late"] == []

            env.now += timedelta(seconds=1)
            await queries._check_saved_query_freshness()
            assert len([e for e in _drain(events) if e.event_type == "late"]) == 1
        finally:
            streaming_manager.unsubscribe(sub_id)

    _run(scenario)


def test_a_query_scheduled_before_freshness_existed_starts_its_clock_when_first_seen(env):
    # Rows written before last_success_at existed carry no reference; alerting on them
    # would be a guess, so the first check starts the clock instead.
    async def scenario():
        qid = await _insert("acme", BAD_SQL, HOURLY)
        env.now = T0 + timedelta(days=30)
        await queries._check_saved_query_freshness()
        assert _runs(qid) == []
        env.now += timedelta(hours=1, minutes=31)
        await queries._check_saved_query_freshness()
        assert len(_late_marks(qid)) == 1

    _run(scenario)


@pytest.mark.parametrize("interval,period,grace", [
    ("hourly", timedelta(hours=1), timedelta(minutes=30)),
    ("daily", timedelta(days=1), timedelta(hours=12)),
    ("weekly", timedelta(days=7), timedelta(days=3, hours=12)),
])
def test_grace_is_half_the_interval(interval, period, grace):
    assert queries._freshness_grace(period) == grace
    assert queries._SCHEDULE_PERIODS[interval] == period


def test_grace_is_never_under_five_minutes():
    assert queries._freshness_grace(timedelta(minutes=2)) == timedelta(minutes=5)


class _Receiver(BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        type(self).received.append((self.path, self.headers.get("X-AURA-Event"), json.loads(body)))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


def test_the_late_webhook_reaches_only_the_owning_tenant(env, tmp_path, monkeypatch):
    from shared import webhook_dispatcher as wd

    monkeypatch.setattr(wd, "_DATA_DIR", str(tmp_path / "webhooks"))
    monkeypatch.setattr(wd, "_STORE_PATH", str(tmp_path / "webhooks" / "subscriptions.json"))
    _Receiver.received = []
    server = HTTPServer(("127.0.0.1", 0), _Receiver)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    async def scenario():
        dispatcher = wd.WebhookDispatcher()
        dispatcher.register("acme", f"{base}/acme", ["query.late"], retries=0)
        dispatcher.register("globex", f"{base}/globex", ["query.*", "*"], retries=0)
        globex_sub, globex_events = streaming_manager.subscribe("query:*", workspace_id="globex")
        await dispatcher.start()
        try:
            late = await _insert("acme", BAD_SQL, HOURLY, last_success_at=T0.isoformat())
            await _insert("globex", BAD_SQL, HOURLY,
                          last_success_at=(T0 + timedelta(hours=1)).isoformat())
            env.now = T0 + timedelta(hours=1, minutes=31)
            await queries._check_saved_query_freshness()
            for _ in range(100):
                if dispatcher.deliveries("acme"):
                    break
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.3)
            assert [(p, e) for p, e, _ in _Receiver.received] == [("/acme", "query.late")]
            assert _Receiver.received[0][2]["data"]["saved_query_id"] == late
            assert dispatcher.deliveries("globex") == []
            assert [e for e in _drain(globex_events) if e.event_type == "late"] == []
        finally:
            streaming_manager.unsubscribe(globex_sub)
            await dispatcher.stop()

    try:
        _run(scenario)
    finally:
        server.shutdown()


def test_query_late_is_in_the_webhook_event_catalog():
    # The UI's event picker (WebhooksPanel) is built from GET /webhooks/events,
    # so a tenant can only subscribe to query.late directly if it is listed.
    from api_gateway.routers import webhooks

    body = asyncio.run(webhooks.list_known_events())
    assert "query.late" in body["events"]
