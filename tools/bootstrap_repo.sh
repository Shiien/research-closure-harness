#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="$(python3 - "${BASH_SOURCE[0]}" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).resolve().parents[1])
PY
)"
TARGET="${1:-$PWD}"
TARGET="$(cd "$TARGET" && pwd)"

BEGIN="<!-- research-closure-harness:start -->"
END="<!-- research-closure-harness:end -->"

merge_markdown_block() {
  local target_file="$1"
  local source_file="$2"
  local title="$3"

  mkdir -p "$(dirname "$target_file")"
  python3 - "$target_file" "$source_file" "$title" "$BEGIN" "$END" <<'PY'
from pathlib import Path
import re
import sys

target = Path(sys.argv[1])
source = Path(sys.argv[2])
title = sys.argv[3]
begin = sys.argv[4]
end = sys.argv[5]

existing = target.read_text(encoding="utf-8") if target.exists() else ""
body = source.read_text(encoding="utf-8").strip()
block = f"{begin}\n\n# {title}\n\n{body}\n\n{end}"
pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.S)

if pattern.search(existing):
    updated = pattern.sub(block, existing)
else:
    updated = existing.rstrip()
    if updated:
        updated += "\n\n"
    updated += block

target.write_text(updated.rstrip() + "\n", encoding="utf-8")
PY
}

copy_if_missing() {
  local source="$1"
  local target="$2"
  if [[ ! -e "$target" ]]; then
    mkdir -p "$(dirname "$target")"
    cp -R "$source" "$target"
  fi
}

mkdir -p \
  "$TARGET/tools" \
  "$TARGET/templates" \
  "$TARGET/docs" \
  "$TARGET/.research/logs" \
  "$TARGET/.agents/skills/research-closure" \
  "$TARGET/.agents/skills/research-handoff" \
  "$TARGET/.claude/skills/research-closure" \
  "$TARGET/.claude/skills/research-handoff" \
  "$TARGET/.claude/hooks"

cp "$SOURCE_ROOT/tools/research_closure.py" "$TARGET/tools/research_closure.py"
chmod +x "$TARGET/tools/research_closure.py"
cp -R "$SOURCE_ROOT/templates/." "$TARGET/templates/"
cp "$SOURCE_ROOT/docs/protocol.md" "$TARGET/docs/research_closure_protocol.md"
cp "$SOURCE_ROOT/.agents/skills/research-closure/SKILL.md" \
   "$TARGET/.agents/skills/research-closure/SKILL.md"
cp "$SOURCE_ROOT/.claude/skills/research-closure/SKILL.md" \
   "$TARGET/.claude/skills/research-closure/SKILL.md"
cp "$SOURCE_ROOT/.agents/skills/research-handoff/SKILL.md" \
   "$TARGET/.agents/skills/research-handoff/SKILL.md"
cp "$SOURCE_ROOT/.claude/skills/research-handoff/SKILL.md" \
   "$TARGET/.claude/skills/research-handoff/SKILL.md"
cp "$SOURCE_ROOT/.claude/hooks/closure_guard.py" \
   "$TARGET/.claude/hooks/research_closure_guard.py"
chmod +x "$TARGET/.claude/hooks/research_closure_guard.py"

if [[ ! -f "$TARGET/.research/state.json" ]]; then
  cp "$SOURCE_ROOT/.research/state.json" "$TARGET/.research/state.json"
fi
touch "$TARGET/.research/logs/.gitkeep"

merge_markdown_block "$TARGET/AGENTS.md" "$SOURCE_ROOT/AGENTS.md" \
  "Research Closure Rules"
merge_markdown_block "$TARGET/CLAUDE.md" "$SOURCE_ROOT/CLAUDE.md" \
  "Research Closure Rules"

cat <<EOF
Research Closure Harness initialized in:
  $TARGET

Next:
  cd "$TARGET"
  research-closure set-project --agenda "..." --question "..." --minimum "..."
  research-closure start-sprint --claim "..." --days 14 --artifact "..."
  research-closure start-day --deliverable "..."
EOF
