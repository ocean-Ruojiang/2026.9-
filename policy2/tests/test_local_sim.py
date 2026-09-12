"""Contract tests for the external simulator bridge, including real child processes."""
import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import unittest

from strategy2.config import Config
from strategy2.local_sim import add_arguments, compare_rows, resolve_live, run_batches


class LiveEnvironmentTests(unittest.TestCase):
    def test_harness_environment_and_fresh_output_subdirectory(self):
        env = dict(SIM_BASE_URL="http://127.0.0.1:54321", SIM_ROBOT_ID="local", SIM_RUN_DIR="case_0001")
        self.assertEqual(resolve_live(environ=env),
                         (env["SIM_BASE_URL"], "local", str(Path("case_0001") / "strategy2")))

    def test_explicit_options_override_environment(self):
        env = dict(SIM_BASE_URL="unused", SIM_ROBOT_ID="unused", SIM_RUN_DIR="unused")
        self.assertEqual(resolve_live("http://127.0.0.1:2027", "id", "out", env),
                         ("http://127.0.0.1:2027", "id", "out"))

    def test_manual_usage_keeps_official_default_and_requires_identity(self):
        self.assertEqual(resolve_live(robot_id="id", output="out", environ={})[0],
                         "http://127.0.0.1:2026")
        with self.assertRaises(ValueError):
            resolve_live(environ={})
        with self.assertRaises(ValueError):
            resolve_live(environ=dict(SIM_ROBOT_ID="id", SIM_RUN_DIR="case"))


class ExternalSimulatorTests(unittest.TestCase):
    def arguments(self, root, configs):
        parser = argparse.ArgumentParser()
        add_arguments(parser)
        return parser.parse_args(["--cases", "1", "--seed", "20260912", "--count", "10",
                                  "--output", str(root / "output"), "--configs", *map(str, configs)])

    def test_real_http_paired_variants_and_truth_check(self):
        # Distinct processes use the same truth; independent counts confirm the certificate.
        cfg = Config.load(Path(__file__).resolve().parents[1] / "configs" / "quick.json")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            configs = []
            for index, variant in enumerate([cfg, replace(cfg, detour_budget_s=0)]):
                config = root / f"variant_{index}.json"
                config.write_text(json.dumps(asdict(variant)), encoding="utf-8")
                configs.append(config)
            report = run_batches(self.arguments(root, configs))
            self.assertTrue(report["all_runs_ok"])
            rows = json.loads((root / "output" / "case_comparison.json").read_text(encoding="utf-8"))
            for variant in rows:
                case = variant["cases"][0]
                self.assertEqual(case["cleared_count"], 10)
                self.assertEqual(case["end_reason"], "user_exit")
                self.assertTrue(case["certificate_consistent"])
                self.assertLess(abs(case["clock_delta_s"]), 1e-4)
            self.assertEqual(rows[0]["cases"][0]["fingerprint"], rows[1]["cases"][0]["fingerprint"])
            # A mismatched scenario must never be described as a paired comparison.
            rows[1]["cases"][0]["fingerprint"] = "different_case"
            comparison = compare_rows(rows)
            self.assertFalse(comparison[1]["paired_cases_match"])
            self.assertEqual(comparison[1]["jointly_successful_pairs"], 0)

    def test_incomplete_strategy_is_retained_as_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "limited.json"
            config.write_text(json.dumps(asdict(replace(Config(), max_actions=1))), encoding="utf-8")
            report = run_batches(self.arguments(root, [config]))
            self.assertFalse(report["all_runs_ok"])
            row = report["comparison"][0]
            self.assertEqual(row["cases"], 1)
            self.assertEqual(row["success_rate"], 0)
            self.assertIsNone(row["mean_successful_virtual_time_s"])
            case = root / "output" / "01_limited" / "case_0001"
            strategy = json.loads((case / "strategy2" / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(strategy["reason"], "action_limit")
            self.assertTrue((case / "scenario.json").is_file())


if __name__ == "__main__":
    unittest.main()
