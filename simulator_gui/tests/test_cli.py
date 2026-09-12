"""Validate a copied directory and the real headless entry point."""
import json
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]

class CLITests(unittest.TestCase):
    def test_moved_server_and_demo_client(self):
        with tempfile.TemporaryDirectory() as temp:
            moved = Path(temp) / "portable 模拟器"
            moved.mkdir()
            for folder in ("src", "examples"):
                shutil.copytree(ROOT / folder, moved / folder, ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
            shutil.copy2(ROOT / "run_server.py", moved)
            log = moved / "results" / "events.jsonl"
            proc = subprocess.Popen([sys.executable, "-I", "-S", "-X", "utf8",
                str(moved / "run_server.py"), "--sources", str(moved / "examples" / "sources.json"),
                "--port", "0", "--log", str(log)], cwd=temp, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8")
            ready = queue.Queue()
            threading.Thread(target=lambda: ready.put(proc.stdout.readline()), daemon=True).start()
            try:
                line = ready.get(timeout=10)
                self.assertTrue(line.startswith("READY "), line)
                address = line.split()[1]
                demo = subprocess.run([sys.executable, "-I", "-S", "-X", "utf8",
                    str(moved / "examples" / "demo_client.py"), "--base-url", address],
                    cwd=temp, capture_output=True, text=True, encoding="utf-8", timeout=10)
                self.assertEqual(demo.returncode, 0, demo.stderr)
                lines = demo.stdout.splitlines()
                self.assertEqual(len(lines), 4)
                clear = json.loads(next(x.split(" ", 1)[1] for x in lines if x.startswith("/clear ")))
                self.assertEqual(clear["clear_result"], "success")
                self.assertGreater(len(log.read_text(encoding="utf-8").splitlines()), 3)
            finally:
                proc.terminate()
                proc.wait(timeout=5)
                proc.stdout.close()

if __name__ == "__main__":
    unittest.main()
