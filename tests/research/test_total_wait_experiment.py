"""Check frozen inputs, factorial comparison and non-destructive artifact output."""
from contextlib import redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simulation import total_wait_experiment as experiment

ROOT=Path(__file__).resolve().parents[2]


class TotalWaitExperimentTests(unittest.TestCase):
    def test_disjoint_unused_phase_seeds_and_full_manifest(self):
        current=set(experiment.DEV_SEEDS)|set(experiment.VALIDATION_SEEDS)
        self.assertFalse(set(experiment.DEV_SEEDS)&set(experiment.VALIDATION_SEEDS))
        for path in (ROOT/'config').glob('*lock*.json'):
            if path.name=='total_wait_lock_20261015.json':continue
            previous=json.loads(path.read_text(encoding='utf-8'))
            self.assertFalse(current&(set(previous.get('development_seeds',[]))|set(previous.get('validation_seeds',[]))),path.name)
        self.assertEqual(len(experiment.POLICY_IDS),20);self.assertEqual(len(experiment.FAMILIES),31)
        self.assertEqual(len(experiment.SOURCE_FILES),61)

    def test_invalid_lock_stops_before_any_simulation(self):
        with patch.object(experiment,'source_hashes',return_value={'x':'a'*64}), \
                patch.object(experiment,'runtime_versions',return_value={}), \
                patch.object(experiment,'build_total_wait_scenarios') as build, \
                patch.object(experiment,'run_total_wait_replay') as replay:
            lock=experiment.manifest();experiment.verify_lock(lock)
            for field in ('source_sha256','settings','families','development_seeds','validation_seeds','runtime_versions'):
                changed=deepcopy(lock);changed[field]=None
                with self.subTest(field=field),self.assertRaises(ValueError):experiment.run('validation',changed)
            build.assert_not_called();replay.assert_not_called()

    def test_pairs_separate_same_information_algorithm_and_reporting_effect(self):
        pairs={name:(a,b) for name,a,b in experiment.COMPARISON_PAIRS}
        self.assertEqual(len(pairs),24)
        self.assertEqual(pairs['review_stable_C1_vs_stable'],('stable_responsive_C1','review_stable_C1'))
        self.assertEqual(pairs['total_wait_C1_vs_updates'],('updates_stable_C1','total_wait_C1'))
        self.assertEqual(pairs['total_wait_C1_vs_review'],('review_stable_C1','total_wait_C1'))
        self.assertIn('stable_responsive_C1_vs_responsive',pairs)

    def test_markdown_discloses_patient_harm_and_censoring_even_if_total_improves(self):
        def metric(waits):
            return dict(total_wait_sum_minutes=sum(waits),untreated=1,
                request_to_treatment_capped_sum_minutes=sum(waits)+8,
                patient_details=[dict(total_wait_minutes=n) for n in waits])
        codes=('stable_responsive_C1','review_stable_C1','updates_stable_C1','total_wait_C1')
        case=dict(scenario={'family':'example'},waiting_metrics={code:metric([10,2] if i==0 else [3,4])
                                                               for i,code in enumerate(codes)})
        rendered=experiment.markdown(dict(phase='development',results=[case],families=['example'],limitations=[]))
        self.assertIn('| example | 12 | 7 | 7 | 7 | +5 | 1→1 |',rendered)
        self.assertIn('| example | 1 | 1 | 0 | +5 |',rendered)
        self.assertIn('미처치는 관찰 종료까지 포함',rendered)
        self.assertIn('새 입력 계약',rendered)

    def test_existing_output_and_path_aliases_rejected_before_work(self):
        with tempfile.TemporaryDirectory(prefix='.total-wait-runner-',dir=ROOT) as name:
            target=Path(name)/'result.json'
            for path in (target,target.with_suffix('.md'),target.with_name('result_audit.json')):
                path.write_bytes(b'preserve')
                with patch.object(experiment,'run') as run,redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                    experiment.main(['--output',str(target)])
                run.assert_not_called();self.assertEqual(path.read_bytes(),b'preserve');path.unlink()
            for alias in (target,target.with_suffix('.md'),target.with_name('result_audit.json')):
                with patch.object(experiment,'run') as run,redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                    experiment.main(['--output',str(target),'--lock','--lock-file',str(alias)])
                run.assert_not_called()

    def test_validation_cannot_relock_or_emit_non_json_artifact(self):
        for args in (['--phase','validation','--lock'],['--output','result.md']):
            with patch.object(experiment,'run') as run,redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                experiment.main(args)
            run.assert_not_called()


if __name__=='__main__':unittest.main()
