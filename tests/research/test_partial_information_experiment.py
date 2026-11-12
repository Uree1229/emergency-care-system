"""Bounded runner tests: no development or validation sweep is executed.

Mocked tiny cases check branch wiring, pairing and provenance. Disposable CLI
artifacts stay in the workspace; existing experiment outputs are never touched.
"""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from simulation import partial_information_experiment as experiment


ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def workspace_tempdir():
    with tempfile.TemporaryDirectory(prefix='.information-runner-test-',dir=ROOT) as name:
        path=Path(name).resolve()
        if not path.is_relative_to(ROOT):
            raise AssertionError('temporary artifact escaped workspace')
        yield path


@contextmanager
def unused_backend():
    """The runner preloads CP-SAT; mocked replays never call its implementation."""
    names=('ortools','ortools.sat','ortools.sat.python','ortools.sat.python.cp_model')
    with patch.dict('sys.modules',{name:ModuleType(name) for name in names}):
        yield


def outcome(pid,arrival,priority=4):
    return dict(id=pid,priority=priority,occur_t=0,search_start_t=0,
        first_commit_t=1 if arrival is not None else None,final_arrive_t=arrival,
        treatment_start_t=arrival+1 if arrival is not None else None)


def raw_branch(code,arrivals):
    return dict(id=code,information_mode=code.split('_')[0],policy={'rule':code.split('_')[1]},
        patient_outcomes=[outcome(pid,at) for pid,at in arrivals],events=[],
        allocation_decisions=[dict(elapsed_seconds=.001)],metrics={'observation_end_t':50})


def replay_fixture(scenario,execution_order):
    arrivals={
        'perfect_R1':[('A',10),('B',20),('C',None),('D',30)],
        'observed_R1':[('D',30),('C',12),('B',None),('A',13)],
        'perfect_H1':[('A',30),('B',35),('C',None),('D',None)],
        'observed_H1':[('C',12),('D',None),('B',40),('A',10)],
        'perfect_C1':[('A',None),('B',None),('C',None),('D',None)],
        'observed_C1':[('D',15),('B',None),('C',None),('A',None)],
    }
    return dict(scenario=deepcopy(scenario),execution_order=list(execution_order),routing_summary={},
        branches=[raw_branch(code,arrivals[code]) for code in experiment.POLICY_IDS])


class PartialInformationExperimentProvenanceTests(unittest.TestCase):
    def test_six_fixed_branches_and_disjoint_three_six_seed_split(self):
        self.assertEqual(experiment.DEV_SEEDS,(17101,17102,17103))
        self.assertEqual(experiment.VALIDATION_SEEDS,(18101,18102,18103,18104,18105,18106))
        self.assertFalse(set(experiment.DEV_SEEDS)&set(experiment.VALIDATION_SEEDS))
        old=json.loads((ROOT/'config/h1_online_lock_20261015.json').read_text(encoding='utf-8'))
        self.assertFalse((set(experiment.DEV_SEEDS)|set(experiment.VALIDATION_SEEDS)) &
                         (set(old['development_seeds'])|set(old['validation_seeds'])))
        self.assertEqual(experiment.POLICY_IDS,tuple(mode+'_'+rule
            for mode in ('perfect','observed') for rule in ('R1','H1','C1')))
        self.assertEqual(len(experiment.FAMILIES),8)
        for spec in experiment.SETTINGS['policies']:
            self.assertEqual((spec['k'],spec['wait'],spec['notify']),(3,2,True))
            if spec['rule'] in ('H1','C1'): self.assertEqual(spec['time_limit_seconds'],.05)
            if spec['rule']=='H1': self.assertEqual(spec['max_moves'],2048)
        self.assertEqual(experiment.SETTINGS['stale_after_minutes'],10)

    def test_sources_settings_runtime_and_both_seed_lists_are_checked_before_replay(self):
        with patch.object(experiment,'source_hashes',return_value={'simulation/a.py':'a'*64}), \
                patch.object(experiment,'runtime_versions',return_value={'python':'test','ortools':'test'}), \
                patch.object(experiment,'build_information_scenarios') as build, \
                patch.object(experiment,'run_information_replay') as replay:
            lock=experiment.manifest()
            self.assertEqual(lock,json.loads(json.dumps(lock)))
            experiment.verify_lock(lock)
            for field in ('source_sha256','settings','runtime_versions','development_seeds',
                          'validation_seeds','families','experiment_id'):
                corrupted=deepcopy(lock)
                if field=='source_sha256': corrupted[field]['simulation/a.py']='b'*64
                elif field=='settings': corrupted[field]['stale_after_minutes']=11
                elif field=='runtime_versions': corrupted[field]['ortools']='changed'
                elif field.endswith('seeds'): corrupted[field][0]+=100
                elif field=='families': corrupted[field].reverse()
                else: corrupted[field]='wrong-experiment'
                with self.subTest(field=field),self.assertRaisesRegex(ValueError,'잠금'):
                    experiment.run('validation',corrupted)
            build.assert_not_called(); replay.assert_not_called()

    def test_current_source_or_setting_change_invalidates_previous_manifest(self):
        with patch.object(experiment,'source_hashes',return_value={'source':'a'*64}), \
                patch.object(experiment,'runtime_versions',return_value={}):
            lock=experiment.manifest()
            with patch.object(experiment,'source_hashes',return_value={'source':'b'*64}), \
                    self.assertRaises(ValueError): experiment.verify_lock(lock)
            settings=deepcopy(experiment.SETTINGS)
            settings['policies'][0]['wait']=0
            with patch.object(experiment,'SETTINGS',settings),self.assertRaises(ValueError):
                experiment.verify_lock(lock)

    def test_manifest_contains_new_observation_engine_audit_and_previous_sources(self):
        hashes=experiment.source_hashes()
        for name in ('simulation/information_observations.py','simulation/partial_information_engine.py',
                     'simulation/partial_information_scenarios.py','simulation/partial_information_audit.py',
                     'simulation/partial_information_experiment.py','simulation/heuristic_assignment.py',
                     'config/heuristic_assignment_selected_20261014.json'):
            self.assertIn(name,hashes)
        self.assertTrue(all(len(value)==64 and int(value,16)>=0 for value in hashes.values()))

    def test_unknown_phase_is_rejected_without_replay(self):
        with patch.object(experiment,'run_information_replay') as replay,self.assertRaises(ValueError):
            experiment.run('not-a-phase')
        replay.assert_not_called()


