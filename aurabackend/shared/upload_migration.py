"""One-time, idempotent migration: files sitting directly in data/uploads/
(pre-S42) belong to the 'default' tenant. Move them into uploads/default/ so
the per-tenant readers (S42) still see them. Subdirs and dotfiles untouched."""
from __future__ import annotations

import logging
import os
import shutil

logger = logging.getLogger("aura.upload_migration")


def migrate_flat_uploads_to_default(uploads_root: str) -> int:
    if not os.path.isdir(uploads_root):
        return 0
    default_dir = os.path.join(uploads_root, "default")
    os.makedirs(default_dir, exist_ok=True)
    moved = 0
    for name in os.listdir(uploads_root):
        if name.startswith("."):            # .gitkeep, .aura_header_cache
            continue
        src = os.path.join(uploads_root, name)
        if not os.path.isfile(src):          # skip tenant subdirs
            continue
        dst = os.path.join(default_dir, name)
        if os.path.exists(dst):
            continue
        shutil.move(src, dst)
        moved += 1
    if moved:
        logger.info("Migrated %d flat upload(s) into default/", moved)
    return moved
