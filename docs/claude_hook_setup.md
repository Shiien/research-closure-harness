# Optional Claude Code Hook Setup

`CLAUDE.md` is advisory context. The optional hook adds a deterministic check before selected shell operations.

The included hook is deliberately conservative and should be reviewed before enabling:

```text
.claude/hooks/closure_guard.py
```

A sample project settings fragment is provided in:

```text
.claude/settings.example.json
```

Copy or merge it into `.claude/settings.json`. Then use `/hooks` in Claude Code to verify that the project hook is registered.

The hook does not attempt to understand research semantics. It only blocks some likely scope-expansion commands when the local `guard` command is already failing. The primary safeguard remains the experiment-card workflow.
