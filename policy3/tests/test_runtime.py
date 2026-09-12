"""Safety properties at the coverage/state/HTTP boundaries.

HTTP transport doubles exercise retry behavior only; they are not simulator
experiment results and do not replace the real HTTP acceptance suite.
"""
from __future__ import annotations

from dataclasses import replace
from contextlib import redirect_stdout
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

import numpy as np

from strategy3.client import Client
from strategy3.config import Config
from strategy3.coverage import Coverage
from strategy3.model import Action, InconsistentState
from strategy3.state import commit, initial_world


class RuntimeStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = Config().validate()

    def setUp(self):
        self.world = initial_world(self.cfg)
        self.coverage = Coverage(self.cfg)

    def measure(self, point, channel=1, result="no_signal", bearing=None, station_id=None):
        details = {} if station_id is None else {"station_id": station_id}
        action = Action(tuple(point), "measure", channel, details=details)
        response = dict(accepted=True, virtual_time_s=self.world.virtual_time + 5, measure_result=result)
        if bearing is not None:
            response["svd_deg"] = bearing
        return action, response

    def test_all_22_negative_station_observations_certify_absence(self):
        self.assertFalse(self.coverage.absent_certificate(1))
        used = []
        for station in self.coverage.stations:
            if self.world.channels[1].status == "ABSENT":
                break
            action, response = self.measure(station.point, station_id=station.id)
            commit(self.world, self.coverage, self.cfg, f"station-{station.id}", action, response)
            used.append(station.id)
        self.assertTrue(self.coverage.absent_certificate(1))
        self.assertEqual(self.world.channels[1].status, "ABSENT")
        self.assertLessEqual(len(used), 22)
        self.assertFalse(self.world.complete())
        self.assertTrue(all(1 in self.coverage.measured[sid] for sid in used))

    def test_single_negative_does_not_prove_absence(self):
        action, response = self.measure((0., 0.))
        commit(self.world, self.coverage, self.cfg, "single", action, response)
        self.assertEqual(self.world.channels[1].status, "UNKNOWN")
        self.assertFalse(self.coverage.absent_certificate(1))

    def test_positive_channel_cannot_become_absent_from_negative_certificate(self):
        action, response = self.measure((0., 0.), result="direction", bearing=0.)
        commit(self.world, self.coverage, self.cfg, "positive", action, response)
        self.assertEqual(self.world.channels[1].status, "KNOWN")
        action, response = self.measure((300., 10.))
        # Deliberately inject a stale/contradictory certificate to test the state
        # transition guard independently of certificate geometry.
        with patch.object(self.coverage, "absent_certificate", return_value=True):
            commit(self.world, self.coverage, self.cfg, "negative", action, response)
        self.assertEqual(self.world.channels[1].status, "KNOWN")
        self.assertEqual(self.world.channels[1].discovered_at, 5.)

    def test_duplicate_request_id_does_not_commit_twice(self):
        action, response = self.measure((0., 0.))
        self.assertTrue(commit(self.world, self.coverage, self.cfg, "repeat", action, response))
        self.assertFalse(commit(self.world, self.coverage, self.cfg, "repeat", action, response))
        self.assertEqual(self.world.actions, 1)
        self.assertEqual(self.world.virtual_time, 5.)
        self.assertEqual(self.world.channels[1].revision, 1)
        self.assertEqual(len(self.world.channels[1].history), 1)
        self.assertEqual(len(self.coverage.negative[1]), 1)

    def test_repeating_location_with_new_id_costs_action_but_not_independent_evidence(self):
        for rid in ("one", "two"):
            action, response = self.measure((0., 0.))
            self.assertTrue(commit(self.world, self.coverage, self.cfg, rid, action, response))
        self.assertEqual(self.world.actions, 2)
        self.assertEqual(self.world.virtual_time, 10.)
        self.assertEqual(len(self.world.channels[1].history), 1)
        self.assertEqual(self.world.channels[1].revision, 1)
        self.assertEqual(len(self.coverage.negative[1]), 1)

    def test_rejected_response_has_no_state_or_coverage_effect(self):
        action, response = self.measure((0., 0.), station_id=self.coverage.stations[0].id)
        response["accepted"] = False
        with self.assertRaises(ValueError):
            commit(self.world, self.coverage, self.cfg, "rejected", action, response)
        self.assertEqual(self.world.actions, 0)
        self.assertFalse(self.world.committed_ids)
        self.assertFalse(self.world.channels[1].history)
        self.assertFalse(self.coverage.negative[1])
        self.assertFalse(any(self.coverage.measured.values()))

    def test_wrong_station_coordinate_is_rejected_before_any_partial_commit(self):
        station = self.coverage.stations[0]
        action, response = self.measure((station.point[0] + 50., station.point[1] + 50.), station_id=station.id)
        polygon = self.world.channels[1].polygon.copy()
        with self.assertRaises(InconsistentState):
            commit(self.world, self.coverage, self.cfg, "wrong-place", action, response)
        self.assertEqual(self.world.actions, 0)
        self.assertEqual(self.world.position, (0., 0.))
        self.assertEqual(self.world.channels[1].revision, 0)
        self.assertFalse(self.world.channels[1].history)
        self.assertFalse(self.coverage.negative[1])
        self.assertFalse(any(self.coverage.measured.values()))
        np.testing.assert_array_equal(self.world.channels[1].polygon, polygon)

    def test_fixed_location_contradiction_preserves_previous_evidence(self):
        action, response = self.measure((0., 0.), result="direction", bearing=0.)
        commit(self.world, self.coverage, self.cfg, "first", action, response)
        action, response = self.measure((0., 0.), result="direction", bearing=2.)
        with self.assertRaises(InconsistentState):
            commit(self.world, self.coverage, self.cfg, "contradiction", action, response)
        self.assertEqual(self.world.actions, 1)
        self.assertEqual(len(self.world.channels[1].history), 1)
        self.assertEqual(self.world.channels[1].history[0].bearing, 0.)

    def test_completion_requires_all_resolved_or_sixteen_cleared(self):
        for c in range(1, 16):
            self.world.channels[c].status = "CLEARED"
        for c in range(17, 21):
            self.world.channels[c].status = "ABSENT"
        self.assertFalse(self.world.complete())
        self.world.channels[16].status = "KNOWN"
        self.assertFalse(self.world.complete())
        self.world.channels[16].status = "ABSENT"
        self.assertTrue(self.world.complete())
        self.world.channels[16].status = "CLEARED"
        for c in range(17, 21):
            self.world.channels[c].status = "UNKNOWN"
        self.assertTrue(self.world.complete())


