#!/usr/bin/env python3
"""Research Closure Harness CLI (claim-graph engine).

The claim graph (`.research/claim_graph.json`) is the engine: a sprint cannot be
frozen, an experiment cannot be opened, and a result cannot be recorded without
it. This CLI drives the lifecycle state machine on top of the graph.

The system is event-driven: every command fires one event (project_set,
sprint_started, experiment_started, ...) recorded in `.research/state.json`,
and `guard` / `next` derive the next event to fire from the state and the
graph's ready frontier.

No third-party dependencies.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from html import escape as html_escape
from pathlib import Path
from typing import Any

try:
    import claim_graph as cg
except ImportError:
    _tools_dir = str(Path(__file__).resolve().parent)
    if _tools_dir not in sys.path:
        sys.path.insert(0, _tools_dir)
    try:
        import claim_graph as cg
    except ImportError as exc:
        raise SystemExit(
            "BLOCKED: tools/claim_graph.py is missing. The claim graph is the "
            "engine of this harness and must live next to research_closure.py."
        ) from exc

STATE_VERSION = 3

# claim-level verdict (resolution map) -> close-sprint decision value
DECISION_MAP = {
    "supported": "advance",
    "falsified": "terminate",
    "narrow": "narrow",
    "terminated": "terminate",
}


def discover_root() -> Path:
    """Find the active research repository.

    Priority:
    1. RESEARCH_CLOSURE_ROOT
    2. Current directory or one of its parents containing .research/state.json
    3. Current directory, which allows `init` to bootstrap a new repository
    """
    explicit = os.environ.get("RESEARCH_CLOSURE_ROOT")
    if explicit:
        return Path(explicit).expanduser().resolve()

    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".research" / "state.json").exists():
            return candidate
    return cwd


ROOT = discover_root()
STATE_PATH = ROOT / ".research" / "state.json"
LOG_DIR = ROOT / ".research" / "logs"
GRAPH_PATH = ROOT / ".research" / "claim_graph.json"
SNAP_DIR = ROOT / ".research" / "snapshots"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        raise SystemExit("State not found. Run: python tools/research_closure.py init")
    try:
        state = json.loads(STATE_PATH.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid state file: {exc}") from exc
    if state.get("version", 0) < STATE_VERSION:
        # v3: day bookkeeping is gone; the history field is the event log.
        state.pop("day", None)
        if "history" in state:
            state.setdefault("events", []).extend(state.pop("history"))
        state.setdefault("events", [])
        state["events"].sort(key=lambda e: e.get("at", ""))
        state["version"] = STATE_VERSION
        save_state(state)
    return state


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")
    record_snapshot(state)


def record_snapshot(state: dict[str, Any]) -> None:
    """Checkpoint state + claim graph after a mutation.

    Every mutation writes a snapshot pair into .research/snapshots/, which is
    what lets the dashboard scrub back through the history of the research
    with full fidelity (no reconstruction from lossy event payloads).
    Best-effort; deduplicated against the previous snapshot.
    """
    try:
        SNAP_DIR.mkdir(parents=True, exist_ok=True)
        state_files = sorted(SNAP_DIR.glob("*_state.json"))
        if state_files:
            try:
                last = json.loads(state_files[-1].read_text(encoding="utf-8"))
            except Exception:
                last = None
            same_state = last == state
            same_graph = False
            if same_state and GRAPH_PATH.exists():
                graph_files = sorted(SNAP_DIR.glob("*_graph.json"))
                if graph_files:
                    same_graph = (graph_files[-1].read_bytes()
                                  == GRAPH_PATH.read_bytes())
            if same_state and (not GRAPH_PATH.exists() or same_graph):
                return
        stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
        # globally monotonic sequence: same-second snapshots must still sort
        # chronologically by filename, not by event name
        seq = len(state_files)
        while (SNAP_DIR / f"{stamp}_{seq:03d}_state.json").exists():
            seq += 1
        events = state.get("events") or []
        event = (events[-1].get("event", "mutation") if events else "init")
        base = SNAP_DIR / f"{stamp}_{seq:03d}_{event}"
        base.with_name(base.name + "_state.json").write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8")
        if GRAPH_PATH.exists():
            base.with_name(base.name + "_graph.json").write_bytes(
                GRAPH_PATH.read_bytes())
    except Exception:
        pass


def load_snapshot_frames() -> list[tuple[str, dict[str, Any], dict[str, Any] | None]]:
    """(label, state, graph) per snapshot, oldest first."""
    frames: list[tuple[str, dict[str, Any], dict[str, Any] | None]] = []
    if not SNAP_DIR.exists():
        return frames
    for f in sorted(SNAP_DIR.glob("*_state.json")):
        try:
            st = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        graph = None
        gf = f.with_name(f.name.replace("_state.json", "_graph.json"))
        if gf.exists():
            try:
                graph = json.loads(gf.read_text(encoding="utf-8"))
            except Exception:
                graph = None
        # filename: <date>_<time>_<seq>_<event>_state.json
        parts = f.stem.replace("_state", "").split("_")
        clock = f"{parts[1][:2]}:{parts[1][2:4]}:{parts[1][4:6]}" if len(parts) > 1 and len(parts[1]) >= 6 else ""
        event = "_".join(parts[3:]) if len(parts) > 3 else "snapshot"
        frames.append((f"{clock} {event}".strip(), st, graph))
    return frames


def append_event(state: dict[str, Any], event: str, payload: dict[str, Any]) -> None:
    state.setdefault("events", []).append(
        {"at": now_iso(), "event": event, "payload": payload}
    )


def write_log(filename: str, content: str) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / filename
    path.write_text(content.strip() + "\n")
    return path


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_graph_or_exit() -> dict[str, Any]:
    if not GRAPH_PATH.exists():
        raise SystemExit(
            f"BLOCKED: no claim graph at {rel(GRAPH_PATH)}. The claim graph is the "
            f"engine of this harness. Run: claim_graph.py init --claim '<the claim>'"
        )
    return cg.load_graph(GRAPH_PATH)


def cmd_init(_: argparse.Namespace) -> int:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if STATE_PATH.exists():
        print(f"State already exists: {rel(STATE_PATH)}")
        return 0
    state = {
        "version": STATE_VERSION,
        "mode": "graduation",
        "project": {"question": "", "long_term_agenda": "", "minimum_completion": ""},
        "sprint": None,
        "active_experiment": None,
        "counters": {"experiment": 0, "idea": 0},
        "events": [],
        "limits": {"active_sprints": 1, "active_experiments": 1},
    }
    save_state(state)
    print(f"Initialized {rel(STATE_PATH)}")
    print("Next: set-project, then claim_graph.py init --claim '<the sprint claim>'")
    return 0


def cmd_set_project(args: argparse.Namespace) -> int:
    state = load_state()
    state["project"] = {
        "question": args.question,
        "long_term_agenda": args.agenda,
        "minimum_completion": args.minimum,
    }
    append_event(state, "project_set", state["project"])
    save_state(state)
    print("Project charter recorded.")
    return 0


def cmd_start_sprint(args: argparse.Namespace) -> int:
    state = load_state()
    if state.get("sprint"):
        raise SystemExit(
            "BLOCKED: an active sprint already exists. Close or explicitly revise it first."
        )
    graph = load_graph_or_exit()
    blocks, _ = cg.validate(graph)
    if blocks:
        raise SystemExit(
            "BLOCKED: claim graph fails validation; fix it before freezing a sprint:\n  "
            + "\n  ".join(blocks)
        )
    if not graph.get("probes"):
        raise SystemExit(
            "BLOCKED: the claim graph has no probes. Author variables, edges and "
            "probes first: claim_graph.py add-variable / add-edge / add-probe."
        )
    if not graph.get("resolution"):
        print("WARNING: the claim graph has no resolution map; close-sprint decisions "
              "will not be machine-checked.")
    if graph.get("claim") and graph["claim"] != args.claim:
        print(f"WARNING: claim graph claim ({graph['claim']!r}) differs from the sprint "
              f"claim ({args.claim!r}). The design hash will freeze as-is.")
    start = datetime.now().astimezone()
    end = start + timedelta(days=args.days)
    sprint = {
        "claim": args.claim,
        "artifact": args.artifact,
        "started_at": start.isoformat(timespec="seconds"),
        "ends_at": end.isoformat(timespec="seconds"),
        "status": "active",
        "claim_graph": {
            "path": rel(GRAPH_PATH),
            "design_hash": cg.design_hash(graph),
            "frozen_at": now_iso(),
        },
    }
    state["sprint"] = sprint
    append_event(state, "sprint_started", sprint)
    save_state(state)
    path = write_log(
        f"{start.date().isoformat()}_sprint.md",
        f"""# Sprint

## Frozen claim

{args.claim}

## Required artifact

{args.artifact}

## Claim graph

Path: {rel(GRAPH_PATH)}
Design hash: {sprint['claim_graph']['design_hash']} (frozen at {sprint['claim_graph']['frozen_at']})

## Start

{start.isoformat(timespec="seconds")}

## End

{end.isoformat(timespec="seconds")}

## Out of scope

To be filled before implementation.
""",
    )
    print(f"Sprint started. Log: {rel(path)}")
    return 0


def cmd_close_sprint(args: argparse.Namespace) -> int:
    state = load_state()
    sprint = state.get("sprint")
    if not sprint:
        raise SystemExit("No active sprint.")
    if state.get("active_experiment"):
        raise SystemExit("BLOCKED: close the active experiment before closing the sprint.")
    graph = load_graph_or_exit()
    proposal = cg.propose_decision(graph)
    if proposal["status"] == "determined":
        expected = DECISION_MAP.get(proposal["then"])
        if expected and args.decision != expected:
            raise SystemExit(
                f"BLOCKED: the resolution map, frozen before results, determines "
                f"'{proposal['then']}' -> close as '{expected}', not '{args.decision}'. "
                f"To override, amend the map explicitly; do not reinterpret it silently."
            )
        if proposal.get("blocked_by_debt"):
            raise SystemExit(
                "BLOCKED: unpaid amendment debt. The graph was repaired to fit an "
                "anomaly and the repair has not been tested. Close as 'narrow'."
            )
    else:
        print(f"NOTE: resolution map not yet determined; probes still ready: "
              f"{cg.frontier(graph) or 'none'}")
    record = {
        **sprint,
        "closed_at": now_iso(),
        "decision": args.decision,
        "evidence": args.evidence,
        "conclusion": args.conclusion,
    }
    append_event(state, "sprint_closed", record)
    state["sprint"] = None
    save_state(state)
    path = write_log(
        f"{today_logname()}_sprint_decision.md",
        f"""# Sprint Decision

## Claim

{sprint['claim']}

## Decision

{args.decision}

## Evidence

{args.evidence}

## Conclusion

{args.conclusion}
""",
    )
    print(f"Sprint closed. Log: {rel(path)}")
    return 0


def today_logname() -> str:
    return datetime.now().astimezone().date().isoformat()


def cmd_new_experiment(args: argparse.Namespace) -> int:
    state = load_state()
    if not state.get("sprint"):
        raise SystemExit("BLOCKED: no active sprint.")
    if state.get("active_experiment"):
        active = state["active_experiment"]["id"]
        raise SystemExit(
            f"BLOCKED: {active} is still active. Close it before opening a new primary experiment."
        )
    graph = load_graph_or_exit()
    if not args.node:
        raise SystemExit(
            "BLOCKED: every experiment must state which probe it runs (--node). "
            f"Ready probes: {cg.frontier(graph) or 'none'}"
        )
    if args.node not in graph.get("probes", {}):
        raise SystemExit(f"BLOCKED: {args.node} is not a probe in the claim graph.")
    ready = cg.frontier(graph)
    if args.node not in ready:
        raise SystemExit(
            f"BLOCKED: {args.node} is not on the ready frontier (ready: {ready or 'none'}). "
            "Its upstream guards are unmet, so its result would not be interpretable."
        )
    probe = graph["probes"][args.node]
    if graph.get("graph_type") == "causal" and probe.get("tests", {}).get("kind") == "edge":
        declared = [c.strip() for c in args.controls.split(",") if c.strip()]
        ok, why = cg.verify_adjustment(
            cg.edge_list(graph), probe["tests"]["from"], probe["tests"]["to"], declared
        )
        if not ok:
            rec, _ = cg.recommend_adjustment(
                graph, probe["tests"]["from"], probe["tests"]["to"]
            )
            raise SystemExit(
                f"BLOCKED: --controls {declared} is not a valid adjustment set: {why}. "
                f"Back-door criterion suggests {rec}."
            )
    state["counters"]["experiment"] += 1
    exp_id = f"EXP-{state['counters']['experiment']:03d}"
    exp = {
        "id": exp_id,
        "question": args.question,
        "hypothesis": args.hypothesis,
        "intervention": args.intervention,
        "measurement": args.measurement,
        "kill_criterion": args.kill,
        "expected_artifact": args.artifact,
        "time_budget_hours": args.hours,
        "started_at": now_iso(),
        "status": "active",
        "claim_graph_node": args.node,
        "controls": args.controls,
        "expected_figure": args.figure,
    }
    state["active_experiment"] = exp
    append_event(state, "experiment_started", exp)
    save_state(state)
    path = write_log(
        f"{exp_id}.md",
        f"""# Experiment {exp_id}

## Frozen sprint claim

{state['sprint']['claim']}

