#!/usr/bin/env python3
"""Research Replay: event-scripted rebuild, export and timeline for the harness.

Three commands:

    run       materialise an event script (example/.../script.json) into a fresh
              research directory. The script is a list of CLI invocations, so
              the result is exactly what the harness itself would produce; any
              prefix of the script yields a reproducible intermediate state.
    export    reverse direction: turn a half-finished research directory into a
              script that rebuilds the same *snapshot* (state + graph + probe
              outcomes + active experiment). Not the exact history — that is
              the event log's job — but a faithful, continuable copy.
    timeline  run a script step by step, snapshotting state + claim graph after
              every step, and emit a self-contained HTML with a scrubber over
              the per-step dashboards (audit / teaching / demo).

No third-party dependencies.
"""
from __future__ import annotations

import argparse
import html as htmlmod
import json
import os
import shutil
import subprocess
import sys
import tempfile
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

TOOLS = Path(__file__).resolve().parent
RC_CLI = TOOLS / "research_closure.py"
GRAPH_CLI = TOOLS / "claim_graph.py"
CLI_MAP = {"research_closure": RC_CLI, "claim_graph": GRAPH_CLI}


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------

def load_script(path: Path) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid script {path}: {exc}") from exc


def flatten_args(args: Any) -> list[str]:
    """Turn an args dict into CLI tokens. store_true flags pass with no value."""
    if args is None:
        return []
    if isinstance(args, list):
        return [str(a) for a in args]
    out: list[str] = []
    for k, v in args.items():
        if v is False or v is None or v == "":
            continue
        flag = k if k.startswith("-") else "--" + k
        if v is True:
            out.append(flag)
        else:
            out.append(flag)
            out.append(str(v))
    return out


def run_step(cli: str, cmd: str, args: Any, cwd: Path, env: dict[str, str]
             ) -> subprocess.CompletedProcess[str]:
    exe = CLI_MAP.get(cli)
    if exe is None:
        raise SystemExit(f"unknown cli {cli!r}; use research_closure or claim_graph")
    argv = [sys.executable, str(exe), cmd] + flatten_args(args)
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True)


def fresh_outdir(args: argparse.Namespace) -> Path:
    out = (Path(args.out).expanduser().resolve() if args.out
           else Path(tempfile.mkdtemp(prefix="replay-")))
    if out.exists() and any(out.iterdir()) and not getattr(args, "force", False):
        raise SystemExit(f"BLOCKED: {out} is not empty; use --force to clear it.")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    return out


def repo_env(out: Path) -> dict[str, str]:
    return {**os.environ, "RESEARCH_CLOSURE_ROOT": str(out)}


def print_result(out: Path) -> None:
    p = run_step("research_closure", "status", {}, out, repo_env(out))
    if p.returncode != 0:
        return
    try:
        st = json.loads(p.stdout)
    except json.JSONDecodeError:
        return
    print("--- result ---")
    print("sprint           :", (st.get("sprint") or {}).get("claim", "-"))
    print("frontier         :", (st.get("claim_graph") or {}).get("ready_frontier", "-"))
    print("active_experiment:", (st.get("active_experiment") or {}).get("id", "-"))
    print("events           :", st.get("events", "-"))


# --------------------------------------------------------------------------
# run: script -> materialised research directory
# --------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    script = load_script(Path(args.script))
    steps = script.get("steps", [])
    if args.until:
        steps = steps[: args.until]
    out = fresh_outdir(args)
    env = repo_env(out)
    p = run_step("research_closure", "init", {}, out, env)
    if p.returncode != 0:
        raise SystemExit(f"init failed: {p.stderr}")
    for i, step in enumerate(steps, 1):
        if step.get("comment"):
            print(f"[{i}] # {step['comment']}")
            continue
        cli, cmd = step["cli"], step["cmd"]
        p = run_step(cli, cmd, step.get("args", {}), out, env)
        note = f"  ({step['note']})" if step.get("note") else ""
        print(f"[{i}] {cli} {cmd} ... {'ok' if p.returncode == 0 else 'FAILED'}{note}")
        if p.returncode != 0:
            raise SystemExit(
                f"step {i} failed ({cli} {cmd}):\n{p.stdout}{p.stderr}")
    print_result(out)
    print(f"Materialised: {out}")
    return 0


# --------------------------------------------------------------------------
# export: half-finished research directory -> rebuild script
# --------------------------------------------------------------------------

