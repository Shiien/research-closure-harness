#!/usr/bin/env python3
"""Optional Claude Code PreToolUse hook for research scope expansion.

The hook is intentionally conservative:
- it only examines Bash tool calls;
- it only reacts to commands containing likely scope-expansion phrases;
- it only blocks when the repository's Research Closure guard already fails.

It discovers the repository from the current working directory, so the same hook
can be installed globally.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

RISK_PHRASES = (
    "new benchmark",
    "new objective",
    "new method",
    "new framework",
    "rewrite the framework",
    "start experiment",
    "launch experiment",
    "parameter sweep",
    "hyperparameter sweep",
)


def find_repo() -> Path | None:
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".research" / "state.json").exists():
            return candidate
    return None


def run_guard(repo: Path) -> subprocess.CompletedProcess[str] | None:
    local_cli = repo / "tools" / "research_closure.py"
    if local_cli.exists():
        command = [sys.executable, str(local_cli), "guard"]
    else:
        global_cli = shutil.which("research-closure")
        if not global_cli:
            return None
        command = [global_cli, "guard"]
    return subprocess.run(
        command,
        cwd=repo,
        capture_output=True,
        text=True,
        env={**os.environ, "RESEARCH_CLOSURE_ROOT": str(repo)},
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if payload.get("tool_name") not in (None, "Bash"):
        return 0

    command = str(payload.get("tool_input", {}).get("command", "")).lower()
    if not any(term in command for term in RISK_PHRASES):
        return 0

    repo = find_repo()
    if repo is None:
        return 0

    proc = run_guard(repo)
    if proc is None or proc.returncode == 0:
        return 0

    result = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "Research Closure Guard failed. Close or explicitly revise the "
                "current sprint/experiment before starting a new branch of work.\n"
                + proc.stdout
            ),
        }
    }
    json.dump(result, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
