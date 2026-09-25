"""BUG-173: builds must resolve from the committed constraints file, not from whatever is newest."""
from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent


def _pins() -> dict[str, str]:
    pins = {}
    for raw in (BACKEND / "constraints.txt").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;]+)", raw.strip())
        if m:
            pins[m.group(1).lower().replace("_", "-")] = m.group(2)
    return pins


def test_constraints_pin_the_packages_that_broke_us():
    pins = _pins()
    assert pins["sqlalchemy"].startswith("2.0."), "BUG-172: SQLAlchemy must stay on 2.0.x"
    assert "greenlet" in pins


def test_every_pip_install_of_requirements_uses_the_constraints_file():
    lines = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8").splitlines()
    lines += (BACKEND / "Dockerfile").read_text(encoding="utf-8").splitlines()
    bad = [
        line.strip()
        for line in lines
        if re.search(r"pip (install|wheel)\b.*-r [\w/.\-]*requirements[\w\-]*\.txt", line)
        and "constraints.txt" not in line
    ]
    assert not bad, f"install without -c constraints.txt: {bad}"
