import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # noqa: I001

from shared.file_service import FileService


def test_list_files_is_scoped_to_subdir(tmp_path, monkeypatch):
    fs = FileService()
    monkeypatch.setattr(fs, "uploads_path", tmp_path)
    (tmp_path / "orgA").mkdir()
    (tmp_path / "orgB").mkdir()
    (tmp_path / "orgA" / "a.csv").write_text("x")
    (tmp_path / "orgB" / "b.csv").write_text("y")
    names_a = {f["filename"] for f in fs.list_files(subdir="orgA")}
    assert names_a == {"a.csv"}
    assert "b.csv" not in names_a
