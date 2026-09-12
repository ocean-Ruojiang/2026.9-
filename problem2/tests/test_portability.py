import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import problem2_cli as cli
import problem2_parallel as pp

CONFIG = json.loads((ROOT / "examples" / "quick.json").read_text(encoding="utf-8"))

class PortabilityTests(unittest.TestCase):
    def test_dual_result_and_empty_region(self):
        result = cli.solve(CONFIG)
        self.assertEqual(result["status"], "completed")
        self.assertGreater(len(result["refined"]), 0)
        for row in result["coarse"] + result["refined"]:
            self.assertTrue(math.isfinite(row["max_value"]))
            self.assertLessEqual(row["mean_value"], row["max_value"] + 1e-9)
        empty = json.loads(json.dumps(CONFIG))
        empty["params"]["guaranteed_radius"] = 50
        self.assertEqual(cli.solve(empty)["status"], "empty_candidate_region")

    def test_process_results_equal_serial(self):
        tasks = [pp.CaseTask((0., 0.), 0., label="a"), pp.CaseTask((0., 0.), 0., label="b")]
        serial = pp.run_cases_parallel(tasks, pp.ParallelConfig(), base_params=CONFIG["params"])
        parallel = pp.run_cases_parallel(tasks, pp.ParallelConfig(case_workers=2), base_params=CONFIG["params"])
        self.assertEqual([r.label for r in parallel], ["a", "b"])
        for a, b in zip(serial, parallel):
            self.assertTrue(a.ok, a.error_message)
            self.assertTrue(b.ok, b.error_message)
            self.assertEqual(a.result.summary(), b.result.summary())

    def test_copied_directory_with_spaces_and_unicode(self):
        with tempfile.TemporaryDirectory() as temp:
            moved = Path(temp) / "portable 第二问"
            moved.mkdir()
            shutil.copytree(ROOT / "src", moved / "src", ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(ROOT / "run_solver.py", moved)
            config = moved / "input.json"
            config.write_text(json.dumps(CONFIG), encoding="utf-8")
            output = moved / "nested" / "result.json"
            env = dict(os.environ)
            env.pop("PYTHONPATH", None)
            p = subprocess.run([sys.executable, "-X", "utf8", str(moved / "run_solver.py"),
                                "--config", str(config), "--output", str(output)],
                               cwd=temp, env=env, capture_output=True, text=True, encoding="utf-8", timeout=90)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["status"], "completed")

if __name__ == "__main__":
    unittest.main()
