"""Check locked tie settings, disjoint holdouts and preserved paired cohorts."""
from contextlib import redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simulation import stable_c1_experiment as experiment


ROOT=Path(__file__).resolve().parent


class StableC1ExperimentTests(unittest.TestCase):
    def test_new_phase_seeds_are_disjoint_from_all_previous_locks(self):
        current=set(experiment.DEV_SEEDS)|set(experiment.VALIDATION_SEEDS)
        self.assertFalse(set(experiment.DEV_SEEDS)&set(experiment.VALIDATION_SEEDS))
        for filename in ('partial_information_lock_20261015.json','opportunity_protection_lock_20261015.json',
                         'h1_online_lock_20261015.json','responsive_confirmation_lock_20261015.json'):
            lock=json.loads((ROOT/'config'/filename).read_text(encoding='utf-8'))
            self.assertFalse(current&(set(lock['development_seeds'])|set(lock['validation_seeds'])))
        self.assertEqual(len(experiment.POLICY_IDS),17)
        self.assertEqual(len(experiment.FAMILIES),26)

    def test_validation_rejects_changed_sources_settings_or_phase_before_replay(self):
        with patch.object(experiment,'source_hashes',return_value={'test':'a'*64}), \
                patch.object(experiment,'runtime_versions',return_value={}), \
                patch.object(experiment,'build_stable_c1_scenarios') as build, \
                patch.object(experiment,'run_stable_c1_replay') as replay:
            lock=experiment.manifest();experiment.verify_lock(lock)
            for field in ('source_sha256','settings','development_seeds','validation_seeds','families','runtime_versions'):
                bad=deepcopy(lock);bad[field]=None
                with self.subTest(field=field),self.assertRaises(ValueError):experiment.run('validation',bad)
            build.assert_not_called();replay.assert_not_called()

    def test_pairs_compare_same_confirmation_settings_and_prior_delay_baseline(self):
        pairs={name:(left,right) for name,left,right in experiment.COMPARISON_PAIRS}
        self.assertEqual(len(pairs),19)
        self.assertEqual(pairs['stable_responsive_C1_vs_responsive'],('responsive_C1','stable_responsive_C1'))
        self.assertEqual(pairs['stable_responsive_C1_vs_protected'],('protected_C1','stable_responsive_C1'))
        for mode in ('observed','protected'):
            self.assertEqual(pairs[f'stable_{mode}_C1_vs_{mode}'],(f'{mode}_C1',f'stable_{mode}_C1'))

    def test_common_cohort_weighting_does_not_hide_zero_or_one_sided_arrivals(self):
        def row(n,v):return {'paired_comparisons':{'test':{'metrics':{'arrival':{
            'denominator':n,'mean_difference_minutes':v,'baseline_only':1,'comparison_only':2}}}}}
        self.assertEqual(experiment._paired_mean([row(1,2),row(3,-2),row(0,None)],'test'),(4,-1))
        self.assertEqual(experiment._paired_mean([row(0,None)],'test'),(0,None))

    def test_markdown_reports_individual_harm_even_when_average_is_zero(self):
        case=dict(scenario={'family':'tie'},summaries={k:{'not_arrived':0}
            for k in ('responsive_C1','stable_responsive_C1')},
            paired_comparisons={'stable_responsive_C1_vs_responsive':dict(metrics={'arrival':{
                'denominator':2,'mean_difference_minutes':0}},arrival_details={'earlier':1,'delayed':1})},
            stability_metrics={'stable_responsive_C1':{'stability_tie_replacements':1}})
        report=dict(phase='development',results=[case],families=['tie'],limitations=[])
        text=experiment.markdown(report)
        self.assertIn('| tie | 0 | 0 | 2 | +0.000 | 1 | 1 | 1 |',text)
        self.assertIn('대기 자체는 남을 수 있다',text)

    def test_existing_artifacts_and_path_aliases_are_rejected_before_work(self):
        with tempfile.TemporaryDirectory(prefix='.stable-c1-runner-',dir=ROOT) as name:
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

    def test_validation_relock_and_non_json_output_do_not_start_work(self):
        for args in (['--phase','validation','--lock'],['--output','result.md']):
            with patch.object(experiment,'run') as run,redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                experiment.main(args)
            run.assert_not_called()


if __name__=='__main__':unittest.main()
