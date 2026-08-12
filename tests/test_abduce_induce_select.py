"""Abduction pricing, induction admission tests, and selection records."""
import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import claim_graph as cg  # noqa: E402

EXAMPLE = ROOT / "templates" / "claim_graph.example.json"


def example_graph():
    return json.loads(EXAMPLE.read_text())


class TempPaths(unittest.TestCase):
    """Point the module's file globals at a scratch directory."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._saved = (cg.GRAPH_PATH, cg.CANDIDATES_PATH, cg.LOG_DIR)
        cg.GRAPH_PATH = self.tmp / ".research" / "claim_graph.json"
        cg.CANDIDATES_PATH = self.tmp / ".research" / "candidates.json"
        cg.LOG_DIR = self.tmp / ".research" / "logs"
        cg.GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self._restore)

    def _restore(self):
        cg.GRAPH_PATH, cg.CANDIDATES_PATH, cg.LOG_DIR = self._saved


class TestAbduction(unittest.TestCase):
    def test_two_variable_repairs_are_all_accommodation_only(self):
        # With two variables there is nothing left for a repair to predict, so
        # every repair buys compatibility without taking on any risk.
        graph = {
            "variables": {"A": {"role": "x", "observed": True},
                          "B": {"role": "y", "observed": True}},
            "edges": [], "assumed_absent": [], "probes": {}, "theory": {},
        }
        cands = cg.abduce(graph, "A", "B", [], "dependency")
        self.assertTrue(cands)
        for c in cands:
            self.assertTrue(c["accommodation_only"], c)
            self.assertEqual(c["exposes"], [])

    def test_a_repair_with_a_new_prediction_is_not_accommodation_only(self):
        cands = cg.abduce(example_graph(), "L", "R", ["E"], "dependency")
        retract = [c for c in cands if c["action"] == "retract_assumed_absent"]
        self.assertEqual(len(retract), 1)
        self.assertFalse(retract[0]["accommodation_only"])
        # The repair now commits to something beyond the anomaly it absorbs.
        exposed = {(e["x"], e["y"], tuple(e["given"])) for e in retract[0]["exposes"]}
        self.assertIn(("E", "R", ("K",)), exposed)

    def test_risky_repairs_are_ranked_before_accommodation_only_ones(self):
        graph = example_graph()
        cands = cg.abduce(graph, "L", "R", ["E"], "dependency")
        flags = [c["accommodation_only"] for c in cands]
        self.assertEqual(flags, sorted(flags))

    def test_repairs_never_introduce_a_cycle(self):
        cands = cg.abduce(example_graph(), "R", "E", ["K"], "dependency")
        for c in cands:
            patched = cg.apply_patch(cg.clone(example_graph()), c["patch"])
            self.assertIsNone(cg.find_cycle(cg.edge_list(patched)), c["detail"])


class TestInduction(TempPaths):
    def induce(self, **kw):
        args = argparse.Namespace(
            id="M2", statement="s", support="EXP-001,EXP-002", entails="K->R"
        )
        for k, v in kw.items():
            setattr(args, k, v)
        with contextlib.redirect_stdout(io.StringIO()):
            return cg.cmd_induce(args)

    def setUp(self):
        super().setUp()
        cg.save_graph(example_graph())

    def test_insufficient_support_is_refused(self):
        with self.assertRaises(SystemExit) as ctx:
            self.induce(support="EXP-001")
        self.assertIn("independent closed probes", str(ctx.exception))

    def test_a_generalisation_covered_by_existing_probes_is_refused(self):
        # P1 already tests K->R, so this node predicts nothing new.
        with self.assertRaises(SystemExit) as ctx:
            self.induce(entails="K->R")
        self.assertIn("already covered", str(ctx.exception))

    def test_an_empty_entailment_is_refused(self):
        with self.assertRaises(SystemExit):
            self.induce(entails="")

    def test_a_node_with_an_untested_entailment_is_admitted(self):
        self.assertEqual(self.induce(entails="K->R,E->R"), 0)
        graph = cg.load_graph()
        self.assertEqual(graph["theory"]["M2"]["provenance"], "induced")
        self.assertEqual(graph["theory"]["M2"]["supported_by"], ["EXP-001", "EXP-002"])


class TestSelection(TempPaths):
    CANDIDATES = [
        {"id": "D1", "kind": "independence_test", "detail": "R _||_ E | K"},
        {"id": "D2", "kind": "independence_test", "detail": "K _||_ L | E"},
    ]

    def setUp(self):
        super().setUp()
        cg.save_graph(example_graph())
        self.payload = cg.emit_candidates(
            "deduction", example_graph(), list(self.CANDIDATES), {"claim": "c"}
        )
        self.selection_path = self.tmp / "selection.json"

    def write_selection(self, **kw):
        sel = {"candidate_set_hash": self.payload["candidate_set_hash"]}
        sel.update(kw)
        self.selection_path.write_text(json.dumps(sel))
        return self.selection_path

    def test_stale_candidate_set_hash_is_refused(self):
        path = self.write_selection(
            candidate_set_hash="0000000000000000",
            selected=["D1"],
            rejected=[{"id": "D2", "reason": "cheaper elsewhere"}],
        )
        with self.assertRaises(SystemExit) as ctx:
            cg.ingest_selection(path)
        self.assertIn("different candidate set", str(ctx.exception))

    def test_an_unaccounted_candidate_is_refused(self):
        path = self.write_selection(selected=["D1"], rejected=[])
        with self.assertRaises(SystemExit) as ctx:
            cg.ingest_selection(path)
        self.assertIn("neither selected nor rejected", str(ctx.exception))

    def test_a_rejection_without_a_reason_is_refused(self):
        path = self.write_selection(
            selected=["D1"], rejected=[{"id": "D2", "reason": "   "}]
        )
        with self.assertRaises(SystemExit) as ctx:
            cg.ingest_selection(path)
        self.assertIn("without a reason", str(ctx.exception))

    def test_an_unknown_candidate_is_refused(self):
        path = self.write_selection(
            selected=["D9"], rejected=[{"id": "D1", "reason": "r"}, {"id": "D2", "reason": "r"}]
        )
        with self.assertRaises(SystemExit) as ctx:
            cg.ingest_selection(path)
        self.assertIn("unknown candidates", str(ctx.exception))

    def test_a_complete_selection_is_recorded(self):
        path = self.write_selection(
            selected=["D1"], rejected=[{"id": "D2", "reason": "no probe budget left"}]
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cg.cmd_select(argparse.Namespace(file=str(path))), 0)
        records = list(cg.LOG_DIR.glob("selection-*.json"))
        self.assertEqual(len(records), 1)
        recorded = json.loads(records[0].read_text())
        self.assertEqual(recorded["selected"], ["D1"])
        self.assertEqual(recorded["rejected"][0]["reason"], "no probe budget left")
        self.assertEqual(
            recorded["candidate_set_hash"], self.payload["candidate_set_hash"]
        )


if __name__ == "__main__":
    unittest.main()
