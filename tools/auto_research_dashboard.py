#!/usr/bin/env python3
"""Render the auto-research self-graph and its epoch history as HTML.

This is the auto-research analogue of `research_closure.py dashboard`: a
self-contained, offline HTML page with a layered DAG, a history scrubber,
node inspection, and the proposal/verification record for every epoch.
It reads only `.research/auto_research.json` and `.research/auto_snapshots/`;
it never mutates harness state.
"""
from __future__ import annotations

import argparse
import json
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path.cwd().resolve()
STATE_PATH = ROOT / ".research" / "auto_research.json"
SNAP_DIR = ROOT / ".research" / "auto_snapshots"
DEFAULT_OUT = ROOT / ".research" / "auto_research_dashboard.html"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        raise SystemExit(f"No auto-research state at {STATE_PATH}")
    return json.loads(STATE_PATH.read_text())


def snapshot_label(path: Path) -> str:
    # timestamp_sequence_label.json; label may contain underscores.
    return "_".join(path.stem.split("_")[3:])


def compact_state(st: dict[str, Any], order: list[str]) -> dict[str, Any]:
    nodes = st.get("nodes", {})
    ranked = {nid: i for i, nid in enumerate(order)}
    present = [nid for nid in order if nid in nodes]
    present += [nid for nid in nodes if nid not in ranked]
    compact_nodes = [
        [nid, nodes[nid].get("status"), round(float(nodes[nid].get("trust", 0.0)), 4)]
        for nid in present
    ]
    edges = [[e.get("from"), e.get("to"), e.get("kind")] for e in st.get("edges", [])]
    counts = {
        "nodes": len(compact_nodes),
        "edges": len(edges),
        "validated": sum(1 for n in nodes.values() if n.get("status") == "validated"),
        "draft": sum(1 for n in nodes.values() if n.get("status") == "draft"),
        "deprecated": sum(1 for n in nodes.values() if n.get("status") == "deprecated"),
        "dependency": sum(1 for e in st.get("edges", []) if e.get("kind") == "dependency"),
        "modification": sum(1 for e in st.get("edges", []) if e.get("kind") == "modification"),
    }
    params = {k: st.get(k) for k in (
        "trust_decay", "affected_trust_decay", "revalidation_threshold",
        "self_test_command")}
    return {"nodes": compact_nodes, "edges": edges, "counts": counts, "params": params}