## Claim-graph probe

{args.node} (guards: {probe.get('guards_in', []) or 'none'})

## Research question

{args.question}

## Hypothesis

{args.hypothesis}

## Intervention

{args.intervention}

## Measurement

{args.measurement}

## Expected artifact

{args.artifact}

## Kill criterion

{args.kill}

## Time budget

{args.hours} hours

## Result

Pending.

## Decision

Pending.
""",
    )
    print(f"Experiment opened: {exp_id} on probe {args.node}. Card: {rel(path)}")
    return 0


def cmd_close_experiment(args: argparse.Namespace) -> int:
    state = load_state()
    exp = state.get("active_experiment")
    if not exp:
        raise SystemExit("No active experiment.")
    if args.id != exp["id"]:
        raise SystemExit(f"Active experiment is {exp['id']}, not {args.id}.")
    record = {
        **exp,
        "closed_at": now_iso(),
        "decision": args.decision,
        "evidence": args.evidence,
        "conclusion": args.conclusion,
        "status": "closed",
    }
    if args.decision == "inconclusive" and not args.defect:
        raise SystemExit(
            "BLOCKED: an inconclusive result must name its defect class. "
            "implementation/measurement/design defects leave the hypothesis untouched and "
            "must not count toward claim narrowing; only 'hypothesis' does."
        )
    if (
        args.decision == "inconclusive"
        and args.defect != "hypothesis"
        and args.outcome
        and args.outcome != "unresolved"
    ):
        raise SystemExit(
            f"BLOCKED: a {args.defect} defect leaves the hypothesis untested, so the probe "
            f"outcome must be 'unresolved', not '{args.outcome}'. Only defect class "
            "'hypothesis' may advance the resolution map."
        )
    record["defect"] = args.defect

    node = exp.get("claim_graph_node")
    graph = load_graph_or_exit()
    outcome = args.outcome or {
        "supported": "positive", "falsified": "negative",
        "inconclusive": "unresolved", "terminated": "unresolved",
    }[args.decision]
    graph["probes"][node]["outcome"] = outcome
    graph["probes"][node]["experiment_id"] = exp["id"]
    graph["probes"][node]["defect"] = args.defect
    cg.save_graph(graph, GRAPH_PATH)
    proposal = cg.propose_decision(graph)
    print(f"Claim-graph node {node} set to {outcome}.")
    if proposal["status"] == "determined":
        print(f"Resolution map determines: {proposal['then']}"
              + (f" (rung: {proposal['rung']})" if proposal.get("rung") else ""))
    else:
        print(f"Line still open. Ready next: {proposal['frontier'] or 'none'}")

    append_event(state, "experiment_closed", record)
    state["active_experiment"] = None
    save_state(state)
    path = LOG_DIR / f"{args.id}.md"
    with path.open("a") as f:
        f.write(
            f"""
## Evidence

{args.evidence}

## Decision

{args.decision}

## Evidence-backed conclusion

{args.conclusion}

## Closed at

{record['closed_at']}
"""
        )
    print(f"Experiment closed: {args.id}. Decision: {args.decision}")
    return 0


def cmd_add_idea(args: argparse.Namespace) -> int:
    state = load_state()
    state["counters"]["idea"] += 1
    idea_id = f"IDEA-{state['counters']['idea']:03d}"
    record = {
        "id": idea_id,
        "idea": args.idea,
        "reason_not_now": args.reason,
        "revisit": args.revisit or "",
        "created_at": now_iso(),
    }
    append_event(state, "idea_backlogged", record)
    save_state(state)
    path = write_log(
        f"{idea_id}.md",
        f"""# {idea_id}

## Idea

{args.idea}

## Why not now

{args.reason}

## Revisit condition

{args.revisit or 'After the current sprint is closed.'}
""",
    )
    print(f"Idea backlogged: {idea_id}. Log: {rel(path)}")
    return 0


def guard_messages(state: dict[str, Any]) -> tuple[list[str], list[str]]:
    blocks: list[str] = []
    warnings: list[str] = []
    project = state.get("project", {})
    if not project.get("question"):
        warnings.append("Project question is empty. Use set-project.")
    sprint = state.get("sprint")
    if not sprint:
        blocks.append("No active sprint claim.")
    else:
        try:
            ends_at = datetime.fromisoformat(sprint["ends_at"])
            if datetime.now().astimezone() > ends_at:
                warnings.append("Sprint deadline has passed; close or explicitly revise it.")
        except Exception:
            warnings.append("Sprint end date could not be parsed.")

    if not GRAPH_PATH.exists():
        blocks.append(
            f"No claim graph at {rel(GRAPH_PATH)}. The claim graph is the engine; run "
            "claim_graph.py init --claim '<the claim>' and author probes before freezing a sprint."
        )
    else:
        graph = cg.load_graph(GRAPH_PATH)
        gblocks, gwarn = cg.validate(graph)
        blocks.extend(gblocks)
        warnings.extend(gwarn)
        if not graph.get("probes"):
            blocks.append(
                "The claim graph has no probes. Author variables, edges and probes with "
                "claim_graph.py add-variable / add-edge / add-probe before freezing a sprint."
            )

        frozen = (sprint or {}).get("claim_graph", {}).get("design_hash")
        if frozen and frozen != cg.design_hash(graph):
            blocks.append(
                "claim graph design has drifted since the sprint was frozen. The "
                "pre-registration no longer matches what will be reported. Record an "
                "amendment or close the sprint."
            )
        exp = state.get("active_experiment")
        if exp:
            if not exp.get("kill_criterion"):
                blocks.append("Active experiment has no kill criterion.")
            if not exp.get("expected_artifact"):
                blocks.append("Active experiment has no expected artifact.")
            node = exp.get("claim_graph_node")
            if node and node not in cg.frontier(graph) + list(cg.outcomes_map(graph)):
                blocks.append(
                    f"active experiment runs {node}, which is not on "
                    f"the ready frontier"
                )
        debts = cg.unpaid_debts(graph)
        if debts:
            warnings.append(
                f"{len(debts)} unpaid amendment debt(s); this line cannot close as supported"
            )
        proposal = cg.propose_decision(graph)
        if proposal["status"] == "determined":
            warnings.append(
                f"resolution map is already determined ({proposal['then']}); "
                f"further probes on this line are out of scope"
            )
    return blocks, warnings


def next_events(state: dict[str, Any], graph: dict[str, Any] | None) -> list[str]:
    """The event chain the state and the graph permit next, primary first."""
    out: list[str] = []
    project = state.get("project", {})
    if not project.get("question"):
        out.append("set-project --question '<the project question>' "
                   "--agenda '<long-term agenda>' --minimum '<minimum completion>'")
    if graph is None:
        out.append("claim_graph.py init --claim '<the sprint claim>'")
        return out
    if not graph.get("probes"):
        out.append("author the claim graph: claim_graph.py add-variable --id X --name '...' "
                   "--role '<intervention|outcome|...>'  (then add-edge, add-probe, add-resolution)")
        return out
    sprint = state.get("sprint")
    if not sprint:
        out.append("start-sprint --claim '<the frozen claim>' --artifact '<required artifact>' [--days 14]")
        return out
    exp = state.get("active_experiment")
    if exp:
        out.append(
            f"close-experiment --id {exp['id']} "
            "--decision <supported|falsified|inconclusive|terminated> "
            "--evidence '<artifact paths>' --conclusion '<what the evidence says>'"
        )
        return out
    proposal = cg.propose_decision(graph)
    if proposal["status"] == "determined":
        expected = DECISION_MAP.get(proposal["then"], "<decide>")
        out.append(f"close-sprint --decision {expected} --evidence '<paths>' --conclusion '<summary>'")
    else:
        ready = cg.frontier(graph)
        if ready:
            out.append(
                f"new-experiment --node {ready[0]} --question '<q>' --hypothesis '<h>' "
                "--intervention '<i>' --measurement '<m>' --kill '<kill criterion>' "
                "--artifact '<expected artifact>' --hours <budget>"
            )
        else:
            out.append(
                "claim_graph.py frontier  # nothing is ready: the line is resolved, "
                "blocked by unmet guards, or waiting on an amendment"
            )
    return out


def cmd_guard(_: argparse.Namespace) -> int:
    state = load_state()
    blocks, warnings = guard_messages(state)
    print("RESEARCH CLOSURE GUARD")
    if state.get("sprint"):
        print(f"Frozen claim: {state['sprint']['claim']}")
    if state.get("active_experiment"):
        print(f"Active experiment: {state['active_experiment']['id']}")
    for msg in warnings:
        print(f"WARNING: {msg}")
    for msg in blocks:
        print(f"BLOCK: {msg}")
    graph = cg.load_graph(GRAPH_PATH) if GRAPH_PATH.exists() else None
    if graph:
        print(f"Ready frontier: {cg.frontier(graph) or 'none'}")
    if state.get("sprint") or graph is None or not graph.get("probes"):
        print(f"Next event: {next_events(state, graph)[0]}")
    if graph:
        print("Dashboard: python tools/research_closure.py dashboard")
    if blocks:
        return 2
    print("PASS: work may proceed within the frozen claim.")
    return 0


def cmd_next(_: argparse.Namespace) -> int:
    state = load_state()
    graph = cg.load_graph(GRAPH_PATH) if GRAPH_PATH.exists() else None
    print("NEXT EVENTS")
    for i, ev in enumerate(next_events(state, graph), 1):
        print(f"  {i}. {ev}")
    return 0


def cmd_events(args: argparse.Namespace) -> int:
    state = load_state()
    events = state.get("events", [])
    if args.json:
        print(json.dumps(events, indent=2))
        return 0
    print(f"EVENT LOG ({len(events)} events)")
    for e in events:
        payload = json.dumps(e.get("payload", {}), ensure_ascii=False, separators=(",", ":"))
        if len(payload) > 100:
            payload = payload[:97] + "..."
        print(f"  {e.get('at')}  {e.get('event')}  {payload}")
    return 0


# --------------------------------------------------------------------------
# dashboard: self-contained interactive DAG for human progress tracking
# --------------------------------------------------------------------------

# NOTE: raw string on purpose — the JS inside keeps its own escape sequences
# (\n, \u2717, ...) and must not be interpreted by Python.
DASHBOARD_CSS = r"""
:root{color-scheme:dark;--bg:#0b1220;--panel:#111a2e;--surface:#0d1626;--surface-strong:#0f172a;
--line:#1e293b;--line-soft:#1a2740;--text:#e2e8f0;--text-soft:#cbd5e1;--muted:#94a3b8;
--subtle:#64748b;--green:#22c55e;--red:#f87171;--amber:#fbbf24;--violet:#a78bfa;--blue:#38bdf8;
--ok-bg:#052e16;--ok-border:#166534;--ok-text:#bbf7d0;--bad-bg:#450a0a;--bad-border:#7f1d1d;
--bad-text:#fecaca;--info-bg:#082f49;--info-border:#0c4a6e;--info-text:#7dd3fc;
--button-hover:#0c4a6e;--theory-fill:#2e1065;--theory-stroke:#a78bfa;--variable-fill:#082f49;
--variable-stroke:#38bdf8;--latent-fill:#1e293b;--latent-stroke:#64748b;--probe-ready-fill:#052e16;
--probe-ready-stroke:#22c55e;--probe-positive-fill:#14532d;--probe-positive-stroke:#4ade80;
--probe-negative-fill:#450a0a;--probe-negative-stroke:#f87171;--probe-neutral-fill:#1e293b;
--probe-neutral-stroke:#94a3b8;--probe-waiting-fill:#111a2e;--probe-waiting-stroke:#475569;
--node-text:#e2e8f0;--badge-text:#7dd3fc;--edge-stroke:#64748b;--guard-stroke:#7dd3fc;
--claim-fill:#172554;--claim-stroke:#60a5fa;--experiment-fill:#312e81;--experiment-stroke:#818cf8;
--result-fill:#052e16;--result-stroke:#4ade80;--decision-fill:#3b0764;--decision-stroke:#c084fc;
--amendment-fill:#451a03;--amendment-stroke:#fbbf24;--flow-stroke:#64748b;--tests-stroke:#38bdf8;
--tooltip-shadow:rgba(0,0,0,.5)}
:root[data-theme="light"]{color-scheme:light;--bg:#f8fafc;--panel:#ffffff;--surface:#f1f5f9;
--surface-strong:#ffffff;--line:#cbd5e1;--line-soft:#e2e8f0;--text:#0f172a;--text-soft:#334155;
--muted:#475569;--subtle:#64748b;--green:#15803d;--red:#b91c1c;--amber:#a16207;
--violet:#7c3aed;--blue:#0369a1;--ok-bg:#dcfce7;--ok-border:#86efac;--ok-text:#166534;
--bad-bg:#fee2e2;--bad-border:#fca5a5;--bad-text:#991b1b;--info-bg:#e0f2fe;
--info-border:#7dd3fc;--info-text:#075985;--button-hover:#bae6fd;--theory-fill:#ede9fe;
--theory-stroke:#7c3aed;--variable-fill:#e0f2fe;--variable-stroke:#0284c7;--latent-fill:#e2e8f0;
--latent-stroke:#64748b;--probe-ready-fill:#dcfce7;--probe-ready-stroke:#16a34a;
--probe-positive-fill:#bbf7d0;--probe-positive-stroke:#15803d;--probe-negative-fill:#fee2e2;
--probe-negative-stroke:#b91c1c;--probe-neutral-fill:#e2e8f0;--probe-neutral-stroke:#64748b;
--probe-waiting-fill:#f1f5f9;--probe-waiting-stroke:#94a3b8;--node-text:#0f172a;
--badge-text:#075985;--edge-stroke:#64748b;--guard-stroke:#0284c7;--claim-fill:#dbeafe;
--claim-stroke:#2563eb;--experiment-fill:#e0e7ff;--experiment-stroke:#6366f1;--result-fill:#dcfce7;
--result-stroke:#16a34a;--decision-fill:#f3e8ff;--decision-stroke:#9333ea;--amendment-fill:#fef3c7;
--amendment-stroke:#d97706;--flow-stroke:#64748b;--tests-stroke:#0284c7;--tooltip-shadow:rgba(15,23,42,.18)}
*{box-sizing:border-box}
body{font-family:system-ui,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--text);margin:0}
header{padding:14px 22px;border-bottom:1px solid var(--line);background:var(--panel)}
h1{font-size:17px;margin:0;letter-spacing:.3px}
h2{font-size:13px;margin:0 0 8px;text-transform:uppercase;letter-spacing:1px;color:var(--muted)}
.meta{color:var(--muted);font-size:12px;margin-top:6px;line-height:1.7}
.chip{display:inline-block;padding:1px 8px;border-radius:99px;font-size:11px;font-weight:600;margin-left:6px}
.chip.ok{background:var(--ok-bg);color:var(--ok-text);border:1px solid var(--ok-border)}
.chip.bad{background:var(--bad-bg);color:var(--bad-text);border:1px solid var(--bad-border)}
.chip.info{background:var(--info-bg);color:var(--info-text);border:1px solid var(--info-border)}
main{display:grid;grid-template-columns:minmax(0,1fr);gap:14px;padding:14px 22px;align-items:start}
main>div+div{margin-top:0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card+.card{margin-top:14px}
#canvas-wrap{position:relative;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:var(--surface);min-height:600px}
svg#dag{display:block;width:100%;height:600px;min-height:600px;cursor:grab;touch-action:none}
svg#dag.dragging{cursor:grabbing}
svg#dag g[data-node]{cursor:move}
svg#dag g[data-node]:hover>*:not(title){filter:brightness(1.08)}
svg#dag g[data-node].node-dragging{cursor:grabbing}
#tooltip{position:absolute;display:none;width:min(480px,calc(100% - 28px));max-height:520px;overflow:auto;
background:var(--surface-strong);border:1px solid var(--line);border-radius:9px;padding:11px 12px;font-size:12px;
line-height:1.55;pointer-events:auto;z-index:20;box-shadow:0 10px 30px var(--tooltip-shadow)}
#tooltip.pinned{border-color:var(--blue);box-shadow:0 12px 34px var(--tooltip-shadow),0 0 0 2px var(--info-bg)}
#tooltip .pin-status{display:flex;align-items:center;gap:6px;margin:-2px 0 8px;color:var(--info-text);
font-size:10px;font-weight:800;letter-spacing:.45px;text-transform:uppercase}
#tooltip .pin-status::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--blue)}
#tooltip .detail-title,#probe-detail .detail-title{font-size:13px;font-weight:750;color:var(--text);margin-bottom:2px}
#tooltip .detail-subtitle,#probe-detail .detail-subtitle{color:var(--muted);margin-bottom:7px}
#tooltip .detail-label,#probe-detail .detail-label{margin-top:8px;color:var(--badge-text);font-size:10px;
font-weight:800;text-transform:uppercase;letter-spacing:.7px}
#tooltip pre,#probe-detail pre{margin:3px 0 0;background:var(--surface);border:1px solid var(--line);border-radius:6px;
padding:7px 8px;font-size:11px;line-height:1.45;overflow:auto;white-space:pre-wrap;max-height:220px}
.artifact-list{display:grid;gap:7px;margin-top:4px}
.artifact-ref{border:1px solid var(--line);border-radius:6px;padding:6px 7px;background:var(--surface)}
.artifact-ref a{color:var(--info-text);font-family:ui-monospace,Consolas,monospace;font-size:10.5px;overflow-wrap:anywhere}
.artifact-ref img{display:block;max-width:100%;max-height:180px;object-fit:contain;margin-top:6px;border-radius:4px;border:1px solid var(--line)}
#zoom-hint{position:absolute;top:8px;right:10px;font-size:11px;color:var(--muted);user-select:none}
#reset-view,#fit-view{position:absolute;top:8px;font-size:11px;color:var(--info-text);cursor:pointer;
border:1px solid var(--info-border);border-radius:6px;padding:2px 8px;background:var(--info-bg);user-select:none}
#fit-view{left:10px}
#reset-view{left:105px}
#probe-detail{font-size:12px;line-height:1.7;min-height:120px;color:var(--text-soft)}
#probe-detail b{color:var(--text)}
table{width:100%;border-collapse:collapse;font-size:11.5px}
th,td{text-align:left;padding:4px 8px;border-bottom:1px solid var(--line-soft);vertical-align:top}
th{color:var(--muted);font-weight:600;text-transform:uppercase;font-size:10.5px;letter-spacing:.6px}
td.mono,code{font-family:ui-monospace,Consolas,monospace;font-size:11px}
.rule{border:1px solid var(--line);border-radius:8px;padding:8px 10px;margin-bottom:8px;font-size:12px}
.rule.fires{border-color:var(--ok-border);background:var(--ok-bg)}
.rule .when{color:var(--text-soft)}
.rule .then{font-weight:700;margin-top:2px}
.rule .note{color:var(--muted);margin-top:4px;font-size:11px}
.banner{border-radius:8px;padding:8px 12px;font-size:12.5px;margin-top:10px;line-height:1.7}
.banner.pass{background:var(--ok-bg);border:1px solid var(--ok-border);color:var(--ok-text)}
.banner.block{background:var(--bad-bg);border:1px solid var(--bad-border);color:var(--bad-text)}
.banner .l{color:var(--muted)}
#next-list{margin:6px 0 0;padding-left:18px;font-size:12px;line-height:1.9}
#legend{display:flex;flex-wrap:wrap;gap:12px;font-size:11px;color:var(--muted);margin-top:8px}
#legend span{display:inline-flex;align-items:center;gap:5px}
.sw{display:inline-block;width:12px;height:12px;border-radius:3px;border:1px solid var(--line)}
footer{padding:10px 22px 18px;color:var(--muted);font-size:11.5px;line-height:1.7}
.empty{color:var(--muted);font-size:12.5px;padding:26px;text-align:center}
.empty.big{padding:44px 30px;line-height:1.9}
.empty-title{font-size:15px;font-weight:700;color:var(--text);margin-bottom:10px}
.empty-sub{margin-top:12px;font-size:11.5px;color:var(--subtle)}
.empty code{background:var(--surface);border:1px solid var(--line);border-radius:5px;padding:1px 6px;font-size:11px}
"""

DASHBOARD_STAGE_HTML = r"""<header>
  <h1><span data-i18n="closureDashboard">Research Closure Dashboard</span> <span id="verdict-chip"></span></h1>
  <div class="meta" id="meta"></div>
  <div id="guard-banner"></div>
