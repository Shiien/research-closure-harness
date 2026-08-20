#!/usr/bin/env bash
set -euo pipefail

PROGRAM="research-closure-harness"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE=""
WITH_CLAUDE_HOOK=0
INSTALL_GLOBAL_RULES=1
UNINSTALL=0
DRY_RUN=0
TEMP_DIR=""

usage() {
  cat <<'EOF'
Usage:
  ./install_research_closure_global.sh [options]

Options:
  --source PATH            Extracted harness directory or zip file.
                           Default: search beside this script.
  --with-claude-hook       Install an optional global Claude PreToolUse hook.
  --without-global-rules   Do not modify global AGENTS.md or CLAUDE.md.
  --uninstall              Remove installed skills, commands, and managed rules.
  --dry-run                Print intended actions without changing files.
  -h, --help               Show this help.

Installed locations:
  Codex skills:     ~/.agents/skills/research-closure/
                    ~/.agents/skills/research-handoff/
                    ~/.agents/skills/auto-research-fast/
                    ~/.agents/skills/auto-research-slow/
  Codex guidance:   ${CODEX_HOME:-~/.codex}/AGENTS.md
  Claude skills:    ${CLAUDE_CONFIG_DIR:-~/.claude}/skills/research-closure/
                    ${CLAUDE_CONFIG_DIR:-~/.claude}/skills/research-handoff/
                    ${CLAUDE_CONFIG_DIR:-~/.claude}/skills/auto-research-fast/
                    ${CLAUDE_CONFIG_DIR:-~/.claude}/skills/auto-research-slow/
  Claude guidance:  ${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md
  DeepSeek Harness skills:
                    ${DSH_HOME:-~/.dsh}/skills/auto-research-fast/
                    ${DSH_HOME:-~/.dsh}/skills/auto-research-slow/
  DeepSeek Harness guidance:
                    ${DSH_HOME:-~/.dsh}/AGENTS.md
  Harness data:     ${XDG_DATA_HOME:-~/.local/share}/research-closure-harness/
  CLI commands:     ~/.local/bin/research-closure
                    ~/.local/bin/research-closure-init
                    ~/.local/bin/research-closure-graph
                    ~/.local/bin/auto-research
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source)
      SOURCE="${2:?--source requires a path}"
      shift 2
      ;;
    --with-claude-hook)
      WITH_CLAUDE_HOOK=1
      shift
      ;;
    --without-global-rules)
      INSTALL_GLOBAL_RULES=0
      shift
      ;;
    --uninstall)
      UNINSTALL=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '[dry-run]'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

expand_path() {
  python3 - "$1" <<'PY'
from pathlib import Path
import os, sys
print(Path(os.path.expandvars(os.path.expanduser(sys.argv[1]))).resolve())
PY
}

CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"
CODEX_SKILL_DIR="$HOME/.agents/skills/research-closure"
CODEX_HANDOFF_SKILL_DIR="$HOME/.agents/skills/research-handoff"
CODEX_AR_FAST_DIR="$HOME/.agents/skills/auto-research-fast"
CODEX_AR_SLOW_DIR="$HOME/.agents/skills/auto-research-slow"
CLAUDE_HOME_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
CLAUDE_SKILL_DIR="$CLAUDE_HOME_DIR/skills/research-closure"
CLAUDE_HANDOFF_SKILL_DIR="$CLAUDE_HOME_DIR/skills/research-handoff"
CLAUDE_AR_FAST_DIR="$CLAUDE_HOME_DIR/skills/auto-research-fast"
CLAUDE_AR_SLOW_DIR="$CLAUDE_HOME_DIR/skills/auto-research-slow"
DSH_HOME_DIR="${DSH_HOME:-$HOME/.dsh}"
DSH_AR_FAST_DIR="$DSH_HOME_DIR/skills/auto-research-fast"
DSH_AR_SLOW_DIR="$DSH_HOME_DIR/skills/auto-research-slow"
DATA_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/research-closure-harness"
BIN_DIR="$HOME/.local/bin"
BACKUP_ROOT="$HOME/.research-closure-backups/$TIMESTAMP"
CODEX_AGENTS="$CODEX_HOME_DIR/AGENTS.md"
CLAUDE_MD="$CLAUDE_HOME_DIR/CLAUDE.md"
DSH_AGENTS="$DSH_HOME_DIR/AGENTS.md"
CLAUDE_HOOK="$CLAUDE_HOME_DIR/hooks/research_closure_guard.py"
CLAUDE_SETTINGS="$CLAUDE_HOME_DIR/settings.json"
BEGIN="<!-- research-closure-harness:start -->"
END="<!-- research-closure-harness:end -->"

