"""Tests for the research replay system (tools/research_replay.py).

run       script -> materialised research directory
export    research directory -> rebuild script (same snapshot)
timeline  script -> scrubber HTML over per-step dashboards
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPLAY = ROOT / "tools" / "research_replay.py"

MINIMAL_SCRIPT = {
    "name": "t_minimal",
    "steps": [
        {"cli": "research_closure", "cmd": "set-project",
         "args": {"--question": "q?", "--agenda": "a", "--minimum": "m"}},
        {"cli": "claim_graph", "cmd": "init", "args": {"--claim": "K predicts R"}},
        {"cli": "claim_graph", "cmd": "add-variable",
         "args": {"--id": "E", "--name": "excitation", "--role": "intervention"}},
        {"cli": "claim_graph", "cmd": "add-variable",
         "args": {"--id": "K", "--name": "condition", "--role": "candidate_predictor"}},
        {"cli": "claim_graph", "cmd": "add-variable",
         "args": {"--id": "R", "--name": "recovery", "--role": "outcome"}},
        {"cli": "claim_graph", "cmd": "add-edge", "args": {"--from": "E", "--to": "K"}},
        {"cli": "claim_graph", "cmd": "add-edge", "args": {"--from": "K", "--to": "R"}},
        {"cli": "claim_graph", "cmd": "add-probe",
         "args": {"--id": "P1",
                  "--tests": '{"kind":"edge","from":"K","to":"R"}',
                  "--metric": "rho", "--prereg": "rho>0.5", "--controls": "E"}},
        {"cli": "claim_graph", "cmd": "add-resolution",
         "args": {"--when": '{"P1":"positive"}', "--then": "supported"}},
        {"cli": "claim_graph", "cmd": "validate"},
        {"cli": "research_closure", "cmd": "start-sprint",
         "args": {"--claim": "K predicts R", "--artifact": "note.md"}},
        {"cli": "research_closure", "cmd": "new-experiment",
         "args": {"--question": "q", "--hypothesis": "h", "--intervention": "i",
                  "--measurement": "m", "--kill": "k", "--artifact": "a",
                  "--hours": 4, "--node": "P1", "--controls": "E"}},
    ],
}

RICH_SCRIPT = {
    "name": "t_rich",
    "steps": [
        {"cli": "research_closure", "cmd": "set-project",
         "args": {"--question": "q?", "--agenda": "a", "--minimum": "m"}},
        {"cli": "claim_graph", "cmd": "init", "args": {"--claim": "K predicts R"}},
        {"cli": "claim_graph", "cmd": "add-variable",
         "args": {"--id": "E", "--name": "excitation", "--role": "intervention"}},
        {"cli": "claim_graph", "cmd": "add-variable",
         "args": {"--id": "K", "--name": "condition", "--role": "candidate_predictor"}},
        {"cli": "claim_graph", "cmd": "add-variable",
         "args": {"--id": "R", "--name": "recovery", "--role": "outcome"}},
        {"cli": "claim_graph", "cmd": "add-edge", "args": {"--from": "E", "--to": "K"}},
        {"cli": "claim_graph", "cmd": "add-edge", "args": {"--from": "K", "--to": "R"}},
        {"cli": "claim_graph", "cmd": "add-probe",
         "args": {"--id": "P1",
                  "--tests": '{"kind":"edge","from":"K","to":"R"}',
                  "--metric": "rho", "--prereg": "rho>0.5", "--controls": "E"}},
        {"cli": "claim_graph", "cmd": "add-probe",
         "args": {"--id": "P2",
                  "--tests": '{"kind":"independence","x":"K","y":"R","given":["E"]}',
                  "--metric": "partial_rho", "--prereg": "|rho|<0.2",
                  "--guards": "P1==positive"}},
        {"cli": "claim_graph", "cmd": "add-resolution",
         "args": {"--when": '{"P1":"positive"}', "--then": "supported"}},
        {"cli": "claim_graph", "cmd": "validate"},
        {"cli": "research_closure", "cmd": "start-sprint",
         "args": {"--claim": "K predicts R", "--artifact": "note.md"}},
        {"cli": "research_closure", "cmd": "new-experiment",
         "args": {"--question": "q", "--hypothesis": "h", "--intervention": "i",
                  "--measurement": "m", "--kill": "k", "--artifact": "a",
                  "--hours": 4, "--node": "P1", "--controls": "E"}},
        {"cli": "research_closure", "cmd": "close-experiment",
         "args": {"--id": "EXP-001", "--decision": "supported",
                  "--evidence": "r.csv", "--conclusion": "c"}},
        {"cli": "research_closure", "cmd": "new-experiment",
         "args": {"--question": "q2", "--hypothesis": "h2", "--intervention": "i2",
                  "--measurement": "m2", "--kill": "k2", "--artifact": "a2",
                  "--hours": 3, "--node": "P2"}},
    ],
}


class ReplayCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def run_replay(self, *args):
        return subprocess.run(
            [sys.executable, str(REPLAY), *args],
            capture_output=True, text=True,
        )

    def write_script(self, script, name="script.json"):
        p = self.base / name
        p.write_text(json.dumps(script), encoding="utf-8")
        return p

    def assertOk(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc

    def state(self, repo):
        return json.loads((repo / ".research" / "state.json").read_text(
            encoding="utf-8"))

    def graph(self, repo):
        return json.loads((repo / ".research" / "claim_graph.json").read_text(
            encoding="utf-8"))


class TestRun(ReplayCase):
    def test_run_materialises_a_continuable_research(self):
        script = self.write_script(MINIMAL_SCRIPT)
        out = self.base / "research"
        proc = self.assertOk(self.run_replay(
            "run", "--script", str(script), "--out", str(out)))
        self.assertIn("Materialised", proc.stdout)
        st = self.state(out)
        self.assertEqual(st["version"], 3)
        self.assertEqual(st["sprint"]["claim"], "K predicts R")
        self.assertEqual(st["active_experiment"]["id"], "EXP-001")
        self.assertEqual(st["active_experiment"]["claim_graph_node"], "P1")
        # the materialised directory passes the guard (continuable)
        env = {**os.environ, "RESEARCH_CLOSURE_ROOT": str(out)}
        p = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "research_closure.py"), "guard"],
            cwd=out, env=env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("PASS", p.stdout)

    def test_run_until_yields_a_reproducible_intermediate_state(self):
        script = self.write_script(MINIMAL_SCRIPT)
        out = self.base / "mid"
        self.assertOk(self.run_replay(
            "run", "--script", str(script), "--until", "1", "--out", str(out)))
        st = self.state(out)
        self.assertEqual(st["project"]["question"], "q?")
        self.assertIsNone(st["sprint"])
        self.assertIsNone(st["active_experiment"])

    def test_run_refuses_a_nonempty_out_without_force(self):
        script = self.write_script(MINIMAL_SCRIPT)
        out = self.base / "occupied"
        out.mkdir()
        (out / "keep.txt").write_text("x")
        proc = self.run_replay("run", "--script", str(script), "--out", str(out))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not empty", proc.stderr)
        self.assertTrue((out / "keep.txt").exists())
        # --force clears it
        self.assertOk(self.run_replay(
            "run", "--script", str(script), "--out", str(out), "--force"))
        self.assertFalse((out / "keep.txt").exists())

    def test_run_rejects_an_unknown_cli(self):
        script = self.write_script({"steps": [
            {"cli": "nonsense", "cmd": "x", "args": {}}]})
        proc = self.run_replay("run", "--script", str(script),
                               "--out", str(self.base / "x"))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unknown cli", proc.stderr)


class TestExport(ReplayCase):
    def _materialise(self, script):
        script_path = self.write_script(script, name="src.json")
        repo = self.base / "src"
        self.assertOk(self.run_replay(
            "run", "--script", str(script_path), "--out", str(repo)))
        return repo

    def test_export_roundtrip_rebuilds_the_same_snapshot(self):
        repo = self._materialise(RICH_SCRIPT)
        script_path = self.base / "exported.json"
        self.assertOk(self.run_replay(
            "export", "--dir", str(repo), "--out", str(script_path)))
        exported = json.loads(script_path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(exported["steps"]), 12)

        copy = self.base / "copy"
        self.assertOk(self.run_replay(
            "run", "--script", str(script_path), "--out", str(copy)))
        sa, sb = self.state(repo), self.state(copy)
        self.assertEqual(sa["project"], sb["project"])
        self.assertEqual(sa["sprint"]["claim"], sb["sprint"]["claim"])
        self.assertEqual((sa["active_experiment"] or {}).get("id"),
                         (sb["active_experiment"] or {}).get("id"))
        self.assertEqual(sa["counters"], sb["counters"])
        ga, gb = self.graph(repo), self.graph(copy)
        self.assertEqual({k: v.get("outcome") for k, v in ga["probes"].items()},
                         {k: v.get("outcome") for k, v in gb["probes"].items()})
        # P1 closed positive; P2 experiment is the active one
        self.assertEqual(ga["probes"]["P1"]["outcome"], "positive")
        self.assertEqual(sb["active_experiment"]["claim_graph_node"], "P2")

    def test_export_preserves_latent_variables(self):
        script = dict(MINIMAL_SCRIPT)
        script["steps"] = list(script["steps"])
        script["steps"].insert(5, {
            "cli": "claim_graph", "cmd": "add-variable",
            "args": {"--id": "U", "--name": "common cause", "--role": "latent",
                     "--latent": True}})
        script["steps"].insert(7, {
            "cli": "claim_graph", "cmd": "add-edge", "args": {"--from": "U", "--to": "K"}})
        repo = self._materialise(script)
        script_path = self.base / "exported.json"
        self.assertOk(self.run_replay(
            "export", "--dir", str(repo), "--out", str(script_path)))
        self.assertIn("--latent", script_path.read_text(encoding="utf-8"))
        copy = self.base / "copy"
        self.assertOk(self.run_replay(
            "run", "--script", str(script_path), "--out", str(copy)))
        self.assertFalse(self.graph(copy)["variables"]["U"]["observed"])

    def test_export_requires_the_claim_graph(self):
        repo = self.base / "bare"
        (repo / ".research").mkdir(parents=True)
        (repo / ".research" / "state.json").write_text(
            json.dumps({"version": 3, "mode": "graduation", "project": {},
                        "sprint": None, "active_experiment": None,
                        "counters": {}, "events": [], "limits": {}}))
        proc = self.run_replay("export", "--dir", str(repo))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("claim-graph engine", proc.stderr)

    def test_amendments_export_as_a_comment(self):
        repo = self._materialise(MINIMAL_SCRIPT)
        g = self.graph(repo)
        g["amendments"] = [{"id": "AM-01", "applied_at": "t", "action": "add_edge",
                            "detail": "L->R", "motivating_anomaly": {},
                            "accommodation_only": False, "owed_implication": {},
                            "cleared_by": None}]
        (repo / ".research" / "claim_graph.json").write_text(
            json.dumps(g), encoding="utf-8")
        script_path = self.base / "exported.json"
        self.assertOk(self.run_replay(
            "export", "--dir", str(repo), "--out", str(script_path)))
        self.assertIn("not reconstructible", script_path.read_text(encoding="utf-8"))


class TestTimeline(ReplayCase):
    def test_timeline_generates_a_scrubber(self):
        script = self.write_script(MINIMAL_SCRIPT)
        html_path = self.base / "replay.html"
        proc = self.assertOk(self.run_replay(
            "timeline", "--script", str(script), "--out", str(html_path),
            "--no-open"))
        html = html_path.read_text(encoding="utf-8")
        self.assertIn('id="slider"', html)
        self.assertIn('id="play"', html)
        self.assertIn('id="first-content"', html)
        self.assertIn('id="state"', html)
        self.assertIn('"nodes"', html)          # per-frame graph-state marks
        self.assertIn("const PAYLOADS", html)   # all payloads embedded
        self.assertIn("initDashboard", html)    # dashboard mounted as a component
        # opens at the LATEST frame by default (scrub back manually)
        self.assertIn("show(stages.length - 1)", html)
        self.assertIn('id="latest"', html)
        # stage visibility must use an explicit .active class: setting
        # style.display = "" falls back to .stage{display:none} and hides
        # every frame (regression: nodes existed in the DOM but were invisible)
        self.assertIn(".stage.active", html)
        self.assertIn('classList.toggle("active"', html)
        self.assertNotIn('style.display = k === i ? ""', html)
        # single self-contained page: no iframes, no frames directory
        self.assertNotIn("<iframe", html)
        self.assertNotIn("srcdoc", html)
        self.assertFalse((self.base / "replay_frames").exists())


if __name__ == "__main__":
    unittest.main()