class TransportTests(unittest.TestCase):
    def make_client(self, retries=1):
        cfg = replace(Config(), http_retries=retries)
        events = []
        return Client("http://127.0.0.1:1", "local", cfg,
                      lambda event, **data: events.append((event, data))), events

    @staticmethod
    def response(value):
        return io.BytesIO(json.dumps(value).encode("utf-8"))

    def test_retry_uses_same_request_id_and_identical_body(self):
        client, events = self.make_client()
        bodies = []

        def open_request(request, timeout):
            bodies.append(request.data)
            if len(bodies) == 1:
                raise URLError("lost response")
            return self.response(dict(accepted=True, virtual_time_s=5., measure_result="no_signal"))

        with patch.object(client.opener, "open", side_effect=open_request), patch("strategy3.client.time.sleep"):
            rid, response = client.call("/measure", Action((0., 0.), "measure", 1))
        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual(json.loads(bodies[0])["request_id"], rid)
        self.assertIsNone(client.pending)
        self.assertTrue(response["accepted"])
        self.assertEqual(sum(event == "http_retry" for event, _ in events), 1)

    def test_uncertain_request_blocks_new_request_id(self):
        client, _ = self.make_client(retries=0)
        with patch.object(client.opener, "open", side_effect=URLError("lost response")):
            with self.assertRaisesRegex(RuntimeError, "uncertain"):
                client.call("/enter")
            pending = client.pending
            with self.assertRaisesRegex(RuntimeError, "Unresolved"):
                client.call("/enter")
        self.assertIsNotNone(pending)
        self.assertEqual(client.pending, pending)

    def test_rejected_http_response_is_not_returned_for_commit(self):
        client, _ = self.make_client()
        with patch.object(client.opener, "open", return_value=self.response(dict(accepted=False, virtual_time_s=0.))):
            with self.assertRaisesRegex(RuntimeError, "rejected"):
                client.call("/enter")
        self.assertIsNone(client.pending)


class RunnerReportingTests(unittest.TestCase):
    def test_initialization_failure_writes_non_success_summary_with_time_fields(self):
        # No HTTP or simulated observations: this verifies failure reporting only.
        from strategy3.runner import run
        with tempfile.TemporaryDirectory() as temp:
            with patch("strategy3.runner.Coverage", side_effect=ValueError("invalid coverage certificate")), redirect_stdout(io.StringIO()):
                result = run(output=temp)
            summary = json.loads((Path(temp) / "summary.json").read_text(encoding="utf-8"))
            logs = [json.loads(line) for line in (Path(temp) / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertNotEqual(result, 0)
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["reason"], "error")
        self.assertEqual(summary["actions"], 0)
        for name in ("virtual_time_s", "real_runtime_s", "strategy_cpu_time_s", "planning_time_s", "inference_time_s"):
            self.assertTrue(math.isfinite(summary[name]), name)
            self.assertGreaterEqual(summary[name], 0., name)
        self.assertEqual(logs[-1]["event"], "finished")
        self.assertEqual(sorted(log["elapsed_s"] for log in logs), [log["elapsed_s"] for log in logs])


if __name__ == "__main__":
    unittest.main()