cleanup() {
  if [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]]; then
    rm -rf "$TEMP_DIR"
  fi
}
trap cleanup EXIT

backup_path() {
  local path="$1"
  if [[ ! -e "$path" ]]; then
    return
  fi
  local relative="${path#$HOME/}"
  local destination="$BACKUP_ROOT/$relative"
  run mkdir -p "$(dirname "$destination")"
  run cp -R "$path" "$destination"
  echo "Backed up: $path -> $destination"
}

remove_managed_block() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] remove managed Research Closure block from $file"
    return
  fi
  python3 - "$file" "$BEGIN" "$END" <<'PY'
from pathlib import Path
import re, sys
path = Path(sys.argv[1])
begin, end = sys.argv[2], sys.argv[3]
text = path.read_text(encoding="utf-8")
text = re.sub(
    r"(?:\n{0,2})" + re.escape(begin) + r".*?" + re.escape(end) + r"\n?",
    "\n",
    text,
    flags=re.S,
).strip()
path.write_text((text + "\n") if text else "", encoding="utf-8")
PY
}

upsert_managed_block() {
  local file="$1"
  local block_file="$2"
  run mkdir -p "$(dirname "$file")"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] add/update managed Research Closure block in $file"
    return
  fi
  python3 - "$file" "$block_file" "$BEGIN" "$END" <<'PY'
from pathlib import Path
import re, sys
path = Path(sys.argv[1])
block_path = Path(sys.argv[2])
begin, end = sys.argv[3], sys.argv[4]
existing = path.read_text(encoding="utf-8") if path.exists() else ""
block = f"{begin}\n\n{block_path.read_text(encoding='utf-8').strip()}\n\n{end}"
pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.S)
if pattern.search(existing):
    updated = pattern.sub(block, existing)
else:
    updated = existing.rstrip()
    if updated:
        updated += "\n\n"
    updated += block
path.write_text(updated.rstrip() + "\n", encoding="utf-8")
PY
}

remove_claude_hook_setting() {
  [[ -f "$CLAUDE_SETTINGS" ]] || return 0
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] remove Research Closure hook entry from $CLAUDE_SETTINGS"
    return
  fi
  python3 - "$CLAUDE_SETTINGS" <<'PY'
from pathlib import Path
import json, sys
path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(f"Cannot safely edit invalid JSON: {path}")
hooks = data.get("hooks", {})
entries = hooks.get("PreToolUse", [])
cleaned = []
for entry in entries:
    hs = entry.get("hooks", []) if isinstance(entry, dict) else []
    if any("research_closure_guard.py" in str(h.get("command", "")) for h in hs if isinstance(h, dict)):
        continue
    cleaned.append(entry)
if cleaned:
    hooks["PreToolUse"] = cleaned
else:
    hooks.pop("PreToolUse", None)
if not hooks:
    data.pop("hooks", None)
path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
}

install_claude_hook_setting() {
  run mkdir -p "$(dirname "$CLAUDE_SETTINGS")"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] merge Research Closure hook into $CLAUDE_SETTINGS"
    return
  fi
  python3 - "$CLAUDE_SETTINGS" <<'PY'
from pathlib import Path
import json, sys
path = Path(sys.argv[1])
if path.exists():
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raise SystemExit(f"Cannot safely edit invalid JSON: {path}")
else:
    data = {}
hooks = data.setdefault("hooks", {})
entries = hooks.setdefault("PreToolUse", [])
command = 'python3 "$HOME/.claude/hooks/research_closure_guard.py"'
entry = {
    "matcher": "Bash",
    "hooks": [{"type": "command", "command": command}],
}
already = any(
    "research_closure_guard.py" in str(h.get("command", ""))
    for item in entries if isinstance(item, dict)
    for h in item.get("hooks", []) if isinstance(h, dict)
)
if not already:
    entries.append(entry)
