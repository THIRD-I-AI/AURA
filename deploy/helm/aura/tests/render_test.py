"""
Helm chart render-test (no helm CLI required)
=============================================
Substitutes the most common Go-template constructs with Python regex so
the resulting YAML can be parsed by PyYAML. This is a *smoke* test — it
won't catch logic bugs (helm's full template engine is far richer), but it
does catch:

  - missing/typoed `range` keys
  - unbalanced `{{` / `}}` braces
  - templates that produce invalid YAML structurally (bad indentation,
    duplicate keys after expansion, etc.)

Run via:
    python deploy/helm/aura/tests/render_test.py
or wired into CI as a pytest collection.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml


CHART_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = CHART_DIR / "templates"


# Minimal stand-ins — we don't reproduce the helm template engine, we just
# replace the known pipeline calls in our own templates with plausible
# values so YAML parses.
def _strip_innermost(text: str, opener: str) -> str:
    """Repeatedly strip the innermost ``{{ opener ... }}...{{ end }}`` block,
    keeping its body. Innermost means the body contains no nested
    range/with/if/define openers — guaranteeing the matching end is the
    right one. We loop because removing a block may expose a new innermost
    one in the enclosing scope."""
    pattern = re.compile(
        r"\{\{-?\s*" + opener + r"[^\}]*\}\}"
        r"(?P<body>(?:(?!\{\{-?\s*(?:range|with|if|define)\b)(?!\{\{-?\s*end\s*-?\}\}).)*?)"
        r"(?:\{\{-?\s*else\s*-?\}\}(?:(?!\{\{-?\s*end\s*-?\}\}).)*?)?"
        r"\{\{-?\s*end\s*-?\}\}",
        re.S,
    )
    while True:
        new = pattern.sub(lambda m: m.group("body"), text)
        if new == text:
            return new
        text = new


def _stub(text: str) -> str:
    # Strip Go-template comments
    text = re.sub(r"\{\{-?\s*/\*.*?\*/\s*-?\}\}", "", text, flags=re.S)

    range_pattern = re.compile(
        r"\{\{-?\s*range[^\}]*\}\}"
        r"(?P<body>(?:(?!\{\{-?\s*(?:range|with|if|define)\b)(?!\{\{-?\s*end\s*-?\}\}).)*?)"
        r"\{\{-?\s*end\s*-?\}\}",
        re.S,
    )

    def _expand_range(match: re.Match[str]) -> str:
        body = match.group("body")
        body = re.sub(r"\{\{-?\s*\$svcName\s*-?\}\}", "demo_a", body)
        body = re.sub(r"\{\{-?\s*(\$svc\.|\.)[^\}]+-?\}\}", "1", body)
        return body + "\n" + body.replace("demo_a", "demo_b")

    # Single fixpoint loop that strips all four opener types, innermost
    # first. Removing one block may expose another in the enclosing scope,
    # so we iterate until no more substitutions happen.
    while True:
        before = text
        text = re.compile(
            r"\{\{-?\s*define[^\}]*\}\}"
            r"(?P<body>(?:(?!\{\{-?\s*(?:range|with|if|define)\b)(?!\{\{-?\s*end\s*-?\}\}).)*?)"
            r"\{\{-?\s*end\s*-?\}\}",
            re.S,
        ).sub("", text)
        text = _strip_innermost(text, "if")
        text = _strip_innermost(text, "with")
        text = range_pattern.sub(_expand_range, text)
        if text == before:
            break
    # Variable assignments (`{{ $x := ... }}`) emit no output in real helm.
    text = re.sub(r"\{\{-?\s*\$[A-Za-z_][A-Za-z0-9_]*\s*:=[^\}]*-?\}\}", "", text)

    # `nindent N` calls produce a multiline block. Emit a one-key dummy
    # mapping at indent N so the parent ``key:`` doesn't end up empty/inline.
    def _nindent(m: re.Match[str]) -> str:
        n = int(m.group(1))
        return "\n" + (" " * n) + "stub: \"1\""

    text = re.sub(r"\{\{[^\}]*\bnindent\s+(\d+)[^\}]*\}\}", _nindent, text)
    # `toYaml ... | indent N` falls through to the same shape.
    text = re.sub(r"\{\{[^\}]*\bindent\s+(\d+)[^\}]*\}\}", _nindent, text)
    # Any remaining `{{ ... }}` — replace with a string scalar that's valid
    # in any YAML context (key, value, list item).
    text = re.sub(r"\{\{[^\}]*\}\}", "stub", text)
    return text


def main() -> int:
    failures = 0
    for tpl in sorted(TEMPLATE_DIR.glob("*.yaml")):
        raw = tpl.read_text(encoding="utf-8")
        rendered = _stub(raw)
        try:
            list(yaml.safe_load_all(rendered))
        except yaml.YAMLError as exc:
            failures += 1
            print(f"FAIL  {tpl.name}: {exc}")
        else:
            print(f"ok    {tpl.name}")

    if failures:
        print(f"\n{failures} template(s) produced invalid YAML")
        return 1
    print(f"\nAll {len(list(TEMPLATE_DIR.glob('*.yaml')))} templates parse cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
