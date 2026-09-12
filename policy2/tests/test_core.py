from dataclasses import replace
import math
import tempfile
import unittest
from pathlib import Path
import numpy as np
from strategy2.config import Config, point
from strategy2.geometry import outer_disk, after_measure, contains, enclosing_circle, guaranteed
from strategy2.coverage import Coverage
from strategy2.state import initial_world, commit
from strategy2.model import Op, Plan, Status
from strategy2.client import Client
from strategy2.simulator import LocalTransport, Target
from strategy2.budget import Stage
from strategy2.beliefs import posterior
from strategy2.gains import Predictor
from strategy2.candidates import Candidates
from strategy2.templates import Templates
from strategy2.q2 import HeuristicQ2
from strategy2.q2 import Q2Result
from strategy2.scheduler import Scheduler
from strategy2.fallback import ClearGrid
from strategy2.geometry import _through


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.cfg = replace(Config(),particles=64,scenarios=8,circle_sides=64).validate()
        self.coverage = Coverage(self.cfg)
        self.world = initial_world(self.cfg)
        self.log = lambda *args,**kwargs: None

    def test_boundary_and_wrapped_bearing_keep_truth(self):
        poly = outer_disk((0,0),1800,64)
        target = np.array([1799.999,-.01])
        for s,error in [((1000.,0.),1.),((1600.,200.),-1.),((1700.,-100.),.9)]:
            b = (math.degrees(math.atan2(target[1]-s[1],target[0]-s[0]))+error)%360
            poly = after_measure(poly,s,"direction",round(b,2)%360,self.cfg)
            self.assertTrue(contains(poly,target)[0])
        self.assertGreater(len(poly),0)

    def test_circle_diameter_is_not_clear_certificate(self):
        p = np.array([[0.,0.],[40.,0.],[20.,20*math.sqrt(3)]])
        center,radius = enclosing_circle(p)
        self.assertGreater(radius,20)
        self.assertFalse(guaranteed(p,center,self.cfg))

    def test_enclosing_circle_matches_exhaustive_small_point_sets(self):
        import itertools
        rng=np.random.default_rng(208)
        for _ in range(30):
            pts=rng.normal(size=(7,2))*100+np.array([1200,-900])
            centers=list(pts)
            centers.extend((a+b)/2 for a,b in itertools.combinations(pts,2))
            centers.extend(_through(a,b,c)[0] for a,b,c in itertools.combinations(pts,3))
            exact=min(float(np.max(np.linalg.norm(pts-c,axis=1))) for c in centers)
            _,actual=enclosing_circle(pts,0)
            self.assertAlmostEqual(actual,exact,places=6)

    def test_shifted_station_coverage_and_per_channel_certificate(self):
        rng = np.random.default_rng(44)
        shifts = []
        for i,b in enumerate(self.coverage.anchors):
            a = rng.uniform(0,2*math.pi)
            shifts.append(point(np.array(b)+80*np.array((math.cos(a),math.sin(a))),self.cfg))
        for p in shifts:
            self.coverage.record_no_signal(20,p)
        self.assertTrue(self.coverage.absent_certificate(20))
        self.assertFalse(self.coverage.absent_certificate(19))
        for angle in np.linspace(0,2*math.pi,720):
            target = 1800*np.array((math.cos(angle),math.sin(angle)))
            self.assertLess(min(math.dist(target,p) for p in shifts),1000)

    def test_config_rejects_invalid_coverage_and_unknown_keys(self):
        with self.assertRaises(ValueError):
            replace(self.cfg,station_offset_m=101).validate()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/"c.json"
            p.write_text('{"particles_typo":1}')
            with self.assertRaises(ValueError):
                Config.load(p)

    def test_external_q2_receives_snapshot(self):
        class MutatingProvider:
            def propose(inner,ch,current,cfg):
                ch.polygon[:]=0
                ch.measured.add((1.,2.))
                return Q2Result(((10.,20.),))
        ch=self.world.channels[1]
        before=ch.polygon.copy()
        candidates=Candidates(self.cfg,self.coverage,MutatingProvider())
        self.assertEqual(candidates.q2(ch,self.world),[(10.,20.)])
        np.testing.assert_array_equal(ch.polygon,before)
        self.assertEqual(ch.measured,set())

    def test_rejected_response_does_not_overwrite_clock(self):
        self.world.virtual_time=123.
        with self.assertRaises(ValueError):
            commit(self.world,self.coverage,self.cfg,"rejected",(0.,0.),Op("measure",1),
                   dict(accepted=False,virtual_time_s=0))
        self.assertEqual(self.world.virtual_time,123.)
        self.assertEqual(self.world.actions,0)

    def test_time_radio_and_idempotent_retry(self):
        local = LocalTransport(targets=[Target(2,300,400,1200)])
        client = Client(local,"local",self.cfg,self.log)
        client.call("/enter")
        op = Op("measure",2)
        prepared = client.prepare("/measure",(300.,400.),op)
        first = local.send(prepared)
        duplicate = local.send(prepared)
        self.assertEqual(first,duplicate)
        response = client.send(prepared)
        self.assertEqual(response["virtual_time_s"],106)
        self.assertTrue(commit(self.world,self.coverage,self.cfg,prepared.request_id,(300.,400.),op,response))
        self.assertFalse(commit(self.world,self.coverage,self.cfg,prepared.request_id,(300.,400.),op,response))
        rid,response = client.call("/clear",(300.,400.),Op("clear",2))
        commit(self.world,self.coverage,self.cfg,rid,(300.,400.),Op("clear",2),response)
        self.assertEqual(self.world.radio,2)
        self.assertEqual(self.world.virtual_time,111)
        _,response = client.call("/clear",(300.,400.),Op("clear",2))
        self.assertEqual(response["virtual_time_s"],114)

    def test_uncertain_transport_reuses_request_body(self):
        local = LocalTransport()
        class DropOnce:
            def __init__(self):
                self.bodies=[]
            def send(inner,prepared,timeout):
                inner.bodies.append(prepared.body)
                response=local.send(prepared)
                if len(inner.bodies)==1:
                    raise TimeoutError("dropped response after server execution")
                return response
        transport=DropOnce()
        client=Client(transport,"local",self.cfg,self.log)
        _,response=client.call("/enter")
        self.assertTrue(response["accepted"])
        self.assertEqual(transport.bodies[0],transport.bodies[1])

    def test_budget_telescopes_and_does_not_reset(self):
        stage=Stage(1,(300.,0.),90,90)
        positions=[(0.,0.),(100.,20.),(210.,-10.)]
        actual=0
        for old,new,service in zip(positions,positions[1:],[6,3]):
            t=math.dist(old,new)/5+service
            actual+=t
            stage.charge(old,new,t,self.cfg)
        spent=actual+math.dist(positions[-1],stage.point)/5-60
        self.assertAlmostEqual(stage.remaining,90-spent)
        self.assertEqual(stage.optional_actions,2)

    def test_prediction_does_not_mutate_and_repeats_have_zero_gain(self):
        response=dict(accepted=True,virtual_time_s=5,measure_result="direction",svd_deg=0.)
        commit(self.world,self.coverage,self.cfg,"a",(0.,0.),Op("measure",1),response)
        ch=self.world.channels[1]
        before=ch.polygon.copy()
        predictor=Predictor(self.cfg,self.coverage)
        self.assertEqual(predictor.atom(self.world,(0.,0.),Op("measure",1)).gain.refine,0)
        predictor.atom(self.world,(600.,200.),Op("measure",1))
        np.testing.assert_array_equal(before,ch.polygon)
        self.assertEqual(len(ch.history),1)
        self.assertEqual(ch.revision,1)

    def test_posterior_respects_negative_clear_and_signal_range(self):
        commit(self.world,self.coverage,self.cfg,"a",(0.,0.),Op("measure",1),
               dict(accepted=True,virtual_time_s=5,measure_result="direction",svd_deg=0.))
        commit(self.world,self.coverage,self.cfg,"b",(750.,0.),Op("clear",1),
               dict(accepted=True,virtual_time_s=158,clear_result="no_target_in_range"))
        belief=posterior(self.world.channels[1],self.cfg)
        self.assertFalse(belief.degraded)
        self.assertTrue(np.all(np.linalg.norm(belief.particles[:,:2]-(750,0),axis=1)>20))
        self.assertTrue(np.all(np.linalg.norm(belief.particles[:,:2],axis=1)<=belief.particles[:,2]))

    def test_all_zero_gain_does_not_skip_mandatory_station(self):
        cfg=replace(self.cfg,detour_budget_s=0,station_offset_m=0)
        # All channels are absent, but only the origin has been examined.
        for c in range(1,21):
            commit(self.world,self.coverage,cfg,str(c),(0.,0.),Op("measure",c),
                   dict(accepted=True,virtual_time_s=6*c,measure_result="no_signal"))
        predictor=Predictor(cfg,self.coverage)
        candidates=Candidates(cfg,self.coverage,HeuristicQ2())
        scheduler=Scheduler(cfg,self.coverage,candidates,Templates(cfg,predictor),predictor,self.log)
        plan=scheduler.next_plan(self.world)
        self.assertEqual(plan.mode,"station")
        self.assertEqual(len(plan.ops),20)
        self.assertFalse(self.world.complete())

    def test_fallback_grid_persists_and_covers_small_polygon(self):
        ch=self.world.channels[1]
        ch.polygon=np.array([[1.,1.],[39.,1.],[39.,39.],[1.,39.]])
        ch.status=Status.KNOWN
        grid=ClearGrid(ch,self.cfg)
        points=[]
        while (p:=grid.next_point(ch)) is not None:
            points.append(p)
            ch.failed_clear.add(p)
        self.assertEqual(len(points),4)
        self.assertEqual(len(set(points)),4)
        for p in ch.polygon:
            self.assertLessEqual(min(math.dist(p,q) for q in points),20)


if __name__ == "__main__":
    unittest.main()