path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
}

if [[ "$UNINSTALL" -eq 1 ]]; then
  echo "Uninstalling Research Closure Harness..."
  backup_path "$CODEX_SKILL_DIR"
  backup_path "$CODEX_HANDOFF_SKILL_DIR"
  backup_path "$CODEX_AR_FAST_DIR"
  backup_path "$CODEX_AR_SLOW_DIR"
  backup_path "$CLAUDE_SKILL_DIR"
  backup_path "$CLAUDE_HANDOFF_SKILL_DIR"
  backup_path "$CLAUDE_AR_FAST_DIR"
  backup_path "$CLAUDE_AR_SLOW_DIR"
  backup_path "$DSH_AR_FAST_DIR"
  backup_path "$DSH_AR_SLOW_DIR"
  backup_path "$CODEX_AGENTS"
  backup_path "$CLAUDE_MD"
  backup_path "$DSH_AGENTS"
  backup_path "$CLAUDE_SETTINGS"

  run rm -rf \
    "$CODEX_SKILL_DIR" \
    "$CODEX_HANDOFF_SKILL_DIR" \
    "$CODEX_AR_FAST_DIR" \
    "$CODEX_AR_SLOW_DIR" \
    "$CLAUDE_SKILL_DIR" \
    "$CLAUDE_HANDOFF_SKILL_DIR" \
    "$CLAUDE_AR_FAST_DIR" \
    "$CLAUDE_AR_SLOW_DIR" \
    "$DSH_AR_FAST_DIR" \
    "$DSH_AR_SLOW_DIR" \
    "$DATA_ROOT"
  if [[ -L "$BIN_DIR/research-closure" || -f "$BIN_DIR/research-closure" ]]; then
    run rm -f "$BIN_DIR/research-closure"
  fi
  if [[ -L "$BIN_DIR/research-closure-init" || -f "$BIN_DIR/research-closure-init" ]]; then
    run rm -f "$BIN_DIR/research-closure-init"
  fi
  if [[ -L "$BIN_DIR/research-closure-graph" || -f "$BIN_DIR/research-closure-graph" ]]; then
    run rm -f "$BIN_DIR/research-closure-graph"
  fi
  if [[ -L "$BIN_DIR/auto-research" || -f "$BIN_DIR/auto-research" ]]; then
    run rm -f "$BIN_DIR/auto-research"
  fi
  remove_managed_block "$CODEX_AGENTS"
  remove_managed_block "$CLAUDE_MD"
  remove_managed_block "$DSH_AGENTS"
  remove_claude_hook_setting
  run rm -f "$CLAUDE_HOOK"

  echo "Uninstall complete. Backups, when created, are under:"
  echo "  $BACKUP_ROOT"
  exit 0
fi

if [[ -z "$SOURCE" ]]; then
  if [[ -d "$SCRIPT_DIR/research-closure-harness" ]]; then
    SOURCE="$SCRIPT_DIR/research-closure-harness"
  elif [[ -f "$SCRIPT_DIR/research-closure-harness.zip" ]]; then
    SOURCE="$SCRIPT_DIR/research-closure-harness.zip"
  elif [[ -f "$SCRIPT_DIR/SKILL.md" && -d "$SCRIPT_DIR/tools" ]]; then
    SOURCE="$SCRIPT_DIR"
  else
    echo "Could not find the harness beside this script." >&2
    echo "Use --source /path/to/research-closure-harness.zip" >&2
    exit 1
  fi
fi

SOURCE="$(expand_path "$SOURCE")"
if [[ -f "$SOURCE" ]]; then
  command -v unzip >/dev/null 2>&1 || {
    echo "unzip is required to install from a zip file." >&2
    exit 1
  }
  TEMP_DIR="$(mktemp -d)"
  run unzip -q "$SOURCE" -d "$TEMP_DIR"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "Dry-run with a zip source cannot inspect extracted files; stopping after planned extraction."
    exit 0
  fi
  if [[ -d "$TEMP_DIR/research-closure-harness" ]]; then
    HARNESS_ROOT="$TEMP_DIR/research-closure-harness"
  else
    HARNESS_ROOT="$(find "$TEMP_DIR" -maxdepth 2 -type f -name SKILL.md -print -quit | xargs -I{} dirname "{}")"
  fi
