#!/usr/bin/env bash
# PreCompact hook: snapshot durable, reconstructible state (git/PR) plus a raw
# transcript tail, so the paired SessionStart(compact) hook can reload it.
# PreCompact's own stdout is NOT injected into context (only SessionStart /
# UserPromptSubmit / PostToolUse / Stop / SubagentStop support that) — this
# hook only writes files; the read-back happens in the other hook.
set -euo pipefail

STATE_DIR=".claude/session-state"
ARCHIVE_DIR="$STATE_DIR/transcript-archive"
mkdir -p "$ARCHIVE_DIR"

INPUT="$(cat)"
TRANSCRIPT_PATH="$(printf '%s' "$INPUT" | python -c "import json,sys; d=json.load(sys.stdin); print(d.get('transcript_path',''))" 2>/dev/null || true)"
TRIGGER="$(printf '%s' "$INPUT" | python -c "import json,sys; d=json.load(sys.stdin); print(d.get('trigger','unknown'))" 2>/dev/null || echo unknown)"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
if [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ]; then
  tail -n 400 "$TRANSCRIPT_PATH" > "$ARCHIVE_DIR/${TS}-${TRIGGER}.jsonl" 2>/dev/null || true
fi

{
  echo "## Snapshot — ${TS} (compaction trigger: ${TRIGGER})"
  echo
  echo "### Git"
  echo '```'
  git branch --show-current 2>/dev/null
  git status --short 2>/dev/null
  echo '```'
  echo
  echo "### Recent commits"
  echo '```'
  git log -8 --oneline 2>/dev/null
  echo '```'
  echo
  echo "### Open PRs"
  echo '```'
  gh pr list --state open --limit 20 2>/dev/null || echo "(gh unavailable)"
  echo '```'
  echo
  echo "_Full transcript tail archived at ${ARCHIVE_DIR}/${TS}-${TRIGGER}.jsonl — Read it if this digest isn't enough._"
} > "$STATE_DIR/latest-digest.md"