</header>
<main>
  <div>
    <div class="card">
      <h2 data-i18n="claimGraph">Connected research flow</h2>
      <div id="canvas-wrap">
        <div id="zoom-hint" data-i18n="panZoom">drag: pan · wheel: zoom</div>
        <div id="reset-view" data-i18n="resetView">reset view</div>
        <div id="fit-view" data-i18n="fitView">fit whole graph</div>
        <svg id="dag"></svg>
        <div id="tooltip"></div>
        <div id="dag-empty"></div>
      </div>
      <div id="legend"></div>
    </div>
    <div class="card">
      <h2 data-i18n="resolutionMap">Decision rules (audit)</h2>
      <div id="resolution"></div>
    </div>
    <div class="card">
      <h2 data-i18n="nextEvents">Next events</h2>
      <ol id="next-list"></ol>
    </div>
    <div class="card">
      <h2 data-i18n="eventLog">Event log</h2>
      <table id="events-table"><thead><tr><th data-i18n="at">at</th><th data-i18n="event">event</th><th data-i18n="payload">payload</th></tr></thead><tbody></tbody></table>
    </div>
  </div>
  <div>
    <div class="card">
      <h2 data-i18n="probeDetail">Node detail</h2>
      <div id="probe-detail" data-i18n="probePrompt">Click any node to inspect the source record behind this projection.</div>
    </div>
    <div class="card">
      <h2 data-i18n="state">State</h2>
      <table id="state-table"></table>
    </div>
  </div>
</main>
<footer>
  <span data-i18n="generated">Generated</span> <span id="gen-at"></span> · <span data-i18n="regenerateWith">regenerate with</span>
  <code>python tools/research_closure.py dashboard</code>
  · <span data-i18n="footerExplain">the design hash freezes the pre-registration; outcomes and amendments move the map.</span>
</footer>
"""

DASHBOARD_JS = (
    "const STAGE_HTML = " + json.dumps(DASHBOARD_STAGE_HTML, ensure_ascii=False) + ";\n"
    + r"""
