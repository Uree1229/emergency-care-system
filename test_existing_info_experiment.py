"""Check new holdouts and disclosure of adverse outcomes with no new reports."""
from contextlib import redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simulation import existing_info_experiment as experiment

ROOT=Path(__file__).resolve().parent


class ExistingInfoExperimentTests(unittest.TestCase):
    def test_new_disjoint_seeds_and_source_manifest(self):
        seeds=set(experiment.DEV_SEEDS)|set(experiment.VALIDATION_SEEDS)
        self.assertFalse(set(experiment.DEV_SEEDS)&set(experiment.VALIDATION_SEEDS))
        for path in (ROOT/'config').glob('*lock*.json'):
            if path.name=='existing_info_lock_20261015.json':continue
            old=json.loads(path.read_text(encoding='utf-8'))
            self.assertFalse(seeds&(set(old.get('development_seeds',[]))|set(old.get('validation_seeds',[]))),path.name)
        self.assertEqual(len(experiment.SOURCE_FILES),69)
        self.assertEqual(len(experiment.POLICY_IDS),4);self.assertEqual(len(experiment.FAMILIES),39)

    def test_invalid_lock_stops_before_any_new_replay(self):
        with patch.object(experiment,'source_hashes',return_value={'x':'a'*64}), \
                patch.object(experiment,'runtime_versions',return_value={}), \
                patch.object(experiment,'build_existing_info_scenarios') as build, \
                patch.object(experiment,'run_existing_info_replay') as replay:
            lock=experiment.manifest();experiment.verify_lock(lock)
            for key in ('source_sha256','settings','runtime_versions','families','development_seeds','validation_seeds'):
                bad=deepcopy(lock);bad[key]=None
                with self.subTest(key=key),self.assertRaises(ValueError):experiment.run('validation',bad)
            build.assert_not_called();replay.assert_not_called()

    def test_factorial_pairs_include_previous_same_information_review(self):
        pairs={name:(a,b) for name,a,b in experiment.COMPARISON_PAIRS}
        self.assertEqual(len(pairs),5)
        self.assertEqual(pairs['existing_vs_review'],('review_stable_C1','existing_info_C1'))
        specs=experiment.SETTINGS['policies']
        self.assertTrue(all(p['resource_updates'] is False for p in specs))
        self.assertFalse(experiment.SETTINGS['resource_updates']['enabled'])

    def test_markdown_discloses_harm_even_when_total_decreases(self):
        def metric(waits,treatment):
            return dict(total_wait_sum_minutes=sum(waits),untreated=0,
                request_to_treatment_capped_sum_minutes=sum(treatment),
                patient_details=[dict(total_wait_minutes=w,request_to_treatment_capped_minutes=t)
                                 for w,t in zip(waits,treatment)])
        values=[metric([10,5],[14,13]),metric([10,5],[14,13]),
                metric([1,9],[4,18]),metric([1,9],[4,18])]
        case=dict(scenario={'family':'adverse'},waiting_metrics=dict(zip(experiment.POLICY_IDS,values)))
        rendered=experiment.markdown(dict(phase='development',results=[case],families=['adverse'],limitations=[]))
        self.assertIn('| adverse | 15 | 15 | 10 | 10 | +5 | 1 | 1 |',rendered)
        self.assertIn('| adverse | +5 | 1 | 1 | 5 | 0→0 |',rendered)
        self.assertIn('추가 자원 변화 보고는 전 분기에서 0건',rendered)
        self.assertIn('미래 병상 중단',rendered)

    def test_existing_paths_and_aliases_are_rejected_before_work(self):
        with tempfile.TemporaryDirectory(prefix='.existing-info-',dir=ROOT) as directory:
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
