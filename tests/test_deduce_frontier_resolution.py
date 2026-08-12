"""Deduction coverage, the ready frontier, and the resolution map."""
import copy
import json
import sys
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


def as_text(impl):
    given = f" | {', '.join(impl['given'])}" if impl["given"] else ""
    return f"{impl['x']} _||_ {impl['y']}{given}"


class TestExampleGraphIsSound(unittest.TestCase):
    def test_validate_reports_no_blocks(self):
        blocks, _ = cg.validate(example_graph())
        self.assertEqual(blocks, [])


class TestDeduction(unittest.TestCase):
    def test_exclusion_restriction_is_reported_as_uncovered(self):
        cov = cg.coverage_report(example_graph())
        uncovered = {as_text(i) for i in cov["uncovered_implications"]}
        # R _||_ E | K is the exclusion restriction the example claim rests on.
        self.assertIn("R _||_ E | K", uncovered)

    def test_a_probe_that_tests_an_implication_covers_it(self):
        cov = cg.coverage_report(example_graph())
        implied = {as_text(i) for i in cov["implications"]}
        uncovered = {as_text(i) for i in cov["uncovered_implications"]}
        # P2 tests L _||_ R | E, so it is implied but not uncovered.
        self.assertIn("L _||_ R | E", implied)
        self.assertNotIn("L _||_ R | E", uncovered)

    def test_untested_absence_assumption_is_covered_by_p2(self):
        cov = cg.coverage_report(example_graph())
        self.assertEqual(cov["untested_assumptions"], [])

    def test_removing_the_probe_exposes_the_absence_assumption(self):
        g = example_graph()
        del g["probes"]["P2"]
        g["probes"]["P3"]["guards_in"] = ["P1==positive"]
        g["resolution"] = [r for r in g["resolution"] if "P2" not in r.get("when", {})]
        cov = cg.coverage_report(g)
        self.assertEqual(cov["untested_assumptions"], ["L->R"])


class TestFrontier(unittest.TestCase):
    def test_only_unguarded_probes_start_ready(self):
        self.assertEqual(cg.frontier(example_graph()), ["P1"])

    def test_a_probe_whose_guard_is_unmet_is_refused(self):
        g = example_graph()
        self.assertNotIn("P2", cg.frontier(g))
        self.assertNotIn("P3", cg.frontier(g))

    def test_a_satisfied_guard_admits_the_next_probe(self):
        g = example_graph()
        g["probes"]["P1"]["outcome"] = "positive"
        self.assertEqual(cg.frontier(g), ["P2"])

    def test_a_wrong_outcome_does_not_admit_the_next_probe(self):
        g = example_graph()
        g["probes"]["P1"]["outcome"] = "unresolved"
        self.assertEqual(cg.frontier(g), [])

    def test_probes_skipped_by_a_fired_rule_leave_the_frontier(self):
        g = example_graph()
        g["probes"]["P1"]["outcome"] = "negative"
        self.assertEqual(cg.skipped_probes(g), {"P2", "P3"})
        self.assertEqual(cg.frontier(g), [])


class TestResolution(unittest.TestCase):
    def test_map_is_open_until_a_rule_matches_fully(self):
        g = example_graph()
        g["probes"]["P1"]["outcome"] = "positive"
        proposal = cg.propose_decision(g)
        self.assertEqual(proposal["status"], "open")
        self.assertEqual(proposal["frontier"], ["P2"])

    def test_positive_then_negative_narrows_on_the_causal_rung(self):
        g = example_graph()
        g["probes"]["P1"]["outcome"] = "positive"
        g["probes"]["P2"]["outcome"] = "negative"
        proposal = cg.propose_decision(g)
        self.assertEqual(proposal["status"], "determined")
        self.assertEqual(proposal["then"], "narrow")
        self.assertEqual(proposal["rung"], "causal->predictive")
        self.assertEqual(proposal["depends_on_assumption"], "L->R")

    def test_unresolved_probe_does_not_advance_the_map(self):
        g = example_graph()
        g["probes"]["P1"]["outcome"] = "positive"
        g["probes"]["P2"]["outcome"] = "unresolved"
        g["probes"]["P2"]["defect"] = "implementation"
        self.assertEqual(cg.propose_decision(g)["status"], "open")

    def test_all_positive_supports_the_claim(self):
        g = example_graph()
        for pid in ("P1", "P2", "P3"):
            g["probes"][pid]["outcome"] = "positive"
        self.assertEqual(cg.propose_decision(g)["then"], "supported")

    def test_unpaid_debt_blocks_a_supported_verdict(self):
        g = example_graph()
        for pid in ("P1", "P2", "P3"):
            g["probes"][pid]["outcome"] = "positive"
        g["amendments"].append(
            {"id": "AM-01", "action": "add_edge", "detail": "L->R", "cleared_by": None}
        )
        proposal = cg.propose_decision(g)
        self.assertEqual(proposal["then"], "supported")
        self.assertTrue(proposal["blocked_by_debt"])

    def test_contradictory_rules_are_a_validation_block(self):
        g = example_graph()
        g["resolution"].append({"when": {"P1": "positive"}, "then": "falsified"})
        blocks, _ = cg.validate(g)
        self.assertTrue(any("can both fire" in b for b in blocks))


class TestValidationBlocks(unittest.TestCase):
    def test_cycle_is_blocked(self):
        g = example_graph()
        g["edges"].append({"from": "R", "to": "E", "from_theory": []})
        blocks, _ = cg.validate(g)
        self.assertTrue(any("cycle" in b for b in blocks))

    def test_control_set_containing_a_descendant_is_blocked(self):
        g = example_graph()
        g["probes"]["P1"]["controls"] = ["E", "R"]
        blocks, _ = cg.validate(g)
        self.assertTrue(any("not a valid adjustment set" in b for b in blocks))

    def test_dropping_a_needed_control_is_blocked(self):
        g = example_graph()
        # E now reaches R directly, so K <- E -> R is an open back-door path.
        g["edges"].append({"from": "E", "to": "R", "from_theory": []})
        g["probes"]["P1"]["controls"] = []
        blocks, _ = cg.validate(g)
        self.assertTrue(any("back-door" in b for b in blocks))

    def test_guard_on_an_undefined_probe_is_blocked(self):
        g = copy.deepcopy(example_graph())
        g["probes"]["P1"]["guards_in"] = ["P9==positive"]
        blocks, _ = cg.validate(g)
        self.assertTrue(any("undefined probe P9" in b for b in blocks))

    def test_too_many_probes_is_blocked(self):
        g = example_graph()
        g["max_probes"] = 2
        blocks, _ = cg.validate(g)
        self.assertTrue(any("exceeds max_probes" in b for b in blocks))


if __name__ == "__main__":
    unittest.main()
