"""전체 이송 비교의 분모·제한 경과시간·검증 잠금 검사."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from simulation.online_allocation_engine import run_online_replay
from simulation.online_allocation_scenarios import build_online_scenarios
from simulation import online_allocation_experiment as experiment


class OnlineAllocationExperiment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases={s['family']:run_online_replay(s) for s in build_online_scenarios(23,
            ['scarce_greedy_trap','scarce_long_detour','no_capacity_control','arrival_assessment_transfer'])}

    def test_transfer_does_not_count_as_new_initial_selection(self):
        case=self.cases['arrival_assessment_transfer']; branch=case['branches'][0]
        summary=experiment.summary_metrics(case['scenario'],branch)
        self.assertGreater(sum(e['type']=='commit' for e in branch['events']),1)
        self.assertEqual(summary['patients'],1)
        self.assertEqual(summary['selection_denominator'],1)
        self.assertEqual(summary['transfers'],1)
        self.assertEqual(summary['arrival_denominator'],1)

    def test_unassigned_control_is_censored_with_zero_success_denominator(self):
        case=self.cases['no_capacity_control']
        for branch in case['branches']:
            summary=experiment.summary_metrics(case['scenario'],branch)
            self.assertEqual(summary['unassigned'],summary['patients'])
            self.assertEqual(summary['arrival_denominator'],0)
            self.assertIsNone(summary['arrival_mean_minutes'])
            self.assertEqual(summary['selection_capped_mean_minutes'],
                branch['metrics']['observation_end_t']-case['scenario']['patients'][0]['search_start_t'])
            self.assertGreater(summary['arrival_capped_mean_minutes'],0)

    def test_long_detour_can_admit_more_people_but_be_slower_for_shared_patient(self):
        case=self.cases['scarce_long_detour']; by={b['id']:b for b in case['branches']}
        pairs=experiment.common_patient_metrics(case['scenario'],by['R1'],by['C1'])
        left=experiment.summary_metrics(case['scenario'],by['R1'])
        right=experiment.summary_metrics(case['scenario'],by['C1'])
        self.assertLess(right['unassigned'],left['unassigned'])
        self.assertEqual(pairs['arrival']['denominator'],1)
        self.assertGreater(pairs['arrival']['mean_difference_minutes'],0)
        self.assertEqual(pairs['arrival']['comparison_only'],1)

    def test_notification_without_redirect_is_not_labelled_failed(self):
        case=deepcopy(self.cases['no_capacity_control']); branch=case['branches'][0]
        branch['events'].append(dict(t=8,type='notify',patient=branch['patient_outcomes'][0]['id']))
        summary=experiment.summary_metrics(case['scenario'],branch)
        self.assertEqual(summary['notifications'],1)
        self.assertEqual(summary['notification_without_redirect'],1)
        self.assertIsNone(summary['notify_to_redirect_mean_minutes'])

    def test_settings_are_json_stable_and_validation_source_changes_rejected(self):
        lock=experiment.manifest(); self.assertEqual(lock,json.loads(json.dumps(lock)))
        experiment.verify_lock(lock)
        changed=deepcopy(lock); changed['settings']['simulation_unit']='seconds'
        with self.assertRaises(ValueError): experiment.verify_lock(changed)
        self.assertFalse(set(experiment.DEV_SEEDS)&set(experiment.VALIDATION_SEEDS))

    def test_export_rejects_development_and_selects_first_seed_without_outcome_scores(self):
        report=dict(phase='development')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'view.json'; source=Path(tmp)/'source.json'
            with self.assertRaises(ValueError): experiment.export_replay(report,path,source)
        # Selection logic uses min(seed), not comparative metric values.
        family='scarce_greedy_trap'; case=deepcopy(self.cases[family])
        case['scenario']['seed']=23
        case['scenario']['family']=family
        later=deepcopy(case); later['scenario']['seed']=24
        later['scenario']['label']='unselected'
        report=dict(phase='validation',kind='online_allocation_comparison',families=[family],
                    limitations=[],results=[later,case])
        with tempfile.TemporaryDirectory(dir=experiment.ROOT/'.codex-build') as tmp:
            path=Path(tmp)/'view.json'; source=Path(tmp)/'source.json'
            source.write_text(json.dumps(report),encoding='utf-8')
            experiment.export_replay(report,path,source)
            data=json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(data['scenarios'][0]['scenario']['seed'],23)
            self.assertIn('source_sha256',data)


if __name__=='__main__': unittest.main()