def experiment_payload(state: dict[str, Any], exp_id: str) -> dict[str, Any] | None:
    for e in state.get("events", []):
        pl = e.get("payload", {})
        if e.get("event") == "experiment_started" and pl.get("id") == exp_id:
            return pl
    return None


def cmd_export(args: argparse.Namespace) -> int:
    repo = Path(args.dir).expanduser().resolve()
    state_path = repo / ".research" / "state.json"
    graph_path = repo / ".research" / "claim_graph.json"
    if not state_path.exists():
        raise SystemExit(f"No .research/state.json at {repo}")
    if not graph_path.exists():
        raise SystemExit(
            f"No claim graph at {repo}; export requires the claim-graph engine.")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    graph = json.loads(graph_path.read_text(encoding="utf-8"))

    steps: list[dict[str, Any]] = []

    def add(cli: str, cmd: str, a: dict[str, Any], note: str | None = None) -> None:
        s: dict[str, Any] = {"cli": cli, "cmd": cmd, "args": a}
        if note:
            s["note"] = note
        steps.append(s)

    project = state.get("project", {})
    if project.get("question"):
        add("research_closure", "set-project", {
            "--question": project.get("question", ""),
            "--agenda": project.get("long_term_agenda", ""),
            "--minimum": project.get("minimum_completion", ""),
        })
    add("claim_graph", "init", {
        "--claim-id": graph.get("claim_id", "SC-001"),
        "--claim": graph.get("claim", ""),
        "--type": graph.get("graph_type", "causal"),
    })
    for vid, v in sorted((graph.get("variables") or {}).items()):
        a = {"--id": vid, "--name": v.get("name", ""), "--role": v.get("role", "")}
        if not v.get("observed", True):
            a["--latent"] = True
        add("claim_graph", "add-variable", a)
    for e in graph.get("edges", []):
        a = {"--from": e["from"], "--to": e["to"]}
        if e.get("from_theory"):
            a["--theory"] = e["from_theory"][0]
        add("claim_graph", "add-edge", a)
    for a in graph.get("assumed_absent", []):
        add("claim_graph", "add-absent", {
            "--from": a["from"], "--to": a["to"],
            "--justification": a.get("justification", ""),
        })
    for pid, probe in sorted((graph.get("probes") or {}).items()):
        a = {
            "--id": pid,
            "--tests": json.dumps(probe.get("tests", {}), ensure_ascii=False),
            "--metric": probe.get("metric", ""),
            "--prereg": probe.get("prereg", ""),
        }
        if probe.get("controls"):
            a["--controls"] = ",".join(probe["controls"])
        if probe.get("guards_in"):
            a["--guards"] = ",".join(probe["guards_in"])
        add("claim_graph", "add-probe", a)
    for r in graph.get("resolution", []):
        a = {"--when": json.dumps(r.get("when", {}), ensure_ascii=False),
             "--then": r.get("then", "")}
        if r.get("rung"):
            a["--rung"] = r["rung"]
        if r.get("depends_on_assumption"):
            a["--depends-on"] = r["depends_on_assumption"]
        if r.get("skip"):
            a["--skip"] = ",".join(r["skip"])
        if r.get("note"):
            a["--note"] = r["note"]
        add("claim_graph", "add-resolution", a)

    sprint = state.get("sprint")
    if sprint:
        days = 14
        try:
            start = datetime.fromisoformat(sprint["started_at"])
            end = datetime.fromisoformat(sprint["ends_at"])
            days = max(1, (end - start).days)
        except Exception:
            pass
        add("research_closure", "start-sprint", {
            "--claim": sprint.get("claim", ""),
            "--artifact": sprint.get("artifact", ""),
            "--days": days,
        })

    amendments = graph.get("amendments") or []
    if amendments:
        steps.append({"comment":
                      f"{len(amendments)} amendment(s) applied to the graph; "
                      "amendments are applied from ranked candidate sets and are "
                      "not reconstructible by script — re-apply them by hand."})

    active_id = (state.get("active_experiment") or {}).get("id")
    opened = [pl for e in state.get("events", [])
              for pl in [e.get("payload", {})]
              if e.get("event") == "experiment_started"]
    if active_id and not any(pl.get("id") == active_id for pl in opened):
        opened.append(state["active_experiment"])
    for pl in opened:
        exp_id = pl.get("id", "")
        a = {
            "--question": pl.get("question", ""),
            "--hypothesis": pl.get("hypothesis", ""),
            "--intervention": pl.get("intervention", ""),
            "--measurement": pl.get("measurement", ""),
            "--kill": pl.get("kill_criterion", ""),
            "--artifact": pl.get("expected_artifact", ""),
            "--hours": pl.get("time_budget_hours", 4),
            "--node": pl.get("claim_graph_node", ""),
        }
        if pl.get("controls"):
            a["--controls"] = pl["controls"]
        if pl.get("expected_figure"):
            a["--figure"] = pl["expected_figure"]
        add("research_closure", "new-experiment", a)
        if exp_id == active_id:
            continue
        node = pl.get("claim_graph_node", "")
        probe = (graph.get("probes") or {}).get(node, {})
        outcome = probe.get("outcome")
        if outcome == "positive":
            decision = "supported"
        elif outcome == "negative":
            decision = "falsified"
        else:
            decision = "inconclusive"
        ca = {"--id": exp_id, "--decision": decision,
              "--evidence": "reconstructed", "--conclusion": "reconstructed"}
        if decision == "inconclusive":
            ca["--defect"] = probe.get("defect", "measurement")
            ca["--outcome"] = "unresolved"
        add("research_closure", "close-experiment", ca,
            note=f"probe {node} outcome {outcome}")
    for e in state.get("events", []):
        pl = e.get("payload", {})
        if e.get("event") != "idea_backlogged":
            continue
        a = {"--idea": pl.get("idea", ""), "--reason": pl.get("reason_not_now", "")}
        if pl.get("revisit"):
            a["--revisit"] = pl["revisit"]
        add("research_closure", "add-idea", a)

    script = {
        "name": repo.name,
        "description": f"Exported from {repo} — rebuilds the same snapshot "
                       "(state, graph, outcomes, active experiment), not the "
                       "exact history; that is the event log's job.",
        "steps": steps,
    }
    blob = json.dumps(script, ensure_ascii=False, indent=2) + "\n"
    out = Path(args.out).expanduser() if args.out else None
    if out:
        out.write_text(blob, encoding="utf-8")
        print(f"Script written: {out}")
    else:
        print(blob)
    print(f"{len(steps)} steps (state v{state.get('version')})")
    return 0


