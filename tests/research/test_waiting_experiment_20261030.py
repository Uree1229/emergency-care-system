"""W1 wrapper contracts use unit namespaces only; no final validation execution."""
from copy import deepcopy
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simulation import waiting_experiment_20261030 as w


class WaitingProtocolTests(unittest.TestCase):
    def setUp(self):self.protocol=w.read(w.PROTOCOL)

    def test_known_and_new_partition_locked_without_post_result_selection(self):
        entries=w.planned_entries(self.protocol)
        known=[e for e in entries if e['phase']=='development_known'];new=[e for e in entries if e['phase']=='validation_new']
        self.assertEqual((36,60),(len(known),len(new)))
        self.assertEqual(96,len({(e['phase'],e['scenario_sha256']) for e in entries}))
        self.assertTrue({e['seed'] for e in known}.isdisjoint(e['seed'] for e in new))
        self.assertEqual(60,len({e['seed'] for e in new}));self.assertTrue(all(e['seed'].bit_length()==641 for e in new))
        self.assertTrue(all(set(e['execution_order'])==set(w.CODES) for e in entries))
        self.assertEqual(0,self.protocol['adoption_gates']['no_harm_transfer']['maximum_positive_individual_arrival_capped_difference_minutes'])
        self.assertTrue(self.protocol['no_post_validation_tuning'])

    def test_unit_seed_namespace_cannot_be_a_validation_entry(self):
        real={w.seed_for(self.protocol,p['id'],i) for p in w.read(w.ROOT/'config/final_evaluation_protocol_20261030.json')['profiles'] for i in range(5)}
        unit=w.seed_for({**self.protocol,'seed_namespace':self.protocol['seed_namespace']+'|unit'},'reference',0)
        self.assertNotIn(unit,real)

    def test_known_input_hash_mismatch_is_rejected_before_running(self):
        original=w.read
        def bad(path):
            value=original(path)
            if path==w.ROOT/self.protocol['development_source_lock']:value['entries'][0]['scenario_sha256']='bad'
            return value
        with patch.object(w,'read',side_effect=bad),self.assertRaisesRegex(ValueError,'known input changed'):w.planned_entries(self.protocol)

    def test_existing_output_rejected_before_reading_protocol(self):
        with tempfile.TemporaryDirectory(dir=w.ROOT/'.codex-build') as directory:
            with patch.object(w,'read',side_effect=AssertionError('must not read')),self.assertRaises(FileExistsError):w.run(Path(directory))


class WaitingMetricsTests(unittest.TestCase):
    def test_zero_cap_arrival_loss_remains_a_failure_transition(self):
        base=dict(id='P',priority=4,selected=True,arrived=True,arrival_capped_minutes=180,arrival_elapsed_minutes=180,treatment_start_t=180,treatment_capped_minutes=180)
        after=dict(base,selected=False,arrived=False,arrival_elapsed_minutes=None,treatment_start_t=None)
        value=w.compare([base],[after])
        self.assertEqual(['P'],value['arrived_lost_ids']);self.assertEqual(['P'],value['zero_capped_delta_arrival_loss_ids'])
        self.assertEqual(0,value['all_patient_capped_delta_minutes']['maximum']);self.assertEqual(['P'],value['treatment_started_lost_ids'])


class WaitingEngineTests(unittest.TestCase):
    def unit_case(self):
        profile=w.read(w.ROOT/'config/final_evaluation_protocol_20261030.json')['profiles'][0]
        protocol=w.read(w.PROTOCOL);protocol['seed_namespace']+='|unit-engine'
        return w.inherited.build_case(profile,w.seed_for(protocol,'unit-reference',0))

    def test_actual_four_arms_detached_input_and_age_audit(self):
        scenario=self.unit_case();before=w.canonical(scenario)
        replay=w.run_replay(scenario,['W1','H1','R1','R0'])
        result=w.case_result(replay,[30,60,120])
        self.assertEqual(before,w.canonical(scenario));self.assertTrue(result['audit']['passed'])
        self.assertEqual(list(w.CODES),[b['id'] for b in replay['branches']])
        self.assertEqual(['H1','R1','R0'],replay['control_replay']['execution_order'])
        for decision in replay['branches'][-1]['allocation_decisions']:
            self.assertEqual({'patients','hospitals','free_resources','travel_minutes'},set(decision['inputs']))
            self.assertEqual({p['id']:decision['t']-p['request_t'] for p in decision['inputs']['patients']},decision['waiting_ages'])
        changed=deepcopy(replay)
        if changed['branches'][-1]['allocation_decisions']:
            changed['branches'][-1]['allocation_decisions'][0]['waiting_ages']={}
            with self.assertRaises(ValueError):w.audit_replay(changed)

    def test_full_run_freezes_all_inputs_raw_reaudit_and_preserves_technical_failure(self):
        scenario=self.unit_case();entry=dict(phase='development_known',profile=scenario['stratum'],replicate=0,
            seed=scenario['seed'],scenario_sha256=w.sha(scenario),execution_order=list(w.CODES))
        with tempfile.TemporaryDirectory(dir=w.ROOT/'.codex-build') as directory:
            output=Path(directory)/'run'
            with patch.object(w,'source_hashes',return_value={}),patch.object(w,'planned_entries',return_value=[entry]),patch.object(w.inherited,'seed_inventory',return_value={'passed':True}):
                report=w.run(output)
                self.assertEqual(1,len(report['cases']));self.assertTrue((output/'inputs/000.json').is_file())
                self.assertTrue(w.verify(output)['passed'])
                raw=output/report['raw_index'][0]['path'];raw.write_bytes(b'bad')
                with self.assertRaises(ValueError):w.verify(output)
                failed=Path(directory)/'failed'
                with patch.object(w,'run_replay',side_effect=RuntimeError('technical failure')):
                    with self.assertRaisesRegex(RuntimeError,'technical failure'):w.run(failed)
                capsule=w.read(failed/'failure.json');self.assertFalse(capsule['passed']);self.assertFalse(capsule['selective_reruns'])
                self.assertEqual(1,capsule['planned_cases']);self.assertTrue((failed/'execution-lock.json').is_file())

    def test_every_profile_runs_in_unit_partition_with_unchanged_observation_whitelist(self):
        protocol=w.read(w.PROTOCOL);protocol['seed_namespace']+='|unit-all-profiles'
        for profile in w.read(w.ROOT/'config/final_evaluation_protocol_20261030.json')['profiles']:
            with self.subTest(profile=profile['id']):
                scenario=w.inherited.build_case(profile,w.seed_for(protocol,'unit-'+profile['id'],0))
                replay=w.run_replay(scenario,['R1','W1','R0','H1']);value=w.case_result(replay,[30,60,120])
                self.assertTrue(value['audit']['passed']);self.assertEqual(len(scenario['patients']),value['metrics']['W1']['patients'])
                self.assertIsNone(value['metrics']['W1']['solver_fallback_rate'])
                self.assertEqual(len(replay['branches'][-1]['waiting_records']),value['metrics']['W1']['waiting_calls'])


if __name__=='__main__':unittest.main()
