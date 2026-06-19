import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.config import AuraSettings


def test_default_backend_is_local():
    s = AuraSettings()
    assert s.storage_backend == "local"


def test_s3_backend_requires_bucket(monkeypatch):
    monkeypatch.setenv("AURA_STORAGE_BACKEND", "s3")
    monkeypatch.delenv("AURA_S3_BUCKET", raising=False)
    with pytest.raises(ValueError, match="AURA_S3_BUCKET"):
        AuraSettings()


def test_s3_backend_with_bucket_ok(monkeypatch):
    monkeypatch.setenv("AURA_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("AURA_S3_BUCKET", "aura-uploads")
    s = AuraSettings()
    assert s.s3_bucket == "aura-uploads"
    assert s.s3_url_style == "path"
