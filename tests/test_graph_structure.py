"""Structural primitives: d-separation, adjustment sets, design hash."""
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import claim_graph as cg  # noqa: E402


CHAIN = [("X", "M"), ("M", "Y")]
FORK = [("C", "X"), ("C", "Y")]
COLLIDER = [("X", "C"), ("Y", "C")]


class TestDSeparation(unittest.TestCase):
    def test_chain_is_blocked_only_by_the_mediator(self):
        self.assertFalse(cg.d_separated(CHAIN, "X", "Y", []))
        self.assertTrue(cg.d_separated(CHAIN, "X", "Y", ["M"]))

    def test_fork_is_blocked_only_by_the_common_cause(self):
        self.assertFalse(cg.d_separated(FORK, "X", "Y", []))
        self.assertTrue(cg.d_separated(FORK, "X", "Y", ["C"]))

    def test_collider_is_closed_until_it_is_conditioned_on(self):
        self.assertTrue(cg.d_separated(COLLIDER, "X", "Y", []))
        # Conditioning on a collider opens the path.
        self.assertFalse(cg.d_separated(COLLIDER, "X", "Y", ["C"]))

    def test_conditioning_on_a_descendant_of_a_collider_also_opens_it(self):
        edges = COLLIDER + [("C", "D")]
        self.assertTrue(cg.d_separated(edges, "X", "Y", []))
        self.assertFalse(cg.d_separated(edges, "X", "Y", ["D"]))


class TestVerifyAdjustment(unittest.TestCase):
    # U is an unmeasured-style common cause of X and Y; M is a mediator.
    CONFOUNDED = [("U", "X"), ("U", "Y"), ("X", "Y")]
    MEDIATED = [("X", "M"), ("M", "Y")]

    def test_rejects_a_descendant_of_the_treatment(self):
        ok, why = cg.verify_adjustment(self.MEDIATED, "X", "Y", ["M"])
        self.assertFalse(ok)
        self.assertIn("descendant", why)

    def test_rejects_an_open_back_door_path(self):
        ok, why = cg.verify_adjustment(self.CONFOUNDED, "X", "Y", [])
        self.assertFalse(ok)
        self.assertIn("back-door", why)

    def test_accepts_a_sufficient_set(self):
        ok, why = cg.verify_adjustment(self.CONFOUNDED, "X", "Y", ["U"])
        self.assertTrue(ok, why)
        self.assertEqual(why, "")


class TestDesignHash(unittest.TestCase):
    def graph(self):
        return {
            "schema_version": 1,
            "claim": "c",
            "graph_type": "causal",
            "theory": {},
            "variables": {
                "X": {"name": "x", "role": "intervention", "observed": True},
                "Y": {"name": "y", "role": "outcome", "observed": True},
            },
            "edges": [{"from": "X", "to": "Y", "from_theory": []}],
            "assumed_absent": [],
            "probes": {
                "P1": {"tests": {"kind": "edge", "from": "X", "to": "Y"},
                       "metric": "m", "prereg": "p", "controls": [], "guards_in": [],
                       "experiment_id": None, "outcome": None}
            },
            "resolution": [{"when": {"P1": "positive"}, "then": "supported"}],
            "amendments": [],
        }

    def test_results_do_not_move_the_hash(self):
        before = cg.design_hash(self.graph())
        g = self.graph()
        g["probes"]["P1"]["outcome"] = "positive"
        g["probes"]["P1"]["experiment_id"] = "EXP-001"
        g["probes"]["P1"]["defect"] = "implementation"
        g["amendments"].append({"id": "AM-01", "cleared_by": None})
        self.assertEqual(before, cg.design_hash(g))

    def test_design_changes_move_the_hash(self):
        before = cg.design_hash(self.graph())
        g = self.graph()
        g["probes"]["P1"]["prereg"] = "something else"
        self.assertNotEqual(before, cg.design_hash(g))

        g = self.graph()
        g["resolution"][0]["then"] = "falsified"
        self.assertNotEqual(before, cg.design_hash(g))

    def test_flipping_a_variable_to_unobserved_moves_the_hash(self):
        # Regression: `observed` decides which implications are testable at all,
        # so it is part of the pre-registration and must be hashed.
        before = cg.design_hash(self.graph())
        g = self.graph()
        g["variables"]["Y"]["observed"] = False
        self.assertNotEqual(before, cg.design_hash(g))


if __name__ == "__main__":
    unittest.main()