const RCH_LOCALE_STORAGE_KEY = "rch-dashboard-locale";
const RCH_I18N = {
  en: {
    closureDashboard:"Research Closure Dashboard", claimGraph:"Connected research flow", panZoom:"drag background: pan · drag node: move · wheel: zoom",
    resetView:"reset view", fitView:"fit whole graph", resolutionMap:"Decision rules (audit)", nextEvents:"Next events", eventLog:"Event log",
    at:"at", event:"event", payload:"payload", probeDetail:"Node detail",
    probePrompt:"Click any node to inspect the source record behind this projection.", state:"State", generated:"Generated",
    regenerateWith:"regenerate with", footerExplain:"the design hash freezes the pre-registration; outcomes and amendments move the map.",
    researchDashboard:"Research Dashboard", theme:"theme", dark:"Dark", light:"Light", language:"language",
    dashboardTheme:"Dashboard theme", dashboardLanguage:"Dashboard language",
    english:"English", chinese:"中文", prev:"prev", play:"play", pause:"pause", next:"next",
    firstContent:"first content", firstContentTitle:"jump to the first frame where the research flow has nodes",
    latest:"latest", latestTitle:"jump to the latest state", latestGenerated:"latest",
    stateNone:"no claim graph yet", stateEmpty:"graph skeleton (no nodes yet)", stateNodes:"nodes rendered",
    block:"BLOCK", pass:"PASS", claim:"claim", noClaimGraphYet:"(no claim graph yet)", type:"type",
    designHash:"design hash", sprint:"sprint", ends:"ends", active:"active", on:"on",
    resolutionMapLabel:"resolution map", closeAs:"close as", open:"open", ready:"ready", none:"none",
    warning:"warning", blockLabel:"block", guardPass:"PASS: work may proceed within the frozen claim.",
    positive:"positive", negative:"negative", unresolved:"unresolved", skipped:"skipped", waiting:"waiting",
    probe:"probe", tests:"tests", metric:"metric", prereg:"prereg", controls:"controls", guardsIn:"guards_in", role:"role",
    status:"status", observed:"observed", roleIntervention:"intervention", roleOutcome:"outcome",
    roleExposure:"exposure", roleMediator:"mediator", roleConfounder:"confounder", roleCovariate:"covariate",
    outcome:"outcome", experiment:"experiment", defect:"defect", unobserved:"unobserved",
    statement:"statement", provenance:"provenance", entails:"entails",
    noGraphTitle:"No claim graph in this repository yet", emptyGraphTitle:"The claim graph exists but has no nodes yet",
    graphEngineHint:"The claim graph is the engine — nodes appear here once it has content.",
    graphSkeletonHint:"The graph skeleton has no variables or probes yet.", then:"then",
    graphContentHint:"The sprint claim, observation model, probes, experiments, results and decision render as one traceable projection as soon as records exist.",
    graphCaption:"claim → observation relation → probe/hypothesis → experiment/intervention → result → claim decision · dotted paths are guards or provenance",
    assumedAbsent:"assumed absent", guard:"guard", unknownProbe:"Unknown probe.", preRegistration:"pre-registration",
    resolutionRules:"resolution rules mentioning", noResolutionMap:"No resolution map until the claim graph exists.",
    noResolutionRules:"No resolution rules pre-registered.", when:"when", fires:"FIRES", skips:"skips",
    dependsAssumption:"depends on assumption", nothingToDo:"(nothing to do)", mode:"mode",
    projectQuestion:"project question", sprintClaim:"sprint claim", sprintStatus:"sprint status",
    activeExperiment:"active experiment", backloggedIdeas:"backlogged ideas", events:"events", noEvents:"no events yet",
    theory:"theory (M)", variableObserved:"variable (observed)", variableLatent:"variable (latent, dashed)",
    probeReady:"probe ready", probePositive:"probe positive", probeNegative:"probe negative",
    waitingSkipped:"waiting/skipped", theoryObservation:"theory→observation", edge:"edge", probeGuard:"probe guard",
    claimNode:"sprint claim", hypothesisNode:"probe / hypothesis", experimentNode:"experiment / intervention",
    resultNode:"evidence / result", decisionNode:"claim decision", amendmentNode:"amendment",
    contains:"contains", models:"models", testedBy:"tested by", executedAs:"executed as", produced:"produced",
    contributes:"contributes", enabledBy:"enabled by", inducedBy:"induced by", motivatedBy:"motivated by",
    supported:"supported", falsified:"falsified", inconclusive:"inconclusive", terminated:"terminated",
    advance:"advance", continueDecision:"continue", narrow:"narrow", activeDecision:"active", pendingResult:"pending",
    workflowDecision:"workflow decision", evidenceLabel:"evidence", conclusion:"conclusion", intervention:"intervention",
    hypothesis:"hypothesis", question:"question", measurement:"measurement", sourceRecord:"source record",
    graphRecord:"graph relation", artifacts:"graph / figure", expectedFigure:"expected figure", openArtifact:"open artifact",
    pinnedDetail:"Pinned detail · click elsewhere to close"
  },
  zh: {
    closureDashboard:"研究闭环仪表盘", claimGraph:"完整研究流程网络", panZoom:"拖动背景：平移 · 拖动节点：移动 · 滚轮：缩放",
    resetView:"重置视图", fitView:"适配整图", resolutionMap:"判定规则（审计）", nextEvents:"下一事件", eventLog:"事件日志",
    at:"时间", event:"事件", payload:"载荷", probeDetail:"节点详情",
    probePrompt:"点击任一节点，查看该投影背后的原始记录。", state:"研究状态", generated:"生成于",
    regenerateWith:"重新生成命令", footerExplain:"设计哈希冻结预注册方案；结果和修订推动结论映射。",
    researchDashboard:"研究仪表盘", theme:"主题", dark:"深色", light:"浅色", language:"语言",
    dashboardTheme:"仪表盘主题", dashboardLanguage:"仪表盘语言",
    english:"English", chinese:"中文", prev:"上一帧", play:"播放", pause:"暂停", next:"下一帧",
    firstContent:"首个有内容帧", firstContentTitle:"跳到研究流程首次出现节点的帧",
    latest:"最新状态", latestTitle:"跳到最新状态", latestGenerated:"最新生成",
    stateNone:"尚无主张图", stateEmpty:"主张图骨架（尚无节点）", stateNodes:"已渲染节点",
    block:"阻塞", pass:"通过", claim:"主张", noClaimGraphYet:"（尚无主张图）", type:"类型",
    designHash:"设计哈希", sprint:"冲刺", ends:"结束日期", active:"活动实验", on:"对应探针",
    resolutionMapLabel:"结论映射", closeAs:"应关闭为", open:"未决", ready:"就绪", none:"无",
    warning:"警告", blockLabel:"阻塞", guardPass:"通过：工作可在冻结主张范围内继续。",
    positive:"正向", negative:"负向", unresolved:"未决", skipped:"已跳过", waiting:"等待中",
    probe:"探针", tests:"检验", metric:"指标", prereg:"预注册", controls:"控制变量", guardsIn:"前置条件", role:"角色",
    status:"状态", observed:"已观测", roleIntervention:"干预", roleOutcome:"结果",
    roleExposure:"暴露", roleMediator:"中介", roleConfounder:"混杂", roleCovariate:"协变量",
    outcome:"结果", experiment:"实验", defect:"缺陷", unobserved:"未观测",
    statement:"陈述", provenance:"来源", entails:"蕴含",
    noGraphTitle:"当前仓库尚未建立主张图", emptyGraphTitle:"主张图已建立，但尚无节点",
    graphEngineHint:"主张图是 RCH 的引擎；加入内容后节点会显示在这里。",
    graphSkeletonHint:"当前图骨架尚无变量或探针。", then:"则",
    graphContentHint:"记录存在后，冲刺主张、观测模型、探针、实验、结果和判定会投影成一张可追溯网络。",
    graphCaption:"主张 → 观测关系 → 探针/假设 → 实验/干预 → 证据结果 → 主张判定 · 点线表示前置条件或来源",
    assumedAbsent:"假定不存在", guard:"前置条件", unknownProbe:"未知探针。", preRegistration:"预注册",
    resolutionRules:"涉及该探针的结论规则", noResolutionMap:"建立主张图后才会出现结论映射。",
    noResolutionRules:"尚未预注册结论规则。", when:"当", fires:"已触发", skips:"跳过",
    dependsAssumption:"依赖假设", nothingToDo:"（当前无待办事件）", mode:"模式",
    projectQuestion:"项目问题", sprintClaim:"冲刺主张", sprintStatus:"冲刺状态",
    activeExperiment:"活动实验", backloggedIdeas:"待办想法", events:"事件数", noEvents:"尚无事件",
    theory:"理论（M）", variableObserved:"变量（已观测）", variableLatent:"变量（潜变量，虚线）",
    probeReady:"探针就绪", probePositive:"探针正向", probeNegative:"探针负向",
    waitingSkipped:"等待/已跳过", theoryObservation:"理论→观测", edge:"边", probeGuard:"探针前置条件",
    claimNode:"冲刺主张", hypothesisNode:"探针/假设", experimentNode:"实验/干预",
    resultNode:"证据/结果", decisionNode:"主张判定", amendmentNode:"修订",
    contains:"包含", models:"建模", testedBy:"由探针检验", executedAs:"执行为", produced:"产生",
    contributes:"汇入判定", enabledBy:"由结果开放", inducedBy:"由结果归纳", motivatedBy:"由异常驱动",
    supported:"支持", falsified:"证伪", inconclusive:"无定论", terminated:"终止",
    advance:"推进", continueDecision:"继续", narrow:"收窄", activeDecision:"进行中", pendingResult:"待定",
    workflowDecision:"工作流决定", evidenceLabel:"证据", conclusion:"结论", intervention:"干预",
    hypothesis:"假设", question:"问题", measurement:"测量", sourceRecord:"原始记录",
    graphRecord:"图关系", artifacts:"图表 / 图片", expectedFigure:"预期图片", openArtifact:"打开文件",
    pinnedDetail:"详情已固定 · 点击其他位置关闭"
  }
};
function readStoredLocale() {
  try { return localStorage.getItem(RCH_LOCALE_STORAGE_KEY); } catch (_) { return null; }
}
const RCH_LOCALE = readStoredLocale() === "zh" ? "zh" : "en";
document.documentElement.lang = RCH_LOCALE === "zh" ? "zh-CN" : "en";
function tr(key) { return (RCH_I18N[RCH_LOCALE] && RCH_I18N[RCH_LOCALE][key]) || RCH_I18N.en[key] || key; }
function translateStatic(root) {
  root.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = tr(el.dataset.i18n); });
  root.querySelectorAll("[data-i18n-title]").forEach(el => { el.title = tr(el.dataset.i18nTitle); });
  root.querySelectorAll("[data-i18n-aria]").forEach(el => { el.setAttribute("aria-label", tr(el.dataset.i18nAria)); });
}

