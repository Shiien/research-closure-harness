# Examples for the Research Replay System

Every example is **two things at once**:

1. `script.json` — an event script: the research process as an ordered list of
   CLI invocations (with notes explaining each step). This is the *source of
   truth* for replay: any prefix of the script reproduces that intermediate
   state, mechanically.
2. `research/` — the **materialised** half-finished research directory
   generated from the script (`.research/state.json`, `.research/claim_graph.json`,
   `.research/logs/`). This is what a "half-finished research" looks like on
   disk and what the harness reads to continue.

Both directories are committed so you can inspect them immediately; regenerate
them any time with the replay tool.

## The examples

| Example | State it represents |
|---|---|
| `conditioning_recovery/` | Rich mid-flight study: sprint frozen, P1 and P2 resolved `positive`, **EXP-003 is open on P3** (the handoff point), one idea backlogged. |
| `minimal_handoff/` | The smallest possible mid-flight state: project + tiny causal graph frozen, **EXP-001 open on P1**, nothing closed yet. |

In both, `guard` PASSes and `next` says: close the active experiment — a clean
place for a human or another agent to pick the work up.

## The replay tool (`tools/research_replay.py`)

```bash
# script -> fresh research directory (auto-inits; any prefix is reproducible)
python tools/research_replay.py run --script example/minimal_handoff/script.json --out /tmp/continued

# stop after N steps -> a reproducible intermediate state
python tools/research_replay.py run --script example/conditioning_recovery/script.json --until 18 --out /tmp/design-only

# half-finished research directory -> rebuild script (same snapshot, not the history)
python tools/research_replay.py export --dir example/conditioning_recovery/research --out /tmp/rebuild.json
python tools/research_replay.py run --script /tmp/rebuild.json --out /tmp/resumed   # continue from the copy

# step-by-step replay -> scrubber HTML over per-step dashboards (open in browser)
python tools/research_replay.py timeline --script example/conditioning_recovery/script.json --out /tmp/replay.html
```

### How this maps to the four use cases

- **Cross-session / cross-agent continuation**: point the harness at the
  materialised directory (`RESEARCH_CLOSURE_ROOT=<dir>` or just `cd` into it),
  run `guard` / `frontier` / `next`, and continue. The event log is the audit
  trail of everything that already happened.
- **Cross-environment migration / backup**: `export` any half-finished research
  directory to a script, move the script (or zip the materialised directory),
  `run` it anywhere — the same snapshot is rebuilt.
- **Teaching / demo**: `timeline` turns a script into a scrubbable story of the
  research evolving step by step (each frame is a real dashboard).
- **Audit / review**: the event log (chronological, in `state.json`), the
  pre-registered resolution map, and the probe outcomes together show exactly
  how the research reached its current position — and what was ruled out.

### Notes

- Replay always starts from `init`; scripts never contain an `init` step.
- Amendments (`amend`) and theory nodes (`induce`) are applied from ranked
  candidate sets and are not mechanically reconstructible — `export` records a
  comment where they occurred so a human can re-apply them.
- The rebuild from `export` is an *equivalent snapshot* (same project, sprint,
  graph design, probe outcomes, active experiment, backlog), not a replay of
  the exact history — the history lives in the event log.
