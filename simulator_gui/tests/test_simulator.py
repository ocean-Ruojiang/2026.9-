import json
from pathlib import Path
import sys
import threading
import unittest
from urllib.request import Request, build_opener, ProxyHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from interference_simulator_core import InterferenceSource, SimulatorConfig, SimulatorError, SimulatorState, make_server

def payload(rid, **extra):
    return dict(arena_id="default", robot_id="local", request_id=rid, **extra)

class SimulatorTests(unittest.TestCase):
    def setUp(self):
        self.state = SimulatorState(SimulatorConfig(random_seed=7, expected_robot_id="local"),
                                    [InterferenceSource(1, 100, 0)])
        self.state.start_session()

    def test_http_cycle_and_request_replay(self):
        server = make_server("127.0.0.1", 0, self.state)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        opener = build_opener(ProxyHandler({}))
        def post(path, body):
            req = Request(f"http://127.0.0.1:{server.server_address[1]}{path}",
                          data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            with opener.open(req, timeout=3) as response:
                return json.load(response)
        try:
            self.assertTrue(post("/enter", payload("enter"))["accepted"])
            request = payload("measure", position={"x": 0, "y": 0}, channel=1)
            measured = post("/measure", request)
            self.assertEqual(measured["measure_result"], "direction")
            self.assertEqual(post("/measure", request), measured)
            self.assertEqual(self.state.virtual_time_s, 5.0)
            cleared = post("/clear", payload("clear", position={"x": 100, "y": 0}, channel=1))
            self.assertTrue(cleared["accepted"])
            self.assertTrue(self.state.behavior_history())
            self.assertTrue(post("/exit", payload("exit"))["accepted"])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)

    def test_fixed_error_and_reset_reproducibility(self):
        def measure(rid):
            return self.state.handle("/measure", payload(rid, position={"x": 0, "y": 0}, channel=1))[1]
        self.state.handle("/enter", payload("e1"))
        a, b = measure("m1"), measure("m2")
        self.assertEqual(a["svd_deg"], b["svd_deg"])
        self.state.start_session()
        self.state.handle("/enter", payload("e2"))
        self.assertEqual(a["svd_deg"], measure("m3")["svd_deg"])

    def test_unknown_position_field_and_real_timeout(self):
        self.state.handle("/enter", payload("e"))
        for path in ("/measure", "/clear"):
            code, response = self.state.handle(path, payload(path, channel=1, position={"x": 0, "y": 0, "z": 1}))
            self.assertEqual(code, 200)
            self.assertFalse(response["accepted"])
        before = self.state.virtual_time_s
        self.state._enter_monotonic -= 1201
        _, response = self.state.handle("/measure", payload("late", channel=1, position={"x": 0, "y": 0}))
        self.assertFalse(response["accepted"])
        self.assertEqual(self.state.virtual_time_s, before)

    def test_directional_coverage(self):
        state = SimulatorState(SimulatorConfig(svd_error_deg=0),
                               [InterferenceSource(1, 0, 0, "directional", 1200, 0)])
        state.start_session()
        state.handle("/enter", payload("e"))
        _, inside = state.handle("/measure", payload("a", channel=1, position={"x": 100, "y": 0}))
        _, outside = state.handle("/measure", payload("b", channel=1, position={"x": -100, "y": 0}))
        self.assertEqual(inside["measure_result"], "direction")
        self.assertEqual(outside["measure_result"], "no_signal")

    def test_problem3_validation_and_truth_access(self):
        with self.assertRaises(SimulatorError):
            SimulatorState(SimulatorConfig(problem_mode="problem3"), [InterferenceSource(1, 0, 0)])
        with self.assertRaises(SimulatorError):
            self.state.sources()

if __name__ == "__main__":
    unittest.main()
