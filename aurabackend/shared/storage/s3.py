"""S3-compatible object-storage backend (S3 / R2 / MinIO) — S45, Approach A.

DuckDB reads s3:// URIs directly via httpfs; boto3 handles write/list/delete.
"""
from __future__ import annotations

from typing import Any, List, Optional
from urllib.parse import urlparse

from shared.config import get_settings
from shared.storage.base import ObjectInfo, StorageBackend, safe_object_name, tenant_slug


class S3Backend(StorageBackend):
    def __init__(self) -> None:
        s = get_settings()
        self._bucket = s.s3_bucket
        self._prefix = (s.s3_prefix or "").strip("/")
        self._region = s.s3_region
        self._endpoint_url = s.s3_endpoint_url  # boto3 wants full URL w/ scheme
        self._key_id = s.s3_access_key_id
        self._secret = s.s3_secret_access_key
        self._url_style = s.s3_url_style  # "path" | "vhost"
        self._use_ssl = s.s3_use_ssl
        self._client: Optional[Any] = None
        if not self._bucket:
            raise ValueError("AURA_S3_BUCKET must be set for the s3 backend.")

    # ── boto3 ──────────────────────────────────────────────────────────
    def _c(self) -> Any:
        if self._client is None:
            import boto3
            from botocore.config import Config
            addressing = "virtual" if self._url_style == "vhost" else "path"
            self._client = boto3.client(
                "s3",
                region_name=self._region,
                endpoint_url=self._endpoint_url or None,
                aws_access_key_id=self._key_id,
                aws_secret_access_key=self._secret,
                config=Config(signature_version="s3v4",
                              s3={"addressing_style": addressing}),
            )
        return self._client

    def _key(self, tenant: str, filename: str) -> str:
        safe_object_name(filename)  # raises ValueError on traversal/separator/NUL
        parts = [p for p in (self._prefix, tenant_slug(tenant), filename) if p]
        return "/".join(parts)

    def _tenant_prefix(self, tenant: str) -> str:
        parts = [p for p in (self._prefix, tenant_slug(tenant)) if p]
        return "/".join(parts) + "/"

    # ── StorageBackend ─────────────────────────────────────────────────
    def write(self, tenant: str, filename: str, data: bytes) -> ObjectInfo:
        key = self._key(tenant, filename)
        self._c().put_object(Bucket=self._bucket, Key=key, Body=data)
        head = self._c().head_object(Bucket=self._bucket, Key=key)
        return ObjectInfo(
            name=filename,
            size=head["ContentLength"],
            fingerprint=f'{head["ETag"].strip(chr(34))}|{head["ContentLength"]}',
            duckdb_uri=self.duckdb_uri(tenant, filename),
        )

    def read(self, tenant: str, filename: str) -> bytes:
        obj = self._c().get_object(Bucket=self._bucket, Key=self._key(tenant, filename))
        return obj["Body"].read()

    def list(self, tenant: str) -> List[ObjectInfo]:
        prefix = self._tenant_prefix(tenant)
        out: List[ObjectInfo] = []
        paginator = self._c().get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                name = obj["Key"][len(prefix):]
                if "/" in name or not name:
                    continue  # flat per-tenant namespace only
                out.append(ObjectInfo(
                    name=name,
                    size=obj["Size"],
                    fingerprint=f'{obj["ETag"].strip(chr(34))}|{obj["Size"]}',
                    duckdb_uri=self.duckdb_uri(tenant, name),
                ))
        return sorted(out, key=lambda o: o.name)

    def delete(self, tenant: str, filename: str) -> bool:
        if not self.exists(tenant, filename):
            return False
        self._c().delete_object(Bucket=self._bucket, Key=self._key(tenant, filename))
        return True

    def exists(self, tenant: str, filename: str) -> bool:
        import botocore.exceptions
        try:
            self._c().head_object(Bucket=self._bucket, Key=self._key(tenant, filename))
            return True
        except botocore.exceptions.ClientError:
            return False

    def duckdb_uri(self, tenant: str, filename: str) -> str:
        return f"s3://{self._bucket}/{self._key(tenant, filename)}"

    def configure_duckdb(self, con: Any) -> None:
        # DuckDB ENDPOINT wants host:port WITHOUT scheme; USE_SSL carries the
        # http/https choice. boto3 wanted the full endpoint_url — translate.
        endpoint_clause = ""
        use_ssl = self._use_ssl
        if self._endpoint_url:
            parsed = urlparse(self._endpoint_url)
            host = parsed.netloc or parsed.path
            endpoint_clause = f", ENDPOINT '{host}'"
            use_ssl = parsed.scheme == "https"
        con.execute("INSTALL httpfs")
        con.execute("LOAD httpfs")
        con.execute(
            "CREATE OR REPLACE SECRET aura_s3 ("
            "TYPE S3, PROVIDER config, "
            f"KEY_ID '{self._key_id or ''}', "
            f"SECRET '{self._secret or ''}', "
            f"REGION '{self._region}', "
            f"URL_STYLE '{self._url_style}', "
            f"USE_SSL {str(use_ssl).lower()}"
            f"{endpoint_clause})"
        )
