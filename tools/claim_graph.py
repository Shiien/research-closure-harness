#!/usr/bin/env python3
"""Claim graph for the Research Closure Harness.

Three layers, one file:

    theory layer        M-nodes: the mechanism you believe
        | entails                (target of induction and abduction)
    observation layer   variables + directed edges + absence assumptions
        | tested_by              (product of deduction)
    probe layer         P-nodes bound to experiment cards

Deduction, induction and abduction are the three maps between those layers.
This module implements only their mechanical parts: enumerating implications,
detecting incompatibility, enumerating repairs, and refusing proposals that
fail a structural test. Choosing among candidates is not mechanised; the CLI
emits a candidate set and ingests a selection record.

No third-party dependencies.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1
GRAPH_TYPES = ("causal", "comparative", "theoretical")
PROVENANCE = ("deduced", "induced", "abduced")
OUTCOMES = ("positive", "negative", "unresolved")

# Public product vocabulary. Keep these names and directions visible in the
# CLI: the three reasoning modes are a user-facing feature, not only internal
# implementation sections.
REASONING_MODES = (
    (
        "Deduction",
        "theory + observation DAG -> testable implications -> probes",
        "deduce",
    ),
    (
        "Induction",
        "independent closed probes -> a theory node with a new prediction",
        "induce",
    ),
    (
        "Abduction",
        "anomaly against the DAG -> candidate structural repairs",
        "abduce",
    ),
)

# Minimum independent closed probes before a generalisation may enter the
# theory layer. Below this, "generalisation" is a synonym for "restatement".
MIN_SUPPORT_FOR_INDUCTION = 2

# Above this many observed variables, pricing a repair stops enumerating every
# conditioning set and falls back to parent sets. A graph this large already
# violates the one-screen rule in docs/claim_graph_protocol.md section 8.
MAX_EXHAUSTIVE_PRICING_VARS = 8


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------

def discover_root() -> Path:
    explicit = os.environ.get("RESEARCH_CLOSURE_ROOT")
    if explicit:
        return Path(explicit).expanduser().resolve()
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".research" / "state.json").exists():
            return candidate
    return cwd


ROOT = discover_root()
GRAPH_PATH = ROOT / ".research" / "claim_graph.json"
CANDIDATES_PATH = ROOT / ".research" / "candidates.json"
LOG_DIR = ROOT / ".research" / "logs"
STATE_PATH = ROOT / ".research" / "state.json"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def append_state_event(event: str, payload: dict[str, Any]) -> None:
    """Append to the unified event log in .research/state.json (best effort)."""
    if not STATE_PATH.exists():
        return
    try:
        state = json.loads(STATE_PATH.read_text())
        state.setdefault("events", []).append(
            {"at": now_iso(), "event": event, "payload": payload}
        )
        STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")
    except Exception:
        pass


def load_graph(path: Path | None = None) -> dict[str, Any]:
    p = path or GRAPH_PATH
    if not p.exists():
        raise SystemExit(f"No claim graph at {p}. Run: claim_graph.py init")
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid claim graph: {exc}") from exc


def save_graph(graph: dict[str, Any], path: Path | None = None) -> None:
    p = path or GRAPH_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(graph, indent=2, sort_keys=False) + "\n")


# --------------------------------------------------------------------------
# design hash: freezes the design, not the results
# --------------------------------------------------------------------------

def design_view(graph: dict[str, Any]) -> dict[str, Any]:
    """The part of the graph that must not move after the sprint starts.

    Outcomes, experiment bindings and the amendment log are appended as work
    proceeds and are deliberately excluded. Everything else is pre-registration.
    """
    probes = {}
    for pid, probe in sorted(graph.get("probes", {}).items()):
        probes[pid] = {
            "tests": probe.get("tests"),
            "metric": probe.get("metric"),
            "prereg": probe.get("prereg"),
            "controls": sorted(probe.get("controls", [])),
            "guards_in": sorted(probe.get("guards_in", [])),
        }
    return {
        "claim": graph.get("claim"),
        "graph_type": graph.get("graph_type"),
        "theory": {
            k: {"statement": v.get("statement"), "entails": v.get("entails", [])}
            for k, v in sorted(graph.get("theory", {}).items())
            if not v.get("retired_at")
        },
        "variables": {
            k: {"role": v.get("role"), "observed": v.get("observed", True)}
            for k, v in sorted(graph.get("variables", {}).items())
        },
        "edges": sorted([f"{e['from']}->{e['to']}" for e in graph.get("edges", [])]),
        "assumed_absent": sorted(
            [f"{a['from']}->{a['to']}" for a in graph.get("assumed_absent", [])]
        ),
        "probes": probes,
        "resolution": graph.get("resolution", []),
        "max_probes": graph.get("max_probes"),
        "max_amendments": graph.get("max_amendments"),
    }


def design_hash(graph: dict[str, Any]) -> str:
    blob = json.dumps(design_view(graph), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------
# graph primitives
# --------------------------------------------------------------------------

def edge_list(graph: dict[str, Any]) -> list[tuple[str, str]]:
    return [(e["from"], e["to"]) for e in graph.get("edges", [])]


def parents_of(edges: Iterable[tuple[str, str]], node: str) -> set[str]:
    return {a for a, b in edges if b == node}


def children_of(edges: Iterable[tuple[str, str]], node: str) -> set[str]:
    return {b for a, b in edges if a == node}


def ancestors(edges: Iterable[tuple[str, str]], nodes: Iterable[str]) -> set[str]:
    edges = list(edges)
    seen = set(nodes)
    stack = list(seen)
    while stack:
        n = stack.pop()
        for p in parents_of(edges, n):
            if p not in seen:
                seen.add(p)
                stack.append(p)
    return seen


def descendants(edges: Iterable[tuple[str, str]], node: str) -> set[str]:
    edges = list(edges)
    seen: set[str] = set()
    stack = [node]
    while stack:
        n = stack.pop()
        for c in children_of(edges, n):
            if c not in seen:
                seen.add(c)
                stack.append(c)
    return seen


def find_cycle(edges: Iterable[tuple[str, str]]) -> list[str] | None:
    edges = list(edges)
    nodes = {n for e in edges for n in e}
    colour: dict[str, int] = {n: 0 for n in nodes}
    path: list[str] = []

    def visit(n: str) -> list[str] | None:
        colour[n] = 1
        path.append(n)
        for c in sorted(children_of(edges, n)):
            if colour.get(c, 0) == 1:
                return path[path.index(c):] + [c]
            if colour.get(c, 0) == 0:
                found = visit(c)
                if found:
                    return found
        path.pop()
        colour[n] = 2
        return None

    for n in sorted(nodes):
        if colour[n] == 0:
            found = visit(n)
            if found:
                return found
    return None


def d_separated(
    edges: Iterable[tuple[str, str]], x: str, y: str, given: Iterable[str]
) -> bool:
    """Standard ancestral-moralisation test for d-separation."""
    edges = list(edges)
    given = set(given)
    if x == y:
        return False
    if x in given or y in given:
        return True

    keep = ancestors(edges, {x, y} | given)
    sub = [(a, b) for a, b in edges if a in keep and b in keep]

    adj: dict[str, set[str]] = defaultdict(set)
    for a, b in sub:
        adj[a].add(b)
        adj[b].add(a)

    par: dict[str, set[str]] = defaultdict(set)
    for a, b in sub:
        par[b].add(a)
    for ps in par.values():
        for p1, p2 in itertools.combinations(sorted(ps), 2):
            adj[p1].add(p2)
            adj[p2].add(p1)

    seen = {x}
    stack = [x]
    while stack:
        n = stack.pop()
        if n == y:
            return False
        for m in adj[n]:
            if m in given or m in seen:
                continue
            seen.add(m)
            stack.append(m)
    return True


# --------------------------------------------------------------------------
# adjustment sets
# --------------------------------------------------------------------------

def verify_adjustment(
    edges: Iterable[tuple[str, str]], x: str, y: str, z: Iterable[str]
) -> tuple[bool, str]:
    """Back-door criterion for the effect of x on y adjusting for z."""
    edges = list(edges)
    z = set(z)
    desc = descendants(edges, x)
    bad = z & desc
    if bad:
        return False, f"adjustment set contains descendant(s) of {x}: {sorted(bad)}"
    pruned = [(a, b) for a, b in edges if a != x]
    if not d_separated(pruned, x, y, z):
        return False, f"an open back-door path from {x} to {y} remains"
    return True, ""


def recommend_adjustment(graph: dict[str, Any], x: str, y: str) -> tuple[list[str], list[str]]:
    """Parents of the treatment, split into observed and unobserved."""
    edges = edge_list(graph)
    variables = graph.get("variables", {})
    ps = sorted(parents_of(edges, x))
    observed = [p for p in ps if variables.get(p, {}).get("observed", True)]
    latent = [p for p in ps if not variables.get(p, {}).get("observed", True)]
    return observed, latent


# --------------------------------------------------------------------------
# deduction
# --------------------------------------------------------------------------

def observed_vars(graph: dict[str, Any]) -> list[str]:
    return sorted(
        k for k, v in graph.get("variables", {}).items() if v.get("observed", True)
    )


def testable_implications(graph: dict[str, Any]) -> list[dict[str, Any]]:
    """Pairwise local-Markov implications over observed variables.

    The full set of conditional independencies implied by a DAG is
    exponential; its local Markov basis is linear and entails the rest. This
    returns the pairwise form of that basis, which is what an experiment can
    actually target.
    """
    edges = edge_list(graph)
    out: list[dict[str, Any]] = []
    for v in observed_vars(graph):
        pa = sorted(parents_of(edges, v))
        nondesc = set(observed_vars(graph)) - descendants(edges, v) - {v} - set(pa)
        for w in sorted(nondesc):
            if any(i["x"] == w and i["y"] == v and i["given"] == pa for i in out):
                continue
            out.append({"x": v, "y": w, "given": pa, "relation": "independent"})
    return out


def probe_covers(probe: dict[str, Any], impl: dict[str, Any]) -> bool:
    tests = probe.get("tests", {})
    if tests.get("kind") != "independence":
        return False
    pair = {tests.get("x"), tests.get("y")}
    return pair == {impl["x"], impl["y"]} and sorted(tests.get("given", [])) == sorted(
        impl["given"]
    )


def coverage_report(graph: dict[str, Any]) -> dict[str, Any]:
    probes = graph.get("probes", {})
    impls = testable_implications(graph)
    uncovered = [i for i in impls if not any(probe_covers(p, i) for p in probes.values())]

    tested_edges = {
        (p["tests"]["from"], p["tests"]["to"])
        for p in probes.values()
        if p.get("tests", {}).get("kind") == "edge"
    }
    untested_edges = [
        f"{a}->{b}" for a, b in edge_list(graph) if (a, b) not in tested_edges
    ]

    untested_assumptions = []
    for a in graph.get("assumed_absent", []):
        impl = {"x": a["from"], "y": a["to"], "given": sorted(parents_of(edge_list(graph), a["from"]))}
        if not any(probe_covers(p, impl) for p in probes.values()):
            untested_assumptions.append(f"{a['from']}->{a['to']}")

    return {
        "implications": impls,
        "uncovered_implications": uncovered,
        "untested_edges": untested_edges,
        "untested_assumptions": untested_assumptions,
    }


def pricing_conditioning_sets(
    ea: list[tuple[str, str]], eb: list[tuple[str, str]],
    shared: list[str], x: str, y: str,
) -> Iterable[list[str]]:
    """Conditioning sets to try when pricing a repair on the pair (x, y).

    Enumerating implications for *probes* is deliberately restricted to the
    local Markov basis, because a probe costs a week. Pricing a repair is a
    different question with a different budget: it asks what the repair newly
    predicts, and a statement missed here silently marks a falsifiable repair
    as accommodation-only, which forecloses `supported` for the whole line.
    Since the protocol caps a graph at one screen, enumerate every conditioning
    set at realistic sizes and fall back to parent sets only beyond that.
    """
    others = [v for v in shared if v not in (x, y)]
    if len(shared) <= MAX_EXHAUSTIVE_PRICING_VARS:
        for r in range(len(others) + 1):
            for combo in itertools.combinations(others, r):
                yield list(combo)
        return
    coarse = {frozenset()}
    for g in (ea, eb):
        for v in (x, y):
            coarse.add(frozenset(parents_of(g, v)) & set(shared))
    for given in coarse:
        yield sorted(given)


def differing_implications(
    graph_a: dict[str, Any], graph_b: dict[str, Any]
) -> list[dict[str, Any]]:
    """Pairwise statements whose truth value differs between two graphs.

    Used to price an amendment. An amendment that changes no other testable
    prediction has absorbed the anomaly without exposing itself to anything,
    which is curve fitting.

    Ordered by the size of the conditioning set, so the cheapest statement to
    test comes first: that is the one an amendment takes on as its debt.
    """
    ea, eb = edge_list(graph_a), edge_list(graph_b)
    shared = sorted(set(observed_vars(graph_a)) & set(observed_vars(graph_b)))
    out = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for x, y in itertools.combinations(shared, 2):
        for given in pricing_conditioning_sets(ea, eb, shared, x, y):
            g = sorted(given)
            if x in g or y in g:
                continue
            key = (x, y, tuple(g))
            if key in seen:
                continue
            seen.add(key)
            sa = d_separated(ea, x, y, g)
            sb = d_separated(eb, x, y, g)
            if sa != sb:
                out.append(
                    {"x": x, "y": y, "given": g, "before": "independent" if sa else "dependent",
                     "after": "independent" if sb else "dependent"}
                )
    out.sort(key=lambda d: (len(d["given"]), d["x"], d["y"], d["given"]))
    return out


# --------------------------------------------------------------------------
# probe frontier and resolution
# --------------------------------------------------------------------------

def parse_guard(expr: str) -> tuple[str, str]:
    if "==" not in expr:
        raise SystemExit(f"Malformed guard: {expr!r}. Expected e.g. 'P1==positive'.")
    left, right = expr.split("==", 1)
    return left.strip(), right.strip()


def outcomes_map(graph: dict[str, Any]) -> dict[str, str]:
    return {
        pid: p["outcome"]
        for pid, p in graph.get("probes", {}).items()
        if p.get("outcome")
    }


def fired_rules(graph: dict[str, Any]) -> list[dict[str, Any]]:
    obs = outcomes_map(graph)
    out = []
    for rule in graph.get("resolution", []):
        when = rule.get("when", {})
        if when and all(obs.get(k) == v for k, v in when.items()):
            out.append(rule)
    return out


def skipped_probes(graph: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for rule in fired_rules(graph):
        out |= set(rule.get("skip", []))
    return out


def frontier(graph: dict[str, Any]) -> list[str]:
    obs = outcomes_map(graph)
    skipped = skipped_probes(graph)
    ready = []
    for pid, probe in sorted(graph.get("probes", {}).items()):
        if pid in obs or pid in skipped:
            continue
        guards = [parse_guard(g) for g in probe.get("guards_in", [])]
        if all(obs.get(dep) == want for dep, want in guards):
            ready.append(pid)
    return ready


def resolution_conflicts(graph: dict[str, Any]) -> list[str]:
    problems = []
    rules = graph.get("resolution", [])
    for r1, r2 in itertools.combinations(rules, 2):
        w1, w2 = r1.get("when", {}), r2.get("when", {})
        shared = set(w1) & set(w2)
        if all(w1[k] == w2[k] for k in shared) and r1.get("then") != r2.get("then"):
            problems.append(
                f"resolution rules {w1} and {w2} can both fire but prescribe "
                f"{r1.get('then')!r} and {r2.get('then')!r}"
            )
    return problems


def propose_decision(graph: dict[str, Any]) -> dict[str, Any]:
    fired = fired_rules(graph)
    debts = unpaid_debts(graph)
    if not fired:
        return {"status": "open", "reason": "no resolution rule is fully matched yet",
                "frontier": frontier(graph)}
    rule = max(fired, key=lambda r: len(r.get("when", {})))
    result = {"status": "determined", "then": rule.get("then"), "rule": rule}
    if rule.get("rung"):
        result["rung"] = rule["rung"]
    if rule.get("depends_on_assumption"):
        result["depends_on_assumption"] = rule["depends_on_assumption"]
    if debts and rule.get("then") == "supported":
        result["blocked_by_debt"] = debts
    return result


# --------------------------------------------------------------------------
# abduction
# --------------------------------------------------------------------------

def clone(graph: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(graph))


def abduce(
    graph: dict[str, Any], x: str, y: str, given: list[str], anomaly: str
) -> list[dict[str, Any]]:
    """Enumerate structural repairs that restore compatibility with an anomaly.

    anomaly='dependency': the data show x and y dependent given `given`, but
    the graph implies independence. anomaly='independence': the reverse.
    """
    edges = edge_list(graph)
    candidates: list[dict[str, Any]] = []

    def accepts(g2: dict[str, Any]) -> bool:
        sep = d_separated(edge_list(g2), x, y, given)
        return (not sep) if anomaly == "dependency" else sep

    if anomaly == "dependency":
        for a, b in ((x, y), (y, x)):
            g2 = clone(graph)
            g2["edges"].append({"from": a, "to": b, "from_theory": []})
            if find_cycle(edge_list(g2)) is None and accepts(g2):
                candidates.append(
                    {"action": "add_edge", "detail": f"{a}->{b}", "new_latent": 0,
                     "patch": {"add_edges": [[a, b]]}}
                )

        g2 = clone(graph)
        latent = f"U_{x}{y}"
        g2["variables"][latent] = {"name": f"latent common cause of {x} and {y}",
                                   "role": "latent", "observed": False}
        g2["edges"] += [{"from": latent, "to": x, "from_theory": []},
                        {"from": latent, "to": y, "from_theory": []}]
        if accepts(g2):
            candidates.append(
                {"action": "add_latent_confounder", "detail": f"{latent}->{x}, {latent}->{y}",
                 "new_latent": 1, "patch": {"add_latent": [latent, x, y]}}
            )

        for a in graph.get("assumed_absent", []):
            g2 = clone(graph)
            g2["assumed_absent"] = [
                s for s in g2["assumed_absent"]
                if not (s["from"] == a["from"] and s["to"] == a["to"])
            ]
            g2["edges"].append({"from": a["from"], "to": a["to"], "from_theory": []})
            if find_cycle(edge_list(g2)) is None and accepts(g2):
                candidates.append(
                    {"action": "retract_assumed_absent",
                     "detail": f"{a['from']}->{a['to']}", "new_latent": 0,
                     "patch": {"retract_absent": [a["from"], a["to"]]}}
                )

        for a, b in edges:
            g2 = clone(graph)
            g2["edges"] = [e for e in g2["edges"] if not (e["from"] == a and e["to"] == b)]
            g2["edges"].append({"from": b, "to": a, "from_theory": []})
            if find_cycle(edge_list(g2)) is None and accepts(g2):
                candidates.append(
                    {"action": "reverse_edge", "detail": f"{a}->{b} becomes {b}->{a}",
                     "new_latent": 0, "patch": {"reverse_edge": [a, b]}}
                )
    else:
        for a, b in edges:
            g2 = clone(graph)
            g2["edges"] = [e for e in g2["edges"] if not (e["from"] == a and e["to"] == b)]
            if accepts(g2):
                candidates.append(
                    {"action": "delete_edge", "detail": f"{a}->{b}", "new_latent": 0,
                     "patch": {"delete_edge": [a, b]}}
                )

    # Price each repair by what it exposes *beyond* the anomaly it was invented
    # to absorb. A repair whose only new prediction is that anomaly has bought
    # compatibility without taking on any risk.
    for c in candidates:
        g2 = apply_patch(clone(graph), c["patch"])
        diff = differing_implications(graph, g2)
        c["exposes"] = [
            d for d in diff
            if not ({d["x"], d["y"]} == {x, y} and sorted(d["given"]) == sorted(given))
        ][:6]
        c["accommodation_only"] = not c["exposes"]

    # Rank by risk taken, then by Occam: repairs that expose new predictions
    # first, then those introducing fewest unobservable variables.
    candidates.sort(key=lambda c: (c["accommodation_only"], c["new_latent"], c["action"]))
    for i, c in enumerate(candidates, 1):
        c["id"] = f"A{i}"
    return candidates


def apply_patch(graph: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    for a, b in patch.get("add_edges", []):
        graph["edges"].append({"from": a, "to": b, "from_theory": []})
    if "add_latent" in patch:
        latent, x, y = patch["add_latent"]
        graph["variables"][latent] = {"name": f"latent common cause of {x} and {y}",
                                      "role": "latent", "observed": False}
        graph["edges"] += [{"from": latent, "to": x, "from_theory": []},
                           {"from": latent, "to": y, "from_theory": []}]
    if "retract_absent" in patch:
        a, b = patch["retract_absent"]
        graph["assumed_absent"] = [
            s for s in graph.get("assumed_absent", [])
            if not (s["from"] == a and s["to"] == b)
        ]
        graph["edges"].append({"from": a, "to": b, "from_theory": []})
    if "reverse_edge" in patch:
        a, b = patch["reverse_edge"]
        graph["edges"] = [e for e in graph["edges"] if not (e["from"] == a and e["to"] == b)]
        graph["edges"].append({"from": b, "to": a, "from_theory": []})
    if "delete_edge" in patch:
        a, b = patch["delete_edge"]
        graph["edges"] = [e for e in graph["edges"] if not (e["from"] == a and e["to"] == b)]
    return graph


def unpaid_debts(graph: dict[str, Any]) -> list[dict[str, Any]]:
    return [a for a in graph.get("amendments", []) if not a.get("cleared_by")]


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

def validate(graph: dict[str, Any]) -> tuple[list[str], list[str]]:
    blocks: list[str] = []
    warnings: list[str] = []

    if graph.get("schema_version") != SCHEMA_VERSION:
        warnings.append(f"schema_version is not {SCHEMA_VERSION}")
    if graph.get("graph_type") not in GRAPH_TYPES:
        blocks.append(f"graph_type must be one of {GRAPH_TYPES}")

    edges = edge_list(graph)
    variables = graph.get("variables", {})
    for a, b in edges:
        for n in (a, b):
            if n not in variables:
                blocks.append(f"edge {a}->{b} references undeclared variable {n}")

    cycle = find_cycle(edges)
    if cycle:
        blocks.append(f"causal graph contains a cycle: {' -> '.join(cycle)}")

    probes = graph.get("probes", {})
    guard_edges: list[tuple[str, str]] = []
    for pid, probe in probes.items():
        for g in probe.get("guards_in", []):
            dep, want = parse_guard(g)
            if dep not in probes:
                blocks.append(f"probe {pid} guards on undefined probe {dep}")
                continue
            if want not in OUTCOMES:
                blocks.append(f"probe {pid} guards on unknown outcome {want!r}")
            guard_edges.append((dep, pid))
    gcycle = find_cycle(guard_edges)
    if gcycle:
        blocks.append(f"probe dependencies contain a cycle: {' -> '.join(gcycle)}")

    max_probes = graph.get("max_probes")
    if isinstance(max_probes, int) and len(probes) > max_probes:
        blocks.append(
            f"{len(probes)} probes exceeds max_probes={max_probes}; narrow the claim "
            f"instead of adding evidence lines"
        )

    blocks.extend(resolution_conflicts(graph))

    for rule in graph.get("resolution", []):
        for pid in list(rule.get("when", {})) + list(rule.get("skip", [])):
            if pid not in probes:
                blocks.append(f"resolution rule references undefined probe {pid}")

    if graph.get("graph_type") == "causal":
        for pid, probe in sorted(probes.items()):
            tests = probe.get("tests", {})
            if tests.get("kind") != "edge":
                continue
            x, y = tests.get("from"), tests.get("to")
            ok, why = verify_adjustment(edges, x, y, probe.get("controls", []))
            if not ok:
                blocks.append(f"probe {pid} controls are not a valid adjustment set: {why}")
            rec, latent = recommend_adjustment(graph, x, y)
            if latent:
                warnings.append(
                    f"probe {pid}: {x} has unobserved parent(s) {latent}; the effect may "
                    f"not be identifiable by adjustment"
                )
            if sorted(probe.get("controls", [])) != sorted(rec) and ok:
                warnings.append(
                    f"probe {pid} controls {sorted(probe.get('controls', []))} differ from "
                    f"parents-of-treatment {rec} but remain valid"
                )

    cov = coverage_report(graph)
    if cov["untested_assumptions"]:
        warnings.append(
            f"load-bearing absence assumptions never tested: {cov['untested_assumptions']}"
        )
    if cov["untested_edges"]:
        warnings.append(f"asserted edges with no probe: {cov['untested_edges']}")

    max_amend = graph.get("max_amendments")
    amendments = graph.get("amendments", [])
    if isinstance(max_amend, int) and len(amendments) > max_amend:
        blocks.append(
            f"{len(amendments)} amendments exceed max_amendments={max_amend}; "
            f"take a claim-lowering decision instead of repairing the graph again"
        )

    for m_id, node in graph.get("theory", {}).items():
        if node.get("retired_at"):
            continue
        if node.get("provenance") not in PROVENANCE:
            blocks.append(f"theory node {m_id} has invalid provenance")
        cited = {t for e in graph.get("edges", []) for t in e.get("from_theory", [])}
        if m_id not in cited and not node.get("entails"):
            warnings.append(f"theory node {m_id} entails nothing in the observation layer")

    return blocks, warnings


# --------------------------------------------------------------------------
# candidate sets and selection records
# --------------------------------------------------------------------------

def emit_candidates(kind: str, graph: dict[str, Any], items: list[dict[str, Any]],
                    context: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "kind": kind,
        "generated_at": now_iso(),
        "graph_design_hash": design_hash(graph),
        "context": context,
        "candidates": items,
        "ranking_instruction": (
            "Rank by expected falsification value: which candidate, if it came back "
            "negative, would do the most damage to the claim, per unit cost. Do not "
            "rank by likelihood of a positive result."
        ),
    }
    blob = json.dumps(payload["candidates"], sort_keys=True, separators=(",", ":"))
    payload["candidate_set_hash"] = hashlib.sha256(blob.encode()).hexdigest()[:16]
    CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATES_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    return payload


def ingest_selection(path: Path) -> dict[str, Any]:
    sel = json.loads(path.read_text())
    if not CANDIDATES_PATH.exists():
        raise SystemExit("No candidate set on disk. Run deduce or abduce first.")
    cands = json.loads(CANDIDATES_PATH.read_text())
    if sel.get("candidate_set_hash") != cands["candidate_set_hash"]:
        raise SystemExit(
            "BLOCKED: selection refers to a different candidate set "
            f"({sel.get('candidate_set_hash')} vs {cands['candidate_set_hash']}). "
            "Re-run the generator and rank again."
        )
    ids = {c.get("id") for c in cands["candidates"]}
    chosen = set(sel.get("selected", []))
    rejected = {r["id"]: r.get("reason", "") for r in sel.get("rejected", [])}
    unknown = (chosen | set(rejected)) - ids
    if unknown:
        raise SystemExit(f"BLOCKED: selection references unknown candidates {sorted(unknown)}")
    unaccounted = ids - chosen - set(rejected)
    if unaccounted:
        raise SystemExit(
            f"BLOCKED: candidates {sorted(unaccounted)} are neither selected nor rejected. "
            "A silent filter is not a decision; every rejection needs a reason."
        )
    missing = [k for k, v in rejected.items() if not v.strip()]
    if missing:
        raise SystemExit(f"BLOCKED: rejections without a reason: {missing}")
    return {"selection": sel, "candidates": cands}


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

SKELETON = {
    "schema_version": SCHEMA_VERSION,
    "claim_id": "SC-001",
    "claim": "",
    "graph_type": "causal",
    "max_probes": 5,
    "max_amendments": 2,
    "theory": {},
    "variables": {},
    "edges": [],
    "assumed_absent": [],
    "probes": {},
    "resolution": [],
    "amendments": [],
}


def cmd_init(args: argparse.Namespace) -> int:
    if GRAPH_PATH.exists() and not args.force:
        raise SystemExit(f"{GRAPH_PATH} already exists. Use --force to overwrite.")
    graph = dict(SKELETON)
    graph["claim_id"] = args.claim_id
    graph["claim"] = args.claim
    graph["graph_type"] = args.type
    save_graph(graph)
    append_state_event("graph_init", {"claim_id": args.claim_id, "claim": args.claim,
                                      "graph_type": args.type, "force": args.force})
    print(f"Claim graph created: {GRAPH_PATH}")
    print(f"Design hash: {design_hash(graph)}")
    print("Next: author the design with add-variable / add-edge / add-absent / "
          "add-probe / add-resolution, then run validate.")
    return 0


# --------------------------------------------------------------------------
# authoring: build the design from the CLI (each add validates before saving)
# --------------------------------------------------------------------------

RESOLUTION_THENS = ("supported", "falsified", "narrow", "terminated")
PROBE_KINDS = ("edge", "independence", "comparison")


def require_variable(graph: dict[str, Any], vid: str) -> None:
    if vid not in graph.get("variables", {}):
        raise SystemExit(
            f"BLOCKED: {vid} is not a declared variable. Add it first: "
            f"claim_graph.py add-variable --id {vid} --name '<name>' --role '<role>'"
        )


def commit(graph: dict[str, Any], event: str, detail: dict[str, Any], message: str) -> None:
    """Validate, save and log; a change that would leave an invalid graph is refused."""
    blocks, _ = validate(graph)
    if blocks:
        raise SystemExit(
            "BLOCKED: this change would leave the graph invalid:\n  " + "\n  ".join(blocks)
        )
    save_graph(graph)
    append_state_event(event, detail)
    print(f"{message} New design hash: {design_hash(graph)}")


def cmd_add_variable(args: argparse.Namespace) -> int:
    graph = load_graph()
    if args.id in graph.get("variables", {}):
        raise SystemExit(f"BLOCKED: variable {args.id} already exists.")
    observed = not args.latent
    graph.setdefault("variables", {})[args.id] = {
        "name": args.name, "role": args.role, "observed": observed,
    }
    commit(graph, "graph_variable_added", {"id": args.id, "role": args.role,
                                           "observed": observed},
           f"Variable {args.id} added.")
    return 0


def cmd_add_edge(args: argparse.Namespace) -> int:
    graph = load_graph()
    require_variable(graph, args.from_)
    require_variable(graph, args.to)
    if args.from_ == args.to:
        raise SystemExit("BLOCKED: self-loops are not allowed.")
    for e in graph.get("edges", []):
        if e["from"] == args.from_ and e["to"] == args.to:
            raise SystemExit(f"BLOCKED: edge {args.from_}->{args.to} already exists.")
    for a in graph.get("assumed_absent", []):
        if a["from"] == args.from_ and a["to"] == args.to:
            raise SystemExit(
                f"BLOCKED: {args.from_}->{args.to} is listed as assumed absent; "
                f"remove that assumption before asserting the edge."
            )
    entry = {"from": args.from_, "to": args.to, "from_theory": []}
    if args.theory:
        if args.theory not in graph.get("theory", {}):
            raise SystemExit(
                f"BLOCKED: no theory node {args.theory}. Create one with claim_graph.py "
                f"induce, or omit --theory."
            )
        entry["from_theory"] = [args.theory]
    graph.setdefault("edges", []).append(entry)
    commit(graph, "graph_edge_added", {"from": args.from_, "to": args.to},
           f"Edge {args.from_}->{args.to} added.")
    return 0


def cmd_add_absent(args: argparse.Namespace) -> int:
    graph = load_graph()
    require_variable(graph, args.from_)
    require_variable(graph, args.to)
    for e in graph.get("edges", []):
        if e["from"] == args.from_ and e["to"] == args.to:
            raise SystemExit(
                f"BLOCKED: {args.from_}->{args.to} is already an asserted edge; "
                f"an edge cannot also be assumed absent."
            )
    graph.setdefault("assumed_absent", []).append(
        {"from": args.from_, "to": args.to, "justification": args.justification}
    )
    commit(graph, "graph_absent_added", {"from": args.from_, "to": args.to},
           f"Absence assumption {args.from_}->{args.to} recorded.")
    return 0


def cmd_add_probe(args: argparse.Namespace) -> int:
    graph = load_graph()
    if args.id in graph.get("probes", {}):
        raise SystemExit(f"BLOCKED: probe {args.id} already exists.")
    try:
        tests = json.loads(args.tests)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"BLOCKED: --tests is not valid JSON: {exc}") from exc
    if not isinstance(tests, dict) or tests.get("kind") not in PROBE_KINDS:
        raise SystemExit(f"BLOCKED: --tests.kind must be one of {PROBE_KINDS}.")
    kind = tests["kind"]
    if kind == "edge":
        require_variable(graph, tests.get("from", ""))
        require_variable(graph, tests.get("to", ""))
    elif kind == "independence":
        require_variable(graph, tests.get("x", ""))
        require_variable(graph, tests.get("y", ""))
        for g in tests.get("given", []):
            require_variable(graph, g)
    else:  # comparison
        for n in ("stronger", "weaker", "on"):
            require_variable(graph, tests.get(n, ""))
    guards = [g.strip() for g in args.guards.split(",") if g.strip()]
    for g in guards:
        dep, _ = parse_guard(g)  # raises on malformed
        if dep not in graph.get("probes", {}):
            raise SystemExit(f"BLOCKED: guard {g!r} references undefined probe {dep}.")
    controls = [c.strip() for c in args.controls.split(",") if c.strip()]
    graph.setdefault("probes", {})[args.id] = {
        "tests": tests,
        "metric": args.metric,
        "prereg": args.prereg,
        "controls": controls,
        "guards_in": guards,
    }
    commit(graph, "graph_probe_added", {"id": args.id, "kind": kind, "guards_in": guards},
           f"Probe {args.id} added.")
    return 0


def cmd_add_resolution(args: argparse.Namespace) -> int:
    graph = load_graph()
    try:
        when = json.loads(args.when)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"BLOCKED: --when is not valid JSON: {exc}") from exc
    if not isinstance(when, dict) or not when:
        raise SystemExit(
            "BLOCKED: --when must be a non-empty JSON object, "
            "e.g. '{\"P1\": \"positive\"}'."
        )
    for pid, want in when.items():
        if pid not in graph.get("probes", {}):
            raise SystemExit(f"BLOCKED: resolution rule references undefined probe {pid}.")
        if want not in OUTCOMES:
            raise SystemExit(
                f"BLOCKED: resolution rule wants unknown outcome {want!r} for {pid}; "
                f"one of {OUTCOMES}."
            )
    rule: dict[str, Any] = {"when": when, "then": args.then}
    if args.rung:
        rule["rung"] = args.rung
    if args.depends_on:
        rule["depends_on_assumption"] = args.depends_on
    skip = [s.strip() for s in args.skip.split(",") if s.strip()]
    for pid in skip:
        if pid not in graph.get("probes", {}):
            raise SystemExit(f"BLOCKED: --skip references undefined probe {pid}.")
    if skip:
        rule["skip"] = skip
    if args.note:
        rule["note"] = args.note
    graph.setdefault("resolution", []).append(rule)
    commit(graph, "graph_resolution_added", {"when": when, "then": args.then},
           f"Resolution rule {when} -> {args.then} added.")
    return 0


def cmd_validate(_: argparse.Namespace) -> int:
    graph = load_graph()
    blocks, warnings = validate(graph)
    print("CLAIM GRAPH VALIDATION")
    print(f"Claim: {graph.get('claim')}")
    print(f"Design hash: {design_hash(graph)}")
    for m in warnings:
        print(f"WARNING: {m}")
    for m in blocks:
        print(f"BLOCK: {m}")
    if blocks:
        return 2
    print("PASS: graph is structurally sound.")
    return 0


def cmd_reasoning(_: argparse.Namespace) -> int:
    print("THREE REASONING MODES")
    for name, direction, command in REASONING_MODES:
        print(f"  {name.upper()}")
        print(f"    {direction}")
        print(f"    command: claim_graph.py {command}")
    return 0


def cmd_deduce(args: argparse.Namespace) -> int:
    graph = load_graph()
    cov = coverage_report(graph)
    if args.json:
        print(json.dumps(cov, indent=2))
        return 0
    print("DEDUCTION: theory + observation DAG -> testable implications -> probes")
    print(f"Testable implications: {len(cov['implications'])}")
    for i in cov["implications"]:
        given = f" | {', '.join(i['given'])}" if i["given"] else ""
        print(f"  {i['x']} ⊥ {i['y']}{given}")
    print(f"\nUncovered by any probe: {len(cov['uncovered_implications'])}")
    for i in cov["uncovered_implications"]:
        given = f" | {', '.join(i['given'])}" if i["given"] else ""
        print(f"  {i['x']} ⊥ {i['y']}{given}")
    if cov["untested_assumptions"]:
        print(f"\nLoad-bearing assumptions never tested: {cov['untested_assumptions']}")
    if cov["untested_edges"]:
        print(f"Asserted edges with no probe: {cov['untested_edges']}")

    items = []
    for n, i in enumerate(cov["uncovered_implications"], 1):
        items.append({
            "id": f"D{n}", "kind": "independence_test",
            "detail": f"{i['x']} ⊥ {i['y']}" + (f" | {', '.join(i['given'])}" if i["given"] else ""),
            "x": i["x"], "y": i["y"], "given": i["given"],
        })
    if items:
        payload = emit_candidates("deduction", graph, items,
                                  {"claim": graph.get("claim")})
        print(f"\nCandidate set written to {CANDIDATES_PATH} "
              f"(hash {payload['candidate_set_hash']}).")
        print("Rank it, then record the choice with: claim_graph.py select --file selection.json")
    return 0


def cmd_frontier(_: argparse.Namespace) -> int:
    graph = load_graph()
    ready = frontier(graph)
    skipped = skipped_probes(graph)
    obs = outcomes_map(graph)
    print("PROBE FRONTIER")
    for pid, probe in sorted(graph.get("probes", {}).items()):
        if pid in obs:
            mark = f"resolved: {obs[pid]}"
        elif pid in skipped:
            mark = "skipped by a fired resolution rule"
        elif pid in ready:
            mark = "READY"
        else:
            mark = f"waiting on {probe.get('guards_in', [])}"
        print(f"  {pid}  {mark}")
    if not ready:
        print("\nNo probe is ready. Either the line is resolved, or a guard is unmet.")
    return 0


def cmd_abduce(args: argparse.Namespace) -> int:
    graph = load_graph()
    x, y = [s.strip() for s in args.between.split(",")]
    given = [s.strip() for s in args.given.split(",")] if args.given else []
    anomaly = "independence" if args.independence else "dependency"
    print("ABDUCTION: anomaly against the DAG -> candidate structural repairs")

    sep = d_separated(edge_list(graph), x, y, given)
    expected_conflict = sep if anomaly == "dependency" else not sep
    if not expected_conflict:
        print(f"No anomaly: the graph already implies "
              f"{'independence' if sep else 'dependence'} for {x},{y} given {given}.")
        print("Abduction is for observations the current graph cannot accommodate.")
        return 0

    cands = abduce(graph, x, y, given, anomaly)
    if not cands:
        print("No single structural repair restores compatibility.")
        print("This usually means the anomaly is measurement-level, not structural. "
              "Classify it on the failure ladder before touching the graph.")
        return 0

    print(f"ANOMALY: {x} and {y} observed {anomaly} given {given or '∅'}")
    print(f"{len(cands)} structural repairs restore compatibility:\n")
    for c in cands:
        print(f"  {c['id']}  {c['action']}: {c['detail']}  "
              f"(new latent: {c['new_latent']})")
        if c["accommodation_only"]:
            print("        ACCOMMODATION ONLY — predicts nothing beyond the anomaly it "
                  "absorbs;\n        applying it forecloses closing this line as 'supported'.")
        for e in c["exposes"][:3]:
            given_s = f" | {', '.join(e['given'])}" if e["given"] else ""
            print(f"        newly predicts {e['x']},{e['y']}{given_s} {e['after']}")
    payload = emit_candidates("abduction", graph, cands,
                              {"anomaly": anomaly, "x": x, "y": y, "given": given})
    print(f"\nCandidate set written to {CANDIDATES_PATH} "
          f"(hash {payload['candidate_set_hash']}).")
    print("Repairs are hypotheses. Send them to add-idea; apply one only at a "
          "sprint boundary via: claim_graph.py amend --apply <id>")
    return 0


def cmd_amend(args: argparse.Namespace) -> int:
    graph = load_graph()
    cands = json.loads(CANDIDATES_PATH.read_text())
    match = [c for c in cands["candidates"] if c.get("id") == args.apply]
    if not match:
        raise SystemExit(f"Candidate {args.apply} not in the current candidate set.")
    cand = match[0]
    before = clone(graph)
    graph = apply_patch(graph, cand["patch"])
    owed = cand.get("exposes") or []
    graph.setdefault("amendments", []).append({
        "id": f"AM-{len(graph.get('amendments', [])) + 1:02d}",
        "applied_at": now_iso(),
        "action": cand["action"],
        "detail": cand["detail"],
        "motivating_anomaly": cands.get("context"),
        "accommodation_only": cand.get("accommodation_only", False),
        "owed_implication": owed[0] if owed else None,
        "cleared_by": None,
    })
    blocks, _ = validate(graph)
    if blocks:
        raise SystemExit("BLOCKED: the amended graph fails validation:\n  " + "\n  ".join(blocks))
    save_graph(graph)
    append_state_event("amendment_applied", {"id": cand["action"], "detail": cand["detail"],
                                             "motivating_anomaly": cands.get("context")})
    print(f"Amendment applied: {cand['action']} {cand['detail']}")
    print(f"New design hash: {design_hash(graph)}")
    if owed:
        o = owed[0]
        given = f" | {', '.join(o['given'])}" if o["given"] else ""
        print(f"DEBT: this line cannot close as 'supported' until a probe tests "
              f"{o['x']},{o['y']}{given} (now predicted {o['after']}).")
    else:
        print("DEBT: accommodation only. This repair makes no prediction beyond the "
              "anomaly it absorbs, so no probe can discharge it. The line can no longer "
              "close as 'supported'; take a claim-lowering decision instead.")
    return 0


def cmd_induce(args: argparse.Namespace) -> int:
    graph = load_graph()
    print("INDUCTION: independent closed probes -> a theory node with a new prediction")
    support = [s.strip() for s in args.support.split(",") if s.strip()]
    if len(support) < MIN_SUPPORT_FOR_INDUCTION:
        raise SystemExit(
            f"BLOCKED: {len(support)} supporting result(s); at least "
            f"{MIN_SUPPORT_FOR_INDUCTION} independent closed probes are required."
        )
    entails = [s.strip() for s in args.entails.split(",") if s.strip()]
    if not entails:
        raise SystemExit("BLOCKED: a generalisation must entail at least one graph element.")

    probes = graph.get("probes", {})
    covered = []
    for e in entails:
        if "->" in e:
            a, b = e.split("->")
            if any(p.get("tests", {}).get("kind") == "edge"
                   and p["tests"].get("from") == a.strip()
                   and p["tests"].get("to") == b.strip() for p in probes.values()):
                covered.append(e)
    if len(covered) == len(entails):
        raise SystemExit(
            "BLOCKED: every entailment is already covered by an existing probe. This is a "
            "summary of the evidence, not a generalisation of it. A theory node must "
            "predict something not yet tested."
        )
    graph.setdefault("theory", {})[args.id] = {
        "statement": args.statement,
        "provenance": "induced",
        "supported_by": support,
        "entails": entails,
        "added_at": now_iso(),
    }
    save_graph(graph)
    append_state_event("theory_node_added", {"id": args.id, "provenance": "induced",
                                             "supported_by": support, "entails": entails})
    print(f"Theory node {args.id} added (induced from {support}).")
    print(f"Untested entailments carried forward: {[e for e in entails if e not in covered]}")
    print(f"New design hash: {design_hash(graph)}")
    return 0


def cmd_retire(args: argparse.Namespace) -> int:
    graph = load_graph()
    node = graph.get("theory", {}).get(args.id)
    if not node:
        raise SystemExit(f"No theory node {args.id}.")
    node["retired_at"] = now_iso()
    node["retired_reason"] = args.reason
    save_graph(graph)
    append_state_event("theory_node_retired", {"id": args.id, "reason": args.reason})
    print(f"Theory node {args.id} retired. Reason recorded.")
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    graph = load_graph()
    probe = graph.get("probes", {}).get(args.probe)
    if not probe:
        raise SystemExit(f"No probe {args.probe} in the graph.")
    if args.probe not in frontier(graph) and not args.force:
        raise SystemExit(
            f"BLOCKED: {args.probe} is not on the ready frontier "
            f"(ready: {frontier(graph) or 'none'}). Its guards are unmet, so its result "
            f"cannot be interpreted. Use --force only to record a superseded run."
        )
    probe["outcome"] = args.outcome
    probe["experiment_id"] = args.experiment
    probe["resolved_at"] = now_iso()
    if args.defect:
        probe["defect"] = args.defect
    save_graph(graph)
    append_state_event("probe_resolved", {"probe": args.probe, "outcome": args.outcome,
                                          "experiment": args.experiment, "defect": args.defect})
    print(f"{args.probe} resolved: {args.outcome}"
          + (f" (defect: {args.defect})" if args.defect else ""))

    for a in graph.get("amendments", []):
        owed = a.get("owed_implication")
        if owed and not a.get("cleared_by") and probe_covers(probe, owed):
            a["cleared_by"] = args.probe
            save_graph(graph)
            print(f"Amendment {a['id']} debt cleared by {args.probe}.")

    proposal = propose_decision(graph)
    if proposal["status"] == "determined":
        print(f"\nResolution map now determines: {proposal['then']}")
        if proposal.get("rung"):
            print(f"Claim-lowering rung: {proposal['rung']}")
        if proposal.get("blocked_by_debt"):
            print("BLOCKED from closing positive: unpaid amendment debt remains.")
    else:
        print(f"\nStill open. Ready next: {proposal['frontier'] or 'none'}")
    return 0


def cmd_propose(_: argparse.Namespace) -> int:
    graph = load_graph()
    proposal = propose_decision(graph)
    print(json.dumps(proposal, indent=2))
    return 0 if proposal["status"] == "determined" else 1


def cmd_select(args: argparse.Namespace) -> int:
    result = ingest_selection(Path(args.file))
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    path = LOG_DIR / f"selection-{stamp}.json"
    path.write_text(json.dumps({
        "recorded_at": now_iso(),
        "kind": result["candidates"]["kind"],
        "candidate_set_hash": result["candidates"]["candidate_set_hash"],
        "graph_design_hash": result["candidates"]["graph_design_hash"],
        **result["selection"],
    }, indent=2) + "\n")
    append_state_event("selection_recorded", {
        "kind": result["candidates"]["kind"],
        "candidate_set_hash": result["candidates"]["candidate_set_hash"],
        "selected": result["selection"].get("selected"),
    })
    print(f"Selection recorded: {path}")
    print(f"Selected: {result['selection'].get('selected')}")
    print("Rejected candidates and their reasons are now part of the audit trail.")
    return 0


def cmd_hash(_: argparse.Namespace) -> int:
    print(design_hash(load_graph()))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Claim graph for the Research Closure Harness")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("init", help="create an empty claim graph")
    sp.add_argument("--claim-id", default="SC-001")
    sp.add_argument("--claim", required=True)
    sp.add_argument("--type", choices=GRAPH_TYPES, default="causal")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("add-variable", help="declare a variable in the observation layer")
    sp.add_argument("--id", required=True)
    sp.add_argument("--name", required=True)
    sp.add_argument("--role", required=True,
                    help="e.g. intervention / outcome / candidate_predictor / latent")
    sp.add_argument("--latent", action="store_true", help="unobserved variable")
    sp.set_defaults(func=cmd_add_variable)

    sp = sub.add_parser("add-edge", help="assert a directed edge between variables")
    sp.add_argument("--from", dest="from_", required=True)
    sp.add_argument("--to", required=True)
    sp.add_argument("--theory", default="", help="theory node that entails this edge")
    sp.set_defaults(func=cmd_add_edge)

    sp = sub.add_parser("add-absent", help="record an assumed-absent edge")
    sp.add_argument("--from", dest="from_", required=True)
    sp.add_argument("--to", required=True)
    sp.add_argument("--justification", required=True)
    sp.set_defaults(func=cmd_add_absent)

    sp = sub.add_parser("add-probe", help="bind a probe (experiment target) to the graph")
    sp.add_argument("--id", required=True)
    sp.add_argument("--tests", required=True,
                    help='JSON, e.g. {"kind":"edge","from":"K","to":"R"} or '
                         '{"kind":"independence","x":"L","y":"R","given":["E"]}')
    sp.add_argument("--metric", required=True)
    sp.add_argument("--prereg", required=True)
    sp.add_argument("--controls", default="", help="comma-separated adjustment set")
    sp.add_argument("--guards", default="", help="comma-separated, e.g. P1==positive")
    sp.set_defaults(func=cmd_add_probe)

    sp = sub.add_parser("add-resolution", help="add a resolution-map rule")
    sp.add_argument("--when", required=True,
                    help='JSON, e.g. {"P1":"positive","P2":"negative"}')
    sp.add_argument("--then", required=True, choices=RESOLUTION_THENS)
    sp.add_argument("--rung", default="", help="claim-lowering rung, e.g. causal->predictive")
    sp.add_argument("--depends-on", default="", help="assumption this rule rests on")
    sp.add_argument("--skip", default="", help="comma-separated probe ids to skip when this fires")
    sp.add_argument("--note", default="")
    sp.set_defaults(func=cmd_add_resolution)

    sp = sub.add_parser("validate", help="run all structural checks")
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser(
        "reasoning", help="show the named Deduction, Induction, and Abduction modes"
    )
    sp.set_defaults(func=cmd_reasoning)

    sp = sub.add_parser(
        "deduce", help="Deduction: enumerate implications and coverage gaps"
    )
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_deduce)

    sp = sub.add_parser("frontier", help="show which probes are ready to run")
    sp.set_defaults(func=cmd_frontier)

    sp = sub.add_parser(
        "abduce", help="Abduction: enumerate structural repairs for an anomaly"
    )
    sp.add_argument("--between", required=True, metavar="X,Y")
    sp.add_argument("--given", default="")
    sp.add_argument("--independence", action="store_true",
                    help="anomaly is an unexpected independence rather than a dependency")
    sp.set_defaults(func=cmd_abduce)

    sp = sub.add_parser("amend", help="apply a selected repair and record its debt")
    sp.add_argument("--apply", required=True, metavar="CANDIDATE_ID")
    sp.set_defaults(func=cmd_amend)

    sp = sub.add_parser(
        "induce", help="Induction: promote closed results into a theory node"
    )
    sp.add_argument("--id", required=True)
    sp.add_argument("--statement", required=True)
    sp.add_argument("--support", required=True, help="comma-separated closed experiment ids")
    sp.add_argument("--entails", required=True, help="comma-separated edges or CI statements")
    sp.set_defaults(func=cmd_induce)

    sp = sub.add_parser("retire", help="retire a theory node")
    sp.add_argument("--id", required=True)
    sp.add_argument("--reason", required=True)
    sp.set_defaults(func=cmd_retire)

    sp = sub.add_parser("resolve", help="record a probe outcome")
    sp.add_argument("--probe", required=True)
    sp.add_argument("--outcome", choices=OUTCOMES, required=True)
    sp.add_argument("--experiment")
    sp.add_argument("--defect",
                    choices=["implementation", "measurement", "design", "hypothesis"])
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_resolve)

    sp = sub.add_parser("propose-decision", help="evaluate the resolution map")
    sp.set_defaults(func=cmd_propose)

    sp = sub.add_parser("select", help="ingest a ranking decision over a candidate set")
    sp.add_argument("--file", required=True)
    sp.set_defaults(func=cmd_select)

    sp = sub.add_parser("hash", help="print the design hash")
    sp.set_defaults(func=cmd_hash)
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
