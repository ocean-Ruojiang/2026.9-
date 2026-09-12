import http.client
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from b_sim.core import Simulator, Source, SessionClosed, decode_request, generate_scenario
from b_sim.server import Server


def scenario():
    value = generate_scenario(123,10)
    value["sources"] = [dict(channel=i, x=100.+i*10, y=0., radius=1000., direction=None)
                        for i in range(1,11)]
    return value


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.
        self.sim = Simulator(scenario(), noise="zero", clock=lambda:self.now)
        self.index = 0

    def body(self, **kw):
        self.index += 1
        return dict(arena_id="default",robot_id="local", request_id=str(self.index), **kw)

    def call(self,path,**kw):
        status,value=self.sim.handle(path,self.body(**kw))
        self.assertEqual(status,200)
        self.assertTrue(value["accepted"])
        return value

    def measure(self,x,y,c=1):
        return self.call("/measure",position=dict(x=x,y=y),channel=c)

    def test_official_timing_example(self):
        self.call("/enter")
        self.assertEqual(self.measure(300,400)["virtual_time_s"],105)
        self.assertEqual(self.measure(300,400,2)["virtual_time_s"],111)
        self.assertEqual(self.call("/clear",position=dict(x=300,y=0),channel=3)["virtual_time_s"],194)
        self.assertEqual(self.sim.channel,2)
        self.assertEqual(self.measure(300,0,2)["virtual_time_s"],199)
        self.assertEqual(self.call("/exit")["virtual_time_s"],199)

    def test_range_and_near_boundaries(self):
        self.call("/enter")
        self.assertEqual(self.measure(-890,0)["measure_result"],"direction")
        self.assertEqual(self.measure(-890.00001,0)["measure_result"],"no_signal")
        self.assertEqual(self.measure(105,0)["measure_result"],"near")
        self.assertEqual(self.measure(104.99999,0)["measure_result"],"direction")

    def test_clear_specific_channel_repeat_and_channel_state(self):
        self.call("/enter")
        self.measure(0,0,7)
        result=self.call("/clear",position=dict(x=90,y=0),channel=1)
        self.assertEqual(result["clear_result"],"success")
        self.assertEqual(self.sim.channel,7)
        self.assertEqual(self.sim.cleared,{1})
        self.assertEqual(self.call("/clear",position=dict(x=90,y=0),channel=1)["clear_result"],"no_target_in_range")
        self.assertEqual(self.measure(110,0)["measure_result"],"no_signal")
        self.assertEqual(self.call("/clear",position=dict(x=139.9999,y=0),channel=3)["clear_result"],"success")

    def test_clear_boundary_outside(self):
        self.call("/enter")
        self.assertEqual(self.call("/clear",position=dict(x=89.99999,y=0),channel=1)["clear_result"],"no_target_in_range")

    def test_idempotency_and_conflict(self):
        self.call("/enter")
        b=self.body(position=dict(x=300,y=400),channel=1)
        first=self.sim.handle("/measure",b)
        self.assertEqual(first,self.sim.handle("/measure",b))
        self.assertEqual(self.sim.counts["measure"],1)
        self.assertEqual(self.sim.handle("/clear",b)[0],409)
        b["position"]["x"]=100
        self.assertEqual(self.sim.handle("/measure",b)[0],409)
        self.assertEqual(self.sim.position,(300.,400.))

    def test_invalid_does_not_reserve_id(self):
        self.call("/enter")
        b=self.body(position=dict(x=0,y=0),channel=1.5)
        self.assertEqual(self.sim.handle("/measure",b)[0],400)
        self.assertEqual(self.sim.virtual_us,0)
        b["channel"]=1.0
        self.assertTrue(self.sim.handle("/measure",b)[1]["accepted"])

    def test_unknown_fields_and_identity(self):
        b=self.body(unknown=3)
        self.assertEqual(self.sim.handle("/enter",b)[1]["accepted"],False)
        del b["unknown"]
        b["robot_id"]="wrong"
        self.assertFalse(self.sim.handle("/enter",b)[1]["accepted"])
        b["robot_id"]="local"
        self.assertTrue(self.sim.handle("/enter",b)[1]["accepted"])

    def test_invalid_numbers(self):
        self.call("/enter")
        for v in [float("nan"),float("inf"),2000000.01,True,10**400]:
            self.assertEqual(self.sim.handle("/measure", self.body(position=dict(x=v,y=0),channel=1))[0],400)
        for c in [True,0,21,1.1]:
            self.assertEqual(self.sim.handle("/measure", self.body(position=dict(x=0,y=0),channel=c))[0],400)
        self.assertEqual(self.sim.virtual_us,0)

    def test_noise_fixed_and_bounded_after_rounding(self):
        self.sim.noise="smooth"
        self.call("/enter")
        for i in range(120):
            p=(i*.57-300, i*.23-100)
            a=self.measure(*p)["svd_deg"]
            b=self.measure(*p)["svd_deg"]
            self.assertEqual(a,b)
            true=math.degrees(math.atan2(-p[1],110-p[0]))%360
            self.assertLessEqual(abs((a-true+180)%360-180),1+1e-12)
            self.assertTrue(0<=a<360)

    def test_outside_domain_is_legal(self):
        self.call("/enter")
        self.assertEqual(self.measure(2000000,0)["measure_result"],"no_signal")
        self.assertGreater(self.sim.virtual_us,0)

    def test_real_and_virtual_deadlines(self):
        self.now=400
        response=self.call("/enter")
        self.assertEqual(response["remaining_real_duration_s"],1100)
        self.now=1499
        self.measure(0,0)
        self.assertEqual(self.sim.virtual_us,5_000_000)
        self.now=1500
        with self.assertRaises(SessionClosed):
            self.measure(0,0)
        self.assertEqual(self.sim.end_reason,"window_timeout")

    def test_virtual_last_action_completes(self):
        self.sim.virtual_limit_us=4_000_000
        self.call("/enter")
        self.assertEqual(self.measure(0,0)["virtual_time_s"],5)
        self.assertEqual(self.sim.end_reason,"virtual_timeout")
        with self.assertRaises(SessionClosed):
            self.measure(0,0)

    def test_exit_replay(self):
        self.call("/enter")
        b=self.body()
        result=self.sim.handle("/exit",b)
        self.assertEqual(result,self.sim.handle("/exit",b))
        with self.assertRaises(SessionClosed):
            self.sim.handle("/exit",self.body())

    def test_directional_near_and_clear(self):
        self.sim.sources[1]=Source(1,110,0,1000,0)
        self.call("/enter")
        self.assertEqual(self.measure(106,0)["measure_result"],"no_signal")
        self.assertEqual(self.measure(114,0)["measure_result"],"near")
        self.assertEqual(self.measure(110,100)["measure_result"],"direction")
        self.assertEqual(self.call("/clear",position=dict(x=106,y=0),channel=1)["clear_result"],"success")

    def test_concurrent_action_rejected(self):
        self.sim.lock.acquire()
        try:
            self.assertEqual(self.sim.handle("/enter",self.body())[0],409)
        finally:
            self.sim.lock.release()

    def test_generators_reproducible(self):
        for layout in ["uniform","edge","clustered","mixed"]:
            for seed in range(10):
                a=generate_scenario(seed,layout=layout)
                self.assertEqual(a,generate_scenario(seed,layout=layout))
                sim=Simulator(a)
                for s in sim.sources.values():
                    self.assertLessEqual(math.hypot(s.x,s.y),1800)
                    stops=[(0,0)]+[(1150*math.cos(k*math.pi/3),1150*math.sin(k*math.pi/3)) for k in range(6)]
                    self.assertLessEqual(min(math.dist(p,(s.x,s.y)) for p in stops),1000)

    def test_json_validation(self):
        for data in [b'{"a":1,"a":2}',b'[]',b'{"a":NaN}',b'\xef\xbb\xbf{}',b'{"x":'+b'['*17+b'0'+b']'*17+b'}']:
            with self.assertRaises((ValueError,UnicodeError)):
                decode_request(data)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.sim=Simulator(scenario())
        self.server=Server(self.sim)
        self.server.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def request(self,method,path,body,headers=None):
        c=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=3)
        c.request(method,path,body,headers or {"Content-Type":"application/json"})
        r=c.getresponse()
        value=(r.status,json.loads(r.read()))
        c.close()
        return value

    def test_wire_protocol(self):
        body=json.dumps(dict(arena_id="default",robot_id="local",request_id="1"))
        self.assertEqual(self.request("GET","/enter","")[0],405)
        self.assertEqual(self.request("POST","/enter/",body)[0],404)
        self.assertEqual(self.request("POST","/enter?x=1",body)[0],404)
        self.assertEqual(self.request("POST","/enter",body,{"Content-Type":"text/plain"})[0],415)
        self.assertEqual(self.request("POST","/enter",b'{"a":1,"a":2}')[0],400)
        self.assertEqual(self.request("POST","/enter",b' '*65537)[0],413)
        status,response=self.request("POST","/enter",body)
        self.assertEqual(status,200)
        self.assertEqual(set(response),{"accepted","real_timestamp_ms","virtual_time_s",
                         "max_virtual_duration_s","max_real_duration_s","remaining_real_duration_s"})
        self.assertEqual(self.request("POST","/enter",body)[1],response)


