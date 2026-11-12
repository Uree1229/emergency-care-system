"""New generator -> fixed arms -> raw audit -> vector analysis contracts.

Only separately namespaced unit inputs are used. No population benchmark or
development/validation seed is executed or selected by its performance.
"""
from copy import deepcopy
import gzip
import hashlib
import json
import unittest
from simulation import final_assignment_engine_20261028 as engine
from simulation import final_assignment_scenarios_20261028 as scenarios
from simulation import final_assignment_audit_20261028 as audit
from simulation import final_assignment_analysis_20261028 as analysis

def check(test,stratum):
    seed=scenarios.seed_for('unit',stratum,20261028)
    source=scenarios.build_case(stratum,seed);before=deepcopy(source)
    replay=engine.run_final_assignment_replay(source)
    test.assertEqual(source,before)
    body=json.dumps(replay,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':')).encode('utf-8')
    packed=gzip.compress(body,mtime=0)
    saved=json.loads(gzip.decompress(packed))
    result=audit.audit_case(saved,source_scenario=source,
        scenario_sha256=engine.canonical_sha256(source),expected_order=['R0','R1','H1'])
    test.assertTrue(result['passed'])
    case=dict(case_id='unit_'+stratum,track='main',stratum=stratum,seed=seed,
        scenario_sha256=engine.canonical_sha256(source),raw_file='unit.json.gz',
        raw_sha256=hashlib.sha256(body).hexdigest(),compressed_sha256=hashlib.sha256(packed).hexdigest(),audit=result)
    summary=analysis.analyze([case],bootstrap_samples=30)
    test.assertEqual(summary['cases'],1)
    test.assertEqual(summary['groups'][0]['patients'],len(source['patients']))
    test.assertFalse(summary['whole_online_optimality_claimed'])
    for branch in replay['branches']:
        test.assertTrue(all(e['attempt']<=64 for e in branch['events'] if e['type']=='ask'))

class IntegrationTests(unittest.TestCase):
    def test_single_full_contract(self):check(self,'routine_single')
    def test_stream_full_contract(self):check(self,'routine_stream')
    def test_surge_full_contract(self):check(self,'surge')
if __name__=='__main__':unittest.main(verbosity=2)
