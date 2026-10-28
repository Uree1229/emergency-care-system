"""C1이 기존 문의→회신→공동 배정 엔진에서 실제 실행되는지 확인한다."""
import copy
import json
import unittest

from simulation.sim import CONFIG, Hospital, Patient, Policy, Simulation


class FixedTravelSimulation(Simulation):
    def travel(self, p, hid, loc, leg=0):
        return {'H1':2,'H2':5}[hid]


class CPSATIntegration(unittest.TestCase):
    def run_world(self, code, **kwargs):
        hospitals=[Hospital('H1',(35.17,126.85),{'basic','special'},{'ed_bed':1}),
                   Hospital('H2',(35.18,126.86),{'basic'},{'ed_bed':1})]
        patients=[Patient('A_FLEX',0,(35.16,126.85),{'basic'},{'ed_bed':1},2,5),
                  Patient('B_SCARCE',0,(35.16,126.85),{'special'},{'ed_bed':1},2,5)]
        policy=Policy(rule=code,k=3,wait=0,notify=True,**kwargs)
        config=dict(CONFIG,horizon=1,tail=35,arrival_rate=0,walkin_rate=0,outage_rate=0)
        sim=FixedTravelSimulation(5,policy,config,world=(hospitals,patients,[]),record=True,
                                 response_fn=lambda p,h,attempt:(False,1),outages=[])
        metrics=sim.run()
        return sim,metrics

    def test_cp_sat_runs_on_real_engine_and_preserves_scarce_offer(self):
        sim,metrics=self.run_world('C1',require_backend=True)
        initial=[e for e in sim.log if e['type']=='commit' and e['t']==1]
        self.assertEqual({e['patient']:e['hospital'] for e in initial},{'A_FLEX':'H2','B_SCARCE':'H1'})
        self.assertEqual(len(sim.policy.rule.records),1)
        self.assertTrue(sim.policy.rule.records[0]['optimal_proven'])
        self.assertEqual(sim.policy.spec()['rule'],'C1')
        json.dumps(sim.policy.spec())

    def test_zero_time_limit_uses_validated_fallback_with_engine(self):
        sim,metrics=self.run_world('C1',time_limit_seconds=0,require_backend=True)
        self.assertGreater(sim.policy.rule.fallbacks,0)
        self.assertEqual(sim.policy.rule.records[0]['fallback_reason'],'time_limit_zero')
        for hospital in sim.hospitals.values():
            self.assertTrue(all(hospital.free(resource)>=0 for resource in hospital.cap))


if __name__=='__main__': unittest.main()