else
  HARNESS_ROOT="$SOURCE"
fi

for required in \
  "$HARNESS_ROOT/SKILL.md" \
  "$HARNESS_ROOT/HANDOFF_SKILL.md" \
  "$HARNESS_ROOT/AUTO_RESEARCH_FAST_SKILL.md" \
  "$HARNESS_ROOT/AUTO_RESEARCH_SLOW_SKILL.md" \
  "$HARNESS_ROOT/tools/research_closure.py" \
  "$HARNESS_ROOT/tools/claim_graph.py" \
  "$HARNESS_ROOT/tools/auto_research.py" \
  "$HARNESS_ROOT/tools/research_replay.py" \
  "$HARNESS_ROOT/tools/bootstrap_repo.sh"; do
  if [[ ! -f "$required" ]]; then
    echo "Invalid harness source; missing: $required" >&2
    exit 1
  fi
done

echo "Installing Research Closure Harness from:"
echo "  $HARNESS_ROOT"

backup_path "$CODEX_SKILL_DIR"
backup_path "$CODEX_HANDOFF_SKILL_DIR"
backup_path "$CODEX_AR_FAST_DIR"
backup_path "$CODEX_AR_SLOW_DIR"
backup_path "$CLAUDE_SKILL_DIR"
backup_path "$CLAUDE_HANDOFF_SKILL_DIR"
backup_path "$CLAUDE_AR_FAST_DIR"
backup_path "$CLAUDE_AR_SLOW_DIR"
backup_path "$DSH_AR_FAST_DIR"
backup_path "$DSH_AR_SLOW_DIR"
backup_path "$DATA_ROOT"
if [[ "$INSTALL_GLOBAL_RULES" -eq 1 ]]; then
  backup_path "$CODEX_AGENTS"
  backup_path "$CLAUDE_MD"
  backup_path "$DSH_AGENTS"
fi
if [[ "$WITH_CLAUDE_HOOK" -eq 1 ]]; then
  backup_path "$CLAUDE_SETTINGS"
  backup_path "$CLAUDE_HOOK"
fi

run mkdir -p \
  "$(dirname "$CODEX_SKILL_DIR")" \
  "$(dirname "$CLAUDE_SKILL_DIR")" \
  "$(dirname "$DSH_AR_FAST_DIR")" \
  "$(dirname "$DATA_ROOT")" \
  "$BIN_DIR"

run rm -rf "$DATA_ROOT"
run cp -R "$HARNESS_ROOT" "$DATA_ROOT"

run rm -rf \
  "$CODEX_SKILL_DIR" \
  "$CODEX_HANDOFF_SKILL_DIR" \
  "$CODEX_AR_FAST_DIR" \
  "$CODEX_AR_SLOW_DIR" \
  "$CLAUDE_SKILL_DIR" \
  "$CLAUDE_HANDOFF_SKILL_DIR" \
  "$CLAUDE_AR_FAST_DIR" \
  "$CLAUDE_AR_SLOW_DIR" \
  "$DSH_AR_FAST_DIR" \
  "$DSH_AR_SLOW_DIR"
run mkdir -p \
  "$CODEX_SKILL_DIR" \
  "$CODEX_HANDOFF_SKILL_DIR" \
  "$CODEX_AR_FAST_DIR" \
  "$CODEX_AR_SLOW_DIR" \
  "$CLAUDE_SKILL_DIR" \
  "$CLAUDE_HANDOFF_SKILL_DIR" \
  "$CLAUDE_AR_FAST_DIR" \
  "$CLAUDE_AR_SLOW_DIR" \
  "$DSH_AR_FAST_DIR" \
  "$DSH_AR_SLOW_DIR"