class PartialInformationExperimentMetricTests(unittest.TestCase):
    def test_raw_events_distinguish_unknown_stale_rejection_and_resource_requests(self):
        events=[dict(type='ask'),dict(type='accept'),dict(type='decline'),
            dict(type='information_wait'),dict(type='information_wait'),dict(type='information_wait'),
            dict(type='information_check',purpose='allocation',reason='UNKNOWN'),
            dict(type='information_check',purpose='allocation',reason='STALE'),
            dict(type='information_check',purpose='allocation',reason='STALE'),
            dict(type='information_check',purpose='inquiry',reason='UNKNOWN'),
            dict(type='information_check',purpose='allocation',reason='FRESH_SHORTAGE'),
            dict(type='information_check',purpose='allocation',reason=None),
            dict(type='resource_confirmation_request'),dict(type='resource_confirmation_timeout'),
            dict(type='confirmation_rejected'),dict(type='confirmation_gate',passed=False),
            dict(type='resource_report_received',applied=False),
            dict(type='resource_report_received',applied=True)]
        value={'events':events,'metrics':{field:999 for field in experiment.INFO_FIELDS}}
        self.assertEqual(experiment.information_metrics(value),dict(information_wait_checks=3,
            resource_confirmation_requests=1,confirmation_rejections=1,ignored_resource_reports=1,
            unknown_allocation_checks=1,stale_allocation_checks=2))
        self.assertEqual(experiment.information_metrics({'events':[]}),
                         {field:0 for field in experiment.INFO_FIELDS})

    def test_common_patient_denominator_preserves_both_one_sided_failures(self):
        replay=replay_fixture({},experiment.POLICY_IDS)
        branches={b['id']:b for b in replay['branches']}
        pair=experiment.common_patient_metrics({},branches['perfect_R1'],branches['observed_R1'])
        self.assertEqual(pair['arrival'],dict(denominator=2,mean_difference_minutes=1.5,
                                             comparison_only=1,baseline_only=1))
        summary=experiment.summary_metrics({},branches['observed_R1'])
        self.assertEqual(summary['arrival_denominator'],3)
        self.assertEqual(summary['not_arrived'],1)
        self.assertEqual(summary['arrival_capped_mean_minutes'],26.25)

    def test_zero_common_denominator_is_none_and_does_not_drop_comparison_only_patient(self):
        by={b['id']:b for b in replay_fixture({},experiment.POLICY_IDS)['branches']}
        pair=experiment.common_patient_metrics({},by['perfect_C1'],by['observed_C1'])['arrival']
        self.assertEqual(pair,dict(denominator=0,mean_difference_minutes=None,
                                   comparison_only=1,baseline_only=0))


