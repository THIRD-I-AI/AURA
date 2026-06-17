"""
Path-traversal safety for the file-upload handler.

The upload endpoint builds the on-disk path from a CLIENT-supplied filename.
Without sanitization a name like ``../keys/signing_ed25519.pem`` could escape
the upload dir and overwrite arbitrary files (including the audit signing
key) — a real risk once tenants share an instance. ``_safe_upload_path`` must
keep every resolved path inside the upload dir, and reject degenerate names.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_gateway.routers.files import _safe_upload_path


def _contained(path: str, root: str) -> bool:
    return os.path.commonpath((os.path.abspath(path), os.path.abspath(root))) == os.path.abspath(root)


def test_normal_filename_resolves_inside(tmp_path) -> None:
    d = str(tmp_path)
    p = _safe_upload_path(d, "customer.csv")
    assert p == os.path.join(d, "customer.csv")
    assert _contained(p, d)


def test_traversal_is_neutralized_never_escapes(tmp_path) -> None:
    d = str(tmp_path)
    for evil in [
        "../keys/signing_ed25519.pem",
        "../../etc/passwd",
        "..\\..\\windows\\system32\\x.dll",
        "/abs/evil.csv",
        "sub/dir/data.csv",
    ]:
        p = _safe_upload_path(d, evil)
        # Either rejected, or safely basenamed into the upload dir — never outside.
        assert p is None or _contained(p, d)


def test_degenerate_names_rejected(tmp_path) -> None:
    d = str(tmp_path)
    for bad in ["", "   ", ".", "..", "/", "../", "../../", "file\x00.csv"]:
        assert _safe_upload_path(d, bad) is None
    assert _safe_upload_path(d, None) is None
