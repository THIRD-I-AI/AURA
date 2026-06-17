import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # noqa: E402

from shared.upload_migration import migrate_flat_uploads_to_default  # noqa: E402


def test_moves_flat_files_into_default_idempotently(tmp_path):
    (tmp_path / "customer.csv").write_text("a")
    (tmp_path / ".gitkeep").write_text("")
    (tmp_path / "orgX").mkdir()
    (tmp_path / "orgX" / "keep.csv").write_text("b")
    moved = migrate_flat_uploads_to_default(str(tmp_path))
    assert moved == 1
    assert (tmp_path / "default" / "customer.csv").exists()
    assert not (tmp_path / "customer.csv").exists()
    assert (tmp_path / "orgX" / "keep.csv").exists()
    assert migrate_flat_uploads_to_default(str(tmp_path)) == 0  # idempotent