class PartialInformationExperimentWiringTests(unittest.TestCase):
    def test_tiny_mock_run_rotates_by_seed_and_family_and_pairs_same_rule(self):
        families=('unit-a','unit-b'); seeds=(7,8)
        def scenarios(seed):
            return [dict(id=f'{seed}-{family}',family=family,seed=seed) for family in families]
        progress=Mock()
        with unused_backend(),patch.object(experiment,'DEV_SEEDS',seeds), \
                patch.object(experiment,'FAMILIES',families), \
                patch.object(experiment,'build_information_scenarios',side_effect=scenarios), \
                patch.object(experiment,'run_information_replay',side_effect=replay_fixture) as replay, \
                patch.object(experiment,'source_hashes',return_value={'unit':'a'*64}), \
                patch.object(experiment,'runtime_versions',return_value={'python':'unit'}):
            report=experiment.run('development',progress=progress)
        ids=experiment.POLICY_IDS
        expected=[ids[offset:]+ids[:offset] for offset in (0,1,1,2)]
        self.assertEqual([call.kwargs['execution_order'] for call in replay.call_args_list],expected)
        self.assertEqual(progress.call_count,4)
        self.assertEqual(progress.call_args.args[:2],(4,4))
        self.assertEqual(len(report['results']),4)
        self.assertEqual(report['seeds'],[7,8])
        for result in report['results']:
            self.assertEqual([b['id'] for b in result['branches']],list(ids))
            self.assertEqual(set(result['summaries']),set(ids))
            self.assertEqual(set(result['information_metrics']),set(ids))
            self.assertEqual(result['paired_vs_perfect']['R1']['arrival']['mean_difference_minutes'],1.5)
            self.assertEqual(result['paired_vs_perfect']['H1']['arrival']['mean_difference_minutes'],-7.5)
            self.assertIsNone(result['paired_vs_perfect']['C1']['arrival']['mean_difference_minutes'])
            self.assertEqual(result['paired_arrival_details']['R1']['denominator'],2)
            self.assertEqual(result['paired_arrival_details']['H1']['earlier'],1)
            self.assertEqual(result['paired_arrival_details']['C1']['denominator'],0)
        self.assertTrue(all(row['policies'][code]['cases']==2 for row in report['summary'] for code in ids))
        json.dumps(report,allow_nan=False)


class PartialInformationExperimentCLITests(unittest.TestCase):
    def test_each_existing_result_companion_is_preserved_before_run(self):
        with workspace_tempdir() as directory:
            output=directory/'result.json'
            for protected in (output,output.with_suffix('.md'),directory/'result_audit.json'):
                protected.write_bytes(b'preserve existing artifact')
                with self.subTest(path=protected.name),patch.object(experiment,'run') as run, \
                        redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as stopped:
                    experiment.main(['--output',str(output)])
                self.assertEqual(stopped.exception.code,2)
                run.assert_not_called()
                self.assertEqual(protected.read_bytes(),b'preserve existing artifact')
                protected.unlink()

    def test_existing_lock_and_validation_relock_are_rejected_before_run(self):
        with workspace_tempdir() as directory:
            output=directory/'new.json'; lock=directory/'lock.json'
            lock.write_bytes(b'preserve existing lock')
            for prefix in ([],['--phase','validation']):
                with self.subTest(prefix=prefix),patch.object(experiment,'run') as run, \
                        redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as stopped:
                    experiment.main(prefix+['--lock','--output',str(output),'--lock-file',str(lock)])
                self.assertEqual(stopped.exception.code,2)
                run.assert_not_called()
                self.assertEqual(lock.read_bytes(),b'preserve existing lock')
                self.assertFalse(output.exists())

    def test_new_output_lock_aliases_and_non_json_reports_are_rejected_before_run(self):
        with workspace_tempdir() as directory:
            output=directory/'result.json'
            arguments=[['--output',str(output),'--lock','--lock-file',str(alias)]
                       for alias in (output,output.with_suffix('.md'),directory/'result_audit.json')]
            arguments.extend([['--output',str(directory/'result.md')],
                              ['--output',str(directory/'result')]])
            for argv in arguments:
                with self.subTest(argv=argv),patch.object(experiment,'run') as run, \
                        redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as stopped:
                    experiment.main(argv)
                self.assertEqual(stopped.exception.code,2)
                run.assert_not_called()
                self.assertEqual(list(directory.iterdir()),[])

    def test_cli_writes_new_json_markdown_audit_and_lock_from_one_audited_result(self):
        report=dict(phase='development',results=[],kind='partial_information_comparison')
        with workspace_tempdir() as directory:
            output=directory/'new.json'; lock=directory/'new_lock.json'
            with patch.object(experiment,'run',return_value=report) as run, \
                    patch.object(experiment,'markdown',return_value='reviewable markdown\n'), \
                    patch.object(experiment,'source_hashes',return_value={}), \
                    patch.object(experiment,'manifest',return_value={'locked':'unit'}), \
                    patch('simulation.partial_information_audit.audit_report',return_value={'passed':True}) as audit, \
                    redirect_stdout(io.StringIO()):
                experiment.main(['--output',str(output),'--lock','--lock-file',str(lock)])
            self.assertEqual(json.loads(output.read_text(encoding='utf-8')),report)
            self.assertEqual(output.with_suffix('.md').read_text(encoding='utf-8'),'reviewable markdown\n')
            self.assertEqual(json.loads((directory/'new_audit.json').read_text()),{'passed':True})
            self.assertEqual(json.loads(lock.read_text()),{'locked':'unit'})
            run.assert_called_once()
            self.assertIs(audit.call_args.args[0],report)


if __name__=='__main__':
    unittest.main()
