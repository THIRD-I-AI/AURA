"""
Sec-2 contract tests for safe_join + sanitize_error.

These are the two primitives that resolve 31 of the 42 CodeQL findings
in the Sec-2 bundle (#11-#35 + #36-#41). Tests pin the security
contract — both the happy path and the boundary-attack cases — so a
future refactor that "simplifies" the helpers can't silently
re-introduce the vulnerability.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from shared.error_handler import sanitize_error
from shared.exceptions import AuraError, NotFoundError
from shared.safe_paths import PathTraversalError, safe_join

# ── safe_join contract ────────────────────────────────────────────────


class TestSafeJoin:
    def test_happy_path_relative_filename(self, tmp_path: Path) -> None:
        """A simple relative filename joins cleanly under the base."""
        result = safe_join(tmp_path, "hello.csv")
        assert result == (tmp_path / "hello.csv").resolve()

    def test_happy_path_nested_subdir(self, tmp_path: Path) -> None:
        """A multi-segment relative path is also fine."""
        result = safe_join(tmp_path, "sub/dir/file.parquet")
        assert result == (tmp_path / "sub" / "dir" / "file.parquet").resolve()

    def test_rejects_empty_input(self, tmp_path: Path) -> None:
        with pytest.raises(PathTraversalError):
            safe_join(tmp_path, "")

    def test_rejects_parent_directory_traversal(self, tmp_path: Path) -> None:
        """The canonical attack: `../../etc/passwd`."""
        with pytest.raises(PathTraversalError):
            safe_join(tmp_path, "../../etc/passwd")

    def test_rejects_single_parent_hop(self, tmp_path: Path) -> None:
        with pytest.raises(PathTraversalError):
            safe_join(tmp_path, "../escaped.csv")

    def test_rejects_absolute_path_posix(self, tmp_path: Path) -> None:
        with pytest.raises(PathTraversalError):
            safe_join(tmp_path, "/etc/passwd")

    @pytest.mark.skipif(
        not __import__("sys").platform.startswith("win"),
        reason="Windows-specific absolute-path encoding",
    )
    def test_rejects_absolute_path_windows(self, tmp_path: Path) -> None:
        with pytest.raises(PathTraversalError):
            safe_join(tmp_path, r"C:\Windows\System32\config\SAM")

    def test_rejects_mixed_traversal(self, tmp_path: Path) -> None:
        """Mixed relative + parent-dir hops still escape."""
        with pytest.raises(PathTraversalError):
            safe_join(tmp_path, "ok/../../etc/passwd")

    def test_rejects_embedded_parent_in_middle(self, tmp_path: Path) -> None:
        """`.../..` should fail at the fail-fast parts check."""
        with pytest.raises(PathTraversalError):
            safe_join(tmp_path, "subdir/../../escape")

    def test_base_as_string_or_path_equivalent(self, tmp_path: Path) -> None:
        """Both `str` and `Path` bases accepted; result identical."""
        r1 = safe_join(str(tmp_path), "x.csv")
        r2 = safe_join(tmp_path, "x.csv")
        assert r1 == r2

    def test_resolved_path_is_under_resolved_base(self, tmp_path: Path) -> None:
        """The returned path can always be made relative to the base."""
        result = safe_join(tmp_path, "deep/nested/file.txt")
        # Should never raise — relative_to is part of the contract.
        result.relative_to(tmp_path.resolve())

    def test_rejects_none_input(self, tmp_path: Path) -> None:
        with pytest.raises(PathTraversalError):
            safe_join(tmp_path, None)  # type: ignore[arg-type]


# ── sanitize_error contract ───────────────────────────────────────────


class TestSanitizeError:
    def test_generic_exception_returns_fallback(self, caplog) -> None:
        """A bare ValueError must NEVER leak its str representation to
        the response — only the fallback string is returned."""
        with caplog.at_level(logging.ERROR):
            msg = sanitize_error(ValueError("secret SQL fragment in here"))
        assert msg == "Internal server error"
        # But the secret should be in the log (for ops).
        assert "secret SQL fragment in here" in caplog.text

    def test_aura_error_message_is_passed_through(self, caplog) -> None:
        """Domain errors are curated by their author; their `.message`
        is safe to expose. Use a bare AuraError so we don't depend on
        subclass-specific message formatting."""
        with caplog.at_level(logging.ERROR):
            msg = sanitize_error(AuraError("Curated user-facing message"))
        assert msg == "Curated user-facing message"

    def test_custom_fallback_used(self) -> None:
        msg = sanitize_error(
            RuntimeError("internal detail"),
            fallback="Something went wrong with ETL",
        )
        assert msg == "Something went wrong with ETL"

    def test_traceback_is_logged_even_on_aura_error(self, caplog) -> None:
        """We always log — even for AuraError — so the operator never
        loses the trail."""
        with caplog.at_level(logging.ERROR):
            sanitize_error(NotFoundError("Object X"))
        assert "NotFoundError" in caplog.text

    def test_context_prefix_appears_in_log(self, caplog) -> None:
        """The `context=` kwarg is prepended to the log line so
        operators can grep for the offending endpoint."""
        with caplog.at_level(logging.ERROR):
            sanitize_error(
                RuntimeError("boom"),
                context="etl execute pipeline=foo",
            )
        assert "etl execute pipeline=foo" in caplog.text

    def test_custom_logger_used_when_provided(self) -> None:
        """When a logger is passed explicitly, the log goes to it
        (verifies dependency-injection contract)."""
        captured = []

        class CaptureLogger:
            def error(self, msg, *args, **kw):
                captured.append(msg % args if args else msg)

        sanitize_error(
            RuntimeError("local detail"),
            logger=CaptureLogger(),
            context="unit test",
        )
        # Should have produced at least one entry on the injected logger.
        assert len(captured) == 1
        assert "unit test" in captured[0]
        assert "RuntimeError" in captured[0]

    def test_unknown_exception_subclass_still_safe(self) -> None:
        """Even a custom Exception subclass that's NOT an AuraError
        returns the fallback, not str(exc)."""
        class MyWeirdError(Exception):
            pass

        msg = sanitize_error(MyWeirdError("filesystem path /etc/secret"))
        assert msg == "Internal server error"
        assert "/etc/secret" not in msg

    def test_aura_error_subclass_preserves_message(self) -> None:
        """Any AuraError subclass — not just NotFoundError — has its
        message exposed."""
        class MyDomainError(AuraError):
            status_code = 418
            error_code = "TEAPOT"

        msg = sanitize_error(MyDomainError("I am a teapot"))
        assert msg == "I am a teapot"