function initDashboard(container, DATA) {
  const G = DATA.graph, D = DATA.derived || {}, S = DATA.state || {};
  container.innerHTML = STAGE_HTML;
  translateStatic(container);
  const $ = (id) => container.querySelector("#" + id);
const esc = (s) => { const d = document.createElement("div"); d.textContent = String(s == null ? "" : s); return d.innerHTML; };
const fmt = (s) => s == null ? "-" : s;

/* ---------- header: verdict, meta, guard ---------- */
(function header(){
  const chip = $("verdict-chip");
  if (D.blocks && D.blocks.length) chip.className = "chip bad", chip.textContent = tr("block");
  else chip.className = "chip ok", chip.textContent = tr("pass");
  const s = S.sprint || {}, p = S.project || {};
  let m = tr("claim") + ": <b>" + esc(G ? G.claim : tr("noClaimGraphYet")) + "</b>";
  if (G) m += " · " + tr("type") + ": " + esc(G.graph_type) + " · " + tr("designHash") + ": <code>" + esc(D.design_hash) + "</code>";
  if (s.claim) m += " · " + tr("sprint") + ": <b>" + esc(s.claim) + "</b>";
  if (s.ends_at) m += " · " + tr("ends") + " " + esc(String(s.ends_at).slice(0, 10));
  if (S.active_experiment) m += " · " + tr("active") + ": <b>" + esc(S.active_experiment.id) + "</b> " + tr("on") + " " + esc(S.active_experiment.claim_graph_node);
  if (D.proposal && D.proposal.status === "determined")
    m += " · " + tr("resolutionMapLabel") + ": <b>" + esc(D.proposal.then) + "</b>" + (D.proposal.expected_close ? " → " + tr("closeAs") + " " + esc(D.proposal.expected_close) : "");
  else if (D.proposal && D.proposal.status === "open")
    m += " · " + tr("resolutionMapLabel") + ": " + tr("open") + " (" + tr("ready") + ": " + esc((D.frontier || []).join(", ") || tr("none")) + ")";
  $("meta").innerHTML = m;

  const banner = $("guard-banner");
  const warns = (D.warnings || []).map(w => tr("warning") + ": " + w).join("<br/>");
  const blocks = (D.blocks || []).map(b => tr("blockLabel") + ": " + b).join("<br/>");
  if ((D.blocks || []).length)
    banner.className = "banner block", banner.innerHTML = "<div>" + blocks + "</div>" + (warns ? "<div class='l'>" + warns + "</div>" : "");
  else
    banner.className = "banner pass", banner.innerHTML = tr("guardPass") + (warns ? "<div class='l'>" + warns + "</div>" : "");
})();

/* ---------- probe status helpers ---------- */
function probeStatus(id, p) {
  const o = (D.outcomes || {})[id];
  if (o === "positive") return "positive";
  if (o === "negative") return "negative";
  if (o === "unresolved") return "unresolved";
  if ((D.skipped || []).indexOf(id) >= 0) return "skipped";
  if ((D.frontier || []).indexOf(id) >= 0) return "ready";
  return "waiting";
}
function probeDetail(id, p) {
  let s = tr("probe") + " " + id + "\n";
  s += tr("tests") + ": " + JSON.stringify(p.tests) + "\n";
  s += tr("metric") + ": " + p.metric + "\n";
  s += tr("prereg") + ": " + p.prereg + "\n";
  if (p.controls && p.controls.length) s += tr("controls") + ": " + p.controls.join(", ") + "\n";
  if (p.guards_in && p.guards_in.length) s += tr("guardsIn") + ": " + p.guards_in.join(", ") + "\n";
  s += tr("outcome") + ": " + ((D.outcomes || {})[id] || "-") + "\n";
  if (p.experiment_id) s += tr("experiment") + ": " + p.experiment_id + "\n";
  if (p.defect) s += tr("defect") + ": " + p.defect;
  return s;
}

/* ---------- DAG layout + rendering ---------- */
const ST = {
  ready:     {fill:"var(--probe-ready-fill)", stroke:"var(--probe-ready-stroke)", badge:tr("ready")},
  positive:  {fill:"var(--probe-positive-fill)", stroke:"var(--probe-positive-stroke)", badge:tr("positive")},
  negative:  {fill:"var(--probe-negative-fill)", stroke:"var(--probe-negative-stroke)", badge:tr("negative")},
  unresolved:{fill:"var(--probe-neutral-fill)", stroke:"var(--probe-neutral-stroke)", badge:tr("unresolved")},
  skipped:   {fill:"var(--probe-waiting-fill)", stroke:"var(--probe-waiting-stroke)", badge:tr("skipped"), dim:true},
  waiting:   {fill:"var(--probe-waiting-fill)", stroke:"var(--probe-waiting-stroke)", badge:tr("waiting")}
};
function decisionLabel(value) {
  const keys = {
    supported:"supported", falsified:"falsified", inconclusive:"inconclusive", terminated:"terminated",
    positive:"positive", negative:"negative", unresolved:"unresolved", ready:"ready", waiting:"waiting",
    skipped:"skipped", open:"open", advance:"advance", continue:"continueDecision", narrow:"narrow",
    active:"activeDecision", pending:"pendingResult"
  };
  return keys[value] ? tr(keys[value]) : (value || tr("pendingResult"));
}
function shortText(value, limit) {
  const text = String(value == null ? "" : value).replace(/\s+/g, " ").trim();
  return text.length > limit ? text.slice(0, Math.max(1, limit - 1)).trimEnd() + "…" : text;
}
function roleLabel(role) {
  const keys = {
    intervention:"roleIntervention", outcome:"roleOutcome", exposure:"roleExposure",
    mediator:"roleMediator", confounder:"roleConfounder", covariate:"roleCovariate"
  };
  return keys[role] ? tr(keys[role]) : (role || "?");
}
function estimatedTextWidth(value) {
  let width = 0;
  for (const ch of String(value || "")) width += /[^\u0000-\u00ff]/.test(ch) ? 15 : 8.8;
  return width;
}
const layout = (function(){
  const nodes = [], edges = [], byId = {};
  if (!G) return {nodes, edges};
  const push = (n) => {
    n.fullTitle = n.title || n.id;
    n.fullSubtitle = n.subtitle || "";
    n.title = shortText(n.fullTitle, 30);
    n.subtitle = shortText(n.fullSubtitle, 40);
    n.w = Math.min(340, Math.max(275, Math.max(estimatedTextWidth(n.title), estimatedTextWidth(n.subtitle)) + 38));
    n.h = 92;
    nodes.push(n);
    byId[n.id] = n;
    return n;
  };

  const events = S.events || [];
  const experiments = {};
  for (const event of events) {
    if (event.event !== "experiment_started" && event.event !== "experiment_closed") continue;
    const record = event.payload || {};
    if (!record.id) continue;
    experiments[record.id] = Object.assign({}, experiments[record.id] || {}, record,
      { event_status: event.event === "experiment_closed" ? "closed" : "active" });
  }
  if (S.active_experiment && S.active_experiment.id)
    experiments[S.active_experiment.id] = Object.assign({}, experiments[S.active_experiment.id] || {},
      S.active_experiment, { event_status:"active" });

  const sprintClosed = events.slice().reverse().map(e => e.event === "sprint_closed" ? e.payload : null)
    .find(p => p && p.claim === G.claim) || null;
  const sprintStarted = events.slice().reverse().map(e => e.event === "sprint_started" ? e.payload : null)
    .find(p => p && p.claim === G.claim) || S.sprint || null;
  const claimId = "$claim:" + (G.claim_id || "claim");
  push({ id:claimId, kind:"claim", title:(G.claim_id || "Claim") + " · " + tr("claimNode"),
    subtitle:G.claim || "", detail:tr("claim") + ": " + (G.claim || "") + "\n" +
      tr("type") + ": " + (G.graph_type || "-") + "\n" + tr("designHash") + ": " + (D.design_hash || "-") +
      (sprintStarted ? "\n" + tr("sprintStatus") + ": " + (sprintClosed ? sprintClosed.decision : sprintStarted.status || "active") : ""),
    graphRecord:{ claim_id:G.claim_id, graph_type:G.graph_type, design_hash:D.design_hash,
      variables:Object.keys(G.variables || {}), probes:Object.keys(G.probes || {}), resolution:G.resolution || [] },
    sourceRecord:{ claim:G.claim, sprint:sprintStarted, closed:sprintClosed } });

  const variableIds = Object.keys(G.variables || {});
  const variableDepth = Object.fromEntries(variableIds.map(id => [id, 0]));
  for (let pass = 0; pass < variableIds.length; pass++)
    for (const e of (G.edges || []))
      if (e.from in variableDepth && e.to in variableDepth)
        variableDepth[e.to] = Math.max(variableDepth[e.to], variableDepth[e.from] + 1);

  const probeIds = Object.keys(G.probes || {}).sort();
  const probeLevel = {};
  function levelOf(pid, seen) {
    if (probeLevel[pid] != null) return probeLevel[pid];
    if ((seen || []).indexOf(pid) >= 0) return 0;
    const guards = ((G.probes[pid] || {}).guards_in || []).map(g => String(g).split("==")[0].trim());
    const level = guards.length ? 1 + Math.max(...guards.map(dep => levelOf(dep, (seen || []).concat(pid)))) : 0;
    probeLevel[pid] = level;
    return level;
  }
  probeIds.forEach(pid => levelOf(pid, []));
  probeIds.sort((a, b) => probeLevel[a] - probeLevel[b] || (a < b ? -1 : 1));
  const laneY = Object.fromEntries(probeIds.map((pid, i) => [pid, 145 + i * 155]));
  const centerY = probeIds.length ? probeIds.map(pid => laneY[pid]).reduce((a, b) => a + b, 0) / probeIds.length : 150;
  byId[claimId].y = centerY;
  byId[claimId].x = 30;

  const references = {};
  for (const pid of probeIds) {
    const tests = (G.probes[pid] || {}).tests || {};
    for (const value of Object.values(tests)) {
      if (typeof value === "string" && variableDepth[value] != null)
        (references[value] ||= []).push(laneY[pid]);
      if (Array.isArray(value)) for (const v of value)
        if (variableDepth[v] != null) (references[v] ||= []).push(laneY[pid]);
    }
  }
  const COL = 380;
  let fallbackY = 80;
  for (const [id, v] of Object.entries(G.variables || {})) {
    const ys = references[id] || [];
    const n = push({ id, kind:"variable", latent:!v.observed,
      title:id + " · " + (v.name || id),
      subtitle:tr("role") + ": " + roleLabel(v.role) + " · " + (v.observed ? tr("observed") : tr("unobserved")),
      detail:(v.name || "") + "\n" + tr("role") + ": " + (v.role || "?") + "\n" +
        (v.observed ? tr("observed") : tr("unobserved")),
      graphRecord:{ incoming:(G.edges || []).filter(e => e.to === id), outgoing:(G.edges || []).filter(e => e.from === id),
        assumed_absent:(G.assumed_absent || []).filter(e => e.from === id || e.to === id) }, sourceRecord:v });
    n.x = 30 + COL * (1 + (variableDepth[id] || 0));
    n.y = ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : fallbackY;
    fallbackY += 90;
  }
  const maxVariableDepth = Math.max(0, ...Object.values(variableDepth));
  const probeX = 30 + COL * (maxVariableDepth + 2);
  const experimentX = probeX + COL;
  const resultX = experimentX + COL;
  const decisionX = resultX + COL;

  let earlyTheoryOffset = 0;
  for (const [id, m] of Object.entries(G.theory || {})) {
    if (m.retired_at) continue;
    const induced = m.provenance === "induced" && (m.supported_by || []).length;
    const n = push({ id, kind:"theory",
      title: id + " · " + (m.statement || tr("theory")),
      subtitle: tr("theory") + " · " + tr("provenance") + ": " + (m.provenance || "?"),
      detail: tr("statement") + ": " + (m.statement || "") + "\n" + tr("provenance") + ": " + (m.provenance || "?") + "\n" + tr("entails") + ": " + ((m.entails || []).join(", ") || "-"),
      graphRecord:{ entails:m.entails || [], supported_by:m.supported_by || [] }, sourceRecord:m });
    n.x = induced ? decisionX : 30 + COL;
    n.y = induced ? centerY + 130 : 20 + earlyTheoryOffset++ * 90;
  }

  function variableName(id) { return ((G.variables || {})[id] || {}).name || id || "?"; }
  function probeSummary(tests) {
    if (!tests) return tr("probe");
    if (tests.kind === "edge")
      return shortText(variableName(tests.from), 20) + " → " + shortText(variableName(tests.to), 20);
    if (tests.kind === "independence")
      return shortText(variableName(tests.x), 16) + " ⟂ " + shortText(variableName(tests.y), 16) +
        ((tests.given || []).length ? " | " + tests.given.join(", ") : "");
    if (tests.kind === "comparison")
      return shortText(variableName(tests.stronger), 14) + " vs " + shortText(variableName(tests.weaker), 14) +
        " → " + shortText(variableName(tests.on), 14);
    return JSON.stringify(tests);
  }
  const resultByProbe = {};
  for (const id of probeIds) {
    const p = G.probes[id];
    const status = probeStatus(id, p);
    const exp = p.experiment_id ? experiments[p.experiment_id] :
      (S.active_experiment && S.active_experiment.claim_graph_node === id ? experiments[S.active_experiment.id] : null);
    const pnode = push({ id, kind:"probe", status,
      title:id + " · " + probeSummary(p.tests),
      subtitle:tr("hypothesisNode") + " · " + decisionLabel(exp && exp.decision ? exp.decision : status),
      detail:probeDetail(id, p) + (exp && exp.hypothesis ? "\n" + tr("hypothesis") + ": " + exp.hypothesis : ""),
      graphRecord:{ tests:p.tests || {}, guards_in:p.guards_in || [], outcome:(D.outcomes || {})[id] || null },
      sourceRecord:Object.assign({id:id}, p, exp ? {experiment:exp} : {}) });
    pnode.x = probeX;
    pnode.y = laneY[id];

    if (exp && exp.id) {
      const expId = "$experiment:" + exp.id;
      const enode = push({ id:expId, kind:"experiment", sourceId:exp.id,
        title:exp.id + " · " + tr("experimentNode"), subtitle:exp.intervention || exp.question || "",
        detail:tr("question") + ": " + (exp.question || "-") + "\n" + tr("hypothesis") + ": " + (exp.hypothesis || "-") +
          "\n" + tr("intervention") + ": " + (exp.intervention || "-") + "\n" + tr("measurement") + ": " + (exp.measurement || "-"),
        graphRecord:{ probe:id, tests:p.tests || {}, guards_in:p.guards_in || [] }, sourceRecord:exp });
      enode.x = experimentX;
      enode.y = laneY[id];
      edges.push({ a:id, b:expId, kind:"workflow", label:tr("executedAs"), detail:tr("executedAs") + " " + exp.id });
      if (exp.decision) {
        const resultId = "$result:" + exp.id;
        const rnode = push({ id:resultId, kind:"result", status:exp.decision, sourceId:exp.id,
          title:tr("resultNode") + " · " + decisionLabel(exp.decision), subtitle:exp.conclusion || exp.evidence || "",
          detail:tr("outcome") + ": " + exp.decision + "\n" + tr("evidenceLabel") + ": " + (exp.evidence || "-") +
            "\n" + tr("conclusion") + ": " + (exp.conclusion || "-") + (exp.defect ? "\n" + tr("defect") + ": " + exp.defect : ""),
          graphRecord:{ probe:id, outcome:exp.decision, contributes_to:G.resolution || [] }, sourceRecord:exp });
        rnode.x = resultX;
        rnode.y = laneY[id];
        resultByProbe[id] = resultId;
        edges.push({ a:expId, b:resultId, kind:"result", label:tr("produced"), detail:tr("produced") + " " + decisionLabel(exp.decision) });
      }
    }
  }

  for (const e of (G.edges || []))
    edges.push({ a:e.from, b:e.to, kind:(e.from_theory && e.from_theory.length) ? "theory-edge" : "edge",
      label:e.from + " → " + e.to, detail:e.from + " → " + e.to });
  for (const a of (G.assumed_absent || []))
    edges.push({ a: a.from, b: a.to, kind: "absent", detail: a.justification });
  const childVariables = new Set((G.edges || []).map(e => e.to));
  for (const id of variableIds.filter(id => !childVariables.has(id)))
    edges.push({ a:claimId, b:id, kind:"scope", label:tr("models"), detail:tr("models") + " " + id });

  for (const [id, p] of Object.entries(G.probes || {})) {
    const tests = p.tests || {};
    const focus = tests.to || tests.on || tests.y || tests.x || tests.from;
    if (focus && byId[focus])
      edges.push({ a:focus, b:id, kind:"tests", label:tr("testedBy"), detail:tr("tests") + ": " + JSON.stringify(tests) });
    else
      edges.push({ a:claimId, b:id, kind:"scope", label:tr("contains"), detail:tr("contains") + " " + id });
    for (const g of (p.guards_in || [])) {
      const dep = String(g).split("==")[0].trim();
      const source = resultByProbe[dep] || dep;
      if (byId[source]) edges.push({ a:source, b:id, kind:"guard", label:tr("enabledBy"), detail:g });
    }
  }

  const proposal = D.proposal || {};
  const decisionId = "$decision:" + (G.claim_id || "claim");
  const finalVerdict = proposal.status === "determined" ? proposal.then : "open";
  const workflowVerdict = sprintClosed ? sprintClosed.decision : (S.sprint ? "active" : "pending");
  const dnode = push({ id:decisionId, kind:"decision", status:finalVerdict,
    title:tr("decisionNode") + " · " + decisionLabel(finalVerdict),
    subtitle:tr("workflowDecision") + ": " + decisionLabel(workflowVerdict),
    detail:tr("resolutionMapLabel") + ": " + finalVerdict + "\n" + tr("workflowDecision") + ": " + workflowVerdict +
      (proposal.rule ? "\n" + tr("sourceRecord") + ": " + JSON.stringify(proposal.rule, null, 2) : ""),
    graphRecord:{ outcomes:D.outcomes || {}, rule:proposal.rule || null, resolution:G.resolution || [] },
    sourceRecord:{ proposal:proposal, sprint_decision:sprintClosed } });
  dnode.x = decisionX;
  dnode.y = centerY;
  const decisionSources = proposal.rule ? Object.keys(proposal.rule.when || {}) : probeIds;
  for (const pid of decisionSources) {
    const source = resultByProbe[pid] || pid;
    if (byId[source]) edges.push({ a:source, b:decisionId, kind:"resolution", label:tr("contributes"), detail:tr("contributes") + " " + finalVerdict });
  }

  let derivedOffset = 0;
  for (const [id, m] of Object.entries(G.theory || {})) {
    if (!byId[id]) continue;
    for (const expId of (m.supported_by || [])) {
      const resultId = "$result:" + expId;
      if (byId[resultId]) edges.push({ a:resultId, b:id, kind:"induction", label:tr("inducedBy"), detail:tr("inducedBy") + " " + expId });
    }
    if (m.provenance === "induced") {
      byId[id].x = decisionX;
      byId[id].y = centerY + 120 + derivedOffset++ * 90;
    } else {
      edges.push({ a:claimId, b:id, kind:"scope", label:tr("contains"), detail:tr("contains") + " " + id });
    }
  }
  for (const amendment of (G.amendments || [])) {
    const id = "$amendment:" + amendment.id;
    const n = push({ id, kind:"amendment", title:amendment.id + " · " + tr("amendmentNode"),
      subtitle:amendment.action + " · " + amendment.detail,
      detail:JSON.stringify(amendment, null, 2), graphRecord:{ motivating_anomaly:amendment.motivating_anomaly || null },
      sourceRecord:amendment });
    n.x = decisionX;
    n.y = centerY + 120 + derivedOffset++ * 90;
    const context = amendment.motivating_anomaly || {};
    const source = context.x && byId[context.x] ? context.x : claimId;
    edges.push({ a:source, b:id, kind:"amendment", label:tr("motivatedBy"), detail:tr("motivatedBy") + " " + amendment.detail });
  }
  return { nodes, edges, byId };
})();

const EDGE_STYLE = {
  "edge":        { stroke:"var(--edge-stroke)", width:2.8, dash:"", marker:"arr-edge" },
  "theory-edge": { stroke:"var(--theory-stroke)", width:2.4, dash:"", marker:"arr-theory" },
  "absent":      { stroke:"var(--red)", width:2.8, dash:"7 5", marker:"arr-absent" },
  "guard":       { stroke:"var(--guard-stroke)", width:2.5, dash:"7 5", marker:"arr-guard" },
  "scope":       { stroke:"var(--claim-stroke)", width:2.5, dash:"4 5", marker:"arr-claim" },
  "tests":       { stroke:"var(--tests-stroke)", width:2.6, dash:"7 4", marker:"arr-tests" },
  "workflow":    { stroke:"var(--experiment-stroke)", width:2.8, dash:"", marker:"arr-experiment" },
  "result":      { stroke:"var(--result-stroke)", width:2.8, dash:"", marker:"arr-result" },
  "resolution":  { stroke:"var(--decision-stroke)", width:3, dash:"", marker:"arr-decision" },
  "induction":   { stroke:"var(--theory-stroke)", width:2.5, dash:"6 4", marker:"arr-theory" },
  "amendment":   { stroke:"var(--amendment-stroke)", width:2.5, dash:"6 4", marker:"arr-amendment" }
};
const MARKERS = [
  ["arr-edge", "var(--edge-stroke)"], ["arr-theory", "var(--theory-stroke)"],
  ["arr-absent", "var(--red)"], ["arr-guard", "var(--guard-stroke)"],
  ["arr-claim", "var(--claim-stroke)"], ["arr-tests", "var(--tests-stroke)"],
  ["arr-experiment", "var(--experiment-stroke)"], ["arr-result", "var(--result-stroke)"],
  ["arr-decision", "var(--decision-stroke)"], ["arr-amendment", "var(--amendment-stroke)"]
];

const SVGNS = "http://www.w3.org/2000/svg";
const svg = $("dag");
function svgEl(tag, attrs) {
  const el = document.createElementNS(SVGNS, tag);
  for (const k in attrs) el.setAttribute(k, attrs[k]);
  return el;
}
function svgText(x, y, content, attrs) {
  const t = svgEl("text", Object.assign({
    x: x, y: y, "text-anchor": "middle", "dominant-baseline": "central",
    fill: "var(--node-text)", "font-size": "13", "font-weight": "600"
  }, attrs || {}));
  t.textContent = content;
  return t;
}
function clearNode(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}
function edgePath(e) {
  const a = layout.byId[e.a], b = layout.byId[e.b];
  if (!a || !b) return "";
  const forward = b.x >= a.x;
  const x1 = forward ? a.x + a.w : a.x, y1 = a.y + a.h / 2;
  const x2 = forward ? b.x - 18 : b.x + b.w + 18, y2 = b.y + b.h / 2;
  if (forward) {
    const dx = Math.max(45, (x2 - x1) * 0.42);
    return "M" + x1 + "," + y1 + " C" + (x1 + dx) + "," + y1 + " " + (x2 - dx) + "," + y2 + " " + x2 + "," + y2;
  }
  if (e.kind === "guard") {
    /* A guard may point from a result on the right back to a later probe on
       the left.  Attach that dependency to the bottoms of both nodes so the
       return path reads as one deliberate connector, not a side hook. */
    const sx = a.x + a.w / 2, sy = a.y + a.h;
    const tx = b.x + b.w / 2, ty = b.y + b.h + 18;
    const routeY = Math.max(sy, ty) + 52;
    const direction = tx < sx ? -1 : 1;
    const radius = Math.min(18, Math.abs(tx - sx) / 4);
    return "M" + sx + "," + sy + " L" + sx + "," + (routeY - radius) +
      " Q" + sx + "," + routeY + " " + (sx + direction * radius) + "," + routeY +
      " L" + (tx - direction * radius) + "," + routeY +
      " Q" + tx + "," + routeY + " " + tx + "," + (routeY - radius) +
      " L" + tx + "," + ty;
  }
  const routeY = Math.max(y1, y2) + 72;
  return "M" + x1 + "," + y1 + " C" + (x1 - 55) + "," + y1 + " " + (x1 - 55) + "," + routeY + " " + x1 + "," + routeY +
    " L" + x2 + "," + routeY + " C" + (x2 + 55) + "," + routeY + " " + (x2 + 55) + "," + y2 + " " + x2 + "," + y2;
}
function edgeLabelPoint(e) {
  const a = layout.byId[e.a], b = layout.byId[e.b];
  if (!a || !b) return {x:0, y:0};
  if (b.x >= a.x) return { x:(a.x + a.w + b.x) / 2, y:(a.y + a.h / 2 + b.y + b.h / 2) / 2 - 7 };
  if (e.kind === "guard") return {
    x:(a.x + a.w / 2 + b.x + b.w / 2) / 2,
    y:Math.max(a.y + a.h, b.y + b.h + 18) + 44
  };
  return { x:(a.x + b.x + b.w) / 2, y:Math.max(a.y + a.h / 2, b.y + b.h / 2) + 65 };
}

function renderDag() {
  const empty = $("dag-empty");
  if (!G || !layout.nodes.length) {
    clearNode(svg);
    const noGraph = !G;
    const title = noGraph
      ? tr("noGraphTitle")
      : tr("emptyGraphTitle");
    const hint = noGraph
      ? tr("graphEngineHint") + "<br/>" +
        "1. <code>claim_graph.py init --claim \"&lt;the sprint claim&gt;\"</code><br/>" +
        "2. <code>add-variable --id X --name \"...\" --role \"...\"</code> " + tr("then") + " <code>add-edge / add-probe / add-resolution</code><br/>" +
        "3. <code>research_closure.py start-sprint ...</code>"
      : tr("graphSkeletonHint") + "<br/>" +
        "<code>claim_graph.py add-variable --id X --name \"...\" --role \"...\"</code> " + tr("then") + " <code>add-edge / add-probe / add-resolution</code>";
    empty.innerHTML = '<div class="empty big"><div class="empty-title">' + title +
      "</div>" + hint +
      '<div class="empty-sub">' + tr("graphContentHint") + '</div></div>';
    return;
  }
  empty.innerHTML = "";
  let W = 0, H = 0;
  for (const n of layout.nodes) { W = Math.max(W, n.x + n.w); H = Math.max(H, n.y + n.h); }
  W += 40; H = Math.max(H + 58, 560);
  clearNode(svg);

  const defs = svgEl("defs", {});
  for (const mk of MARKERS) {
    const marker = svgEl("marker", {
      id: mk[0], viewBox: "0 0 16 16", refX: "14", refY: "8",
      markerWidth: "16", markerHeight: "16", markerUnits:"userSpaceOnUse", orient: "auto",
    });
    marker.appendChild(svgEl("path", { d: "M0,0 L16,8 L0,16 L4,8 z", fill: mk[1] }));
    defs.appendChild(marker);
  }
  svg.appendChild(defs);

  const viewport = svgEl("g", { id: "viewport" });
  svg.appendChild(viewport);

  const edgeRecords = [];
  for (const e of layout.edges) {
    const st = EDGE_STYLE[e.kind];
    const path = svgEl("path", {
      d: edgePath(e), fill: "none", stroke: st.stroke,
      "stroke-width": st.width, "marker-end": "url(#" + st.marker + ")",
    });
    if (st.dash) path.setAttribute("stroke-dasharray", st.dash);
    path.setAttribute("data-edge", "1");
    path.setAttribute("data-kind", e.kind);
    path.setAttribute("data-detail", e.detail || e.label || "");
    viewport.appendChild(path);
    const edgeRecord = { edge:e, path:path, label:null, absentMark:null };
    if (e.label && e.kind !== "edge") {
      const lp = edgeLabelPoint(e);
      const label = svgText(lp.x, lp.y, shortText(e.label, 24),
        { "data-edge-label":e.kind, "font-size":"11", fill:"var(--subtle)", "font-weight":"700" });
      viewport.appendChild(label);
      edgeRecord.label = label;
    }
    if (e.kind === "absent") {
      const a = layout.byId[e.a], b = layout.byId[e.b];
      edgeRecord.absentMark = svgText((a.x + a.w + b.x) / 2, (a.y + b.y + b.h) / 2, "\u2717",
        { "font-size": "15", fill: "var(--red)", "font-weight": "800" });
      viewport.appendChild(edgeRecord.absentMark);
    }
    edgeRecords.push(edgeRecord);
  }
  const nodeGroups = {};
  for (const n of layout.nodes) {
    const g = svgEl("g", {});
    g.setAttribute("data-node", n.id);
    g.setAttribute("data-kind", n.kind);
    g.setAttribute("data-detail", n.detail);
    g.setAttribute("data-probe", n.kind === "probe" ? n.id : "");
    g.setAttribute("data-node-x", n.x);
    g.setAttribute("data-node-y", n.y);
    g.setAttribute("transform", "translate(" + n.x + "," + n.y + ")");
    const accessibleTitle = svgEl("title", {});
    accessibleTitle.textContent = n.fullTitle + " — " + n.fullSubtitle + "\n" + n.detail;
    g.appendChild(accessibleTitle);
    let fill = "var(--surface)", stroke = "var(--line)";
    if (n.kind === "claim") fill = "var(--claim-fill)", stroke = "var(--claim-stroke)";
    else if (n.kind === "theory") fill = "var(--theory-fill)", stroke = "var(--theory-stroke)";
    else if (n.kind === "experiment") fill = "var(--experiment-fill)", stroke = "var(--experiment-stroke)";
    else if (n.kind === "result") {
      const st = n.status === "supported" ? ST.positive : n.status === "falsified" ? ST.negative : ST.unresolved;
      fill = st.fill; stroke = st.stroke;
    } else if (n.kind === "decision") {
      if (n.status === "falsified") fill = ST.negative.fill, stroke = ST.negative.stroke;
      else fill = "var(--decision-fill)", stroke = "var(--decision-stroke)";
    } else if (n.kind === "amendment") fill = "var(--amendment-fill)", stroke = "var(--amendment-stroke)";
    else if (n.kind === "variable") fill = n.latent ? "var(--latent-fill)" : "var(--variable-fill)", stroke = n.latent ? "var(--latent-stroke)" : "var(--variable-stroke)";
    else if (n.kind === "probe") { const st = ST[n.status] || ST.waiting; fill = st.fill; stroke = st.stroke; }

    if (n.kind === "variable") {
      if (n.latent) {
        g.appendChild(svgEl("ellipse", { cx: n.w / 2, cy: n.h / 2,
          rx: n.w / 2, ry: n.h / 2, fill, stroke,
          "stroke-width": 2.2, "stroke-dasharray": "6 4" }));
      } else {
        g.appendChild(svgEl("rect", { x: 0, y: 0, width: n.w, height: n.h, rx: 9,
          fill, stroke, "stroke-width": 2.2 }));
      }
    } else {
      g.appendChild(svgEl("rect", { x: 0, y: 0, width: n.w, height: n.h, rx: 11,
        fill, stroke, "stroke-width": n.kind === "claim" || n.kind === "decision" ? 3 : 2.3 }));
    }
    g.appendChild(svgText(n.w / 2, 31, n.title,
      { "data-label-line": "title", "font-size": "17", "font-weight": "750" }));
    g.appendChild(svgText(n.w / 2, 66, n.subtitle,
      { "data-label-line": "subtitle", "font-size": "13", fill: "var(--muted)", "font-weight": "600" }));
    viewport.appendChild(g);
    nodeGroups[n.id] = g;
  }
  viewport.appendChild(svgText(20, H - 16,
    tr("graphCaption"),
    { "text-anchor": "start", "dominant-baseline": "auto", "font-size": "13", fill: "var(--subtle)" }));
  const defaultViewW = Math.min(W, 2200);
  svg.setAttribute("viewBox", "0 0 " + defaultViewW + " " + H);
  svg.setAttribute("preserveAspectRatio", "xMinYMin meet");

  const g = svg.querySelectorAll("g[data-node]"), epaths = svg.querySelectorAll("path[data-edge]");
  const tip = $("tooltip");
  let tipHideTimer = null, pinnedNodeId = null;
  const cancelTipHide = () => { if (tipHideTimer) clearTimeout(tipHideTimer); tipHideTimer = null; };
  const showTip = (ev, html) => {
    if (pinnedNodeId) return;
    cancelTipHide(); tip.innerHTML = html; tip.style.display = "block"; moveTip(ev);
  };
  const moveTip = (ev) => {
    const r = svg.getBoundingClientRect();
    let x = ev.clientX - r.left + 16, y = ev.clientY - r.top + 16;
    const tw = Math.min(480, r.width - 28), th = Math.min(tip.scrollHeight, 520);
    if (x + tw > r.width - 8) x = Math.max(8, ev.clientX - r.left - tw - 16);
    if (y + th > r.height - 8) y = Math.max(8, r.height - th - 8);
    tip.style.left = x + "px"; tip.style.top = y + "px";
  };
  const hideTip = (immediate, force) => {
    if (pinnedNodeId && !force) return;
    cancelTipHide();
    if (immediate) tip.style.display = "none";
    else tipHideTimer = setTimeout(() => { tip.style.display = "none"; tipHideTimer = null; }, 140);
  };
  const unpinTip = () => {
    pinnedNodeId = null;
    tip.classList.remove("pinned");
    tip.removeAttribute("data-pinned-node");
    hideTip(true, true);
  };
  const pinNodeDetail = (ev, node) => {
    cancelTipHide();
    pinnedNodeId = node.id;
    tip.classList.add("pinned");
    tip.setAttribute("data-pinned-node", node.id);
    tip.innerHTML = "<div class='pin-status'>" + esc(tr("pinnedDetail")) + "</div>" + renderNodeDetail(node);
    tip.style.display = "block";
    moveTip(ev);
    showNodeDetail(node.id);
  };
  tip.addEventListener("mouseenter", cancelTipHide);
  tip.addEventListener("mouseleave", () => { if (!pinnedNodeId) hideTip(true); });
  let canvasDrag = null, nodeDrag = null, scale = 1, tx = 0, ty = 0;
  const unitPerPixel = () => svg.viewBox.baseVal.width / Math.max(1, svg.getBoundingClientRect().width);
  const refreshEdges = () => {
    for (const item of edgeRecords) {
      item.path.setAttribute("d", edgePath(item.edge));
      if (item.label) {
        const lp = edgeLabelPoint(item.edge);
        item.label.setAttribute("x", lp.x); item.label.setAttribute("y", lp.y);
      }
      if (item.absentMark) {
        const a = layout.byId[item.edge.a], b = layout.byId[item.edge.b];
        item.absentMark.setAttribute("x", (a.x + a.w + b.x) / 2);
        item.absentMark.setAttribute("y", (a.y + b.y + b.h) / 2);
      }
    }
  };
  for (const nodeGroup of g) {
    const node = layout.byId[nodeGroup.dataset.node];
    nodeGroup.addEventListener("mouseenter", (ev) => {
      if (!nodeDrag && !pinnedNodeId) showTip(ev, renderNodeDetail(node));
    });
    nodeGroup.addEventListener("mousemove", (ev) => { if (!nodeDrag && !pinnedNodeId) moveTip(ev); });
    nodeGroup.addEventListener("mouseleave", () => { if (!nodeDrag && !pinnedNodeId) hideTip(); });
    nodeGroup.addEventListener("mousedown", (ev) => {
      if (ev.button !== 0) return;
      ev.preventDefault(); ev.stopPropagation(); unpinTip();
      nodeDrag = { node:node, group:nodeGroup, x:ev.clientX, y:ev.clientY,
        nodeX:node.x, nodeY:node.y, moved:false };
      nodeGroup.classList.add("node-dragging");
      viewport.appendChild(nodeGroup);
    });
  }
  for (const p of epaths) {
    p.addEventListener("mouseenter", (ev) => {
      if (pinnedNodeId) return;
      const t = p.dataset.kind === "absent" ? tr("assumedAbsent") + ": " + p.dataset.detail
             : p.dataset.kind === "guard" ? tr("guard") + ": " + p.dataset.detail : p.dataset.detail;
      showTip(ev, "<div class='detail-label'>" + esc(p.dataset.kind) + "</div><pre>" + esc(t) + "</pre>");
    });
    p.addEventListener("mousemove", (ev) => { if (!pinnedNodeId) moveTip(ev); });
    p.addEventListener("mouseleave", () => { if (!pinnedNodeId) hideTip(); });
  }

  /* pan + zoom: transform the inner viewport group (the root <svg> element
     cannot carry a transform attribute — the old code silently did nothing) */
  const apply = () => {
    viewport.setAttribute("transform", "translate(" + tx + "," + ty + ") scale(" + scale + ")");
    viewport.setAttribute("data-pan-x", tx); viewport.setAttribute("data-pan-y", ty); viewport.setAttribute("data-scale", scale);
  };
  apply();
  svg.onmousedown = (ev) => {
    if (ev.button !== 0) return;
    canvasDrag = { x: ev.clientX, y: ev.clientY, tx:tx, ty:ty };
    svg.classList.add("dragging");
  };
  const doc = container.ownerDocument;
  doc.addEventListener("mousedown", (ev) => {
    if (pinnedNodeId && !tip.contains(ev.target)) unpinTip();
  });
  doc.addEventListener("mousemove", (ev) => {
    if (nodeDrag) {
      const dx = (ev.clientX - nodeDrag.x) * unitPerPixel() / scale;
      const dy = (ev.clientY - nodeDrag.y) * unitPerPixel() / scale;
      nodeDrag.node.x = nodeDrag.nodeX + dx; nodeDrag.node.y = nodeDrag.nodeY + dy;
      nodeDrag.moved = nodeDrag.moved || Math.abs(ev.clientX - nodeDrag.x) + Math.abs(ev.clientY - nodeDrag.y) > 4;
      nodeDrag.group.setAttribute("transform", "translate(" + nodeDrag.node.x + "," + nodeDrag.node.y + ")");
      nodeDrag.group.setAttribute("data-node-x", nodeDrag.node.x); nodeDrag.group.setAttribute("data-node-y", nodeDrag.node.y);
      refreshEdges();
      return;
    }
    if (!canvasDrag) return;
    tx = canvasDrag.tx + (ev.clientX - canvasDrag.x) * unitPerPixel();
    ty = canvasDrag.ty + (ev.clientY - canvasDrag.y) * unitPerPixel();
    apply();
  });
  doc.addEventListener("mouseup", (ev) => {
    if (nodeDrag) {
      const finished = nodeDrag;
      finished.group.classList.remove("node-dragging");
      nodeDrag = null;
      if (!finished.moved) pinNodeDetail(ev, finished.node);
    }
    canvasDrag = null; svg.classList.remove("dragging");
  });
  svg.onwheel = (ev) => {
    ev.preventDefault();
    scale = Math.min(3, Math.max(0.3, scale * (ev.deltaY < 0 ? 1.12 : 0.9)));
    apply();
  };
  $("reset-view").onclick = () => {
    svg.setAttribute("viewBox", "0 0 " + defaultViewW + " " + H);
    scale = 1; tx = 0; ty = 0; apply();
  };
  $("fit-view").onclick = () => {
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    scale = 1; tx = 0; ty = 0; apply();
  };
}

/* ---------- detail panel for any projected node ---------- */
function artifactRefs(node) {
  const record = node && node.sourceRecord ? node.sourceRecord : {};
  const values = [];
  for (const key of ["expected_figure", "figure", "figures", "graph", "graph_path", "expected_artifact", "evidence"]) {
    const value = record[key];
    if (Array.isArray(value)) values.push(...value);
    else if (value) values.push(...String(value).split(/[,\n]/));
  }
  const refs = [], seen = new Set();
  for (const raw of values) {
    const path = String(raw || "").trim();
    if (!path || seen.has(path)) continue;
    if (!/^(https?:\/\/|file:\/\/|\/)|[\\/]|\.(png|jpe?g|gif|webp|svg|pdf|html?|json|csv)$/i.test(path)) continue;
    seen.add(path); refs.push(path);
  }
  return refs;
}
function artifactHref(path) {
  if (/^(https?:\/\/|file:\/\/)/i.test(path)) return path;
  const root = String(DATA.project_root || "").replace(/\/$/, "");
  const absolute = path.startsWith("/") ? path : root + "/" + path;
  return "file://" + absolute.split("/").map(encodeURIComponent).join("/");
}
function artifactHtml(node) {
  const refs = artifactRefs(node);
  if (!refs.length) return "";
  return "<div class='detail-label'>" + tr("artifacts") + "</div><div class='artifact-list'>" + refs.map(path => {
    const href = artifactHref(path), image = /\.(png|jpe?g|gif|webp|svg)(\?.*)?$/i.test(path);
    return "<div class='artifact-ref'><a href='" + esc(href) + "' target='_blank' rel='noopener'>" +
      esc(path) + "</a>" + (image ? "<img src='" + esc(href) + "' alt='" + esc(path) + "' onerror=\"this.style.display='none'\"/>" : "") + "</div>";
  }).join("") + "</div>";
}
function renderNodeDetail(node) {
  if (!node) return tr("unknownProbe");
  const graph = JSON.stringify(node.graphRecord || {}, null, 2);
  const source = JSON.stringify(node.sourceRecord || {}, null, 2);
  return "<div class='detail-title'>" + esc(node.fullTitle || node.title) + "</div>" +
    "<div class='detail-subtitle'>" + esc(node.fullSubtitle || node.subtitle) + "</div>" +
    "<div class='detail-label'>" + tr("graphRecord") + "</div><pre>" + esc(graph) + "</pre>" + artifactHtml(node) +
    "<div class='detail-label'>" + tr("sourceRecord") + "</div><pre>" + esc(source) + "</pre>";
}
function showNodeDetail(id) {
  const node = layout.byId[id] || null;
  const panel = $("probe-detail");
  if (!node) { panel.innerHTML = tr("unknownProbe"); return; }
  panel.innerHTML = renderNodeDetail(node);
}
function showProbeDetail(id) { showNodeDetail(id); }

/* ---------- resolution map ---------- */
(function renderResolution() {
  const box = $("resolution");
  if (!G) { box.innerHTML = '<div class="empty">' + tr("noResolutionMap") + '</div>'; return; }
  const rules = G.resolution || [];
  if (!rules.length) { box.innerHTML = '<div class="empty">' + tr("noResolutionRules") + '</div>'; return; }
  const outs = D.outcomes || {};
  box.innerHTML = rules.map((r, i) => {
    const fired = Object.entries(r.when || {}).every(kv => outs[kv[0]] === kv[1]);
    const whenTxt = Object.keys(r.when || {}).map(k => esc(k) + "=" + esc(r.when[k])).join(", ");
    let h = '<div class="rule' + (fired ? " fires" : "") + '">';
    h += '<span class="when">' + tr("when") + ' { ' + whenTxt + ' }</span>';
    h += '<div class="then">→ ' + esc(r.then) + (r.rung ? " <span style='color:var(--amber)'>(" + esc(r.rung) + ")</span>" : "") + (fired ? ' <span class="chip ok">' + tr("fires") + '</span>' : "") + '</div>';
    if (r.skip && r.skip.length) h += '<div class="note">' + tr("skips") + ': ' + esc(r.skip.join(", ")) + "</div>";
    if (r.depends_on_assumption) h += '<div class="note">' + tr("dependsAssumption") + ': ' + esc(r.depends_on_assumption) + "</div>";
    if (r.note) h += '<div class="note">' + esc(r.note) + "</div>";
    return h + "</div>";
  }).join("");
})();

/* ---------- next events / state / events log ---------- */
(function renderNext() {
  $("next-list").innerHTML = (D.next_events || [tr("nothingToDo")]).map(e => "<li><code>" + esc(e) + "</code></li>").join("");
})();

(function renderState() {
  const s = S.sprint || {}, p = S.project || {}, ex = S.active_experiment || {};
  const rows = [
    [tr("mode"), fmt(S.mode)], [tr("projectQuestion"), fmt(p.question)],
    [tr("sprintClaim"), fmt(s.claim)], [tr("sprintStatus"), fmt(s.status)],
    [tr("activeExperiment"), ex.id ? ex.id + " " + tr("on") + " " + ex.claim_graph_node : "-"],
    [tr("backloggedIdeas"), fmt(S.backlogged_ideas)], [tr("events"), String((S.events || []).length)]
  ];
  $("state-table").innerHTML = rows.map(r => "<tr><td>" + esc(r[0]) + "</td><td class='mono'>" + esc(r[1]) + "</td></tr>").join("");
})();

(function renderEvents() {
  const tbody = $("events-table").querySelector("tbody");
  tbody.innerHTML = (S.events || []).slice().reverse().map(e => {
    const pl = JSON.stringify(e.payload || {});
    return "<tr><td class='mono'>" + esc(String(e.at).slice(11, 19)) + "</td><td>" + esc(e.event) + "</td>" +
      "<td class='mono' title='" + esc(pl) + "'>" + esc(pl.length > 60 ? pl.slice(0, 57) + "..." : pl) + "</td></tr>";
  }).join("") || "<tr><td colspan='3'>" + tr("noEvents") + "</td></tr>";
})();

/* ---------- legend ---------- */
(function renderLegend() {
  const items = [
    ["var(--claim-fill)", tr("claimNode")], ["var(--variable-fill)", tr("variableObserved")],
    ["var(--probe-ready-fill)", tr("hypothesisNode")], ["var(--experiment-fill)", tr("experimentNode")],
    ["var(--result-fill)", tr("resultNode")], ["var(--decision-fill)", tr("decisionNode")],
    ["var(--theory-fill)", tr("theory")], ["var(--amendment-fill)", tr("amendmentNode")],
    ["var(--tests-stroke)", tr("testedBy")], ["var(--guard-stroke)", tr("enabledBy")]
  ];
  $("legend").innerHTML = items.map(i => "<span><i class='sw' style='background:" + i[0] + "'></i>" + i[1] + "</span>").join("");
})();

  $("gen-at").textContent = DATA.generated_at || "";
  renderDag();
}
"""
)


def render_dashboard_page(name: str,
                          frames: list[tuple[str, dict[str, Any], dict[str, Any] | None]]
                          ) -> str:
    """The unified dashboard page: one mountable dashboard per frame, a
    scrubber to walk history, opening on the LATEST frame by default.

    Used both by `dashboard` (frames from the snapshot journal + current
    state) and by `timeline` (frames from a script) — there is no separate
    "replay mode", just one view.
    """
    if not frames:
        state = load_state()
        graph = cg.load_graph(GRAPH_PATH) if GRAPH_PATH.exists() else None
        frames = [("current", state, graph)]
    labels = json.dumps([lbl for lbl, _, _ in frames], ensure_ascii=False)
    marks = json.dumps([
        ("none" if g is None
         else "nodes" if (g.get("variables") or g.get("probes"))
         else "empty")
        for _, _, g in frames])
    payloads = json.dumps(
        [dashboard_payload(st, g) for _, st, g in frames],
        ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate"/>
<meta http-equiv="Pragma" content="no-cache"/>
<title>Research Closure Dashboard</title>
<style>
{DASHBOARD_CSS}
*{{box-sizing:border-box}}
body{{margin:0;font-family:system-ui,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--text)}}
#controls{{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:10px;
padding:10px 16px;background:var(--panel);border-bottom:1px solid var(--line);flex-wrap:wrap}}
h1{{font-size:15px;margin:0 8px 0 0}}
#idx{{font-size:12px;color:var(--muted);min-width:52px}}
button{{background:var(--info-bg);color:var(--info-text);border:1px solid var(--info-border);border-radius:6px;
padding:4px 12px;font-size:12px;cursor:pointer}}
button:hover{{background:var(--button-hover)}}
.control-select{{display:inline-flex;align-items:center;gap:5px;color:var(--muted);font-size:11px}}
.control-select select{{background:var(--surface);color:var(--text);border:1px solid var(--line);border-radius:6px;
padding:4px 7px;font-size:12px;cursor:pointer}}
#slider{{flex:1;min-width:180px;accent-color:var(--blue)}}
#label{{flex-basis:100%;font-size:12px;color:var(--muted)}}
#state{{font-size:11px;font-weight:600;border-radius:99px;padding:2px 10px;border:1px solid var(--line)}}
#state.none{{color:var(--muted)}}
#state.empty{{color:var(--amber);border-color:#78350f}}
#state.nodes{{color:#4ade80;border-color:#166534}}
.stage{{display:none;height:calc(100vh - 66px);overflow:auto;background:var(--bg)}}
.stage.active{{display:block}}
.stage header{{border-top:2px solid var(--line)}}
</style>
</head>
<body>
<div id="controls">
  <h1><span data-i18n="researchDashboard">Research Dashboard</span> {html_escape(name)}</h1>
  <span id="idx">1/{len(frames)}</span>
  <span id="state"></span>
  <span id="gen-at-tl" style="font-size:11px;color:var(--muted)"></span>
  <label class="control-select" for="theme-select"><span data-i18n="theme">theme</span>
    <select id="theme-select" aria-label="Dashboard theme" data-i18n-aria="dashboardTheme">
      <option value="dark" data-i18n="dark">Dark</option>
      <option value="light" data-i18n="light">Light</option>
    </select>
  </label>
  <label class="control-select" for="locale-select"><span data-i18n="language">language</span>
    <select id="locale-select" aria-label="Dashboard language" data-i18n-aria="dashboardLanguage">
      <option value="en" data-i18n="english">English</option>
      <option value="zh" data-i18n="chinese">中文</option>
    </select>
  </label>
  <button id="prev">&#9664; <span data-i18n="prev">prev</span></button>
  <button id="play">&#9654; <span data-i18n="play">play</span></button>
  <button id="next"><span data-i18n="next">next</span> &#9654;</button>
  <button id="first-content" title="jump to the first frame where the DAG has nodes" data-i18n-title="firstContentTitle"><span data-i18n="firstContent">first content</span> &#9193;</button>
  <button id="latest" title="jump to the latest state" data-i18n-title="latestTitle"><span data-i18n="latest">latest</span> &#9195;</button>
  <input id="slider" type="range" min="0" max="{len(frames) - 1}" value="{len(frames) - 1}" step="1"/>
  <div id="label"></div>
</div>
<div id="stages"></div>
<script>
{DASHBOARD_JS}
</script>
<script>
const THEME_STORAGE_KEY = "rch-dashboard-theme";
const themeSelect = document.getElementById("theme-select");
const localeSelect = document.getElementById("locale-select");
function readStoredTheme() {{
  try {{ return localStorage.getItem(THEME_STORAGE_KEY); }} catch (_) {{ return null; }}
}}
function applyTheme(theme) {{
  const selected = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = selected;
  themeSelect.value = selected;
  try {{ localStorage.setItem(THEME_STORAGE_KEY, selected); }} catch (_) {{}}
}}
themeSelect.onchange = () => applyTheme(themeSelect.value);
applyTheme(readStoredTheme() || "dark");
localeSelect.value = RCH_LOCALE;
localeSelect.onchange = () => {{
  try {{ localStorage.setItem(RCH_LOCALE_STORAGE_KEY, localeSelect.value); }} catch (_) {{}}
  location.reload();
}};
translateStatic(document);
document.title = tr("closureDashboard");

const PAYLOADS = {payloads};
const labels = {labels};
const marks = {marks};
const STATE_TEXT = {{ none: tr("stateNone"), empty: tr("stateEmpty"), nodes: tr("stateNodes") }};
const stagesWrap = document.getElementById("stages");
PAYLOADS.forEach((p, i) => {{
  const div = document.createElement("div");
  div.className = "stage";
  stagesWrap.appendChild(div);
  initDashboard(div, p);
}});
const stages = Array.from(document.querySelectorAll(".stage"));
const slider = document.getElementById("slider");
const idxEl = document.getElementById("idx");
const labelEl = document.getElementById("label");
const stateEl = document.getElementById("state");
const genAtEl = document.getElementById("gen-at-tl");
if (genAtEl && PAYLOADS[PAYLOADS.length - 1] && PAYLOADS[PAYLOADS.length - 1].generated_at) {{
  genAtEl.textContent = tr("latestGenerated") + " " + String(PAYLOADS[PAYLOADS.length - 1].generated_at).slice(0, 19).replace("T", " ");
}}
let current = 0, timer = null;
function show(i) {{
  current = i;
  stages.forEach((s, k) => {{ s.classList.toggle("active", k === i); }});
  slider.value = i;
  idxEl.textContent = (i + 1) + "/" + stages.length;
  labelEl.textContent = labels[i] || "";
  const mark = marks[i] || "none";
  stateEl.textContent = STATE_TEXT[mark] || mark;
  stateEl.className = mark;
}}
function play() {{
  if (timer) {{ clearInterval(timer); timer = null; document.getElementById("play").innerHTML = "\\u25b6 <span>" + tr("play") + "</span>"; return; }}
  document.getElementById("play").innerHTML = "\\u23f8 <span>" + tr("pause") + "</span>";
  timer = setInterval(() => {{ show((current + 1) % stages.length); }}, 1600);
}}
document.getElementById("prev").onclick = () => show(Math.max(0, current - 1));
document.getElementById("next").onclick = () => show(Math.min(stages.length - 1, current + 1));
document.getElementById("play").onclick = play;
slider.oninput = () => show(Number(slider.value));
document.getElementById("first-content").onclick = () => {{
  const i = marks.indexOf("nodes");
  show(i >= 0 ? i : 0);
}};
document.getElementById("latest").onclick = () => show(stages.length - 1);
show(stages.length - 1);
</script>
</body>
</html>
"""


