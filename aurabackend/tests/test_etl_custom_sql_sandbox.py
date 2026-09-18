"""
BUG-053 -- ETL's `custom_sql` transform step spliced caller-supplied SQL
verbatim into the pipeline's DuckDB query, and the shared DuckDB
connection factory (shared/duckdb_factory.py) applies no
enable_external_access restriction. Since this is the SAME connection
used to load the tenant-sandboxed source file and write the sink, it
can't simply be locked down without breaking those legitimate uses --
so a `custom_sql` transform could read arbitrary local files (other
tenants' uploads, host config/secrets) or, with httpfs/S3 configured,
reach the network, completely bypassing the per-tenant sandboxing
enforced for the source file just a few lines above.

Fixed: `_validate_custom_sql` blocks the concrete DuckDB table-functions
and statements that read files, attach other databases, or reach the
network. A transform only ever needs to compute over the table it
receives via `{{input}}` -- it never legitimately needs any of these.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_gateway.routers.etl import (  # noqa: E402
    ETLTransformStep,
    _build_transform_sql,
    _validate_custom_sql,
)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv_auto('/etc/passwd')",
        "SELECT * FROM read_csv('C:/Users/other_tenant/secret.csv')",
        "SELECT * FROM read_parquet('s3://bucket/other-tenant-data.parquet')",
        "SELECT * FROM read_json_auto('http://169.254.169.254/latest/meta-data/')",
        "ATTACH 'other.duckdb' AS other; SELECT * FROM other.secrets",
        "COPY {{input}} TO '/tmp/exfil.csv'",
        "PRAGMA database_list",
        "INSTALL httpfs; LOAD httpfs; SELECT * FROM read_csv('s3://x')",
        "SELECT * FROM 'https://evil.example.com/data.csv'",
        # BUG-114: DuckDB's replacement-scan syntax -- a bare string literal
        # used directly as a table reference, with no read_csv/ATTACH/etc.
        # keyword and no "://" to trip the other two checks.
        "SELECT * FROM '/etc/passwd'",
        "(SELECT column0 FROM '/etc/hostname' LIMIT 1)",
        "SELECT (SELECT secret_col FROM 'C:/other_tenant/upload.csv') AS leaked",
        "SELECT * FROM {{input}} JOIN 'other_tenant.csv' ON 1=1",
    ],
)
def test_validate_custom_sql_rejects_file_and_network_access(sql):
    with pytest.raises(ValueError):
        _validate_custom_sql(sql)


def test_validate_custom_sql_allows_double_quoted_identifiers():
    """A double-quoted table/column reference is a legitimate identifier,
    not a file-scan string literal -- must not be rejected (BUG-114)."""
    _validate_custom_sql("SELECT * FROM \"MyTable\" WHERE score > 50")


def test_validate_custom_sql_allows_plain_computation():
    """The legitimate case -- a transform computing over the already-
    loaded table -- must not be rejected."""
    _validate_custom_sql("SELECT *, score * 2 AS doubled FROM {{input}} WHERE score > 50")
    _validate_custom_sql("SELECT name, COUNT(*) FROM {{input}} GROUP BY name")


def test_build_transform_sql_rejects_malicious_custom_sql_step():
    """End-to-end through _build_transform_sql, the function ETL execute
    actually calls -- proves the check is wired in, not just defined."""
    steps = [
        ETLTransformStep(
            id="s1", type="custom_sql",
            config={"sql": "SELECT * FROM read_csv_auto('/etc/passwd')"},
        )
    ]
    with pytest.raises(ValueError):
        _build_transform_sql("source_data", steps)


def test_build_transform_sql_allows_benign_custom_sql_step():
    steps = [
        ETLTransformStep(
            id="s1", type="custom_sql",
            config={"sql": "SELECT * FROM {{input}} WHERE 1=1"},
        )
    ]
    sql = _build_transform_sql("source_data", steps)
    assert "read_csv" not in sql.lower()
    assert "source_data" in sql