class BatchTests(unittest.TestCase):
    def test_external_strategy_end_to_end(self):
        with tempfile.TemporaryDirectory() as td:
            result=subprocess.run([sys.executable,str(ROOT/"run.py"),"batch","--cases","3",
                    "--workers","2","--radius-mode","minimum","--layout","mixed","--out",td],
                    capture_output=True,text=True,encoding="utf-8",timeout=45)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            summary=json.loads((Path(td)/"summary.json").read_text())
            self.assertEqual(summary["successful_runs"],3)
            for path in Path(td).glob("case_*/summary.json"):
                row=json.loads(path.read_text())
                self.assertEqual(row["clearance_ratio"],1)
                self.assertEqual(row["clear_failure"],0)
                expected=row["move_time_s"]+5*row["measure"]+row["switches"]+5*row["clear_success"]+3*row["clear_failure"]
                self.assertAlmostEqual(row["virtual_time_s"],expected,places=6)

    def test_failure_exported(self):
        with tempfile.TemporaryDirectory() as td:
            result=subprocess.run([sys.executable,str(ROOT/"run.py"),"batch","--cases","1","--out",td,
                    "--command",sys.executable,"-c","raise RuntimeError('test crash')"],
                    capture_output=True,text=True,encoding="utf-8",timeout=10)
            self.assertEqual(result.returncode,1)
            row=json.loads((Path(td)/"case_0001"/"summary.json").read_text())
            self.assertFalse(row["run_ok"])
            self.assertEqual(row["cleared_count"],0)
            self.assertIsNone(row["average_time_per_cleared_s"])

    def test_hanging_strategy_is_stopped_and_saved(self):
        with tempfile.TemporaryDirectory() as td:
            code="from b_sim.client import Client; import time; Client().enter(); time.sleep(20)"
            result=subprocess.run([sys.executable,str(ROOT/"run.py"),"batch","--cases","1",
                    "--real-limit","0.2","--out",td,"--command",sys.executable,"-c",code],
                    capture_output=True,text=True,encoding="utf-8",timeout=10)
            self.assertEqual(result.returncode,1,result.stdout+result.stderr)
            row=json.loads((Path(td)/"case_0001"/"summary.json").read_text())
            self.assertEqual(row["end_reason"],"real_timeout")
            self.assertEqual(row["virtual_time_s"],0)

    def test_replay_keeps_world_and_decisions(self):
        with tempfile.TemporaryDirectory() as td:
            first,second=Path(td)/"first",Path(td)/"second"
            common=[sys.executable,str(ROOT/"run.py"),"batch","--cases","1","--seed","55"]
            for dest,extra in [(first,[]),(second,["--scenario",str(first/"case_0001"/"scenario.json")])]:
                p=subprocess.run(common+["--out",str(dest)]+extra,capture_output=True,
                                 text=True,encoding="utf-8",timeout=15)
                self.assertEqual(p.returncode,0,p.stdout+p.stderr)
            a=json.loads((first/"case_0001"/"summary.json").read_text())
            b=json.loads((second/"case_0001"/"summary.json").read_text())
            for key in ["virtual_time_s","move_distance_m","measure","clear_success","clear_failure","switches"]:
                self.assertEqual(a[key],b[key])


if __name__ == "__main__":
    unittest.main()
