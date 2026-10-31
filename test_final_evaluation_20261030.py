"""New wrapper contracts; test seeds are separate from every final entry."""
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simulation import final_evaluation_20261030 as f


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.protocol=f.read(f.PROTOCOL)

    def test_unique_future_seed_partition_and_disjoint_unit_partition(self):
        p=self.protocol
        final=[f.seed_for(p,x['id'],i) for x in p['profiles'] for i in range(p['replicates'])]
        unit=[f.seed_for(p,'unit-'+x['id'],0) for x in p['profiles']]
        self.assertEqual(36,len(final));self.assertEqual(36,len(set(final)))
        self.assertTrue(set(final).isdisjoint(unit))
        self.assertTrue(all(2**512<=seed<2**513 for seed in final))

    def test_profile_transformations_are_effective_and_deterministic(self):
        for profile in self.protocol['profiles']:
            with self.subTest(profile=profile['id']):
                seed=f.seed_for(self.protocol,'unit-'+profile['id'],0)
                case=f.build_case(profile,seed)
                self.assertEqual(f.canonical(case),f.canonical(f.build_case(profile,seed)))
                self.assertEqual(profile['hospital_count'],len(case['hospitals']))
                offsets={p['occur_t'] for p in case['patients']}
                if profile['arrival']=='simultaneous':self.assertEqual({0},offsets)
                if profile['arrival']=='waves':self.assertEqual({0,3,6},offsets)
                if profile['arrival']=='stream':self.assertEqual(len(case['patients']),len(offsets))
                if profile['change']=='outage':self.assertTrue(case['exogenous_events'])
                if profile['change']=='arrival_condition':
                    self.assertTrue(any('arrival_assessment_adds_capabilities' in p for p in case['patients']))
                for h in case['hospitals']:
                    for resource,capacity in h['capacity'].items():
                        self.assertLessEqual(sum(x['demand'].get(resource,0) for x in h['initial_occupancy']),capacity)

    def test_silence_control_changes_potential_replies_without_policy_execution(self):
        p=deepcopy(self.protocol['profiles'][0]);seed=f.seed_for(self.protocol,'unit-silence',0)
        p['silence']=0;left=f.build_case(p,seed)
        p['silence']=1;right=f.build_case(p,seed)
        a=[r for hospitals in left['response_environment'].values() for h in hospitals.values() for r in h['attempts']]
        b=[r for hospitals in right['response_environment'].values() for h in hospitals.values() for r in h['attempts']]
        self.assertTrue(all(not r['silent'] for r in a));self.assertTrue(all(r['silent'] for r in b))
        self.assertEqual([r['delay'] for r in a],[r['delay'] for r in b])

    def test_local_seed_inventory_detects_collision_and_qualifies_scope(self):
        with tempfile.TemporaryDirectory(dir=f.ROOT/'.codex-build') as folder:
            root=Path(folder);(root/'config').mkdir()
            f.write(root/'config/prior.json',dict(cases=[dict(seed=123),dict(seed=2**300)]))
            with patch.object(f,'ROOT',root):
                result=f.seed_inventory([2**512+4])
                self.assertEqual(301,result['existing_max_seed_bits'])
                self.assertEqual(2,result['existing_unique_seed_count'])
                self.assertIn('Compressed',result['limitations'])
                with self.assertRaises(ValueError):f.seed_inventory([123])


class MetricTests(unittest.TestCase):
    def test_percentiles_and_zero_denominators(self):
        result=f.describe(list(range(1,101)))
        self.assertEqual(95,result['p95']);self.assertEqual(99,result['p99'])
        self.assertEqual(50.5,result['mean']);self.assertIsNone(f.describe([])['mean'])

    def test_lost_arrival_stays_in_paired_capped_metric(self):
        left=[dict(id='P',priority=4,arrived=True,selected=True,arrival_capped_minutes=20,arrival_elapsed_minutes=20)]
        right=[dict(id='P',priority=4,arrived=False,selected=False,arrival_capped_minutes=180,arrival_elapsed_minutes=None)]
        result=f.compare(left,right)
        self.assertEqual(['P'],result['arrived_lost_ids'])
        self.assertEqual(160,result['all_patient_capped_delta_minutes']['maximum'])
        self.assertEqual(160,result['high_priority_maximum_capped_worsening_minutes'])
        self.assertEqual(0,result['common_arrival_delta_minutes']['denominator'])
        with self.assertRaises(ValueError):f.compare(left,[])

    def test_new_output_required_before_reading_protocol(self):
        with tempfile.TemporaryDirectory(dir=f.ROOT/'.codex-build') as folder:
            with self.assertRaises(FileExistsError):f.run(Path(folder))