# --------------------------------------------------------------------------
# timeline: script -> scrubber HTML over per-step dashboards
# --------------------------------------------------------------------------

def cmd_timeline(args: argparse.Namespace) -> int:
    script = load_script(Path(args.script))
    steps = script.get("steps", [])
    if args.until:
        steps = steps[: args.until]
    out = Path(tempfile.mkdtemp(prefix="replay-tl-"))
    env = repo_env(out)
    p = run_step("research_closure", "init", {}, out, env)
    if p.returncode != 0:
        raise SystemExit(f"init failed: {p.stderr}")

    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    import research_closure as rc  # reuse the dashboard renderer

    snapshots: list[tuple[str, str, str]] = []  # (label, dashboard html, mark)

    def snapshot(label: str) -> None:
        state = json.loads(
            (out / ".research" / "state.json").read_text(encoding="utf-8"))
        gpath = out / ".research" / "claim_graph.json"
        graph = (json.loads(gpath.read_text(encoding="utf-8"))
                 if gpath.exists() else None)
        payload = rc.dashboard_payload(state, graph)
        if graph is None:
            mark = "none"
        elif graph.get("variables") or graph.get("probes"):
            mark = "nodes"
        else:
            mark = "empty"
        snapshots.append((label, rc.render_dashboard(payload), mark))

    snapshot("init")
    for i, step in enumerate(steps, 1):
        if step.get("comment"):
            continue
        p = run_step(step["cli"], step["cmd"], step.get("args", {}), out, env)
        if p.returncode != 0:
            raise SystemExit(
                f"step {i} failed ({step['cli']} {step['cmd']}):\n{p.stdout}{p.stderr}")
        label = step.get("note") or f"{step['cli']} {step['cmd']}"
        snapshot(f"{i}. {label}")

    html = build_timeline_html(script, snapshots)
    target = Path(args.out).expanduser()
    target.write_text(html, encoding="utf-8")
    print(f"Timeline written: {target} ({len(snapshots)} snapshots)")
    if not args.no_open:
        webbrowser.open(target.resolve().as_uri())
    return 0