def dashboard_payload(state: dict[str, Any], graph: dict[str, Any] | None) -> dict[str, Any]:
    derived: dict[str, Any] = {
        "next_events": next_events(state, graph),
        "blocks": [],
        "warnings": [],
    }
    if graph is None:
        derived["proposal"] = {"status": "no-graph"}
    else:
        derived["design_hash"] = cg.design_hash(graph)
        derived["frontier"] = cg.frontier(graph)
        derived["outcomes"] = cg.outcomes_map(graph)
        derived["skipped"] = sorted(cg.skipped_probes(graph))
        proposal = cg.propose_decision(graph)
        if proposal["status"] == "determined":
            proposal["expected_close"] = DECISION_MAP.get(proposal.get("then"))
        derived["proposal"] = proposal
        derived["debts"] = cg.unpaid_debts(graph)
    blocks, warnings = guard_messages(state)
    derived["blocks"] = blocks
    derived["warnings"] = warnings
    return {
        "generated_at": now_iso(),
        "project_root": str(ROOT),
        "state": state,
        "graph": graph,
        "derived": derived,
        "command": "python tools/research_closure.py dashboard",
    }


def cmd_dashboard(args: argparse.Namespace) -> int:
    state = load_state()
    graph = cg.load_graph(GRAPH_PATH) if GRAPH_PATH.exists() else None
    # frames: snapshot journal (oldest first) + the current state, deduped
    frames = load_snapshot_frames()
    last = frames[-1] if frames else None
    current_json = json.dumps(state, sort_keys=True)
    same = (last is not None
            and json.dumps(last[1], sort_keys=True) == current_json
            and (last[2] == graph or (last[2] is None and graph is None)))
    if not same:
        frames.append(("current state", state, graph))
    html = render_dashboard_page(ROOT.name, frames)
    out = Path(args.out).expanduser() if args.out else ROOT / ".research" / "dashboard.html"
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"Dashboard written: {rel(out)} ({len(frames)} frames, opens at latest)")
    if not args.no_open:
        try:
            import webbrowser
            webbrowser.open(out.resolve().as_uri())
        except Exception as exc:
            print(f"(could not open the browser: {exc})")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    state = load_state()
    out: dict[str, Any] = {
        "version": state.get("version"),
        "mode": state.get("mode"),
        "project": state.get("project"),
        "sprint": state.get("sprint"),
        "active_experiment": state.get("active_experiment"),
        "backlogged_ideas": state.get("counters", {}).get("idea", 0),
        "events": len(state.get("events", [])),
    }
    if GRAPH_PATH.exists():
        try:
            graph = cg.load_graph(GRAPH_PATH)
            proposal = cg.propose_decision(graph)
            out["claim_graph"] = {
                "design_hash": cg.design_hash(graph),
                "ready_frontier": cg.frontier(graph),
                "resolution": proposal["status"],
                "then": proposal.get("then"),
            }
        except SystemExit as exc:
            out["claim_graph"] = {"error": str(exc)}
    print(json.dumps(out, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Research Closure Harness (claim-graph engine)")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("init")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("set-project")
    sp.add_argument("--question", required=True)
    sp.add_argument("--agenda", required=True)
    sp.add_argument("--minimum", required=True)
    sp.set_defaults(func=cmd_set_project)

    sp = sub.add_parser("start-sprint")
    sp.add_argument("--claim", required=True)
    sp.add_argument("--days", type=int, default=14)
    sp.add_argument("--artifact", required=True)
    sp.set_defaults(func=cmd_start_sprint)

    sp = sub.add_parser("close-sprint")
    sp.add_argument("--decision", choices=["continue", "narrow", "terminate", "advance"], required=True)
    sp.add_argument("--evidence", required=True)
    sp.add_argument("--conclusion", required=True)
    sp.set_defaults(func=cmd_close_sprint)

    sp = sub.add_parser("new-experiment")
    sp.add_argument("--question", required=True)
    sp.add_argument("--hypothesis", required=True)
    sp.add_argument("--intervention", required=True)
    sp.add_argument("--measurement", required=True)
    sp.add_argument("--kill", required=True)
    sp.add_argument("--artifact", required=True)
    sp.add_argument("--hours", type=float, required=True)
    sp.add_argument("--node", help="claim-graph probe this card runs, e.g. P2")
    sp.add_argument("--controls", default="", help="comma-separated adjustment set")
    sp.add_argument("--figure", default="", help="expected figure")
    sp.set_defaults(func=cmd_new_experiment)

    sp = sub.add_parser("close-experiment")
    sp.add_argument("--id", required=True)
    sp.add_argument("--decision", choices=["supported", "falsified", "inconclusive", "terminated"], required=True)
    sp.add_argument("--evidence", required=True)
    sp.add_argument("--conclusion", required=True)
    sp.add_argument("--defect", choices=["implementation", "measurement", "design", "hypothesis"],
                    help="required when --decision inconclusive")
    sp.add_argument("--outcome", choices=["positive", "negative", "unresolved"],
                    help="probe outcome, when the card is bound to a claim-graph node")
    sp.set_defaults(func=cmd_close_experiment)

    sp = sub.add_parser("add-idea")
    sp.add_argument("--idea", required=True)
    sp.add_argument("--reason", required=True)
    sp.add_argument("--revisit")
    sp.set_defaults(func=cmd_add_idea)

    sp = sub.add_parser("guard")
    sp.set_defaults(func=cmd_guard)

    sp = sub.add_parser("next", help="show the next events the harness expects")
    sp.set_defaults(func=cmd_next)

    sp = sub.add_parser("events", help="show the event log")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_events)

    sp = sub.add_parser(
        "dashboard",
        help="render an interactive DAG dashboard (self-contained HTML) for human progress tracking",
    )
    sp.add_argument("--out", default="", help="output path (default .research/dashboard.html)")
    sp.add_argument("--no-open", action="store_true", help="do not open the browser")
    sp.set_defaults(func=cmd_dashboard)

    sp = sub.add_parser("status")
    sp.set_defaults(func=cmd_status)
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
