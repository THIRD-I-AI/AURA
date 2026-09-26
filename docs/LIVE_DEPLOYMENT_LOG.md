# Live deployment ledger

Target: `https://dataaura.duckdns.org`. One row per `scripts/verify_live_deployment.py --append-log` run.
`Fixes live` counts the `fix:` probes that PASS against the deployed system; `NOT live` are merged bug
fixes the deployed system does not yet exhibit (not deployed, or regressed).

| Run (UTC) | Features | Fixes live | Live | NOT live | Skipped |
|---|---|---|---|---|---|
| 2026-09-26 14:03 UTC | 14/14 | 0/5 | - | bug196_dashboard_fs_locked, bug179_oversized_upload_413, bug188_preview_limit_400, bug174_170_connection_settings, bug146_xlsx_profiles | - |
| 2026-09-26 15:42 UTC (build absent) | 14/14 | 5/5 | bug196_dashboard_fs_locked, bug179_oversized_upload_413, bug188_preview_limit_400, bug174_170_connection_settings, bug146_xlsx_profiles | - | - |
