"""One-shot demo pre-warm: compute + sign + persist every demo scenario's
audit artifact, so the gateway can serve the instant /demo path by loading
the JSON (no in-process audit that would starve its event loop).

Run at deploy time (or whenever scenarios/data change), in its OWN process so
the GIL-bound dowhy work never touches a request-serving loop:

    python -m counterfactual_service.warm_demos

Writes to ``$AURA_DEMO_ARTIFACT_DIR`` (default ``data/demo_artifacts/``) and
persists each artifact to the audit store so /verify and /report.pdf resolve.
The signature uses the same persisted ED25519 key the gateway loads, so the
loaded artifact verifies in that deployment.
"""
from __future__ import annotations

import asyncio
import sys

from counterfactual_service.main import (
    _DEMO_ARTIFACT_DIR,
    prewarm_demo_scenarios,
)


def main() -> int:
    asyncio.run(prewarm_demo_scenarios())
    artifacts = sorted(p.name for p in _DEMO_ARTIFACT_DIR.glob("*.json")) if _DEMO_ARTIFACT_DIR.exists() else []
    print(f"warmed {len(artifacts)} demo artifact(s) -> {_DEMO_ARTIFACT_DIR}: {artifacts}")
    return 0 if artifacts else 1


if __name__ == "__main__":
    sys.exit(main())
