"""Pure bounded-driver contracts. No real scenario, producer or audit execution."""
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock,patch

from inquiry_lab import cohort_wavecap_diagnostic as d


def p(pid='A',selection=100,travel=60,created=120):
    return dict(id=pid,created_at=created,failed=selection is None,selection_seconds=selection,
        estimated_arrival_seconds=None if selection is None else selection+travel,
        travel_seconds=None if selection is None else travel,destination=None if selection is None else 'H',
        inquiry_count=1,contacted=['H'])


def case(index=0,origin='repair',before=None,after=None):
    before=[p()] if before is None else before;after=[p(selection=95)] if after is None else after
    return dict(source_index=index,compact=dict(parent_stage=origin,kind='mix',seed=index,family='synthetic',scenario_id='unit',
        original_origin=None,branches={**{n:dict(patients=deepcopy(before)) for n in d.BASELINES},d.PRIMARY:dict(patients=deepcopy(after))}))


def known(old=None,recent=None):return [case() if old is None else old,case(1,'development') if recent is None else recent]


class BoundedDiagnosticTests(unittest.TestCase):
    def test_both_origins_strict_decrease_and_twenty_seven_checks(self):
        value=d.early_gate(known())
        self.assertTrue(value['passed']);self.assertEqual(len(value['checks']),27)
        self.assertFalse(value['operational_adoption'])

    def test_empty_missing_origin_or_empty_patient_case_cannot_pass(self):
        self.assertFalse(d.early_gate([])['passed'])
        self.assertFalse(d.early_gate([case()])['passed'])
        with self.assertRaises(AssertionError):d.early_gate(known(case(before=[],after=[])))

    def test_duplicate_missing_and_typed_identifiers(self):
        for before,after in (([p('A'),p('A')],[p('A')]),([p('A'),p('B')],[p('A')])):
            with self.assertRaises(AssertionError):d.early_gate(known(case(before=before,after=after)))
        row=case(before=[p(1),p('1')],after=[p(1,95),p('1',95)])
        self.assertTrue(d.early_gate(known(row))['passed'])
        with self.assertRaises(AssertionError):d.early_gate([case(),case()])

    def test_failure_cap300_arrival1800_and_identity_swap(self):
        row=case(before=[p('A'),p('B',None)],after=[p('A',None),p('B',90)])
        v=d.comparison([row],d.TAILWAIT)
        self.assertEqual(v['wait_sum_delta_seconds'],-10)
        self.assertEqual((v['lost_selections'],v['new_selections']),(1,1))
        self.assertEqual(v['maximum_individual_wait_increase_seconds'],200)
        self.assertEqual(v['maximum_individual_arrival_cost_increase_seconds'],1640)
        self.assertFalse(d.early_gate(known(row))['passed'])

    def test_late_created_at_does_not_change_elapsed_arrival(self):
        row=case(before=[p(created=250)],after=[p(selection=95,created=250)])
        v=d.comparison([row],d.TAILWAIT)
        self.assertEqual(v['patient_comparisons'][0]['before_arrival_cost_seconds'],160)
        self.assertEqual(v['patient_comparisons'][0]['after_arrival_cost_seconds'],155)
        self.assertEqual(v['arrival_cost_sum_delta_seconds'],-5)

    def test_cross_slice_gain_cannot_offset_old_wait_loss(self):
        row=case(after=[p(selection=101)])
        value=d.early_gate(known(row,case(1,'development',after=[p(selection=1)])))
        self.assertFalse(value['checks']['repair_wait_not_greater_than_tailwait'])
        self.assertTrue(value['checks']['development_wait_strictly_smaller_than_tailwait'])
        self.assertFalse(value['passed'])

    def test_total_gain_cannot_hide_three_fifteen_fifty_individual_delay(self):
        row=case(before=[p('A',250),p('B',100),p('C',100),p('D',100)],
            after=[p('A',1),p('B',103),p('C',115),p('D',150)])
        value=d.early_gate(known(row));v=value['scopes']['repair']['tailwait']
        self.assertLess(v['wait_sum_delta_seconds'],0)
        self.assertEqual((v['wait_increase_patient_count'],v['wait_increase_sum_seconds'],v['maximum_individual_wait_increase_seconds']),(3,68,50))
        self.assertFalse(value['checks']['repair_individual_wait_not_worse_than_tailwait'])

    def test_both_selected_arrival_harm_fails_even_with_wait_gain(self):
        value=d.early_gate(known(case(after=[p(selection=50,travel=400)])))
        self.assertTrue(value['checks']['repair_wait_strictly_smaller_than_tailwait'])
        self.assertFalse(value['checks']['repair_both_selected_arrival_not_worse_than_tailwait'])
        self.assertFalse(value['passed'])

    def test_pairshield_and_stoploss_are_independent_success_constraints(self):
        for name in (d.PAIRSHIELD,d.STOPLOSS):
            row=case(before=[p('A'),p('B',None)],after=[p('A',95),p('B',None)])
            row['compact']['branches'][name]['patients'][1]=p('B',90)
            value=d.early_gate(known(row))
            label='pairshield' if name==d.PAIRSHIELD else 'stoploss'
            self.assertFalse(value['passed']);self.assertEqual(value['scopes']['repair'][label]['lost_selections'],1)

    def test_pairshield_257_arrival_harm_is_not_excused_by_tailwait_parent(self):
        row=case(before=[p(travel=317)],after=[p(selection=95,travel=317)])
        row['compact']['branches'][d.PAIRSHIELD]['patients']=[p(travel=60)]
        value=d.early_gate(known(row))
        self.assertFalse(value['checks']['repair_both_selected_arrival_not_worse_than_pairshield'])
        self.assertEqual(value['scopes']['repair']['pairshield']['maximum_both_selected_arrival_increase_seconds'],252)

    def test_subset_does_not_claim_original_full_goals(self):
        goals={'original_counts':[9,7,13],'prior_retention_counts':[9,5],'pareto_counts':[17,9]}
        value=d.legacy_assessments(known(),dict(old_full_goals=goals))
        self.assertFalse(value['evaluated']);self.assertEqual(value['goals'],goals)

    def test_alias_disagreement_and_invalid_s_plus_t_rejected(self):
        row=p();row['contacted_hospitals']=['different']
        with self.assertRaises(AssertionError):d.rows([row])
        row=p();row['estimated_arrival_seconds']+=1
        with self.assertRaises(AssertionError):d.rows([row])

    def test_failed_smoke_stops_expansion_before_pool_or_runner_or_new_started(self):
        with tempfile.TemporaryDirectory(dir=d.BUILD/'tmp') as directory:
            b=Path(directory);(b/'tmp').mkdir()
            d.write_new(b/'initial-report.json',dict(early_gate=dict(passed=False)))
            with patch.object(d,'BUILD',b),patch.dict('os.environ',{'TEMP':str(b/'tmp'),'TMP':str(b/'tmp')}), \
                 patch.object(d,'frozen',return_value={}),patch.object(d,'ProcessPoolExecutor',side_effect=AssertionError('pool reached')) as pool, \
                 patch.object(d,'candidate_worker',side_effect=AssertionError('runner reached')) as runner:
                with self.assertRaises(AssertionError):d.execute('expand')
                pool.assert_not_called();runner.assert_not_called()
            self.assertFalse((b/'expand-started.json').exists());self.assertFalse((b/'expand-cases').exists())

    def test_started_failure_preserves_prefix_without_retry_claim(self):
        with tempfile.TemporaryDirectory(dir=d.BUILD/'tmp') as directory:
            b=Path(directory);(b/'initial-cases').mkdir();(b/'initial-proofs').mkdir()
            d.write_new(b/'initial-started.json',{});(b/'initial-cases/a.gz').write_bytes(b'synthetic')
            d.write_new(b/'initial-proofs/a.json',dict(passed=True))
            with patch.object(d,'BUILD',b),patch.object(d,'_execute',side_effect=RuntimeError('synthetic failure')):
                with self.assertRaisesRegex(RuntimeError,'synthetic failure'):d.execute('initial')
            value=d.read(b/'initial-failure.json')
            self.assertEqual((value['raw_prefix_saved'],value['durable_completed_audit_proofs']),(1,1))
            self.assertFalse(value['total_started_semantic_attempt_count_measured']);self.assertFalse(value['automatic_performance_retry'])

    def test_report_and_index_cannot_override_unchanged_durable_proof(self):
        with tempfile.TemporaryDirectory(dir=d.BUILD/'tmp') as directory:
            b=Path(directory);(b/'tmp').mkdir();(b/'initial-proofs').mkdir()
            d.write_new(b/'diagnostic-source-freeze.json',{})
            protocol=dict(source_sha256={'synthetic.py':'pin'},initial_indices=list(d.INITIAL_INDICES),entries=[{} for _ in range(360)])
            cases=[];items=[];proofs=[]
            for i in d.INITIAL_INDICES:
                origin='repair' if i<240 else 'development';c=case(i,origin)
                c.update(file=f'{i:03d}_{i}.json.gz',input_sha256='input'+str(i),raw_sha256='raw'+str(i),compressed_sha256='packed'+str(i),audit=dict(passed=True,new_executions=0))
                entry=dict(kind='mix',seed=i,family='synthetic',scenario_id='unit',parent_stage=origin,original_origin=None,input_sha256=c['input_sha256'])
                protocol['entries'][i]=entry
                item={k:c[k] for k in ('file','source_index','input_sha256','raw_sha256','compressed_sha256')};items.append(item)
                proof_file=c['file']+'.audit.json'
                saved=dict(passed=True,phase='initial',source_index=i,raw_sha256=c['raw_sha256'],compressed_sha256=c['compressed_sha256'],
                    input_sha256=c['input_sha256'],source_map_sha256=d.sha(d.raw(dict(sorted(protocol['source_sha256'].items())))),
                    audit=c['audit'],compact_sha256=d.sha(d.raw(c['compact'])))
                d.write_new(b/'initial-proofs'/proof_file,saved)
                proofs.append(dict(file=c['file'],raw_sha256=c['raw_sha256'],audit=c['audit'],compact_sha256=saved['compact_sha256'],
                    proof_file=proof_file,proof_sha256=d.digest(b/'initial-proofs'/proof_file)));cases.append(c)
            d.write_new(b/'initial-raw-index.json',dict(all_outcomes_saved=True,cases=items))
            # Change the report and index together, retaining the real proof bytes.
            p0=cases[0]['compact']['branches'][d.PRIMARY]['patients'][0]
            p0.update(selection_seconds=94,estimated_arrival_seconds=154)
            proofs[0]['compact_sha256']=d.sha(d.raw(cases[0]['compact']))
            d.write_new(b/'initial-audit-index.json',dict(passed=True,source_sha256=protocol['source_sha256'],cases=proofs))
            report=dict(cases=cases,early_gate=d.early_gate(cases),source_sha256=protocol['source_sha256'],
                source_freeze_sha256=d.digest(b/'diagnostic-source-freeze.json'),raw_index_sha256=d.digest(b/'initial-raw-index.json'),
                audit_index_sha256=d.digest(b/'initial-audit-index.json'))
            self.assertTrue(report['early_gate']['passed']);d.write_new(b/'initial-report.json',report)
            with patch.object(d,'BUILD',b),patch.dict('os.environ',{'TEMP':str(b/'tmp'),'TMP':str(b/'tmp')}), \
                 patch.object(d,'frozen',return_value=protocol),patch.object(d,'ProcessPoolExecutor',side_effect=AssertionError('pool reached')) as pool:
                with self.assertRaises(AssertionError):d.execute('expand')
                pool.assert_not_called()
            self.assertFalse((b/'expand-started.json').exists())

    def test_raw_input_sha_before_audit_and_normalized_compact(self):
        entry=dict(source_index=0,kind='mix',seed=7,family='synthetic',scenario_id='unit',parent_stage='repair',original_origin=None,
            snapshot_sha256='s',environment_sha256='e',baseline_branches={n:dict(patients=[p()]) for n in d.BASELINES})
        scenario=dict(id='unit',family='synthetic',seed=7,patients=[dict(id='A')]);entry['input_sha256']=d.sha(d.raw(scenario))
        replay=dict(scenario=scenario,arms=[[d.NAME,'immediate']],initial_snapshot_sha256='s',environment_sha256='e',
            branches=[dict(policy=d.NAME,rule='immediate',snapshot_sha256='s',environment_sha256='e',patients=[p(selection=95)])])
        body=d.raw(replay);packed=gzip.compress(body,mtime=0)
        with tempfile.TemporaryDirectory(dir=d.BUILD/'tmp') as directory:
            b=Path(directory);(b/'initial-cases').mkdir();(b/'initial-cases/a.gz').write_bytes(packed)
            item=dict(file='a.gz',source_index=0,input_sha256=entry['input_sha256'],raw_sha256=d.sha(body),compressed_sha256=d.sha(packed))
            audit=Mock(return_value=dict(passed=True,new_executions=0));module=types.SimpleNamespace(audit_replay=audit)
            with patch.object(d,'BUILD',b),patch.object(d,'digest',return_value='pin'),patch.object(d.importlib,'import_module',return_value=module):
                value=d.audit_worker(('initial',item,entry,{'inquiry_lab/cohort_wavecap_audit.py':'pin'}))
                self.assertEqual(value['source_index'],0);self.assertEqual(value['compact']['branches'][d.PRIMARY]['patients'],d.rows([p(selection=95)]))
                audit.assert_called_once()
                entry['input_sha256']='changed';audit.reset_mock()
                with self.assertRaises(AssertionError):d.audit_worker(('initial',item,entry,{'inquiry_lab/cohort_wavecap_audit.py':'pin'}))
                audit.assert_not_called()


if __name__=='__main__':unittest.main()
