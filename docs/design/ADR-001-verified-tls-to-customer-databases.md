# ADR-001: Verified TLS to customer databases

- **Status:** accepted, 2026-10-29. §1–§5 shipped for the Postgres and MySQL connectors (BUG-560); §6 (fingerprint and lock state in the UI) and the streaming sink (BUG-557) are next.
- **Scope:** layer 1 of the secure-pipeline plan. Layer 2 (service identity inside AURA) and layer 3
  (signed run receipts feeding UASR) get their own ADRs.
- **Fixes:** BUG-560. The connection form's "Use SSL" switch is stored but never used, so every
  Postgres and MySQL connection, including its password, is sent in plaintext.

## Context

- `connectors/registry.py` offers an `ssl` field. `gateway_connections.ssl` stores it, and
  `api_gateway/persistence.py` reads it back.
- `connectors/base.py::ConnectorConfig` has no TLS field, and `postgresql_connector.py` and
  `mysql_connector.py` call `asyncpg.create_pool` and `aiomysql.create_pool` without `ssl`. The
  switch is dropped on the way to the driver.
- BUG-547 now connects to the IP address that passed the SSRF check, not the name, so DNS
  rebinding cannot redirect the connection. Both drivers verify the server certificate against the
  host they were given (asyncpg `connect_utils.py` and aiomysql `connection.py` pass
  `server_hostname=host`). With a pinned IP, a normal verifying context checks the certificate
  against the IP and fails for every real database certificate.

## Decision

### 1. Connect to the pinned IP, verify the certificate as the original name

A small `ssl.SSLContext` subclass, `PinnedNameContext` in `shared/db_tls.py`, overrides
`wrap_bio` and `wrap_socket` to replace whatever `server_hostname` the driver passes with the name
the customer entered. The TCP connection still goes to the vetted IP (BUG-547 keeps holding), while
the certificate check and the SNI sent to the server use the real name.

A prototype run on 2026-10-29 used a local TLS server with a certificate for `db.customer.test`,
connected through `127.0.0.1`:

| Case | Result |
|---|---|
| Expected name `db.customer.test` | Verified. SNI sent: `db.customer.test`. |
| Expected name `evil.test` | Refused: hostname mismatch. |
| Control: plain context given the IP | Refused: IP address mismatch (the problem this solves). |

### 2. Modes, following libpq's `sslmode`

| Mode | Encrypts | Verifies chain | Verifies name | When |
|---|---|---|---|---|
| `disable` | no | no | no | Explicit choice only; the UI warns. Allowed only for hosts permitted by `AURA_CONNECTORS_ALLOW_PRIVATE_HOSTS`. |
| `require` | yes | no | no | For self-signed servers whose CA the customer cannot supply. Labelled "encrypted, not verified". |
| `verify-full` | yes | yes | yes | **Default** whenever TLS is on, and the value the old `ssl: true` maps to. |

- No `verify-ca` mode: a chain check without a name check adds little over `require`.
- Minimum TLS version is 1.2. Python's default ciphers are kept.

### 3. Trust and client identity

- **Trust:** the system trust store (certifi in the image), plus an optional customer CA bundle in
  PEM. The bundle covers managed databases with private CAs (RDS, Cloud SQL, Azure).
- **mTLS:** an optional client certificate and key, for customers who require AURA to present one.
- **Storage:** all three go into the existing Fernet-encrypted `config_encrypted` JSON as `tls_mode`,
  `tls_ca_pem`, `tls_client_cert_pem` and `tls_client_key_pem`. No new columns, so no migration.
  The client key is handled like the password: it is never returned by the API (the API answers
  `has_client_key: true`) and never logged (`security.md`).
- **Hygiene:** PEM is parsed and validated on save, rejecting bad PEM or an expired certificate with
  a 400. Any temp file that `ssl.SSLContext.load_cert_chain` needs is written 0600 and deleted
  straight after the load.

### 4. The existing `ssl` column

- Kept as is. `ssl = true` with no `tls_mode` reads as `verify-full`; `ssl = false` with no
  `tls_mode` reads as today's plaintext.
- Plaintext stays the effective default for existing connections, so nothing breaks on deploy. It is
  surfaced instead: see §6.
- New connections default to `verify-full`.

### 5. Where it applies

- **Postgres:** `create_pool(ssl=ctx)`. With `direct_tls=False`, asyncpg sends the SSLRequest
  upgrade. The pinned context also covers the `connection_string` path when the DSN host is pinned.
- **MySQL:** `create_pool(ssl=ctx)`.
- **Also wired here:** the streaming database sink. BUG-557 is fixed here too, because the sink must
  connect to the pinned address anyway.
- **Not in scope:** BigQuery, which is always TLS through Google's client, and DuckDB and FAISS,
  which are local.

### 6. What the connection exposes

- The test-connection response returns the negotiated TLS version, the cipher, and the SHA-256
  fingerprint and expiry date of the server certificate.
- The connections list shows a lock state: verified, encrypted-not-verified, or **plaintext**.
- These fingerprints are the input layer 3 records in each run receipt. A changed fingerprint
  between runs then becomes a signal UASR can act on.

### 7. Errors name the true cause

Each failure gets its own policy-error message, sanitized through `sanitize_error`:

- certificate expired
- name mismatch
- unknown CA
- server does not support TLS
- TLS required by the server

## Consequences

- **Gains:**
  - Credentials and query results are encrypted in transit when TLS is on.
  - A man-in-the-middle cannot pose as the database under `verify-full`.
  - BUG-547's rebinding protection holds under TLS.
- **Costs:**
  - A customer whose database has a private CA must upload the bundle, or explicitly pick
    `require`.
  - Existing plaintext connections keep working but are flagged.
- **Risk:** `PinnedNameContext` relies on asyncio's `SSLProtocol` calling `context.wrap_bio(...)`.
  CPython 3.11–3.13 do this. It is checked against real servers in CI (below), so a change in a
  later Python shows up as a test failure, not a silent downgrade.

## Verification plan

1. **Tier A unit tests:**
   - `PinnedNameContext` against a local TLS server: right name verified, wrong name refused,
     expired certificate refused, untrusted CA refused, client certificate presented.
   - Mode mapping from the old `ssl` value.
   - PEM validation on save.
   - The client key never appears in API responses or logs.
2. **Tier B integration lane** (new CI job, per `testing.md`):
   - Postgres and MySQL containers with TLS enabled from a test CA.
   - `verify-full` connects through a pinned IP. A wrong CA and a wrong name are refused.
   - A server requiring a client certificate accepts one and refuses a connection without it.

## Alternatives considered

- **Pass the name to the driver and rely on the SSRF check alone.** Rejected: this reopens BUG-547's
  rebinding gap.
- **Disable hostname checking and pin the certificate fingerprint per connection.** Rejected as the
  default: it breaks on every certificate rotation. It may come later as an extra option for
  customers who want it.
- **A TLS-terminating proxy sidecar.** Rejected: more moving parts on a single free-tier box, and
  the proxy-to-AURA hop would need layer 2 first.
