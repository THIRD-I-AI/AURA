"""BUG-212: the causal endpoint must not run its blocking work on the event loop."""
import threading

import pytest

from causal_service import main as causal_main
from causal_service.models import CausalDiscoverRequest, DataSource


def _rows(n, seed=0):
    import random

    r = random.Random(seed)
    return [{"a": r.random(), "y": r.random()} for _ in range(n)]


@pytest.mark.asyncio
async def test_load_and_attribute_run_off_the_event_loop_thread(monkeypatch):
    loop_thread = threading.get_ident()
    seen = {}
    real_load, real_attribute = causal_main._load, causal_main.attribute

    def load(src, name):
        seen.setdefault("load", set()).add(threading.get_ident())
        return real_load(src, name)

    def attribute(*a, **k):
        seen["attribute"] = threading.get_ident()
        return real_attribute(*a, **k)

    monkeypatch.setattr(causal_main, "_load", load)
    monkeypatch.setattr(causal_main, "attribute", attribute)

    req = CausalDiscoverRequest(
        target_metric="y",
        training_data=DataSource(rows=_rows(60)),
        anomaly_data=DataSource(rows=_rows(5, seed=1)),
        method="correlation",
        enforce_stationarity=False,
    )
    res = await causal_main.causal_discover(req)

    assert res.attributions is not None
    assert loop_thread not in seen["load"], "data loading ran on the event loop"
    assert seen["attribute"] != loop_thread, "attribution ran on the event loop"
