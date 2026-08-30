---
description: Architecture efficiency — batching, lazy hydration, serialization, caching, chunking
paths:
  - "aurabackend/pipeline/**"
  - "aurabackend/uasr/**"
  - "aurabackend/connectors/**"
  - "aurabackend/api_gateway/**"
  - "aurabackend/counterfactual_service/**"
---

# Performance Rules

- **Fast pipeline processing** — prefer one batched bulk call over a loop of
  isolated per-row calls when parsing dense documents or relational tables.
- **Lazy data hydration** — postpone fetching/assembling a heavy resource until
  the step that actually needs it, not at construction time.
- **Lightweight serialization** — use a fast binary format (Parquet, protobuf)
  for internal transfers instead of nested JSON/text where the shape is
  large or repeated.
- **Cache-aware computation** — memoize immutable/deterministic results in a
  local key-value lookup rather than recomputing or re-querying them (see the
  gateway's schema-context and file-metadata caches, `P-2b`/`P-2a` in
  `docs/SPRINTS.md`, for the established pattern in this repo).
- **Memory-safe chunking** — stream large incoming datasets through bounded
  iterative buffers; don't load a full body into memory when a chunked read
  is available.
