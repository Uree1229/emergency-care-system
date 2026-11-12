"""Protect seed isolation, locked choices, metric denominators and artifacts."""
from contextlib import redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simulation import responsive_confirmation_experiment as experiment


ROOT=Path(__file__).resolve().parents[2]


class ResponsiveConfirmationExperimentTests(unittest.TestCase):
    def test_new_seeds_do_not_reuse_previous_development_or_validation(self):
        current=set(experiment.DEV_SEEDS)|set(experiment.VALIDATION_SEEDS)
        self.assertFalse(set(experiment.DEV_SEEDS)&set(experiment.VALIDATION_SEEDS))
        for filename in ('partial_information_lock_20261015.json','opportunity_protection_lock_20261015.json',
                         'h1_online_lock_20261015.json'):
            lock=json.loads((ROOT/'config'/filename).read_text(encoding='utf-8'))
            self.assertFalse(current&(set(lock['development_seeds'])|set(lock['validation_seeds'])))
        self.assertEqual(len(experiment.POLICY_IDS),14)
        self.assertEqual(len(experiment.FAMILIES),22)

    def test_validation_rejects_changed_lock_before_scenario_or_replay(self):
        with patch.object(experiment,'source_hashes',return_value={'test':'a'*64}), \
                patch.object(experiment,'runtime_versions',return_value={}), \
                patch.object(experiment,'build_responsive_confirmation_scenarios') as build, \
                patch.object(experiment,'run_responsive_confirmation_replay') as replay:
            lock=experiment.manifest();experiment.verify_lock(lock)
            for field in ('source_sha256','settings','development_seeds','validation_seeds','families','runtime_versions'):
                bad=deepcopy(lock);bad[field]=None
                with self.subTest(field=field),self.assertRaises(ValueError):experiment.run('validation',bad)
            build.assert_not_called();replay.assert_not_called()

    def test_patient_inquiries_resource_requests_and_watch_calls_remain_distinct(self):
        branch=dict(events=[dict(type=k) for k in ('ask','accept','resource_prefetch','resource_prefetch',
            'resource_confirmation_request','resource_watch_check','resource_watch_cycle','resource_watch_cycle',
            'report_driven_wake')],metrics={'confirmation_intervention_wall_seconds':.003})
        self.assertEqual(experiment.responsiveness_metrics(branch),dict(prefetch_checks=2,watch_checks=1,
            watch_cycles=2,report_driven_wakes=1,confirmation_intervention_wall_seconds=.003))
        self.assertEqual(experiment.information_metrics(branch)['resource_confirmation_requests'],1)

    def test_pairs_name_both_baselines_and_each_component(self):
        pairs={name:(left,right) for name,left,right in experiment.COMPARISON_PAIRS}
        self.assertEqual(len(pairs),13)
        for rule in ('H1','C1'):
            self.assertEqual(pairs[f'responsive_{rule}_vs_protected'],(f'protected_{rule}',f'responsive_{rule}'))
            self.assertEqual(pairs[f'responsive_{rule}_vs_observed'],(f'observed_{rule}',f'responsive_{rule}'))
            for mode in ('early','watch'):
                self.assertEqual(pairs[f'{mode}_{rule}_vs_protected'],(f'protected_{rule}',f'{mode}_{rule}'))

    def test_paired_mean_uses_common_arrival_weights_and_excludes_missing_mean(self):
        def row(n,value):return {'paired_comparisons':{'test':{'metrics':{'arrival':{
            'denominator':n,'mean_difference_minutes':value}}}}}
        cases=[row(1,2),row(3,-2),row(0,None)]
        self.assertEqual(experiment._paired_mean(cases,'test'),(4,-1))
        self.assertEqual(experiment._paired_mean([row(0,None)],'test'),(0,None))

    def test_cli_preserves_reports_companions_lock_and_rejects_aliases(self):
        with tempfile.TemporaryDirectory(prefix='.responsive-runner-',dir=ROOT) as name:
            directory=Path(name);target=directory/'result.json'
            for path in (target,target.with_suffix('.md'),directory/'result_audit.json'):
                path.write_bytes(b'preserve')
                with patch.object(experiment,'run') as run,redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                    experiment.main(['--output',str(target)])
                self.assertEqual(path.read_bytes(),b'preserve');run.assert_not_called();path.unlink()
            for alias in (target,target.with_suffix('.md'),directory/'result_audit.json'):
                with patch.object(experiment,'run') as run,redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                    experiment.main(['--output',str(target),'--lock','--lock-file',str(alias)])
                run.assert_not_called()

    def test_cli_rejects_validation_relock_and_wrong_extension_without_work(self):
        for args in (['--phase','validation','--lock'],['--output','result.md']):
            with patch.object(experiment,'run') as run,redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                experiment.main(args)
            run.assert_not_called()


if __name__=='__main__':unittest.main()
