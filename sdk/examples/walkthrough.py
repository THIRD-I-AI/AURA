"""
Notebook-style walkthrough — run this in a Jupyter cell or as a script.

In a Jupyter notebook, the final ``artifact`` evaluation will render as
a rich HTML card. From the command line, set AURA_BASE_URL and run:

    python examples/walkthrough.py
"""
from __future__ import annotations

import os

from aura_counterfactual import Client

SAMPLE_QUERY = {
    "question":  "What would Q3 revenue have been if we hadn't raised prices in May?",
    "treatment": {"column": "price_change_may", "actual": 0.08, "counterfactual": 0.0},
    "outcome":   {"column": "monthly_revenue", "agg": "sum",
                   "window": ["2025-07-01", "2025-09-30"]},
    "dag":       {"edges": [
        ["seasonality",       "monthly_revenue"],
        ["price_change_may",  "monthly_revenue"],
        ["seasonality",       "price_change_may"],
    ]},
    "dataset":   {"source_id": "uploaded_file:sales_2025.csv"},
    "audience":  "analyst",
}


def main() -> None:
    base_url = os.environ.get("AURA_BASE_URL", "http://localhost:8000")
    with Client(base_url=base_url) as c:
        info = c.info()
        print(f"Engine v{info.engine_version} · DoWhy={info.dowhy_available} · "
              f"signing={info.signing_available} · pdf={info.pdf_available}")

        print("\nSubmitting...")
        artifact = c.run(SAMPLE_QUERY, timeout_s=180.0)

        print(f"\n{artifact.confidence.upper()} confidence — "
              f"avg point estimate {artifact.average_point:+.2f}")
        for est in artifact.succeeded_estimators:
            print(f"  {est.method}: {est.point:+.4f} "
                  f"[{est.ci_lower:.3f}, {est.ci_upper:.3f}]")

        if artifact.high_severity_challenges:
            print("\nHigh-severity challenges from the adversarial critic:")
            for c_ in artifact.high_severity_challenges:
                print(f"  • {c_.text}")
                if c_.suggested_check:
                    print(f"    → {c_.suggested_check}")

        record_hash = artifact.audit_record_hash
        print(f"\naudit_record_hash: {record_hash}")
        print(f"signature_status:  {artifact.signature_status}")

        # Replay later — byte-identical
        again = c.replay(record_hash)
        assert again.audit_record_hash == record_hash
        print("✓ Replay byte-stable")

        # Verify signature
        v = c.verify(record_hash)
        print(f"✓ Signature verify: {v.verified} ({v.signature_status})")

        # If the deployment supports PDF, download
        if info.pdf_available:
            pdf = c.report_pdf(record_hash)
            with open("report.pdf", "wb") as f:
                f.write(pdf)
            print(f"✓ Wrote report.pdf ({len(pdf):,} bytes)")


if __name__ == "__main__":
    main()