def build_timeline_html(script: dict[str, Any],
                        snapshots: list[tuple[str, str, str]]) -> str:
    name = script.get("name", "research replay")
    labels = json.dumps([lbl for lbl, _, _ in snapshots], ensure_ascii=False)
    marks = json.dumps([m for _, _, m in snapshots])
    stages = []
    for i, (_, body, _) in enumerate(snapshots):
        stages.append(
            f'<div class="stage" data-i="{i}">'
            f'<iframe srcdoc="{htmlmod.escape(body, quote=True)}"></iframe></div>')
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Research Replay: {htmlmod.escape(name)}</title>
<style>
:root{{--bg:#0b1220;--panel:#111a2e;--line:#1e293b;--text:#e2e8f0;--muted:#94a3b8;--blue:#38bdf8;
--green:#22c55e;--amber:#fbbf24;--red:#f87171}}
*{{box-sizing:border-box}}
body{{margin:0;font-family:system-ui,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--text)}}
#controls{{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:10px;
padding:10px 16px;background:var(--panel);border-bottom:1px solid var(--line);flex-wrap:wrap}}
h1{{font-size:15px;margin:0 8px 0 0}}
#idx{{font-size:12px;color:var(--muted);min-width:52px}}
button{{background:#082f49;color:var(--blue);border:1px solid #0c4a6e;border-radius:6px;
padding:4px 12px;font-size:12px;cursor:pointer}}
button:hover{{background:#0c4a6e}}
#slider{{flex:1;min-width:180px;accent-color:var(--blue)}}
#label{{flex-basis:100%;font-size:12px;color:var(--muted)}}
#state{{font-size:11px;font-weight:600;border-radius:99px;padding:2px 10px;border:1px solid var(--line)}}
#state.none{{color:var(--muted)}}
#state.empty{{color:var(--amber);border-color:#78350f}}
#state.nodes{{color:#4ade80;border-color:#166534}}
.stage{{display:none}}
.stage iframe{{width:100%;height:calc(100vh - 66px);border:0;display:block;background:var(--bg)}}
</style>
</head>
<body>
<div id="controls">
  <h1>Replay: {htmlmod.escape(name)}</h1>
  <span id="idx">1/{len(snapshots)}</span>
  <span id="state"></span>
  <button id="prev">&#9664; prev</button>
  <button id="play">&#9654; play</button>
  <button id="next">next &#9654;</button>
  <button id="first-content" title="jump to the first frame where the DAG has nodes">first content &#9193;</button>
  <input id="slider" type="range" min="0" max="{len(snapshots) - 1}" value="0" step="1"/>
  <div id="label"></div>
</div>
<div id="stages">
{chr(10).join(stages)}
</div>
<script>
const labels = {labels};
const marks = {marks};
const STATE_TEXT = {{ none: "no claim graph yet", empty: "graph skeleton (no nodes yet)", nodes: "nodes rendered" }};
const stages = Array.from(document.querySelectorAll(".stage"));
const slider = document.getElementById("slider");
const idxEl = document.getElementById("idx");
const labelEl = document.getElementById("label");
const stateEl = document.getElementById("state");
let current = 0, timer = null;
function show(i) {{
  current = i;
  stages.forEach((s, k) => {{ s.style.display = k === i ? "" : "none"; }});
  slider.value = i;
  idxEl.textContent = (i + 1) + "/" + stages.length;
  labelEl.textContent = labels[i] || "";
  const mark = marks[i] || "none";
  stateEl.textContent = STATE_TEXT[mark] || mark;
  stateEl.className = mark;
}}
function play() {{
  if (timer) {{ clearInterval(timer); timer = null; document.getElementById("play").textContent = "\\u25b6 play"; return; }}
  document.getElementById("play").textContent = "\\u23f8 pause";
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
show(0);
</script>
</body>
</html>
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Research Replay: script -> research directory, "
                    "directory -> script, script -> timeline HTML")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("run", help="materialise a script into a fresh research directory")
    sp.add_argument("--script", required=True, help="path to script.json")
    sp.add_argument("--out", default="", help="target directory (default: temp dir)")
    sp.add_argument("--until", type=int, default=0,
                    help="stop after this many steps (reproducible intermediate state)")
    sp.add_argument("--force", action="store_true", help="clear a non-empty --out")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("export", help="turn a research directory into a rebuild script")
    sp.add_argument("--dir", required=True, help="half-finished research directory")
    sp.add_argument("--out", default="", help="script path (default: print to stdout)")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("timeline", help="step-by-step replay with a scrubber (HTML)")
    sp.add_argument("--script", required=True, help="path to script.json")
    sp.add_argument("--out", required=True, help="output HTML path")
    sp.add_argument("--until", type=int, default=0, help="stop after this many steps")
    sp.add_argument("--no-open", action="store_true", help="do not open the browser")
    sp.set_defaults(func=cmd_timeline)
    return p


def main() -> int:
    args = build_parser().parse_args()
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
