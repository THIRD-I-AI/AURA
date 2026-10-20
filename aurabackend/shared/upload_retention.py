"""Keep the upload store from growing without bound.

Two rules, both configurable:

* Files the live verification uploads (``verify_<8 hex>_<label>.<ext>``, the shape
  ``scripts/verify_live_deployment.py`` gives them) expire after
  ``AURA_VERIFY_UPLOAD_TTL_HOURS`` (default 24; 0 keeps them). Seventy-two of them
  once piled up in one account and made every chat prompt overflow (BUG-444).
* Each tenant's uploads are capped at ``AURA_TENANT_STORAGE_QUOTA_MB`` in total
  (default 1024; 0 means no cap), so one account cannot fill the disk.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

# The same pattern scripts/cleanup_verify_resources.py uses: anchored both ends, so a
# customer's "verify_report.csv" never matches.
VERIFY_UPLOAD_RE = re.compile(r"^verify_[0-9a-f]{8}_[a-z0-9_]+\.(csv|tsv|xlsx|xls|json|parquet)$")


def _env_number(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def verify_upload_ttl_seconds() -> float:
    return _env_number("AURA_VERIFY_UPLOAD_TTL_HOURS", 24) * 3600


def tenant_quota_bytes() -> int:
    return int(_env_number("AURA_TENANT_STORAGE_QUOTA_MB", 1024) * 1024 * 1024)


def expire_verify_uploads(
    uploads_root: str,
    now: Optional[float] = None,
    removed: Optional[List[Tuple[str, str]]] = None,
) -> int:
    """Delete verification uploads older than the TTL, directly inside each tenant
    directory of *uploads_root*. Regular files only, never a symlink. Returns how
    many were deleted. Blocking: call it from a thread.

    Each deleted file is appended to *removed* as (tenant directory, file name), so
    the async caller can purge its metadata-DB rows too (BUG-461)."""
    ttl = verify_upload_ttl_seconds()
    root = Path(uploads_root)
    if ttl <= 0 or not root.is_dir():
        return 0
    cutoff = (time.time() if now is None else now) - ttl
    deleted = 0
    for tenant_dir in root.iterdir():
        if not tenant_dir.is_dir() or tenant_dir.is_symlink():
            continue
        for f in tenant_dir.iterdir():
            try:
                if (
                    VERIFY_UPLOAD_RE.match(f.name)
                    and f.is_file() and not f.is_symlink()
                    and f.stat().st_mtime < cutoff
                ):
                    f.unlink()
                    deleted += 1
                    from shared.data_utils import remove_catalog_files

                    remove_catalog_files(str(f))  # BUG-450
                    if removed is not None:
                        removed.append((tenant_dir.name, f.name))
            except OSError:
                continue
    return deleted


def catalog_bytes(tenant_dir: Optional[Path], replacing: Optional[str] = None) -> int:
    """Disk used by the catalog's sidecars (entries and Parquet copies) for one
    tenant's local uploads; they count against the quota too. The sidecars of
    *replacing*, a file about to be overwritten, are left out: they go with it."""
    if tenant_dir is None:
        return 0
    total = 0
    try:
        for f in (tenant_dir / ".aura_catalog").iterdir():
            if f.is_file() and not (replacing and f.name.startswith(f"{replacing}.")):
                total += f.stat().st_size
    except OSError:
        return total
    return total


def tenant_dir_bytes(tenant_dir: Optional[Path], replacing: Optional[str] = None) -> Optional[int]:
    """Bytes held by every regular file directly in a local tenant directory -- of
    any extension -- plus its catalog sidecars; None when there is no local
    directory (remote storage), so the caller falls back to the backend listing.

    BUG-447: the limit used the backend's listing, which shows only the readable
    data extensions, while the upload route stores any name; a .bin or .txt upload
    never counted. *replacing* (a file about to be overwritten) is left out with
    its sidecars."""
    if tenant_dir is None:
        return None
    total = 0
    try:
        for f in tenant_dir.iterdir():
            if f.name != replacing and f.is_file() and not f.is_symlink():
                total += f.stat().st_size
    except OSError:
        pass
    return total + catalog_bytes(tenant_dir, replacing)


def over_quota(existing_sizes: Iterable[int], incoming: int) -> bool:
    """Would storing *incoming* more bytes take the tenant past its quota?"""
    quota = tenant_quota_bytes()
    return quota > 0 and sum(existing_sizes) + incoming > quota
