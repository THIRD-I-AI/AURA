#!/usr/bin/env python3
"""Pre-commit hook: refuse to commit files containing provider API keys.

Each pattern requires the provider's prefix followed by the expected
secret shape (length + character class) so we don't false-positive on
harmless identifiers that happen to start with the prefix.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Groq",          re.compile(r"gsk_[A-Za-z0-9]{40,}")),
    ("OpenAI",        re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{30,}")),
    ("Google/Gemini", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("GitHub PAT",    re.compile(r"ghp_[A-Za-z0-9]{36}")),
    ("GitHub OAuth",  re.compile(r"gho_[A-Za-z0-9]{36}")),
    ("Anthropic",     re.compile(r"sk-ant-[A-Za-z0-9_-]{90,}")),
    ("AWS Access",    re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Slack Bot",     re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
]

# Files that are expected to contain placeholder secrets; .env.example
# and docs SHOULD have `YOUR_KEY_HERE` style dummies.
ALLOW_LIST = {
    ".env.example",
    "docs/secrets.md",
    "scripts/hooks/block_secrets.py",
}


def scan(path: Path) -> list[tuple[str, int, str]]:
    hits: list[tuple[str, int, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return hits
    for lineno, line in enumerate(text.splitlines(), 1):
        for provider, pattern in PATTERNS:
            if pattern.search(line):
                hits.append((provider, lineno, line.strip()[:120]))
    return hits


def main(argv: list[str]) -> int:
    bad = False
    for arg in argv[1:]:
        p = Path(arg)
        rel = p.as_posix()
        if any(rel.endswith(a) for a in ALLOW_LIST):
            continue
        for provider, lineno, snippet in scan(p):
            bad = True
            print(f"{rel}:{lineno}: blocked {provider} key: {snippet}", file=sys.stderr)
    if bad:
        print(
            "\nSECRET LEAK BLOCKED. Remove the key and rotate it at the provider "
            "console before committing.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