run cp "$HARNESS_ROOT/SKILL.md" "$CODEX_SKILL_DIR/SKILL.md"
run cp "$HARNESS_ROOT/SKILL.md" "$CLAUDE_SKILL_DIR/SKILL.md"
run cp "$HARNESS_ROOT/HANDOFF_SKILL.md" "$CODEX_HANDOFF_SKILL_DIR/SKILL.md"
run cp "$HARNESS_ROOT/HANDOFF_SKILL.md" "$CLAUDE_HANDOFF_SKILL_DIR/SKILL.md"
run cp "$HARNESS_ROOT/AUTO_RESEARCH_FAST_SKILL.md" "$CODEX_AR_FAST_DIR/SKILL.md"
run cp "$HARNESS_ROOT/AUTO_RESEARCH_FAST_SKILL.md" "$CLAUDE_AR_FAST_DIR/SKILL.md"
run cp "$HARNESS_ROOT/AUTO_RESEARCH_FAST_SKILL.md" "$DSH_AR_FAST_DIR/SKILL.md"
run cp "$HARNESS_ROOT/AUTO_RESEARCH_SLOW_SKILL.md" "$CODEX_AR_SLOW_DIR/SKILL.md"
run cp "$HARNESS_ROOT/AUTO_RESEARCH_SLOW_SKILL.md" "$CLAUDE_AR_SLOW_DIR/SKILL.md"
run cp "$HARNESS_ROOT/AUTO_RESEARCH_SLOW_SKILL.md" "$DSH_AR_SLOW_DIR/SKILL.md"

run chmod +x \
  "$DATA_ROOT/tools/research_closure.py" \
  "$DATA_ROOT/tools/claim_graph.py" \
  "$DATA_ROOT/tools/auto_research.py" \
  "$DATA_ROOT/tools/research_replay.py" \
  "$DATA_ROOT/tools/bootstrap_repo.sh" \
  "$DATA_ROOT/.claude/hooks/closure_guard.py"

run ln -sfn "$DATA_ROOT/tools/research_closure.py" "$BIN_DIR/research-closure"
run ln -sfn "$DATA_ROOT/tools/bootstrap_repo.sh" "$BIN_DIR/research-closure-init"
run ln -sfn "$DATA_ROOT/tools/claim_graph.py" "$BIN_DIR/research-closure-graph"
run ln -sfn "$DATA_ROOT/tools/research_replay.py" "$BIN_DIR/research-closure-replay"
run ln -sfn "$DATA_ROOT/tools/auto_research.py" "$BIN_DIR/auto-research"

if [[ "$INSTALL_GLOBAL_RULES" -eq 1 ]]; then
  CODEX_BLOCK="$(mktemp)"
  CLAUDE_BLOCK="$(mktemp)"
  DSH_BLOCK="$(mktemp)"
  cat >"$CODEX_BLOCK" <<'EOF'
## Research Closure + Auto-Research A/B

In repositories containing `.research/auto_research.json`:

- At session start run `auto-research ab-status` and `auto-research ab-next`.
- A (fast) invokes `$auto-research-fast` and only proposes candidates.
- B (slow) invokes `$auto-research-slow` and criticises, verifies, applies, or revalidates.
- The loop is: A proposes -> B criticises -> B hard-verifies -> B applies -> feedback to A.
- M0 is immutable. Soft judgment never replaces an exit-0 hard verification.

For research planning, experiment implementation, result analysis, scope changes, or paper-progress work:

- Invoke the `$research-closure` skill.
- Invoke the `$research-handoff` skill when agent output is pasted in for interpretation, or when writing a task for an agent that does not share the current conversation.
- In a repository containing `.research/state.json`, run `research-closure guard` before substantial work.
- Do not open a new method family or primary experiment before the previous experiment has an explicit decision.
- Treat new ideas as backlog items unless they directly test the frozen sprint claim.
- End substantial research work with: artifact, evidence, decision, and one next action.
EOF
  cat >"$CLAUDE_BLOCK" <<'EOF'
## Research Closure + Auto-Research A/B

In repositories containing `.research/auto_research.json`:

- At session start run `auto-research ab-status` and `auto-research ab-next`.
- A (fast) uses `/auto-research-fast` and only proposes candidates.
- B (slow) uses `/auto-research-slow` and criticises, verifies, applies, or revalidates.
- The loop is: A proposes -> B criticises -> B hard-verifies -> B applies -> feedback to A.
- M0 is immutable. Soft judgment never replaces an exit-0 hard verification.

For research planning, experiment implementation, result analysis, scope changes, or paper-progress work:

