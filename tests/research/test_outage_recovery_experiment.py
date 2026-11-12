"""Check locked recovery cohorts, complete ablations and honest tail reporting."""
from contextlib import redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simulation import outage_recovery_experiment as experiment

ROOT=Path(__file__).resolve().parents[2]


class OutageRecoveryExperimentTests(unittest.TestCase):
    def test_unused_disjoint_seeds_and_full_sources(self):
        seeds=set(experiment.DEV_SEEDS)|set(experiment.VALIDATION_SEEDS)
        self.assertFalse(set(experiment.DEV_SEEDS)&set(experiment.VALIDATION_SEEDS))
        for path in (ROOT/'config').glob('*lock*.json'):
            if path.name=='outage_recovery_lock_20261015.json':continue
            old=json.loads(path.read_text(encoding='utf-8'))
            self.assertFalse(seeds&(set(old.get('development_seeds',[]))|set(old.get('validation_seeds',[]))),path.name)
        self.assertEqual(len(experiment.SOURCE_FILES),77)
        self.assertEqual(len(experiment.POLICY_IDS),6);self.assertEqual(len(experiment.FAMILIES),48)

    def test_invalid_lock_prevents_any_replay(self):
        with patch.object(experiment,'source_hashes',return_value={'x':'a'*64}), \
                patch.object(experiment,'runtime_versions',return_value={}), \
                patch.object(experiment,'build_outage_recovery_scenarios') as build, \
                patch.object(experiment,'run_outage_recovery_replay') as replay:
            lock=experiment.manifest();experiment.verify_lock(lock)
            for key in ('source_sha256','settings','runtime_versions','families','development_seeds','validation_seeds'):
                bad=deepcopy(lock);bad[key]=None
                with self.subTest(key=key),self.assertRaises(ValueError):experiment.run('validation',bad)
            build.assert_not_called();replay.assert_not_called()

    def test_pairs_keep_previous_early_and_previous_review_baselines(self):
        pairs={name:(a,b) for name,a,b in experiment.COMPARISON_PAIRS}
        self.assertEqual(len(pairs),9)
        self.assertEqual(pairs['recovery_vs_existing'],('existing_info_C1','outage_recovery_C1'))
        self.assertEqual(pairs['recovery_vs_review'],('review_stable_C1','outage_recovery_C1'))
        self.assertEqual(pairs['recovery_vs_retained'],('retained_backup_C1','outage_recovery_C1'))
        self.assertTrue(all(p['resource_updates'] is False for p in experiment.SETTINGS['policies']))

    def test_markdown_discloses_individual_worsening_even_if_sum_improves(self):
        def metric(waits,times):
            return dict(total_wait_sum_minutes=sum(waits),untreated=0,
                request_to_treatment_capped_sum_minutes=sum(times),
                patient_details=[dict(total_wait_minutes=w,request_to_treatment_capped_minutes=t)
                                 for w,t in zip(waits,times)])
        values={code:metric([10,5],[14,13]) for code in experiment.POLICY_IDS}
        values['outage_recovery_C1']=metric([1,9],[4,18])
        report=dict(phase='development',results=[dict(scenario={'family':'adverse'},waiting_metrics=values)],
                    families=['adverse'],limitations=[])
        rendered=experiment.markdown(report)
        self.assertIn('| adverse | 15 | 15 | 15 | 10 | +5 | 1 | 1 |',rendered)
        self.assertIn('| adverse | +5 | 1 | 1 | 5 | 0→0 |',rendered)
        self.assertIn('추가 자원 변화 보고 0건',rendered)

    def test_existing_paths_and_aliases_do_not_start_work(self):
        with tempfile.TemporaryDirectory(prefix='.outage-recovery-',dir=ROOT) as directory:
            target=Path(directory)/'report.json'
            for path in (target,target.with_suffix('.md'),target.with_name('report_audit.json')):
                path.write_bytes(b'preserve')
                with patch.object(experiment,'run') as run,redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                    experiment.main(['--output',str(target)])
                run.assert_not_called();self.assertEqual(path.read_bytes(),b'preserve');path.unlink()
            for alias in (target,target.with_suffix('.md'),target.with_name('report_audit.json')):
                with patch.object(experiment,'run') as run,redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                    experiment.main(['--output',str(target),'--lock','--lock-file',str(alias)])
                run.assert_not_called()

    def test_validation_cannot_relock_or_use_non_json_path(self):
        for args in (['--phase','validation','--lock'],['--output','x.md']):
            with patch.object(experiment,'run') as run,redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                experiment.main(args)
            run.assert_not_called()


if __name__=='__main__':unittest.main()
