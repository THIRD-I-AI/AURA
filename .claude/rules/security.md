---
description: Mission-critical security invariants — injection, data isolation, secrets in logs, crypto
paths:
  - "aurabackend/**"
  - "frontend/src/**"
  - "deploy/**"
  - "infrastructure/**"
---

# Security Rules

Background: this repo already carries real, closed CodeQL findings for several
of these classes (see `docs/SPRINTS.md` — Sec-2 through Sec-8). Treat a
regression into any of them as a HIGH-severity bug, not a style nit.

- **Strict parameter binding** — raw string concatenation into a SQL/DDL
  statement is prohibited. Use bound parameters, or the one shared quoter at
  `aurabackend/shared/sql_identifiers.py` when an identifier (not a value) must
  be spliced in (see `backend.md`).
- **Least-privilege scope** — internal APIs return only the caller's own
  tenant's data. Never proxy a broad record dump or a raw upstream/engine
  error string to a client consumer; sanitize through
  `aurabackend/shared/error_handler.py::sanitize_error` (or its frontend
  equivalent) first.
- **Redaction enforcer** — stdout, terminal traces, and log lines must mask PII
  and auth strings (tokens, API keys, session secrets). Never `print`/`log` a
  raw `Authorization` header, password, or signing key.
- **Secure memory lifecycle** — after a decryption/signing operation, drop the
  local variable holding the raw secret (`del`, or let it go out of scope
  immediately) rather than keeping it alive longer than the operation needs.
- **Explicit cryptographic paths** — use vetted primitives (AES-256-GCM,
  Argon2id, ED25519 — matching what's already in use, see `S19`/`S34a` in
  `docs/SPRINTS.md`). Never hand-roll a hash/cipher construction.