def build_data() -> dict[str, Any]:
    final = load_state()
    final_order = list(final.get("nodes", {}).keys())
    proposals = sorted(final.get("proposals", {}).values(), key=lambda p: p.get("id", ""))

    node_defs: dict[str, Any] = {}
    for nid, node in final.get("nodes", {}).items():
        node_defs[nid] = {
            "type": node.get("type"),
            "layer": node.get("layer"),
            "status": node.get("status"),
            "trust": round(float(node.get("trust", 0.0)), 4),
            "statement": node.get("statement", ""),
            "immutable": bool(node.get("immutable", False)),
            "proposal_id": node.get("proposal_id", ""),
        }

    prop_by_id = {p.get("id"): p for p in proposals}
    prop_rows = []
    for p in proposals:
        pid = p.get("id", "")
        epoch = int(pid.split("-")[-1]) if pid.startswith("P-") else 0
        ver = p.get("verification") or {}
        prop_rows.append({
            "id": pid,
            "epoch": epoch,
            "track": p.get("track"),
            "title": p.get("title"),
            "status": p.get("status"),
            "verification_exit": ver.get("exit_code"),
            "proposed_at": p.get("proposed_at"),
            "applied_at": p.get("applied_at"),
            "modification_node": p.get("modification_node"),
            "affected_nodes": p.get("affected_nodes") or [],
        })

    snapshots = sorted(SNAP_DIR.glob("*.json"))
    if not snapshots:
        raise SystemExit("No auto-research snapshots; run at least one pipeline command first")
    labels = [snapshot_label(p) for p in snapshots]
    node_added = [i for i, label in enumerate(labels) if label == "node_added"]
    # First four node_added snapshots are the bootstrap graph (A1, A2, I1, V1);
    # each following node_added starts one auto-research epoch.
    if len(node_added) < 5:
        raise SystemExit("Snapshot journal has no epoch node-additions")

    epoch_count = len(proposals)
    frames = []
    for epoch in range(1, epoch_count + 1):
        if epoch < epoch_count:
            next_epoch_add = node_added[4 + epoch]  # epoch+1 starts here
            frame_file = snapshots[next_epoch_add - 1]  # last completed epoch state
        else:
            frame_file = snapshots[-1]
        st = json.loads(frame_file.read_text())
        compact = compact_state(st, final_order)
        prop = prop_by_id.get(f"P-{epoch:03d}", {})
        title = prop.get("title") or f"epoch {epoch}"
        frames.append({
            "epoch": epoch,
            "label": f"Epoch {epoch:03d} · {title}",
            "title": title,
            "track": prop.get("track", "?"),
            "applied": sum(1 for v in st.get("proposals", {}).values() if v.get("status") == "applied"),
            "snapshot": frame_file.name,
            **compact,
        })

    last_self_test = final.get("last_self_test") or {}
    verifications = final.get("verifications", [])
    meta = {
        "root": ROOT.name,
        "nodes": len(final.get("nodes", {})),
        "edges": len(final.get("edges", [])),
        "proposals": len(proposals),
        "applied": sum(1 for p in proposals if p.get("status") == "applied"),
        "events": len(final.get("events", [])),
        "verifications": len(verifications),
        "hard_verifications": sum(1 for r in verifications if r.get("level") == "hard"),
        "self_tests": sum(1 for r in verifications if r.get("level") == "self_test"),
        "last_self_test": {
            "passed": last_self_test.get("passed"),
            "exit_code": last_self_test.get("exit_code"),
            "at": last_self_test.get("at"),
        },
        "params": {k: final.get(k) for k in (
            "trust_decay", "affected_trust_decay", "revalidation_threshold",
            "self_test_command")},
    }
    return {
        "generated_at": now_iso(),
        "meta": meta,
        "node_defs": node_defs,
        "proposals": prop_rows,
        "frames": frames,
    }


