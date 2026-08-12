"""End-to-end CLI behaviour, with and without a claim graph.

Every command runs as a subprocess against a scratch repository, so these tests
exercise the real entry points including the optional `claim_graph` import.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "tools" / "research_closure.py"
GRAPH_CLI = ROOT / "tools" / "claim_graph.py"
EXAMPLE = ROOT / "templates" / "claim_graph.example.json"


class RepoCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.run_cli("init")

    def run_cli(self, *args, cli=None):
        env = {**os.environ, "RESEARCH_CLOSURE_ROOT": str(self.repo)}
        return subprocess.run(
            [sys.executable, str(cli or CLI), *args],
            cwd=self.repo, env=env, capture_output=True, text=True,
        )

    def run_graph(self, *args):
        return self.run_cli(*args, cli=GRAPH_CLI)

    def assertOk(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc

    def install_graph(self):
        shutil.copy(EXAMPLE, self.repo / ".research" / "claim_graph.json")

    def state(self):
        return json.loads((self.repo / ".research" / "state.json").read_text())

    def graph(self):
        return json.loads((self.repo / ".research" / "claim_graph.json").read_text())

    def start_sprint(self):
        return self.run_cli(
            "start-sprint", "--claim", "K predicts R", "--days", "14",
            "--artifact", "note.md",
        )

    def new_experiment(self, *extra):
        return self.run_cli(
            "new-experiment", "--question", "q", "--hypothesis", "h",
            "--intervention", "i", "--measurement", "m", "--kill", "k",
            "--artifact", "a", "--hours", "4", *extra,
        )


class TestWithoutClaimGraph(RepoCase):
    """A repository that never creates a graph must behave exactly as before."""

    def test_guard_blocks_only_on_the_missing_sprint(self):
        proc = self.run_cli("guard")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("BLOCK: No active sprint claim.", proc.stdout)

    def test_guard_passes_once_a_sprint_is_frozen(self):
        self.assertOk(self.start_sprint())
        proc = self.assertOk(self.run_cli("guard"))
        self.assertIn("PASS: work may proceed within the frozen claim.", proc.stdout)
        self.assertNotIn("claim graph", proc.stdout)

    def test_sprint_records_no_graph_metadata(self):
        self.assertOk(self.start_sprint())
        self.assertNotIn("claim_graph", self.state()["sprint"])
        self.assertEqual(self.state()["version"], 1)

    def test_new_and_close_experiment_need_no_node(self):
        self.assertOk(self.start_sprint())
        proc = self.assertOk(self.new_experiment())
        self.assertIn("Experiment opened: EXP-001", proc.stdout)
        proc = self.assertOk(self.run_cli(
            "close-experiment", "--id", "EXP-001", "--decision", "supported",
            "--evidence", "results.csv", "--conclusion", "c",
        ))
        self.assertIn("Decision: supported", proc.stdout)
        self.assertNotIn("Claim-graph node", proc.stdout)
        self.assertIsNone(self.state()["active_experiment"])

    def test_close_sprint_is_not_constrained(self):
        self.assertOk(self.start_sprint())
        proc = self.assertOk(self.run_cli(
            "close-sprint", "--decision", "advance", "--evidence", "e",
            "--conclusion", "c",
        ))
        self.assertIn("Sprint closed.", proc.stdout)


class TestGraphAwareCli(RepoCase):
    def setUp(self):
        super().setUp()
        self.install_graph()

    def test_start_sprint_freezes_the_design_hash(self):
        self.assertOk(self.start_sprint())
        frozen = self.state()["sprint"]["claim_graph"]
        expected = self.assertOk(self.run_graph("hash")).stdout.strip()
        self.assertEqual(frozen["design_hash"], expected)
        self.assertEqual(frozen["path"], ".research/claim_graph.json")
        self.assertEqual(self.state()["version"], 2)

    def test_start_sprint_refuses_an_invalid_graph(self):
        graph = self.graph()
        graph["edges"].append({"from": "R", "to": "E", "from_theory": []})
        (self.repo / ".research" / "claim_graph.json").write_text(json.dumps(graph))
        proc = self.start_sprint()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("claim graph fails validation", proc.stderr)

    def test_guard_detects_design_drift(self):
        self.assertOk(self.start_sprint())
        graph = self.graph()
        graph["probes"]["P1"]["prereg"] = "rho > 0.1 over 3 instances"
        (self.repo / ".research" / "claim_graph.json").write_text(json.dumps(graph))
        proc = self.run_cli("guard")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("drifted since the sprint was frozen", proc.stdout)

    def test_recording_an_outcome_is_not_design_drift(self):
        self.assertOk(self.start_sprint())
        self.assertOk(self.new_experiment("--node", "P1", "--controls", "E"))
        self.assertOk(self.run_cli(
            "close-experiment", "--id", "EXP-001", "--decision", "supported",
            "--evidence", "e", "--conclusion", "c",
        ))
        proc = self.assertOk(self.run_cli("guard"))
        self.assertNotIn("drifted", proc.stdout)

    def test_experiment_must_name_a_probe(self):
        self.assertOk(self.start_sprint())
        proc = self.new_experiment()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("must state which probe it runs (--node)", proc.stderr)
        self.assertIn("Ready probes: ['P1']", proc.stderr)

    def test_experiment_off_the_frontier_is_blocked(self):
        self.assertOk(self.start_sprint())
        proc = self.new_experiment("--node", "P2", "--controls", "E")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not on the ready frontier", proc.stderr)

    def test_unknown_probe_is_blocked(self):
        self.assertOk(self.start_sprint())
        proc = self.new_experiment("--node", "P9")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not a probe in the claim graph", proc.stderr)

    def test_invalid_control_set_is_blocked(self):
        self.assertOk(self.start_sprint())
        proc = self.new_experiment("--node", "P1", "--controls", "E,R")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not a valid adjustment set", proc.stderr)
        self.assertIn("descendant", proc.stderr)

    def test_a_positive_result_opens_the_next_probe(self):
        self.assertOk(self.start_sprint())
        self.assertOk(self.new_experiment("--node", "P1", "--controls", "E"))
        self.assertEqual(self.state()["active_experiment"]["claim_graph_node"], "P1")
        proc = self.assertOk(self.run_cli(
            "close-experiment", "--id", "EXP-001", "--decision", "supported",
            "--evidence", "e", "--conclusion", "c",
        ))
        self.assertIn("Claim-graph node P1 set to positive.", proc.stdout)
        self.assertIn("Ready next: ['P2']", proc.stdout)
        self.assertEqual(self.graph()["probes"]["P1"]["outcome"], "positive")
        self.assertEqual(self.graph()["probes"]["P1"]["experiment_id"], "EXP-001")


class TestDefectClassDoesNotCostAClaim(RepoCase):
    """Property: an implementation or measurement defect leaves the probe
    unresolved, so it cannot advance the resolution map."""

    def setUp(self):
        super().setUp()
        self.install_graph()
        self.assertOk(self.start_sprint())
        self.assertOk(self.new_experiment("--node", "P1", "--controls", "E"))

    def close(self, *extra):
        return self.run_cli(
            "close-experiment", "--id", "EXP-001", "--decision", "inconclusive",
            "--evidence", "e", "--conclusion", "c", *extra,
        )

    def test_inconclusive_without_a_defect_class_is_blocked(self):
        proc = self.close()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("must name its defect class", proc.stderr)
        self.assertIsNone(self.graph()["probes"]["P1"]["outcome"])

    def test_implementation_defect_leaves_the_probe_unresolved(self):
        proc = self.assertOk(self.close("--defect", "implementation"))
        self.assertIn("Claim-graph node P1 set to unresolved.", proc.stdout)
        probe = self.graph()["probes"]["P1"]
        self.assertEqual(probe["outcome"], "unresolved")
        self.assertEqual(probe["defect"], "implementation")

    def test_implementation_defect_does_not_advance_the_resolution_map(self):
        self.assertOk(self.close("--defect", "implementation"))
        # P1 negative would fire the `falsified` rule. `unresolved` must not.
        proc = self.run_graph("propose-decision")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["status"], "open")
        proc = self.assertOk(self.run_cli("guard"))
        self.assertNotIn("resolution map is already determined", proc.stdout)

    def test_a_measurement_defect_may_not_be_recorded_as_a_negative(self):
        proc = self.close("--defect", "measurement", "--outcome", "negative")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("must be 'unresolved'", proc.stderr)
        self.assertIsNone(self.graph()["probes"]["P1"]["outcome"])

    def test_a_hypothesis_defect_may_be_recorded_as_a_negative(self):
        proc = self.assertOk(self.close("--defect", "hypothesis", "--outcome", "negative"))
        self.assertIn("Claim-graph node P1 set to negative.", proc.stdout)
        self.assertIn("Resolution map determines: falsified", proc.stdout)


class TestSprintDecisionIsComputed(RepoCase):
    def setUp(self):
        super().setUp()
        self.install_graph()
        self.assertOk(self.start_sprint())
        self.assertOk(self.new_experiment("--node", "P1", "--controls", "E"))
        self.assertOk(self.run_cli(
            "close-experiment", "--id", "EXP-001", "--decision", "falsified",
            "--evidence", "e", "--conclusion", "c",
        ))

    def close_sprint(self, decision):
        return self.run_cli(
            "close-sprint", "--decision", decision, "--evidence", "e",
            "--conclusion", "c",
        )

    def test_a_decision_the_map_does_not_licence_is_refused(self):
        proc = self.close_sprint("advance")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("determines 'falsified' -> close as 'terminate'", proc.stderr)

    def test_the_decision_the_map_determines_is_accepted(self):
        proc = self.assertOk(self.close_sprint("terminate"))
        self.assertIn("Sprint closed.", proc.stdout)

    def test_guard_warns_once_the_map_is_determined(self):
        proc = self.run_cli("guard")
        self.assertIn("resolution map is already determined (falsified)", proc.stdout)


class TestGraphCliRunsFromAnywhere(RepoCase):
    def test_reasoning_modes_are_explicitly_named(self):
        proc = self.assertOk(self.run_graph("reasoning"))
        self.assertIn("THREE REASONING MODES", proc.stdout)
        self.assertIn("DEDUCTION", proc.stdout)
        self.assertIn("INDUCTION", proc.stdout)
        self.assertIn("ABDUCTION", proc.stdout)
        self.assertIn("claim_graph.py deduce", proc.stdout)
        self.assertIn("claim_graph.py induce", proc.stdout)
        self.assertIn("claim_graph.py abduce", proc.stdout)

    def test_reasoning_commands_label_their_output(self):
        self.install_graph()
        proc = self.assertOk(self.run_graph("deduce"))
        self.assertTrue(proc.stdout.startswith("DEDUCTION:"), proc.stdout)

        proc = self.assertOk(self.run_graph(
            "abduce", "--between", "L,R", "--given", "E",
        ))
        self.assertTrue(proc.stdout.startswith("ABDUCTION:"), proc.stdout)

        proc = self.assertOk(self.run_graph(
            "induce", "--id", "M2", "--statement", "E also predicts R",
            "--support", "EXP-001,EXP-002", "--entails", "E->R",
        ))
        self.assertTrue(proc.stdout.startswith("INDUCTION:"), proc.stdout)

    def test_graph_cli_discovers_the_repository(self):
        self.install_graph()
        self.assertOk(self.run_graph("validate"))
        proc = self.assertOk(self.run_graph("frontier"))
        self.assertIn("P1  READY", proc.stdout)

    def test_import_survives_a_symlinked_entry_point(self):
        # `install_research_closure_global.sh` links ~/.local/bin/research-closure
        # at tools/research_closure.py; the optional import must still resolve.
        bin_dir = self.repo / "bin"
        bin_dir.mkdir()
        link = bin_dir / "research-closure"
        link.symlink_to(CLI)
        self.install_graph()
        self.assertOk(self.start_sprint())
        env = {**os.environ, "RESEARCH_CLOSURE_ROOT": str(self.repo)}
        proc = subprocess.run(
            [sys.executable, str(link), "guard"],
            cwd=self.repo, env=env, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        # Proof the graph layer was actually loaded through the symlink.
        self.assertIn("asserted edges with no probe", proc.stdout)


if __name__ == "__main__":
    unittest.main()
