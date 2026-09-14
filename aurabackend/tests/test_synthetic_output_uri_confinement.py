"""
BUG-058 -- GenerateRequest.output_uri was a fully caller-controlled write
destination (file://, s3://, gs://, abfs://) with no confinement to the
caller's own tenant storage area. Any authenticated caller could set
output_uri to file:///<arbitrary absolute path> and have the gateway's
own filesystem access write synthetic data there, or reach cloud storage
the app's own credentials can access but the caller shouldn't -- turning
"generate synthetic data" into an arbitrary-file-write primitive.

Fixed: a local path/file:// output_uri is rewritten to the caller's own
tenant subdirectory before it ever reaches SyntheticDatasetWriter,
keeping only the requested basename. Cloud URIs (s3/gs/abfs) pass
through unchanged -- this is an enterprise bring-your-own-cloud feature
by design; only unsupported schemes are rejected.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from api_gateway.routers.synthetic import _SYNTHETIC_OUTPUT_ROOT, _confine_output_uri  # noqa: E402
from shared.storage.base import tenant_slug  # noqa: E402


def test_absolute_local_path_confined_to_tenant_dir():
    confined = _confine_output_uri("/etc/passwd", "tenant-a", "job1")
    expected_root = os.path.join(_SYNTHETIC_OUTPUT_ROOT, tenant_slug("tenant-a"))
    assert confined.startswith(expected_root)
    assert "passwd" in confined
    assert confined != "/etc/passwd"


def test_file_uri_confined_to_tenant_dir():
    confined = _confine_output_uri("file:///opt/aura/secrets/keys.pem", "tenant-a", "job1")
    expected_root = os.path.join(_SYNTHETIC_OUTPUT_ROOT, tenant_slug("tenant-a"))
    assert confined.startswith(expected_root)
    assert "keys.pem" in confined


def test_windows_absolute_path_confined_to_tenant_dir():
    confined = _confine_output_uri("C:/Windows/System32/config", "tenant-a", "job1")
    expected_root = os.path.join(_SYNTHETIC_OUTPUT_ROOT, tenant_slug("tenant-a"))
    assert confined.startswith(expected_root)


def test_different_tenants_get_different_confined_roots():
    a = _confine_output_uri("data.parquet", "tenant-a", "job1")
    b = _confine_output_uri("data.parquet", "tenant-b", "job1")
    assert a != b
    assert tenant_slug("tenant-a") in a
    assert tenant_slug("tenant-b") in b


def test_traversal_attempt_does_not_escape_tenant_dir():
    confined = _confine_output_uri("../../../etc/passwd", "tenant-a", "job1")
    expected_root = os.path.realpath(os.path.join(_SYNTHETIC_OUTPUT_ROOT, tenant_slug("tenant-a")))
    assert os.path.commonpath((os.path.realpath(confined), expected_root)) == expected_root


@pytest.mark.parametrize("uri", ["s3://bucket/prefix", "gs://bucket/prefix", "abfs://container/prefix"])
def test_cloud_uris_pass_through_unchanged(uri):
    """BYOC by design -- the caller's own cloud destination, not this
    server's filesystem, so no confinement applies."""
    assert _confine_output_uri(uri, "tenant-a", "job1") == uri


def test_unsupported_scheme_rejected():
    with pytest.raises(ValueError):
        _confine_output_uri("http://evil.example.com/exfil", "tenant-a", "job1")
    with pytest.raises(ValueError):
        _confine_output_uri("ftp://evil.example.com/exfil", "tenant-a", "job1")