AUTO_DASH_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate"/>
<meta http-equiv="Pragma" content="no-cache"/>
<title>Auto-Research Epoch Dashboard</title>
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
#controls{position:sticky;top:0;z-index:10;display:flex;align-items:center;gap:10px;
padding:10px 16px;background:var(--panel);border-bottom:1px solid var(--line);flex-wrap:wrap}
#controls h1{font-size:15px;margin:0 8px 0 0}
#idx{font-size:12px;color:var(--muted);min-width:56px}
button{background:#082f49;color:var(--blue);border:1px solid #0c4a6e;border-radius:6px;
padding:4px 12px;font-size:12px;cursor:pointer}
button:hover{background:#0c4a6e}
button.active{background:#0c4a6e;color:#e0f2fe}
#slider{flex:1;min-width:180px;accent-color:var(--blue)}
#label{flex-basis:100%;font-size:12px;color:var(--muted);line-height:1.6}
main{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:14px;padding:14px 22px;align-items:start}
@media(max-width:1100px){main{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card+.card{margin-top:14px}
#canvas-wrap{position:relative;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:#0d1626;height:72vh;min-height:420px}
svg#dag{display:block;width:100%;height:100%;cursor:grab;touch-action:none}
svg#dag.dragging{cursor:grabbing}
#tooltip{position:absolute;display:none;max-width:420px;background:#0f172a;border:1px solid #334155;
border-radius:8px;padding:8px 10px;font-size:12px;line-height:1.6;white-space:pre-wrap;
pointer-events:none;z-index:20;box-shadow:0 8px 24px rgba(0,0,0,.5)}
#zoom-hint{position:absolute;top:8px;right:10px;font-size:11px;color:var(--muted);user-select:none}
#reset-view{position:absolute;top:8px;right:90px;font-size:11px;color:var(--blue);cursor:pointer;
border:1px solid #0c4a6e;border-radius:6px;padding:2px 8px;background:#082f49;user-select:none}
#node-detail{font-size:12px;line-height:1.7;min-height:150px;color:#cbd5e1}
#node-detail b{color:var(--text)}
#node-detail pre{margin:6px 0 0;background:#0d1626;border:1px solid var(--line);border-radius:6px;
padding:6px 8px;font-size:11px;overflow-x:auto;white-space:pre-wrap}
#legend{display:flex;flex-wrap:wrap;gap:12px;font-size:11px;color:var(--muted);margin-top:8px}
#legend span{display:inline-flex;align-items:center;gap:5px}
.sw{display:inline-block;width:12px;height:12px;border-radius:3px;border:1px solid #334155}
#summary-table{width:100%;border-collapse:collapse;font-size:11.5px}
#summary-table td{border-bottom:1px solid #1a2740;padding:4px 6px}
#summary-table td:first-child{color:var(--muted)}
#params{font-size:11.5px;line-height:1.8;color:#cbd5e1}
#proposal-table-wrap{max-height:52vh;overflow:auto;border:1px solid var(--line);border-radius:8px}
table.proposals{width:100%;border-collapse:collapse;font-size:11.5px}
table.proposals th{position:sticky;top:0;background:var(--panel);z-index:1}
table.proposals th,table.proposals td{text-align:left;padding:5px 8px;border-bottom:1px solid #1a2740;vertical-align:top}
table.proposals th{color:var(--muted);font-weight:600;text-transform:uppercase;font-size:10.5px;letter-spacing:.6px}
table.proposals tr:hover{background:#0d1626;cursor:pointer}
table.proposals tr.current{background:#082f49}
td.mono,code{font-family:ui-monospace,Consolas,monospace;font-size:11px}
.empty{color:var(--muted);font-size:12.5px;padding:26px;text-align:center}
footer{padding:10px 22px 18px;color:var(--muted);font-size:11.5px;line-height:1.7}
</style>
</head>
<body>
<header>
  <h1>Auto-Research Epoch Dashboard <span id="verdict-chip"></span></h1>
  <div class="meta" id="meta"></div>
</header>
<div id="controls">
  <h1>Epochs</h1>
  <span id="idx">-/-</span>
  <button id="first">&#9198; first</button>
  <button id="prev">&#9664; prev</button>
  <button id="play">&#9654; play</button>
  <button id="next">next &#9654;</button>
  <button id="latest">latest &#9197;</button>
  <input id="slider" type="range" min="0" max="0" value="0" step="1"/>
  <div id="label"></div>
</div>
<main>
  <div>
    <div class="card">
      <h2>Self-graph DAG</h2>
      <div id="canvas-wrap">
        <div id="zoom-hint">drag: pan · wheel: zoom</div>
        <div id="reset-view">reset view</div>
        <svg id="dag"></svg>
        <div id="tooltip"></div>
      </div>
      <div id="legend"></div>
    </div>
    <div class="card">
      <h2>Proposals and verification records</h2>
      <div id="proposal-table-wrap"><table class="proposals">
        <thead><tr><th>epoch</th><th>proposal</th><th>track</th><th>title</th><th>ver</th><th>applied as</th></tr></thead>
        <tbody id="proposal-tbody"></tbody>
      </table></div>
    </div>
  </div>
  <div>
    <div class="card">
      <h2>Node detail</h2>
      <div id="node-detail">Click a node to inspect its claim, status, trust, and adjacent edges.</div>
    </div>
    <div class="card">
      <h2>Epoch summary</h2>
      <table id="summary-table"></table>
    </div>
    <div class="card">
      <h2>Final parameters</h2>
      <div id="params"></div>
    </div>
  </div>
</main>
<footer>
  Generated <span id="gen-at"></span> · regenerate with <code>python3 tools/auto_research_dashboard.py</code>
  · frames are reconstructed from <code>.research/auto_snapshots/</code>, not inferred.
</footer>
<script>
const DATA = __AUTO_DATA__;
const DEFS = DATA.node_defs, FRAMES = DATA.frames, PROPS = DATA.proposals, META = DATA.meta;
const esc = (s) => { const d = document.createElement("div"); d.textContent = String(s == null ? "" : s); return d.innerHTML; };
const fmt = (s) => s == null ? "-" : s;
const ORDER = {};
Object.keys(DEFS).forEach((id, i) => { ORDER[id] = i; });
let current = 0, playTimer = null;

const TYPE_STYLE = {
  "assumption":    { fill: "#1e3a8a", stroke: "#3b82f6", label: "assumption" },
  "inference":     { fill: "#4c1d95", stroke: "#a78bfa", label: "inference" },
  "verify":        { fill: "#14532d", stroke: "#22c55e", label: "verify" },
  "modification":  { fill: "#451a03", stroke: "#f59e0b", label: "modification" }
};
const EDGE_STYLE = {
  "dependency":   { stroke: "#64748b", width: 1.6, dash: "", marker: "arr-dep" },
  "modification": { stroke: "#f59e0b", width: 1.6, dash: "5 3", marker: "arr-mod" }
};
const SVGNS = "http://www.w3.org/2000/svg";
const svg = document.getElementById("dag");

function svgEl(tag, attrs) {
  const el = document.createElementNS(SVGNS, tag);
  for (const k in attrs) el.setAttribute(k, attrs[k]);
  return el;
}
function clearNode(node) { while (node.firstChild) node.removeChild(node.firstChild); }

function nodeColor(nid, status) {
  if (nid === "M0") return { fill: "#78350f", stroke: "#fbbf24", label: "L0 M0" };
  const st = TYPE_STYLE[DEFS[nid] ? DEFS[nid].type : ""] || TYPE_STYLE.assumption;
  if (status === "deprecated") return { fill: "#450a0a", stroke: "#f87171", label: st.label + " · deprecated" };
  if (status === "draft") return { fill: "#111a2e", stroke: "#7dd3fc", label: st.label + " · draft" };
  return st;
}

function layout(frame) {
  const byLayer = {L0: [], L1: [], L2: [], L3: [], L4: []};
  for (const n of frame.nodes) {
    const def = DEFS[n[0]];
    if (!def) continue;
    (byLayer[def.layer] = byLayer[def.layer] || []).push(n);
  }
  const ROW_MAX = 48, COL_W = 236, ROW_H = 52, MARGIN = 48, GAP_X = 28;
  let x = MARGIN;
  const layerMeta = [];
  for (const layer of ["L0", "L1", "L2", "L3", "L4"]) {
    const nodes = (byLayer[layer] || []).slice().sort((a, b) => (ORDER[a[0]] - ORDER[b[0]]));
    if (!nodes.length) continue;
    const cols = Math.max(1, Math.ceil(nodes.length / ROW_MAX));
    layerMeta.push({ layer, nodes, x, cols, width: cols * COL_W });
    x += cols * COL_W + GAP_X;
  }
  const layoutNodes = [];
  for (const lm of layerMeta) {
    lm.nodes.forEach((n, i) => {
      const col = Math.floor(i / ROW_MAX), row = i % ROW_MAX;
      layoutNodes.push({
        id: n[0], status: n[1], trust: n[2],
        x: lm.x + col * COL_W, y: MARGIN + row * ROW_H,
        w: 204, h: 44, layer: lm.layer
      });
    });
  }
  const W = Math.max(1200, x + MARGIN);
  const H = Math.max(700, MARGIN * 2 + Math.min(ROW_MAX, Math.max.apply(null, layerMeta.map(l => l.nodes.length))) * ROW_H);
  const byId = {};
  layoutNodes.forEach(n => { byId[n.id] = n; });
  return { nodes: layoutNodes, byId, W, H };
}

let panState = null;
let docMove = null, docUp = null;
function renderGraph(frame) {
  clearNode(svg);
  const lay = layout(frame);
  svg.setAttribute("viewBox", "0 0 " + lay.W + " " + lay.H);
  const defs = svgEl("defs", {});
  for (const mk of [["arr-dep", EDGE_STYLE.dependency.stroke], ["arr-mod", EDGE_STYLE.modification.stroke]]) {
    const marker = svgEl("marker", { id: mk[0], viewBox: "0 0 10 10", refX: "9", refY: "5",
      markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" });
    marker.appendChild(svgEl("path", { d: "M0,0L10,5L0,10z", fill: mk[1] }));
    defs.appendChild(marker);
  }
  svg.appendChild(defs);
  const viewport = svgEl("g", { id: "viewport" });
  svg.appendChild(viewport);

  for (const e of frame.edges) {
    const a = lay.byId[e[0]], b = lay.byId[e[1]];
    if (!a || !b) continue;
    const st = EDGE_STYLE[e[2]] || EDGE_STYLE.dependency;
    const x1 = a.x + a.w / 2, y1 = a.y + a.h / 2, x2 = b.x + b.w / 2, y2 = b.y + b.h / 2;
    const dx = Math.max(32, (x2 - x1) * 0.35);
    const path = svgEl("path", {
      d: "M" + x1 + "," + y1 + " C" + (x1 + dx) + "," + y1 + " " + (x2 - dx) + "," + y2 + " " + x2 + "," + y2,
      fill: "none", stroke: st.stroke, "stroke-width": st.width,
      "marker-end": "url(#" + st.marker + ")"
    });
    if (st.dash) path.setAttribute("stroke-dasharray", st.dash);
    path.dataset.edge = "1";
    path.dataset.kind = e[2];
    path.dataset.detail = e[2] + ": " + e[0] + " \u2192 " + e[1];
    viewport.appendChild(path);
  }

  for (const n of lay.nodes) {
    const def = DEFS[n.id] || {};
    const c = nodeColor(n.id, n.status);
    const g = svgEl("g", {});
    g.dataset.node = n.id;
    g.dataset.detail = n.id + "\n" + def.type + " · " + def.layer + " · " + n.status +
      "\ntrust " + Number(n.trust).toFixed(4) + "\n" + def.statement;
    const rect = svgEl("rect", { x: n.x, y: n.y, width: n.w, height: n.h, rx: 8,
      fill: c.fill, stroke: c.stroke, "stroke-width": n.id === "M0" ? 2.2 : 1.5 });
    if (n.status === "draft") rect.setAttribute("stroke-dasharray", "5 3");
    if (n.status === "deprecated") rect.setAttribute("stroke-dasharray", "7 4");
    if (n.status === "deprecated") rect.setAttribute("opacity", "0.62");
    g.appendChild(rect);
    const t1 = svgEl("text", { x: n.x + n.w / 2, y: n.y + 15,
      "text-anchor": "middle", "font-size": "11", "font-weight": "700", fill: "#e2e8f0" });
    t1.textContent = n.id;
    g.appendChild(t1);
    const t2 = svgEl("text", { x: n.x + n.w / 2, y: n.y + 31,
      "text-anchor": "middle", "font-size": "9.5", fill: "#cbd5e1" });
    t2.textContent = c.label + " · t=" + Number(n.trust).toFixed(2);
    g.appendChild(t2);
    viewport.appendChild(g);
  }

  const tip = document.getElementById("tooltip");
  const showTip = (ev, text) => { tip.textContent = text; tip.style.display = "block"; moveTip(ev); };
  const moveTip = (ev) => { const r = svg.getBoundingClientRect(); let x = ev.clientX - r.left + 14, y = ev.clientY - r.top + 14;
    if (x + 430 > r.width) x -= 460; if (y + 120 > r.height) y -= 130; tip.style.left = x + "px"; tip.style.top = y + "px"; };
  const hideTip = () => { tip.style.display = "none"; };
  svg.querySelectorAll("g[data-node]").forEach((g) => {
    g.addEventListener("mouseenter", (ev) => showTip(ev, g.dataset.detail));
    g.addEventListener("mousemove", moveTip);
    g.addEventListener("mouseleave", hideTip);
    g.addEventListener("click", () => showNodeDetail(g.dataset.node, frame));
  });
  svg.querySelectorAll("path[data-edge]").forEach((p) => {
    p.addEventListener("mouseenter", (ev) => showTip(ev, p.dataset.detail));
    p.addEventListener("mousemove", moveTip);
    p.addEventListener("mouseleave", hideTip);
  });

  panState = { scale: 1, tx: 0, ty: 0, drag: null };
  if (docMove) document.removeEventListener("mousemove", docMove);
  if (docUp) document.removeEventListener("mouseup", docUp);
  const apply = () => viewport.setAttribute("transform", "translate(" + panState.tx + "," + panState.ty + ") scale(" + panState.scale + ")");
  svg.onmousedown = (ev) => { panState.drag = { x: ev.clientX, y: ev.clientY, tx: panState.tx, ty: panState.ty }; svg.classList.add("dragging"); };
  docMove = (ev) => {
    if (!panState.drag) return;
    panState.tx = panState.drag.tx + (ev.clientX - panState.drag.x);
    panState.ty = panState.drag.ty + (ev.clientY - panState.drag.y);
    apply();
  };
  docUp = () => { panState.drag = null; svg.classList.remove("dragging"); };
  document.addEventListener("mousemove", docMove);
  document.addEventListener("mouseup", docUp);
  svg.onwheel = (ev) => {
    ev.preventDefault();
    panState.scale = Math.min(3.2, Math.max(0.25, panState.scale * (ev.deltaY < 0 ? 1.12 : 0.9)));
    apply();
  };
  document.getElementById("reset-view").onclick = () => { panState.scale = 1; panState.tx = 0; panState.ty = 0; apply(); };
  apply();
}

function showNodeDetail(id, frame) {
  const def = DEFS[id] || {};
  const node = (frame.nodes || []).find(n => n[0] === id);
  const status = node ? node[1] : def.status;
  const trust = node ? Number(node[2]) : def.trust;
  const ins = frame.edges.filter(e => e[1] === id).map(e => e[2] + " " + e[0] + " \u2192 " + e[1]);
  const outs = frame.edges.filter(e => e[0] === id).map(e => e[0] + " \u2192 " + e[1] + " (" + e[2] + ")");
  let h = "<b>" + esc(id) + "</b> — " + esc(def.type || "?") + " · " + esc(def.layer || "?") +
    " · " + esc(status) + " · trust " + Number(trust).toFixed(4);
  if (def.immutable) h += " · <span style='color:var(--amber)'>IMMUTABLE L0</span>";
  h += "<pre>" + esc(def.statement || "") + "</pre>";
  if (def.proposal_id) h += "<div>modification proposal: <b>" + esc(def.proposal_id) + "</b></div>";
  h += "<div style='margin-top:6px'>incoming (" + ins.length + ")</div><pre>" + esc(ins.join("\n") || "-") + "</pre>";
  h += "<div>outgoing (" + outs.length + ")</div><pre>" + esc(outs.join("\n") || "-") + "</pre>";
  document.getElementById("node-detail").innerHTML = h;
}

function renderSummary(frame) {
  const rows = [
    ["epoch", frame.epoch + "/" + FRAMES.length],
    ["proposal", esc(frame.track) + " · " + esc(frame.title)],
    ["snapshot", esc(frame.snapshot)],
    ["applied proposals", String(frame.applied) + " / " + META.applied],
    ["nodes", String(frame.counts.nodes) + " (final " + META.nodes + ")"],
    ["edges", String(frame.counts.edges) + " (final " + META.edges + ")"],
    ["status", frame.counts.validated + " validated · " + frame.counts.draft + " draft · " + frame.counts.deprecated + " deprecated"],
    ["dependency edges", String(frame.counts.dependency)],
    ["modification edges", String(frame.counts.modification)]
  ];
  document.getElementById("summary-table").innerHTML = rows.map(r =>
    "<tr><td>" + esc(r[0]) + "</td><td class='mono'>" + esc(r[1]) + "</td></tr>").join("");
  document.getElementById("params").innerHTML = Object.entries(frame.params).map(kv =>
    "<div><b>" + esc(kv[0]) + ":</b> <code>" + esc(kv[1]) + "</code></div>").join("");
}

function renderProposalTable() {
  const tbody = document.getElementById("proposal-tbody");
  tbody.innerHTML = PROPS.map(p => {
    const ver = p.verification_exit;
    const verCell = ver == null ? "-" : (ver === 0 ? '<span style="color:#4ade80">0</span>' : '<span style="color:#f87171">' + esc(ver) + "</span>");
    return "<tr data-epoch='" + p.epoch + "'><td class='mono'>" + p.epoch + "</td><td class='mono'>" + esc(p.id) +
      "</td><td>" + esc(p.track) + "</td><td>" + esc(p.title) + "</td><td>" + verCell +
      "</td><td class='mono'>" + esc(p.modification_node || "-") + "</td></tr>";
  }).join("");
  tbody.querySelectorAll("tr").forEach(tr => {
    tr.addEventListener("click", () => show(Number(tr.dataset.epoch) - 1));
  });
}

function renderHeader() {
  document.getElementById("verdict-chip").className = "chip ok";
  document.getElementById("verdict-chip").textContent = META.last_self_test.passed ? "SELF-TEST PASS" : "CHECK STATE";
  document.getElementById("meta").innerHTML =
    "L0 meta-goal: <b>" + esc(DEFS.M0 ? DEFS.M0.statement : "Improve this system's own auto-research capability.") + "</b>" +
    "<br/>" + META.nodes + " nodes · " + META.edges + " edges · " + META.applied + "/" + META.proposals +
    " proposals applied · " + META.self_tests + " self-tests · " + META.hard_verifications + " hard verifications";
  document.getElementById("gen-at").textContent = DATA.generated_at || "";
}

function renderLegend() {
  const items = [
    ["#78350f", "M0 immutable"], ["#1e3a8a", "assumption"], ["#4c1d95", "inference"],
    ["#14532d", "verify"], ["#451a03", "modification"], ["#7dd3fc", "draft (dashed)"],
    ["#f87171", "deprecated (dashed)"], ["#64748b", "dependency"], ["#f59e0b", "modification edge"]
  ];
  document.getElementById("legend").innerHTML = items.map(i =>
    "<span><i class='sw' style='background:" + i[0] + "'></i>" + i[1] + "</span>").join("");
}

function show(i) {
  current = Math.max(0, Math.min(FRAMES.length - 1, i));
  const frame = FRAMES[current];
  renderGraph(frame);
  renderSummary(frame);
  document.getElementById("slider").value = current;
  document.getElementById("idx").textContent = (current + 1) + "/" + FRAMES.length;
  document.getElementById("label").innerHTML = "<b>Epoch " + String(frame.epoch).padStart(3, "0") + "</b> · " +
    esc(frame.title) + " <span style='color:var(--muted)'>· " + frame.counts.nodes + " nodes · " +
    frame.counts.edges + " edges · " + frame.counts.validated + " validated</span>";
  document.querySelectorAll("#proposal-tbody tr").forEach(tr => {
    tr.classList.toggle("current", Number(tr.dataset.epoch) - 1 === current);
  });
  document.getElementById("node-detail").innerHTML = "Click a node to inspect its claim, status, trust, and adjacent edges.";
}

function play() {
  if (playTimer) { clearInterval(playTimer); playTimer = null; document.getElementById("play").innerHTML = "&#9654; play"; return; }
  document.getElementById("play").innerHTML = "&#9208; pause";
  playTimer = setInterval(() => { show((current + 1) % FRAMES.length); }, 850);
}

(function init() {
  document.getElementById("slider").max = FRAMES.length - 1;
  renderHeader();
  renderLegend();
  renderProposalTable();
  document.getElementById("first").onclick = () => show(0);
  document.getElementById("prev").onclick = () => show(current - 1);
  document.getElementById("next").onclick = () => show(current + 1);
  document.getElementById("latest").onclick = () => show(FRAMES.length - 1);
  document.getElementById("play").onclick = play;
  document.getElementById("slider").oninput = () => show(Number(document.getElementById("slider").value));
  show(FRAMES.length - 1);
})();
</script>
</body>
</html>
"""


def render_html(data: dict[str, Any]) -> str:
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return AUTO_DASH_HTML.replace("__AUTO_DATA__", blob)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render the auto-research self-graph and epoch history as self-contained HTML")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help=f"output path (default {DEFAULT_OUT})")
    parser.add_argument("--no-open", action="store_true", help="do not open the browser")
    args = parser.parse_args()

    data = build_data()
    html = render_html(data)
    out = Path(args.out).expanduser()
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"Auto-research dashboard written: {out}")
    print(f"  epochs={len(data['frames'])} nodes={data['meta']['nodes']} "
          f"edges={data['meta']['edges']} applied={data['meta']['applied']} "
          f"bytes={len(html)}")
    if not args.no_open:
        try:
            webbrowser.open(out.resolve().as_uri())
        except Exception as exc:
            print(f"(could not open browser: {exc})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
