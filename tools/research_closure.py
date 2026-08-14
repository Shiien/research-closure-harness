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
DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Research Closure Dashboard</title>
<style>
:root{--bg:#0b1220;--panel:#111a2e;--line:#1e293b;--text:#e2e8f0;--muted:#94a3b8;
--green:#22c55e;--red:#f87171;--amber:#fbbf24;--violet:#a78bfa;--blue:#38bdf8;}
*{box-sizing:border-box}
body{font-family:system-ui,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--text);margin:0}
header{padding:14px 22px;border-bottom:1px solid var(--line);background:var(--panel)}
h1{font-size:17px;margin:0;letter-spacing:.3px}
h2{font-size:13px;margin:0 0 8px;text-transform:uppercase;letter-spacing:1px;color:var(--muted)}
.meta{color:var(--muted);font-size:12px;margin-top:6px;line-height:1.7}
.chip{display:inline-block;padding:1px 8px;border-radius:99px;font-size:11px;font-weight:600;margin-left:6px}
.chip.ok{background:#052e16;color:#4ade80;border:1px solid #166534}
.chip.bad{background:#450a0a;color:#fca5a5;border:1px solid #7f1d1d}
.chip.info{background:#082f49;color:#7dd3fc;border:1px solid #0c4a6e}
main{display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:14px;padding:14px 22px;align-items:start}
@media(max-width:1000px){main{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card+.card{margin-top:14px}
#canvas-wrap{position:relative;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:#0d1626;min-height:320px}
svg#dag{display:block;width:100%;min-height:320px;cursor:grab;touch-action:none}
svg#dag.dragging{cursor:grabbing}
#tooltip{position:absolute;display:none;max-width:360px;background:#0f172a;border:1px solid #334155;
border-radius:8px;padding:8px 10px;font-size:12px;line-height:1.6;white-space:pre-wrap;
pointer-events:none;z-index:20;box-shadow:0 8px 24px rgba(0,0,0,.5)}
#zoom-hint{position:absolute;top:8px;right:10px;font-size:11px;color:var(--muted);user-select:none}
#reset-view{position:absolute;top:8px;right:88px;font-size:11px;color:var(--blue);cursor:pointer;
border:1px solid #0c4a6e;border-radius:6px;padding:2px 8px;background:#082f49;user-select:none}
#probe-detail{font-size:12px;line-height:1.7;min-height:120px;color:#cbd5e1}
#probe-detail b{color:var(--text)}
#probe-detail pre{margin:6px 0 0;background:#0d1626;border:1px solid var(--line);border-radius:6px;
padding:6px 8px;font-size:11px;overflow-x:auto;white-space:pre-wrap}
table{width:100%;border-collapse:collapse;font-size:11.5px}
th,td{text-align:left;padding:4px 8px;border-bottom:1px solid #1a2740;vertical-align:top}
th{color:var(--muted);font-weight:600;text-transform:uppercase;font-size:10.5px;letter-spacing:.6px}
td.mono,code{font-family:ui-monospace,Consolas,monospace;font-size:11px}
.rule{border:1px solid var(--line);border-radius:8px;padding:8px 10px;margin-bottom:8px;font-size:12px}
.rule.fires{border-color:#166534;background:#052e16}
.rule .when{color:#cbd5e1}
.rule .then{font-weight:700;margin-top:2px}
.rule .note{color:var(--muted);margin-top:4px;font-size:11px}
.banner{border-radius:8px;padding:8px 12px;font-size:12.5px;margin-top:10px;line-height:1.7}
.banner.pass{background:#052e16;border:1px solid #166534;color:#bbf7d0}
.banner.block{background:#450a0a;border:1px solid #7f1d1d;color:#fecaca}
.banner .l{color:var(--muted)}
#next-list{margin:6px 0 0;padding-left:18px;font-size:12px;line-height:1.9}
#legend{display:flex;flex-wrap:wrap;gap:12px;font-size:11px;color:var(--muted);margin-top:8px}
#legend span{display:inline-flex;align-items:center;gap:5px}
.sw{display:inline-block;width:12px;height:12px;border-radius:3px;border:1px solid #334155}
footer{padding:10px 22px 18px;color:var(--muted);font-size:11.5px;line-height:1.7}
.empty{color:var(--muted);font-size:12.5px;padding:26px;text-align:center}
.empty.big{padding:44px 30px;line-height:1.9}
.empty-title{font-size:15px;font-weight:700;color:var(--text);margin-bottom:10px}
.empty-sub{margin-top:12px;font-size:11.5px;color:#64748b}
.empty code{background:#0d1626;border:1px solid var(--line);border-radius:5px;padding:1px 6px;font-size:11px}
</style>
</head>
<body>
<header>
  <h1>Research Closure Dashboard <span id="verdict-chip"></span></h1>
  <div class="meta" id="meta"></div>
  <div id="guard-banner"></div>
</header>
<main>
  <div>
    <div class="card">
      <h2>Claim graph (DAG)</h2>
      <div id="canvas-wrap">
        <div id="zoom-hint">drag: pan · wheel: zoom</div>
        <div id="reset-view">reset view</div>
        <svg id="dag"></svg>
        <div id="tooltip"></div>
        <div id="dag-empty"></div>
      </div>
      <div id="legend"></div>
    </div>
    <div class="card">
      <h2>Resolution map</h2>
      <div id="resolution"></div>
    </div>
    <div class="card">
      <h2>Next events</h2>
      <ol id="next-list"></ol>
    </div>
    <div class="card">
      <h2>Event log</h2>
      <table id="events-table"><thead><tr><th>at</th><th>event</th><th>payload</th></tr></thead><tbody></tbody></table>
    </div>
  </div>
  <div>
    <div class="card">
      <h2>Probe detail</h2>
      <div id="probe-detail">Click a probe node to inspect its pre-registration and outcome.</div>
    </div>
    <div class="card">
      <h2>State</h2>
      <table id="state-table"></table>
    </div>
  </div>
</main>
<footer>
  Generated <span id="gen-at"></span> · regenerate with <code>python tools/research_closure.py dashboard</code>
  · the design hash freezes the pre-registration; outcomes and amendments move the map.
</footer>
<script>
const DATA = /*__DATA__*/;
const G = DATA.graph, D = DATA.derived || {}, S = DATA.state || {};
const $ = (id) => document.getElementById(id);
const esc = (s) => { const d = document.createElement("div"); d.textContent = String(s == null ? "" : s); return d.innerHTML; };
const fmt = (s) => s == null ? "-" : s;

/* ---------- header: verdict, meta, guard ---------- */
(function header(){
  const chip = $("verdict-chip");
  if (D.blocks && D.blocks.length) chip.className = "chip bad", chip.textContent = "BLOCK";
  else chip.className = "chip ok", chip.textContent = "PASS";
  const s = S.sprint || {}, p = S.project || {};
  let m = "claim: <b>" + esc(G ? G.claim : "(no claim graph yet)") + "</b>";
  if (G) m += " · type: " + esc(G.graph_type) + " · design hash: <code>" + esc(D.design_hash) + "</code>";
  if (s.claim) m += " · sprint: <b>" + esc(s.claim) + "</b>";
  if (s.ends_at) m += " · ends " + esc(String(s.ends_at).slice(0, 10));
  if (S.active_experiment) m += " · active: <b>" + esc(S.active_experiment.id) + "</b> on " + esc(S.active_experiment.claim_graph_node);
  if (D.proposal && D.proposal.status === "determined")
    m += " · resolution map: <b>" + esc(D.proposal.then) + "</b>" + (D.proposal.expected_close ? " → close as " + esc(D.proposal.expected_close) : "");
  else if (D.proposal && D.proposal.status === "open")
    m += " · resolution map: open (ready: " + esc((D.frontier || []).join(", ") || "none") + ")";
  $("meta").innerHTML = m;

  const banner = $("guard-banner");
  const warns = (D.warnings || []).map(w => "warning: " + w).join("<br/>");
  const blocks = (D.blocks || []).map(b => "block: " + b).join("<br/>");
  if ((D.blocks || []).length)
    banner.className = "banner block", banner.innerHTML = "<div>" + blocks + "</div>" + (warns ? "<div class='l'>" + warns + "</div>" : "");
  else
    banner.className = "banner pass", banner.innerHTML = "PASS: work may proceed within the frozen claim." + (warns ? "<div class='l'>" + warns + "</div>" : "");
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
  let s = "probe " + id + "\n";
  s += "tests: " + JSON.stringify(p.tests) + "\n";
  s += "metric: " + p.metric + "\n";
  s += "prereg: " + p.prereg + "\n";
  if (p.controls && p.controls.length) s += "controls: " + p.controls.join(", ") + "\n";
  if (p.guards_in && p.guards_in.length) s += "guards_in: " + p.guards_in.join(", ") + "\n";
  s += "outcome: " + ((D.outcomes || {})[id] || "-") + "\n";
  if (p.experiment_id) s += "experiment: " + p.experiment_id + "\n";
  if (p.defect) s += "defect: " + p.defect;
  return s;
}

/* ---------- DAG layout + rendering ---------- */
const ST = {
  ready:     {fill:"#052e16", stroke:"#22c55e", badge:"READY"},
  positive:  {fill:"#14532d", stroke:"#4ade80", badge:"positive"},
  negative:  {fill:"#450a0a", stroke:"#f87171", badge:"negative"},
  unresolved:{fill:"#1e293b", stroke:"#94a3b8", badge:"unresolved"},
  skipped:   {fill:"#111a2e", stroke:"#475569", badge:"skipped", dim:true},
  waiting:   {fill:"#111a2e", stroke:"#475569", badge:"waiting"}
};
const layout = (function(){
  const nodes = [], edges = [], byId = {};
  if (!G) return {nodes, edges};
  const push = (n) => { n.label = n.id; n.w = Math.max(88, n.label.length * 9 + 30); n.h = n.kind === "probe" ? 44 : 38; nodes.push(n); byId[n.id] = n; };
  for (const [id, m] of Object.entries(G.theory || {})) {
    if (m.retired_at) continue;
    push({ id, kind: "theory", layer: 0,
      detail: "statement: " + (m.statement || "") + "\nprovenance: " + (m.provenance || "?") + "\nentails: " + ((m.entails || []).join(", ") || "-") });
  }
  for (const [id, v] of Object.entries(G.variables || {}))
    push({ id, kind: "variable", layer: 1, latent: !v.observed,
      detail: (v.name || "") + "\nrole: " + (v.role || "?") + (v.observed ? "" : "\n(unobserved)") });
  for (const [id, p] of Object.entries(G.probes || {}))
    push({ id, kind: "probe", layer: 2, status: probeStatus(id, p), detail: probeDetail(id, p) });
  for (const e of (G.edges || []))
    edges.push({ a: e.from, b: e.to, kind: (e.from_theory && e.from_theory.length) ? "theory-edge" : "edge" });
  for (const a of (G.assumed_absent || []))
    edges.push({ a: a.from, b: a.to, kind: "absent", detail: a.justification });
  for (const [id, p] of Object.entries(G.probes || {}))
    for (const g of (p.guards_in || [])) {
      const dep = String(g).split("==")[0].trim();
      if (byId[dep]) edges.push({ a: dep, b: id, kind: "guard", label: g });
    }
  const M = 30, GAP_X = 36, GAP_Y = [150, 170, 0];
  let y = M;
  for (const layer of [0, 1, 2]) {
    const row = (nodes.filter(n => n.layer === layer)).sort((a, b) => a.id < b.id ? -1 : 1);
    let x = M;
    for (const n of row) { n.x = x; n.y = y; x += n.w + GAP_X; }
    if (row.length && layer < 2) y += GAP_Y[layer];
  }
  return { nodes, edges, byId };
})();

const EDGE_STYLE = {
  "edge":        { stroke:"#64748b", width:2, dash:"", marker:"arr-edge" },
  "theory-edge": { stroke:"#a78bfa", width:1.6, dash:"", marker:"arr-theory" },
  "absent":      { stroke:"#ef4444", width:2, dash:"6 4", marker:"arr-absent" },
  "guard":       { stroke:"#7dd3fc", width:1.4, dash:"2 4", marker:"arr-guard" }
};
const MARKERS = [
  ['arr-edge', "#64748b"], ['arr-theory', "#a78bfa"],
  ['arr-absent', "#ef4444"], ['arr-guard', "#7dd3fc"]
].map(p => '<marker id="' + p[0] + '" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" fill="' + p[1] + '"/></marker>').join("");

const svg = $("dag");
function edgePath(e) {
  const a = layout.byId[e.a], b = layout.byId[e.b];
  if (!a || !b) return "";
  const x1 = a.x + a.w / 2, y1 = a.y + a.h / 2, x2 = b.x + b.w / 2, y2 = b.y + b.h / 2;
  const dx = (x2 - x1) * 0.35;
  return "M" + x1 + "," + y1 + " C" + (x1 + dx) + "," + y1 + " " + (x2 - dx) + "," + y2 + " " + x2 + "," + y2;
}
function nodeSvg(n) {
  if (n.kind === "theory")
    return '<rect x="' + n.x + '" y="' + n.y + '" width="' + n.w + '" height="' + n.h + '" rx="9" fill="#2e1065" stroke="#a78bfa" stroke-width="1.5"/>' + nodeText(n);
  if (n.kind === "variable") {
    if (n.latent)
      return '<ellipse cx="' + (n.x + n.w / 2) + '" cy="' + (n.y + n.h / 2) + '" rx="' + n.w / 2 + '" ry="' + n.h / 2 + '" fill="#1e293b" stroke="#64748b" stroke-width="1.5" stroke-dasharray="5 3"/>' + nodeText(n);
    return '<rect x="' + n.x + '" y="' + n.y + '" width="' + n.w + '" height="' + n.h + '" rx="7" fill="#082f49" stroke="#38bdf8" stroke-width="1.5"/>' + nodeText(n);
  }
  const st = ST[n.status] || ST.waiting;
  const badge = st.badge ? '<text x="' + (n.x + n.w - 8) + '" y="' + (n.y + 10) + '" text-anchor="end" font-size="9" fill="#7dd3fc" font-weight="700">' + esc(st.badge) + '</text>' : "";
  return '<rect x="' + n.x + '" y="' + n.y + '" width="' + n.w + '" height="' + n.h + '" rx="10" fill="' + st.fill + '" stroke="' + st.stroke + '" stroke-width="2"/>' + nodeText(n) + badge;
}
function nodeText(n) {
  return '<text x="' + (n.x + n.w / 2) + '" y="' + (n.y + n.h / 2) + '" text-anchor="middle" dominant-baseline="central" fill="#e2e8f0" font-size="13" font-weight="600">' + esc(n.label) + '</text>';
}

function renderDag() {
  const empty = $("dag-empty");
  if (!G || !layout.nodes.length) {
    svg.innerHTML = "";
    const noGraph = !G;
    const title = noGraph
      ? "No claim graph in this repository yet"
      : "The claim graph exists but has no nodes yet";
    const hint = noGraph
      ? "The claim graph is the engine \u2014 nodes appear here once it has content.<br/>" +
        "1. <code>claim_graph.py init --claim \"&lt;the sprint claim&gt;\"</code><br/>" +
        "2. <code>add-variable --id X --name \"...\" --role \"...\"</code> then <code>add-edge / add-probe / add-resolution</code><br/>" +
        "3. <code>research_closure.py start-sprint ...</code>"
      : "The graph skeleton has no variables or probes yet.<br/>" +
        "<code>claim_graph.py add-variable --id X --name \"...\" --role \"...\"</code> then <code>add-edge / add-probe / add-resolution</code>";
    empty.innerHTML = '<div class="empty big"><div class="empty-title">' + title +
      "</div>" + hint +
      '<div class="empty-sub">Theory (M), variable and probe (P) nodes render here as soon as the claim graph has content. The exact next commands are listed in the "Next events" panel below.</div></div>';
    return;
  }
  empty.innerHTML = "";
  let W = 0, H = 0;
  for (const n of layout.nodes) { W = Math.max(W, n.x + n.w); H = Math.max(H, n.y + n.h); }
  W += 40; H += 50;
  let parts = ['<defs>' + MARKERS + '</defs>'];
  for (const e of layout.edges) {
    const st = EDGE_STYLE[e.kind];
    const cls = e.kind === "absent" ? ' stroke-dasharray="' + st.dash + '"' : (st.dash ? ' stroke-dasharray="' + st.dash + '"' : "");
    parts.push('<path d="' + edgePath(e) + '" fill="none" stroke="' + st.stroke + '" stroke-width="' + st.width + '" marker-end="url(#' + st.marker + ')"' + cls + ' data-edge="1" data-kind="' + e.kind + '" data-detail="' + esc(e.detail || e.label || "") + '"/>');
    if (e.kind === "absent") {
      const a = layout.byId[e.a], b = layout.byId[e.b];
      parts.push('<text x="' + ((a.x + a.w + b.x) / 2) + '" y="' + ((a.y + b.y + b.h) / 2) + '" text-anchor="middle" font-size="13" fill="#f87171" font-weight="700">&#10007;</text>');
    }
  }
  for (const n of layout.nodes)
    parts.push('<g data-node="' + n.id + '" data-kind="' + n.kind + '" data-detail="' + esc(n.detail) + '" data-probe="' + (n.kind === "probe" ? n.id : "") + '">' + nodeSvg(n) + '</g>');
  parts.push('<text x="20" y="' + (H - 16) + '" font-size="11" fill="#64748b">theory (M) → variables/edges (observed solid · latent dashed · ✗ assumed absent) → probes (P)</text>');
  svg.innerHTML = parts.join("");
  svg.setAttribute("viewBox", "0 0 " + W + " " + H);

  const g = svg.querySelectorAll("g[data-node]"), epaths = svg.querySelectorAll("path[data-edge]");
  const tip = $("tooltip");
  const showTip = (ev, text) => { tip.textContent = text; tip.style.display = "block"; moveTip(ev); };
  const moveTip = (ev) => { const r = svg.getBoundingClientRect(); let x = ev.clientX - r.left + 14, y = ev.clientY - r.top + 14; if (x + 360 > r.width) x -= 380; tip.style.left = x + "px"; tip.style.top = y + "px"; };
  const hideTip = () => { tip.style.display = "none"; };
  for (const n of g) {
    n.addEventListener("mouseenter", (ev) => showTip(ev, n.dataset.detail));
    n.addEventListener("mousemove", moveTip);
    n.addEventListener("mouseleave", hideTip);
    if (n.dataset.probe) n.addEventListener("click", () => showProbeDetail(n.dataset.probe));
  }
  for (const p of epaths) {
    p.addEventListener("mouseenter", (ev) => {
      const t = p.dataset.kind === "absent" ? "assumed absent: " + p.dataset.detail
             : p.dataset.kind === "guard" ? "guard: " + p.dataset.detail : p.dataset.detail;
      showTip(ev, t);
    });
    p.addEventListener("mousemove", moveTip);
    p.addEventListener("mouseleave", hideTip);
  }

  /* pan + zoom */
  let drag = null, scale = 1, tx = 0, ty = 0;
  const apply = () => { svg.setAttribute("transform", "translate(" + tx + "," + ty + ") scale(" + scale + ")"); };
  svg.onmousedown = (ev) => { drag = { x: ev.clientX, y: ev.clientY, tx: tx, ty: ty }; svg.classList.add("dragging"); };
  window.onmousemove = (ev) => {
    if (!drag) return;
    tx = drag.tx + (ev.clientX - drag.x); ty = drag.ty + (ev.clientY - drag.y); apply();
  };
  window.onmouseup = () => { drag = null; svg.classList.remove("dragging"); };
  svg.onwheel = (ev) => {
    ev.preventDefault();
    scale = Math.min(3, Math.max(0.3, scale * (ev.deltaY < 0 ? 1.12 : 0.9)));
    apply();
  };
  $("reset-view").onclick = () => { scale = 1; tx = 0; ty = 0; apply(); };
}

/* ---------- probe detail panel ---------- */
function showProbeDetail(id) {
  const p = (G && G.probes && G.probes[id]) || null;
  const panel = $("probe-detail");
  if (!p) { panel.innerHTML = "Unknown probe."; return; }
  let h = "<b>" + esc(id) + "</b> — " + esc(probeStatus(id, p)) + "<br/>" + esc(p.metric) + "<br/>";
  h += "outcome: <b>" + esc((D.outcomes || {})[id] || "-") + "</b>" + (p.experiment_id ? " (experiment " + esc(p.experiment_id) + ")" : "") + "<br/>";
  h += "guards_in: " + (p.guards_in && p.guards_in.length ? esc(p.guards_in.join(", ")) : "none") + "<br/>";
  h += "controls: " + (p.controls && p.controls.length ? esc(p.controls.join(", ")) : "-");
  h += "<pre>" + esc(JSON.stringify(p.tests, null, 1)) + "</pre>";
  h += "<div>pre-registration:</div><pre>" + esc(p.prereg) + "</pre>";
  const rules = ((G.resolution || []).map((r, i) => ({ r, i }))).filter(x => Object.keys(x.r.when || {}).indexOf(id) >= 0);
  if (rules.length) {
    h += "<div style='margin-top:8px'>resolution rules mentioning " + esc(id) + ":</div>";
    for (const x of rules) h += "<pre>" + esc(JSON.stringify(x.r.when)) + " → " + esc(x.r.then) + "</pre>";
  }
  panel.innerHTML = h;
}

/* ---------- resolution map ---------- */
(function renderResolution() {
  const box = $("resolution");
  if (!G) { box.innerHTML = '<div class="empty">No resolution map until the claim graph exists.</div>'; return; }
  const rules = G.resolution || [];
  if (!rules.length) { box.innerHTML = '<div class="empty">No resolution rules pre-registered.</div>'; return; }
  const outs = D.outcomes || {};
  box.innerHTML = rules.map((r, i) => {
    const fired = Object.entries(r.when || {}).every(kv => outs[kv[0]] === kv[1]);
    const whenTxt = Object.keys(r.when || {}).map(k => esc(k) + "=" + esc(r.when[k])).join(", ");
    let h = '<div class="rule' + (fired ? " fires" : "") + '">';
    h += '<span class="when">when { ' + whenTxt + ' }</span>';
    h += '<div class="then">→ ' + esc(r.then) + (r.rung ? " <span style='color:var(--amber)'>(" + esc(r.rung) + ")</span>" : "") + (fired ? ' <span class="chip ok">FIRES</span>' : "") + '</div>';
    if (r.skip && r.skip.length) h += '<div class="note">skips: ' + esc(r.skip.join(", ")) + "</div>";
    if (r.depends_on_assumption) h += '<div class="note">depends on assumption: ' + esc(r.depends_on_assumption) + "</div>";
    if (r.note) h += '<div class="note">' + esc(r.note) + "</div>";
    return h + "</div>";
  }).join("");
})();

/* ---------- next events / state / events log ---------- */
(function renderNext() {
  $("next-list").innerHTML = (D.next_events || ["(nothing to do)"]).map(e => "<li><code>" + esc(e) + "</code></li>").join("");
})();

(function renderState() {
  const s = S.sprint || {}, p = S.project || {}, ex = S.active_experiment || {};
  const rows = [
    ["mode", fmt(S.mode)], ["project question", fmt(p.question)],
    ["sprint claim", fmt(s.claim)], ["sprint status", fmt(s.status)],
    ["active experiment", ex.id ? ex.id + " on " + ex.claim_graph_node : "-"],
    ["backlogged ideas", fmt(S.backlogged_ideas)], ["events", String((S.events || []).length)]
  ];
  $("state-table").innerHTML = rows.map(r => "<tr><td>" + esc(r[0]) + "</td><td class='mono'>" + esc(r[1]) + "</td></tr>").join("");
})();

(function renderEvents() {
  const tbody = $("events-table").querySelector("tbody");
  tbody.innerHTML = (S.events || []).slice().reverse().map(e => {
    const pl = JSON.stringify(e.payload || {});
    return "<tr><td class='mono'>" + esc(String(e.at).slice(11, 19)) + "</td><td>" + esc(e.event) + "</td>" +
      "<td class='mono' title='" + esc(pl) + "'>" + esc(pl.length > 60 ? pl.slice(0, 57) + "..." : pl) + "</td></tr>";
  }).join("") || "<tr><td colspan='3'>no events yet</td></tr>";
})();

/* ---------- legend ---------- */
(function renderLegend() {
  const items = [
    ["#2e1065", "theory (M)"], ["#082f49", "variable (observed)"], ["#1e293b", "variable (latent, dashed)"],
    ["#052e16", "probe ready"], ["#14532d", "probe positive"], ["#450a0a", "probe negative"],
    ["#1e293b", "unresolved"], ["#111a2e", "waiting/skipped"],
    ["#a78bfa", "theory→observation"], ["#64748b", "edge"], ["#ef4444", "assumed absent"], ["#7dd3fc", "probe guard"]
  ];
  $("legend").innerHTML = items.map(i => "<span><i class='sw' style='background:" + i[0] + "'></i>" + i[1] + "</span>").join("");
})();

$("gen-at").textContent = DATA.generated_at || "";
renderDag();
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
        "state": state,
        "graph": graph,
        "derived": derived,
        "command": "python tools/research_closure.py dashboard",
    }


def render_dashboard(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, ensure_ascii=False, indent=1).replace("</", "<\\/")
    return DASHBOARD_HTML.replace("/*__DATA__*/", blob)


def cmd_dashboard(args: argparse.Namespace) -> int:
    state = load_state()
    graph = cg.load_graph(GRAPH_PATH) if GRAPH_PATH.exists() else None
    html = render_dashboard(dashboard_payload(state, graph))
    out = Path(args.out).expanduser() if args.out else ROOT / ".research" / "dashboard.html"
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"Dashboard written: {rel(out)}")
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
