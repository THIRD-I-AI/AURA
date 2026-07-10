"""
End-to-end walkthrough of the UASR client against a running service.

Start the service first (see the repo README / docker-compose), then:

    python uasr_sdk/examples/walkthrough.py --url http://localhost:8000

It registers a baseline, sends a healthy batch (accepted), then a
x100 unit-bug batch (detected as drift), and prints the metrics + audit.
"""
from __future__ import annotations

import argparse

from uasr_client import UASRClient, UASRConnectionError


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--source", default="orders_demo")
    args = ap.parse_args()

    healthy = [{"amount": float(x)} for x in range(500)]
    unit_bug = [{"amount": float(x) * 100} for x in range(500)]  # cents-as-dollars

    try:
        with UASRClient(args.url) as uasr:
            print("deployment:", uasr.deployment().model_dump())

            base = uasr.register_baseline(args.source, healthy)
            print(f"baseline: {base.row_count} rows, ref={base.reference_version}")

            clean = uasr.ingest(args.source, healthy, batch_id="healthy-1")
            print(f"healthy batch -> drift_detected={clean.drift_detected}")

            bad = uasr.ingest(args.source, unit_bug, batch_id="unitbug-1")
            print(f"unit-bug batch -> drift_detected={bad.drift_detected}, "
                  f"severity={bad.severity}, shim_deployed={bad.shim_deployed}")

            print("metrics:", uasr.metrics().model_dump())
            print("sources:", [s.model_dump() for s in uasr.sources()])
    except UASRConnectionError as e:
        raise SystemExit(f"Could not reach the UASR service at {args.url}: {e}")


if __name__ == "__main__":
    main()
