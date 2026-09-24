"""BUG-172: the SQLAlchemy / greenlet declaration must stay coherent.

SQLAlchemy 2.1.0 (released 2026-09-24) stopped installing greenlet by default.
Our range was `sqlalchemy>=2.0,<3.0`, so CI (and any Docker image built from
requirements.txt) resolved 2.1.0 on release day, and every import of
`sqlalchemy.ext.asyncio` -- the gateway, alembic's env.py, half the test suite --
failed with "requires that the Python 'greenlet' library is installed".

This pins the declaration, not the installed version: it reads the two files that
define what gets installed. It is meant to fail loudly if someone widens the
range or drops greenlet, and to be edited deliberately when SQLAlchemy 2.1 is
adopted on purpose (bump the cap here in the same change).
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def _requirement_lines(text_lines):
    for raw in text_lines:
        line = raw.split("#", 1)[0].strip()
        if line:
            yield line


def _from_requirements_txt() -> list[str]:
    return list(_requirement_lines((BACKEND / "requirements.txt").read_text(encoding="utf-8").splitlines()))


def _from_pyproject() -> list[str]:
    data = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    return list(data["project"]["dependencies"])


SOURCES = {"requirements.txt": _from_requirements_txt, "pyproject.toml": _from_pyproject}


def _spec_for(deps: list[str], name: str) -> str | None:
    for dep in deps:
        m = re.match(rf"^{re.escape(name)}(\[[^\]]*\])?\s*(.*)$", dep, flags=re.IGNORECASE)
        if m:
            return m.group(2).strip()
    return None


@pytest.mark.parametrize("source", SOURCES)
def test_sqlalchemy_is_capped_below_2_1(source):
    spec = _spec_for(SOURCES[source](), "sqlalchemy")
    assert spec is not None, f"sqlalchemy is not declared in {source}"
    assert "<2.1" in spec.replace(" ", ""), (
        f"{source} declares sqlalchemy '{spec}'. SQLAlchemy 2.1 no longer installs greenlet, which "
        "sqlalchemy.ext.asyncio requires (BUG-172). If you are adopting 2.1 on purpose, raise this "
        "cap and update this test in the same change."
    )


@pytest.mark.parametrize("source", SOURCES)
def test_greenlet_is_declared_explicitly(source):
    spec = _spec_for(SOURCES[source](), "greenlet")
    assert spec is not None, (
        f"greenlet is not declared in {source}; sqlalchemy.ext.asyncio needs it and newer SQLAlchemy "
        "releases no longer pull it in (BUG-172)."
    )


def test_both_dependency_files_agree():
    for name in ("sqlalchemy", "greenlet"):
        specs = {src: (_spec_for(load(), name) or "").replace(" ", "") for src, load in SOURCES.items()}
        assert len(set(specs.values())) == 1, f"{name} is declared differently across files: {specs}"


def test_the_installed_environment_can_import_the_asyncio_layer():
    """The failure itself, in whatever environment is running the tests."""
    import sqlalchemy.ext.asyncio  # noqa: F401  (raises ImportError without greenlet)