- Use the `/research-closure` skill.
- Use the `/research-handoff` skill when agent output is pasted in for interpretation, or when writing a task for an agent that does not share the current conversation.
- In a repository containing `.research/state.json`, run `research-closure guard` before substantial work.
- Do not open a new method family or primary experiment before the previous experiment has an explicit decision.
- Treat new ideas as backlog items unless they directly test the frozen sprint claim.
- End substantial research work with: artifact, evidence, decision, and one next action.
EOF
  cat >"$DSH_BLOCK" <<'EOF'
## Research Closure + Auto-Research A/B

In repositories containing `.research/auto_research.json`:

- At session start run `auto-research ab-status` and `auto-research ab-next`.
- A (fast) uses the `auto-research-fast` skill and only proposes candidates.
- B (slow) uses the `auto-research-slow` skill and criticises, verifies, applies, or revalidates.
- The loop is: A proposes -> B criticises -> B hard-verifies -> B applies -> feedback to A.
- M0 is immutable. Soft judgment never replaces an exit-0 hard verification.

For research planning, experiment implementation, result analysis, scope changes, or paper-progress work:

- Use the `research-closure` skill.
- Use the `research-handoff` skill when interpreting another agent's output.
- In a repository containing `.research/state.json`, run `research-closure guard` before substantial work.
- End substantial research work with: artifact, evidence, decision, and one next action.
EOF
  upsert_managed_block "$CODEX_AGENTS" "$CODEX_BLOCK"
  upsert_managed_block "$CLAUDE_MD" "$CLAUDE_BLOCK"
  upsert_managed_block "$DSH_AGENTS" "$DSH_BLOCK"
  rm -f "$CODEX_BLOCK" "$CLAUDE_BLOCK" "$DSH_BLOCK"
fi

if [[ "$WITH_CLAUDE_HOOK" -eq 1 ]]; then
  run mkdir -p "$(dirname "$CLAUDE_HOOK")"
  run cp "$HARNESS_ROOT/.claude/hooks/closure_guard.py" "$CLAUDE_HOOK"
  run chmod +x "$CLAUDE_HOOK"
  install_claude_hook_setting
fi

cat <<EOF

Installation complete.

Codex skills:
  $CODEX_SKILL_DIR/SKILL.md
  $CODEX_HANDOFF_SKILL_DIR/SKILL.md
  $CODEX_AR_FAST_DIR/SKILL.md
  $CODEX_AR_SLOW_DIR/SKILL.md

Claude Code skills:
  $CLAUDE_SKILL_DIR/SKILL.md
  $CLAUDE_HANDOFF_SKILL_DIR/SKILL.md
  $CLAUDE_AR_FAST_DIR/SKILL.md
  $CLAUDE_AR_SLOW_DIR/SKILL.md

DeepSeek Harness skills:
  $DSH_AR_FAST_DIR/SKILL.md
  $DSH_AR_SLOW_DIR/SKILL.md

Commands:
  $BIN_DIR/research-closure
  $BIN_DIR/research-closure-init
  $BIN_DIR/research-closure-graph
  $BIN_DIR/auto-research

Initialize a repository:
  cd /path/to/repository
  research-closure-init

Then:
  research-closure set-project --agenda "..." --question "..." --minimum "..."
  research-closure-graph init --claim "..." && research-closure-graph validate
  research-closure start-sprint --claim "..." --days 14 --artifact "..."
  research-closure next

Auto-research:
  auto-research ab-status
  auto-research ab-next

EOF

if [[ "$INSTALL_GLOBAL_RULES" -eq 1 ]]; then
  echo "DeepSeek Harness guidance: $DSH_AGENTS"
fi

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    echo "Note: $BIN_DIR is not currently on PATH."
    echo "Add this line to ~/.zshrc or ~/.bashrc:"
    echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
    ;;
esac

if [[ "$WITH_CLAUDE_HOOK" -eq 1 ]]; then
  echo "Claude hook installed. Restart Claude Code and run /hooks to inspect it."
else
  echo "Claude hook was not installed. Re-run with --with-claude-hook to enable it."
fi

echo "Backups, when created, are under:"
echo "  $BACKUP_ROOT"