class EngineTests(unittest.TestCase):
    def test_execution_lock_raw_reaudit_tamper_and_failure_preservation(self):
        protocol=f.read(f.PROTOCOL)
        protocol['seed_namespace']+='|unit-run'
        protocol['profiles']=protocol['profiles'][:1];protocol['replicates']=1
        original_read=f.read
        with tempfile.TemporaryDirectory(dir=f.ROOT/'.codex-build') as folder:
            output=Path(folder)/'run'
            def read_protocol(path):
                return deepcopy(protocol) if path==f.PROTOCOL else original_read(path)
            with patch.object(f,'read',side_effect=read_protocol),patch.object(f,'source_hashes',return_value={}),patch.object(f,'seed_inventory',return_value={'passed':True}):
                report=f.run(output)
                lock=original_read(output/'execution-lock.json')
                self.assertEqual(1,len(lock['entries']));self.assertFalse(lock['selective_reruns'])
                self.assertEqual(lock['entries'][0]['scenario_sha256'],report['cases'][0]['scenario_sha256'])
                self.assertTrue(f.verify(output)['passed'])
                raw=output/report['raw_index'][0]['path']
                raw.write_bytes(b'tampered')
                with self.assertRaises(ValueError):f.verify(output)
                failed=Path(folder)/'failed'
                with patch.object(f,'run_final_assignment_replay',side_effect=RuntimeError('injected failure')):
                    with self.assertRaisesRegex(RuntimeError,'injected failure'):f.run(failed)
                self.assertTrue((failed/'execution-lock.json').exists())
                capsule=original_read(failed/'failure.json')
                self.assertFalse(capsule['passed']);self.assertFalse(capsule['selective_reruns'])
                self.assertFalse((failed/'report.json').exists())

    def test_all_profiles_real_engine_current_decision_auditor_and_independent_event_totals(self):
        protocol=f.read(f.PROTOCOL)
        for profile in protocol['profiles']:
            with self.subTest(profile=profile['id']):
                case=f.build_case(profile,f.seed_for(protocol,'unit-engine-'+profile['id'],0))
                original=f.canonical(case)
                replay=f.run_final_assignment_replay(case,execution_order=['H1','R1','R0'])
                result=f.case_result(replay,protocol['threshold_minutes'])
                self.assertEqual(original,f.canonical(case));self.assertTrue(result['audit']['passed'])
                for branch in replay['branches']:
                    metrics=result['metrics'][branch['id']]
                    events=branch['events'];patients=branch['patient_outcomes']
                    self.assertEqual(sum(e['type']=='ask' for e in events),metrics['inquiries'])
                    self.assertEqual(sum(p['first_commit_t'] is None for p in patients),metrics['unassigned'])
                    self.assertEqual(sum(p['final_arrive_t'] is None for p in patients),metrics['not_arrived'])
                    self.assertEqual(len(case['patients']),metrics['arrival_within_experimental_threshold']['30']['denominator'])
                    self.assertIsNone(metrics['solver_fallback_rate'])
                    self.assertFalse(branch['execution_contract']['wall_time_added_to_simulation_clock'])
                tampered=deepcopy(replay)
                asks=[e for e in tampered['branches'][0]['events'] if e['type']=='ask']
                if asks:
                    tampered['branches'][0]['events'].remove(asks[0])
                    with self.assertRaises(Exception):f.case_result(tampered,protocol['threshold_minutes'])


if __name__=='__main__':unittest.main()
