from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from strategy2.config import Config
from strategy2.experiments import simulate
from strategy2.runner import run
from strategy2.simulator import LocalTransport


class IntegrationTests(unittest.TestCase):
    def config(self):
        return replace(Config(),name="integration",particles=64,scenarios=8,
                       candidate_cap=16,station_candidate_cap=8,optional_pool_cap=4)

    def test_minimum_count_edge_case_and_forced_grid(self):
        cfg=replace(self.config(),detour_budget_s=0,station_offset_m=0,
                    finish_primary_limit=1,finish_extra_budget_s=0)
        with tempfile.TemporaryDirectory() as d:
            result=simulate(cfg,14,"edge",Path(d)/"run")
            self.assertEqual(result["true_count"],10)
            self.assertEqual(result["reason"],"complete")
            self.assertEqual(result["true_cleared"],10)
            self.assertFalse(result["false_completion"])
            self.assertGreater(len(result["grid_fallbacks"]),0)

    def test_maximum_count_with_initial_near(self):
        with tempfile.TemporaryDirectory() as d:
            result=simulate(self.config(),0,"near",Path(d)/"run")
            self.assertEqual(result["true_count"],16)
            self.assertEqual(result["reason"],"complete")
            self.assertEqual(result["true_cleared"],16)
            self.assertFalse(result["false_completion"])

    def test_action_limit_is_not_reported_complete(self):
        cfg=replace(self.config(),max_actions=1)
        with tempfile.TemporaryDirectory() as d:
            result=simulate(cfg,11,"uniform",Path(d)/"run")
            self.assertEqual(result["reason"],"action_limit")
            self.assertFalse(result["complete_certificate"])
            self.assertFalse(result["false_completion"])


if __name__=="__main__":
    unittest.main()
