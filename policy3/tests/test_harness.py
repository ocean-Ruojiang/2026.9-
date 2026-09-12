"""Check acceptance-suite legality and result semantics, without replacing HTTP physics."""
from __future__ import annotations

import copy
import math
import unittest

from strategy3.local_sim import acceptance_cases, random_cases, simulator_api, summarize


class AcceptanceSuiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = acceptance_cases()

    def test_exact_split_legal_unique_channels_and_directional_sources(self):
        self.assertEqual(len(self.cases), 15)
        self.assertEqual(sum(c.category == "random" for c in self.cases), 10)
        self.assertEqual(sum(c.category == "edge" for c in self.cases), 5)
        _, _, _, validate, _, _ = simulator_api()
        for case in self.cases:
            sources = validate(case.scenario)
            self.assertGreaterEqual(len(sources), 10)
            self.assertLessEqual(len(sources), 16)
            self.assertTrue(any(source.direction is not None for source in sources))
            self.assertEqual(len({source.channel for source in sources}), len(sources))

    def test_reproducible_scenarios(self):
        self.assertEqual([c.scenario for c in self.cases], [c.scenario for c in acceptance_cases()])
        self.assertNotEqual(self.cases[0].scenario, acceptance_cases(98765)[0].scenario)

    def test_special_cases_exercise_directions_and_minimum_radius(self):
        outward, inward, tangent, clustered, stress = self.cases[10:]
        self.assertTrue(all(source["radius"] == 1000 for case in self.cases[10:] for source in case.scenario["sources"]))
        for case, sign in [(outward, 1), (inward, -1)]:
            for source in case.scenario["sources"]:
                if source["direction"] is None:
                    continue
                angle = math.radians(source["direction"])
                dot = source["x"] * math.cos(angle) + source["y"] * math.sin(angle)
                self.assertGreater(sign * dot, 1700)
        self.assertTrue(stress.non_typical_stress)
        self.assertEqual(stress.noise, "hash")
        self.assertTrue(all(source["direction"] is not None for source in stress.scenario["sources"]))
        self.assertFalse(any(case.non_typical_stress for case in self.cases[:-1]))

    def test_dynamic_random_count_accommodates_requested_directions(self):
        cases = random_cases(3, 25, 16, None, "mixed", "random", "smooth")
        self.assertTrue(all(len(c.scenario["sources"]) == 16 for c in cases))

    def test_failed_cases_remain_in_aggregate_and_missing_cpu_is_not_zero(self):
        base = dict(target_count=10, cleared_count=10, discovered_count=10, run_ok=True,
                    virtual_time_s=1000., real_time_s=10., process_wall_time_s=11.,
                    strategy_cpu_time_s=8., planning_time_s=7., move_distance_m=1000., measure=20, case_index=1)
        failed = copy.deepcopy(base)
        failed.update(case_index=2, cleared_count=4, discovered_count=6, run_ok=False,
                      strategy_cpu_time_s=None, virtual_time_s=2000.)
        report = summarize([base, failed])
        self.assertEqual(report["cases"], 2)
        self.assertEqual(report["total_cleared"], 14)
        self.assertEqual(report["success_rate"], .5)
        self.assertEqual(report["mean_virtual_time_s"], 1500.)
        self.assertEqual(report["mean_strategy_cpu_time_s"], 8.)
        self.assertEqual(report["cpu_time_available_cases"], 1)
        self.assertEqual(report["failed_case_indices"], [2])


if __name__ == "__main__":
    unittest.main()
