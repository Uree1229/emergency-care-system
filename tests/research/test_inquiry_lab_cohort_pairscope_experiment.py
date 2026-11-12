"""Synthetic protocol contracts only; no lab scenario/model/policy execution."""
from contextlib import ExitStack
from copy import deepcopy
import ast
import gzip
import hashlib
import json
from pathlib import Path
import random
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import inquiry_lab
from inquiry_lab import cohort_pairscope_experiment as e


def patient(pid='P01', failed=False, selection=40, created=0):
    return dict(id=pid, created_at=created, failed=failed,
        selection_seconds=None if failed else selection,
        estimated_arrival_seconds=None if failed else selection + 60,
        travel_seconds=None if failed else 60, destination=None if failed else 'H01',
        inquiry_count=1, contacted=['H01'])


def metric(failed=51, wait=53497, count=415):
    return dict(patients=count, successes=count-failed, failed=failed,
                wait_sum_seconds=wait, failed_by_priority={'4': failed, '2': 0, '1': 0})


def compact(seed=1, kind='mix', parent_stage=None, new=None, old=None):
    new = patient(selection=20) if new is None else new
    old = patient(selection=40) if old is None else old
    branches = {e.arm(*pair): dict(patients=[deepcopy(old)]) for pair in e.CONTROLS}
    branches[e.PRIMARY] = dict(patients=[deepcopy(new)])
    return dict(kind=kind, seed=seed, family='synthetic', scenario_id='unit',
                parent_stage=parent_stage, original_origin=None, branches=branches)


class GateTests(unittest.TestCase):
    def assess(self, metrics, origins, ci, stage, **kwargs):
        kwargs.setdefault('paired_metrics', {e.STOPLOSS: dict(lost_selections=0)})
        kwargs.setdefault('origin_paired_metrics', {o: {e.STOPLOSS: dict(lost_selections=0)}
                                                  for o in ('repair','development')})
        return e.assess(metrics, origins, ci, stage, **kwargs)

    def known(self, repair=None, development=None):
        return {'repair': {e.PRIMARY: metric(135, 113296, 827) if repair is None else repair, e.STOPLOSS: metric(135,113429,827)},
                'development': {e.PRIMARY: metric() if development is None else development, e.STOPLOSS: metric(52,54627)}}

    def fresh(self, candidate=None, cs=None, hedge=None, stoploss=None):
        return {e.PRIMARY: metric() if candidate is None else candidate,
                e.COHORT_SUCCESS: metric(51, 53498) if cs is None else cs,
                e.HEDGE: metric(52, 54759) if hedge is None else hedge,
                e.STOPLOSS: metric(52,54627) if stoploss is None else stoploss}

    def test_known_boundaries_with_parent_identity_gate(self):
        result = self.assess({}, self.known(), {}, 'repair')
        self.assertTrue(result['passed'])
        self.assertEqual(len(result['checks']), 9)
        self.assertFalse(any('best' in k or 'pooled' in k for k in result['checks']))

    def test_old827_failure_cannot_be_offset_by_other_slice(self):
        result = self.assess({}, self.known(metric(136, 110000, 827), metric(0, 0)), {}, 'repair')
        self.assertFalse(result['passed'])
        self.assertFalse(result['checks']['repair_failure_goal'])

    def test_old827_wait_cannot_be_offset_by_other_slice(self):
        result = self.assess({}, self.known(metric(134, 113297, 827), metric(0, 0)), {}, 'repair')
        self.assertFalse(result['passed'])
        self.assertFalse(result['checks']['repair_wait_goal'])

    def test_old415_both_success_and_strict_wait_required(self):
        for failed, wait in ((52, 1), (0, 53498)):
            with self.subTest(failed=failed, wait=wait):
                self.assertFalse(self.assess({}, self.known(development=metric(failed, wait)), {}, 'repair')['passed'])

    def test_missing_training_slice_rejected(self):
        result = self.assess({}, {'repair': self.known()['repair']}, {}, 'repair')
        self.assertFalse(result['passed'])
        self.assertFalse(result['checks']['both_observed_training_slices_present'])

    def test_fresh_does_not_add_best_control_requirement(self):
        values = self.fresh()
        values['fixed_3|immediate'] = metric(0, 0)
        result = self.assess(values, {}, {}, 'development')
        self.assertTrue(result['passed'])
        self.assertEqual(len(result['checks']), 7)

    def test_fresh_requires_both_baselines_and_both_endpoints(self):
        for kwargs in ({'candidate': metric(52, 1)}, {'candidate': metric(0, 53498)},
                       {'hedge': metric(50, 60000)}, {'hedge': metric(100, 53497)}):
            with self.subTest(kwargs=kwargs):
                self.assertFalse(self.assess(self.fresh(**kwargs), {}, {}, 'development')['passed'])

    def test_validation_ci_uses_and_not_old_lex_or(self):
        ci = {name: dict(ci95_failed_per_patient=[-1, -0.01],
                         ci95_wait_per_patient=[-2, 0.01]) for name in (e.COHORT_SUCCESS, e.HEDGE, e.STOPLOSS)}
        result = self.assess(self.fresh(), {}, ci, 'validation')
        self.assertFalse(result['passed'])
        self.assertTrue(result['checks']['failure_CI_nonincrease_vs_' + e.COHORT_SUCCESS])
        self.assertFalse(result['checks']['wait_CI_strict_decrease_vs_' + e.COHORT_SUCCESS])

    def test_validation_zero_failure_bound_allowed_wait_zero_rejected(self):
        ci = {name: dict(ci95_failed_per_patient=[0, 0], ci95_wait_per_patient=[-2, -1])
              for name in (e.COHORT_SUCCESS, e.HEDGE, e.STOPLOSS)}
        self.assertTrue(self.assess(self.fresh(), {}, ci, 'validation')['passed'])
        ci[e.HEDGE]['ci95_wait_per_patient'][1] = 0
        self.assertFalse(self.assess(self.fresh(), {}, ci, 'validation')['passed'])
        self.assertFalse(self.assess(self.fresh(), {}, {}, 'validation')['passed'])

    def test_unknown_stage_rejected(self):
        with self.assertRaises(ValueError):
            self.assess({}, {}, {}, 'other')

    def test_missing_parent_identity_pairs_fail_for_each_stage(self):
        known=e.assess({},self.known(),{},'repair')
        self.assertFalse(known['passed'])
        for origin in ('repair','development'):
            self.assertFalse(known['checks'][origin+'_parent_stoploss_success_identities_preserved'])
        for stage in ('development','validation'):
            ci={n:dict(ci95_failed_per_patient=[0,0],ci95_wait_per_patient=[-2,-1])
                for n in (e.COHORT_SUCCESS,e.HEDGE,e.STOPLOSS)}
            value=e.assess(self.fresh(),{},ci,stage)
            self.assertFalse(value['passed'])
            self.assertFalse(value['checks']['success_identities_preserved_vs_'+e.STOPLOSS])

    def test_parent_identity_loss_cannot_be_offset_between_training_slices(self):
        for lost_origin in ('repair','development'):
            pairs={origin:{e.STOPLOSS:dict(lost_selections=int(origin==lost_origin))}
                   for origin in ('repair','development')}
            result=self.assess({},self.known(),{},'repair',origin_paired_metrics=pairs)
            self.assertFalse(result['passed'])
            self.assertFalse(result['checks'][lost_origin+'_parent_stoploss_success_identities_preserved'])
            other='development' if lost_origin=='repair' else 'repair'
            self.assertTrue(result['checks'][other+'_parent_stoploss_success_identities_preserved'])

    def test_each_training_slice_must_strictly_reduce_its_own_parent_wait(self):
        for origin in ('repair','development'):
            values=self.known()
            values[origin][e.STOPLOSS]['wait_sum_seconds']=values[origin][e.PRIMARY]['wait_sum_seconds']
            result=self.assess({},values,{},'repair')
            self.assertFalse(result['passed'])
            self.assertFalse(result['checks'][origin+'_wait_strict_decrease_vs_parent_stoploss'])

    def test_fresh_parent_endpoints_are_required_in_addition_to_CS_and_Hedge(self):
        for parent in (metric(50,60000),metric(100,53497)):
            result=self.assess(self.fresh(stoploss=parent),{}, {},'development')
            self.assertFalse(result['passed'])

    def test_fresh_lost_parent_identity_fails_even_with_endpoint_gains(self):
        result=self.assess(self.fresh(candidate=metric(0,1)),{}, {},'development',
                           paired_metrics={e.STOPLOSS:dict(lost_selections=1)})
        self.assertFalse(result['passed'])
        self.assertFalse(result['checks']['success_identities_preserved_vs_'+e.STOPLOSS])

    def test_validation_Stoploss_CI_is_required_for_both_endpoints(self):
        valid={n:dict(ci95_failed_per_patient=[0,0],ci95_wait_per_patient=[-2,-1])
               for n in (e.COHORT_SUCCESS,e.HEDGE,e.STOPLOSS)}
        self.assertTrue(self.assess(self.fresh(),{},valid,'validation')['passed'])
        for field,upper in (('ci95_failed_per_patient',.01),('ci95_wait_per_patient',0)):
            intervals=deepcopy(valid);intervals[e.STOPLOSS][field][1]=upper
            self.assertFalse(self.assess(self.fresh(),{},intervals,'validation')['passed'])
        intervals=deepcopy(valid);del intervals[e.STOPLOSS]
        self.assertFalse(self.assess(self.fresh(),{},intervals,'validation')['passed'])


class IdentityAccountingTests(unittest.TestCase):
    def test_net_success_gain_and_wait_reduction_cannot_replace_lost_parent_identity(self):
        before=[patient('lost',selection=100),patient('gained1',failed=True),patient('gained2',failed=True)]
        after=[patient('lost',failed=True),patient('gained1',selection=10),patient('gained2',selection=10)]
        row=compact()
        for value in row['branches'].values():value['patients']=deepcopy(before)
        row['branches'][e.PRIMARY]['patients']=after
        result=e.evaluate([dict(compact=row)],'development')
        pair=result['paired']['whole'][e.STOPLOSS]
        self.assertEqual((pair['failed_count_delta'],pair['wait_sum_delta_seconds']),(-1,-380))
        self.assertEqual((pair['lost_selections'],pair['new_selections']),(1,2))
        self.assertEqual(pair['lost_identifiers'][0]['patient'],'lost')
        self.assertFalse(result['assessment']['passed'])
        self.assertFalse(result['assessment']['checks']['success_identities_preserved_vs_'+e.STOPLOSS])

    def test_evaluate_feeds_separate_training_pairs_to_identity_gates(self):
        rows=[dict(compact=compact(seed=1,parent_stage='repair',new=patient(failed=True))),
              dict(compact=compact(seed=2,parent_stage='development',old=patient(failed=True)))]
        with patch.object(e,'regression_outcomes',return_value={}):result=e.evaluate(rows,'repair')
        self.assertFalse(result['assessment']['checks']['repair_parent_stoploss_success_identities_preserved'])
        self.assertTrue(result['assessment']['checks']['development_parent_stoploss_success_identities_preserved'])
        self.assertEqual(result['origin_paired']['repair']['whole'][e.STOPLOSS]['lost_identifiers'][0]['parent_stage'],'repair')
        self.assertEqual(result['metrics'],{})

    def test_evaluate_validation_bootstraps_all_three_comparators_only(self):
        intervals=dict(ci95_failed_per_patient=[0,0],ci95_wait_per_patient=[-2,-1])
        with patch.object(e,'paired_lex_ci',return_value=intervals) as boot:
            result=e.evaluate([dict(compact=compact())],'validation')
        self.assertEqual([c.args[1] for c in boot.call_args_list],[e.COHORT_SUCCESS,e.HEDGE,e.STOPLOSS])
        self.assertEqual(set(result['whole_lex_ci']),{e.COHORT_SUCCESS,e.HEDGE,e.STOPLOSS})
        self.assertTrue(result['assessment']['passed'])

    def test_identity_requirement_is_parent_specific_accounting_metadata(self):
        self.assertTrue(e.SETTINGS['individual_success_identity_preservation_is_hard_gate'])
        self.assertEqual(e.SETTINGS['parent_success_identity_baseline'],e.STOPLOSS)
        self.assertIn('accounting-only',e.SETTINGS['parent_success_identity_scope'])

    def test_pairscope_factor_is_explicit_and_identity_anchor_is_unchanged(self):
        self.assertTrue(e.SETTINGS['no_tail_first_pair_evaluation'])
        self.assertTrue(e.SETTINGS['expected_future_queue_envelope'])
        self.assertIn('first-contact pairs that have no tail',e.SETTINGS['changed_factor'])
        self.assertTrue(e.SETTINGS['protected_wait_first_selection'])
        self.assertEqual(e.SETTINGS['protected_selection_order'],
            'admitted minimum total capped-wait band, then unchanged tier probability tie rules, then embedded Stoploss anchor')
        self.assertEqual(e.SETTINGS['parent_success_identity_baseline'],e.STOPLOSS)

    def test_parent_seal_pin_matches_actual_immutable_parent_bytes(self):
        self.assertEqual(e.EXPECTED_PARENT,e.file_sha(e.PARENT/'verification.json'))


class ArithmeticTests(unittest.TestCase):
    def test_arrival_is_elapsed_and_raw_compact_aliases_match(self):
        row=patient(created=120,selection=40)
        rawrow=dict(row);rawrow['destination_hospital_id']=rawrow.pop('destination')
        rawrow['contacted_hospitals']=rawrow.pop('contacted')
        self.assertEqual(e.patient_rows([row]),e.patient_rows([rawrow]))
        value=e.scope_metrics([compact(new=row)],(e.PRIMARY,))[e.PRIMARY]
        self.assertEqual(value['J'],100)
        self.assertEqual(value['arrival_cost_sum_seconds'],100)
        self.assertNotEqual(value['J'],-20)  # created_at is not subtracted twice.

    def test_arrival_harm_report_does_not_change_wait_gate(self):
        old=patient(selection=40);new=patient(selection=20)
        new.update(travel_seconds=200,estimated_arrival_seconds=220,destination='H02')
        c=compact(new=new,old=old)
        pair=e.paired([c],e.HEDGE)
        self.assertEqual(pair['wait_sum_delta_seconds'],-20)
        self.assertEqual(pair['maximum_both_selected_arrival_increase_seconds'],120)
        self.assertEqual(pair['J_mean_delta_seconds'],120)
        result=e.evaluate([dict(compact=c)],'development')
        self.assertTrue(result['assessment']['passed'])

    def test_patient_projection_failure_cap_and_tiers(self):
        a = compact(new=patient(failed=True), old=patient(selection=100))
        values = e.scope_metrics([a], (e.PRIMARY, e.HEDGE))
        self.assertEqual(values[e.PRIMARY]['wait_sum_seconds'], 300)
        self.assertEqual(values[e.PRIMARY]['failed_by_priority'], {'4': 1, '2': 0, '1': 0})
        self.assertEqual(values[e.HEDGE]['wait_sum_seconds'], 100)
        for invalid in (patient(failed=False, selection=301), patient(failed=False, selection=True)):
            with self.assertRaises(AssertionError):
                e.patient_rows([invalid])
        with self.assertRaises(AssertionError):
            e.patient_rows([patient(), patient()])

    def test_identity_key_contains_origin_family_and_seed(self):
        cases = [compact(7, 'family:a', 'repair', patient(failed=True), patient()),
                 compact(7, 'family:b', 'repair', patient(), patient(failed=True)),
                 compact(7, 'family:a', 'development', patient(), patient(failed=True))]
        value = e.paired(cases, e.HEDGE)
        self.assertEqual((value['lost_selections'], value['new_selections']), (1, 2))
        keys = [(r['parent_stage'], r['kind'], r['seed'], r['patient'])
                for r in value['lost_identifiers'] + value['gained_identifiers']]
        self.assertEqual(len(set(keys)), 3)
        self.assertEqual(value['wait_sum_delta_seconds'], -260)
        self.assertEqual(value['maximum_individual_wait_increase_seconds'], 260)

    def test_ci_cluster_sums_and_rng_reference(self):
        rows = [compact(1, new=patient(selection=20), old=patient(selection=40)),
                compact(2, new=patient(failed=True), old=patient(selection=100))]
        # Add a second patient in the second scenario so denominators matter.
        for name in rows[1]['branches']:
            rows[1]['branches'][name]['patients'].append(patient('P02', selection=50))
        result = e.paired_lex_ci(rows, e.HEDGE, replicates=100, seed=123)
        rng = random.Random(123)
        fs, ws = [], []
        data = [(1, 0, -20), (2, 1, 200)]
        for _ in range(100):
            chosen = [data[rng.randrange(2)] for _ in range(2)]
            n = sum(r[0] for r in chosen)
            fs.append(sum(r[1] for r in chosen)/n)
            ws.append(sum(r[2] for r in chosen)/n)
        self.assertEqual(result['ci95_failed_per_patient'], [sorted(fs)[2], sorted(fs)[96]])
        self.assertEqual(result['ci95_wait_per_patient'], [sorted(ws)[2], sorted(ws)[96]])
        self.assertEqual(result['confidence_scope'], e.CONFIDENCE_SCOPE)

    def test_known_evaluation_keeps_two_origins_without_pooled_metric(self):
        cases = [dict(compact=compact(1, parent_stage='repair')),
                 dict(compact=compact(1, parent_stage='development'))]
        with patch.object(e, 'paired_lex_ci', side_effect=AssertionError('CI is validation only')), \
             patch.object(e, 'regression_outcomes', return_value={}):
            result = e.evaluate(cases, 'repair')
        self.assertEqual(result['metrics'], {})
        self.assertEqual(result['whole_lex_ci'], {})
        self.assertEqual(set(result['origin_metrics']), {'repair', 'development'})
        for origin in result['origin_metrics'].values():
            self.assertEqual(origin['whole'][e.PRIMARY]['patients'], 1)

    def test_legacy_identity_targets_are_reports_with_complete_keys(self):
        row=compact(seed=7,kind='mix',parent_stage='repair',new=patient(failed=True))
        targets={'old_six':[dict(parent_stage='repair',original_origin='repair',kind='mix',seed=7,patient='P01')]}
        result=e.regression_outcomes([dict(compact=row)],targets)
        self.assertFalse(result['old_six'][0]['selected'])
        self.assertEqual(result['old_six'][0]['kind'],'mix')
        self.assertIsNone(result['old_six'][0]['estimated_arrival_seconds'])

    def test_parent_identity_target_derivation_preserves_declared_groups(self):
        entry=dict(parent_stage='repair',original_origin='repair',kind='mix',seed=7,
                   branches={e.HEDGE:dict(patients=[patient()])})
        inherited={'old_six':[dict(parent_stage='repair',original_origin='repair',kind='mix',seed=7,patient='P01')]}
        targets=e.tracked_targets([entry],inherited)
        self.assertEqual(targets,inherited)
        self.assertIsNot(targets,inherited)
        wrong=deepcopy(inherited);wrong['old_six'][0]['parent_stage']='development'
        with self.assertRaises(AssertionError):e.tracked_targets([entry],wrong)

    def test_development_does_not_compute_validation_ci(self):
        with patch.object(e, 'paired_lex_ci', side_effect=AssertionError('CI is validation only')):
            result = e.evaluate([dict(compact=compact())], 'development')
        self.assertEqual(result['whole_lex_ci'], {})
        self.assertTrue(result['assessment']['passed'])


class ShapeAndFreezeTests(unittest.TestCase):
    def test_seven_numerical_and_gate_functions_are_parent_ast_exact(self):
        names = ('wait', 'scope_metrics', 'paired', 'paired_lex_ci', 'regression_outcomes', 'assess', 'evaluate')
        def functions(path):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            return {node.name: ast.dump(node, include_attributes=False)
                    for node in tree.body if isinstance(node, ast.FunctionDef)}
        prior = functions(e.ROOT / 'inquiry_lab/cohort_pareto_experiment.py')
        current = functions(Path(e.__file__))
        self.assertEqual({name: current[name] for name in names},
                         {name: prior[name] for name in names})

    @staticmethod
    def synthetic_seeds(role, count):
        bases = {'development-mix': 100000, 'validation-mix': 200000,
                 'development-stress': 300000, 'validation-stress': 400000}
        return tuple(bases[role] + i for i in range(count))

    def test_tasks_have_exact_shapes_without_real_namespace_seeds(self):
        entries = [dict(kind='mix', seed=i) for i in range(360)]
        with patch.object(e, 'seeds', side_effect=self.synthetic_seeds), \
             patch.object(e, 'control_ledger', return_value=dict(entries=entries)):
            repair, dev, validation = [e.tasks(s) for s in e.ORDER]
        self.assertEqual((len(repair), len(dev), len(validation)), (360, 120, 368))
        self.assertTrue(all(x[2] == e.REPAIR_ARMS for x in repair))
        self.assertTrue(all(len(x[2]) == 23 for x in dev + validation))
        self.assertEqual(sum(len(x[2]) for x in repair + dev + validation), 11584)
        self.assertEqual(len({x[1] for x in dev + validation}), 425)

    def test_reserved_inputs_include_unexecuted_validation(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            build = root/'build'; (build/'preservation').mkdir(parents=True)
            prior = root/'old'; prior.mkdir()
            (prior/'input-manifest.json').write_bytes(e.raw(dict(entries=[
                dict(stage='validation', seed=12345), dict(stage='development', seed=45)])))
            (prior/'source-freeze.json').write_bytes(e.raw(dict(protocol=dict(namespace='reserved'))))
            (build/'preservation/before.json').write_bytes(e.raw(dict(strict_reference_sha256={
                'old/input-manifest.json': 'bound', 'old/source-freeze.json': 'bound'})))
            with patch.object(e, 'ROOT', root), patch.object(e, 'BUILD', build), \
                 patch.object(e, 'control_ledger', return_value=dict(entries=[dict(seed=2)])):
                self.assertEqual(e.reserved_seeds(), {2, 45, 12345})
                self.assertEqual(e.reserved_namespaces(), {'reserved'})

    def run_synthetic_freeze(self, overlap=False):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            build = Path(directory); (build/'preservation').mkdir(); (build/'preflight').mkdir()
            (build/e.LEDGER_FILE).write_bytes(b'{}')
            (build/'preservation/before.json').write_bytes(b'{}')
            sources = {'synthetic.py': 'digest'}
            protocol = dict(source_sha256=sources)
            counts = {'test_inquiry_lab_cohort_pairscope_' + name: 1
                      for name in ('policy', 'context', 'model', 'runner', 'audit', 'experiment')}
            (build/'tests-output.txt').write_text('Synthetic test execution output.\n',encoding='utf-8')
            (build/'tests.json').write_bytes(e.raw(dict(passed=True, errors=0, failures=0,
                skipped=0, module_counts=counts, source_sha256_before=sources, source_sha256_after=sources)))
            (build/'preflight/parent-equivalence.json').write_bytes(e.raw(dict(passed=True,
                full_raw_equal=True, controls=[list(a) for a in e.CONTROLS],
                formal_performance_runs=0, used_to_tune_policy_or_gates=False,
                source_sha256_before=sources,source_sha256_after=sources,
                candidate_checks=[dict(label=label,file=label+'.gz',audit=dict(passed=True),
                    compressed_sha256=e.sha(b'synthetic bytes')) for label in ('saved_seed7','family_seed7')])))
            for label in ('saved_seed7','family_seed7'):
                (build/'preflight'/(label+'.gz')).write_bytes(b'synthetic bytes')
            known = [dict(kind='mix', seed=i, parent_stage='repair' if i<240 else 'development',
                          scenario_sha256='old-input') for i in range(360)]
            calls = []
            def fake_build(kind, seed):
                self.assertTrue((build/'source-freeze.json').exists())
                calls.append((kind, seed))
                return {'synthetic_input_only': True, 'kind': kind, 'seed': seed}
            fake = types.ModuleType('inquiry_lab.harness'); fake.build = fake_build
            with ExitStack() as stack:
                stack.enter_context(patch.object(e, 'BUILD', build))
                stack.enter_context(patch.object(e, 'protocol', return_value=protocol))
                stack.enter_context(patch.object(e, 'control_ledger', return_value=dict(entries=known)))
                stack.enter_context(patch.object(e, 'seeds', side_effect=self.synthetic_seeds))
                stack.enter_context(patch.object(e, 'reserved_seeds', return_value={100000} if overlap else set()))
                stack.enter_context(patch.object(e, 'reserved_namespaces', return_value=set()))
                stack.enter_context(patch.object(inquiry_lab, 'harness', fake, create=True))
                if overlap:
                    with self.assertRaises(AssertionError):
                        e.freeze()
                    self.assertFalse((build/'source-freeze.json').exists())
                    self.assertEqual(calls, [])
                else:
                    e.freeze()
                    manifest = e.read(build/'input-manifest.json')
                    self.assertEqual(len(manifest['entries']), 848)
                    self.assertEqual(len(calls), 488)
                    self.assertEqual({x['parent_stage'] for x in manifest['entries'][:360]}, {'repair', 'development'})

    def test_freeze_writes_source_before_stub_inputs(self):
        self.run_synthetic_freeze()

    def test_overlap_rejected_before_any_stub_inputs(self):
        self.run_synthetic_freeze(overlap=True)


class SavedCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.temp.name)
        self.build = self.root/'new'; self.build.mkdir()
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(e, 'BUILD', self.build))
        self.stack.enter_context(patch.object(e, 'source_hashes', return_value={'source.py': 'pinned'}))
        self.stack.enter_context(patch.object(e, 'verify_freeze', return_value=dict(protocol=dict(source_sha256={'source.py':'pinned'}))))
        self.stack.enter_context(patch.object(e, 'verify_prior', return_value={}))
        class InlinePool:
            def __init__(self, **kwargs):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def map(self,fn,items,**kwargs):return map(fn,items)
        self.stack.enter_context(patch.object(e,'ProcessPoolExecutor',InlinePool))
        self.audit = Mock(return_value=dict(passed=True, new_executions=0, synthetic_only=True))
        module = types.ModuleType('inquiry_lab.cohort_pairscope_audit'); module.audit_replay = self.audit
        self.stack.enter_context(patch.dict(sys.modules, {'inquiry_lab.cohort_pairscope_audit': module}))
        self.todo, self.known = [], []
        self.stack.enter_context(patch.object(e, 'tasks', side_effect=lambda stage: self.todo))
        self.stack.enter_context(patch.object(e, 'control_ledger', side_effect=lambda: dict(entries=self.known)))

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    def save(self, stage='repair', count=2):
        folder = self.build/(stage+'-cases'); folder.mkdir()
        metadata, manifest = [], []
        for pos in range(count):
            seed = 100 + pos
            arms = e.REPAIR_ARMS if stage=='repair' else e.ARMS
            parent_stage = ('repair' if pos==0 else 'development') if stage=='repair' else None
            scenario = dict(id='synthetic-'+str(pos), seed=seed, family='unit', horizon_seconds=300,
                            patients=[dict(id='P01')])
            branches = [dict(policy=p, rule=q, snapshot_sha256='snapshot',
                environment_sha256='environment', patients=[patient(selection=30+pos)]) for p,q in arms]
            replay = dict(scenario=scenario, arms=[list(a) for a in arms], branches=branches,
                          initial_snapshot_sha256='snapshot', environment_sha256='environment')
            body=e.raw(replay); packed=gzip.compress(body, mtime=0); name=f'{pos:03d}_{seed}.json.gz'
            (folder/name).write_bytes(packed)
            metadata.append(dict(file=name, raw_sha256=e.sha(body), compressed_sha256=e.sha(packed)))
            manifest.append(dict(stage=stage,parent_stage=parent_stage,kind='mix',seed=seed,sha256=e.sha(e.raw(scenario))))
            self.todo.append(('mix',seed,arms))
            self.known.append(dict(parent_stage=parent_stage, original_origin=None, kind='mix',seed=seed,
                scenario_sha256=e.sha(e.raw(scenario)),initial_snapshot_sha256='snapshot',environment_sha256='environment',
                branches={e.arm(*p):dict(patients=[patient(selection=40+pos)]) for p in e.CONTROLS}))
        (self.build/(stage+'-raw-index.json')).write_bytes(e.raw(dict(cases=metadata,all_outcomes_saved=True)))
        (self.build/'input-manifest.json').write_bytes(e.raw(dict(entries=manifest)))
        return metadata

    def test_multiple_known_origins_and_once_only_semantic_audit(self):
        self.save()
        first=e.stage_cases('repair',allow_new_audit=True)
        second=e.stage_cases('repair')
        self.assertEqual(first,second)
        self.assertEqual(self.audit.call_count,2)
        self.assertEqual([x['parent_stage'] for x in first],['repair','development'])
        self.assertEqual([x['compact']['branches'][e.PRIMARY]['patients'][0]['selection_seconds'] for x in first],[30,31])
        self.assertEqual([x['compact']['branches'][e.HEDGE]['patients'][0]['selection_seconds'] for x in first],[40,41])

    def test_fresh23_arms_have_no_cached_parent_projection(self):
        self.save('development')
        values=e.stage_cases('development',allow_new_audit=True)
        self.assertEqual(len(values[1]['compact']['branches']),23)
        self.assertIsNone(values[1]['parent_stage'])
        self.assertEqual(self.audit.call_count,2)

    def test_durable_proofs_reused_if_aggregate_collection_was_interrupted(self):
        self.save();e.stage_cases('repair',allow_new_audit=True)
        e.semantic_path('repair').unlink()
        with patch.object(e,'ProcessPoolExecutor',side_effect=AssertionError('valid proofs must not create pool')):
            value=e.stage_cases('repair',allow_new_audit=True)
        self.assertEqual(len(value),2);self.assertEqual(self.audit.call_count,2)
        proof=e.read(e.semantic_path('repair'))
        self.assertEqual((proof['semantic_audits_in_this_collection'],proof['semantic_audit_proofs_reused']),(0,2))

    def test_durable_proof_tamper_rejected_without_audit_rerun(self):
        items=self.save();e.stage_cases('repair',allow_new_audit=True);e.semantic_path('repair').unlink()
        path=e.semantic_case_path('repair',items[0]);proof=e.read(path);proof['source_map_sha256']='changed'
        path.write_bytes(e.raw(proof))
        with patch.object(e,'ProcessPoolExecutor',side_effect=AssertionError('bad proof must stop before pool')):
            with self.assertRaises(AssertionError):e.stage_cases('repair',allow_new_audit=True)
        self.assertEqual(self.audit.call_count,2)

    def test_incomplete_raw_stage_stops_before_proof_pool(self):
        self.save();path=self.build/'repair-raw-index.json';index=e.read(path);index['all_outcomes_saved']=False;path.write_bytes(e.raw(index))
        with patch.object(e,'ProcessPoolExecutor',side_effect=AssertionError('must not reach pool')) as pool:
            with self.assertRaises(AssertionError):e.prepare_semantic_proofs('repair')
        pool.assert_not_called();self.audit.assert_not_called()
        self.assertFalse((self.build/'repair-semantic-proofs').exists())

    def test_saved_collection_requires_existing_bound_proof(self):
        self.save()
        with self.assertRaises(ValueError):e.stage_cases('repair')
        self.audit.assert_not_called()

    def test_raw_drift_rejected_without_audit_reexecution(self):
        items=self.save();e.stage_cases('repair',allow_new_audit=True)
        path=self.build/'repair-cases'/items[1]['file'];path.write_bytes(path.read_bytes()+b'drift')
        with self.assertRaises(AssertionError):e.stage_cases('repair')
        self.assertEqual(self.audit.call_count,2)

    def test_proof_cannot_be_reused_after_source_drift(self):
        self.save();e.stage_cases('repair',allow_new_audit=True)
        with patch.object(e,'source_hashes',return_value={'source.py':'changed'}):
            with self.assertRaises(AssertionError):e.stage_cases('repair')
        self.assertEqual(self.audit.call_count,2)

    def test_incomplete_proof_index_rejected(self):
        self.save();e.stage_cases('repair',allow_new_audit=True)
        path=e.semantic_path('repair');value=e.read(path);value['cases'].pop();path.write_bytes(e.raw(value))
        with self.assertRaises(AssertionError):e.verify_semantic_index('repair')

    def test_parent_header_mismatch_blocks_before_audit(self):
        self.save();self.known[0]['environment_sha256']='different'
        with self.assertRaises(AssertionError):e.stage_cases('repair',allow_new_audit=True)
        self.audit.assert_not_called()

    def test_patient_omission_and_manifest_mismatch_rejected(self):
        self.save();self.known[0]['branches'][e.HEDGE]['patients']=[]
        with self.assertRaises(AssertionError):e.stage_cases('repair',allow_new_audit=True)
        self.audit.assert_not_called()

    def test_audit_must_not_mutate_saved_replay(self):
        self.save()
        def changed(replay):
            replay['scenario']['id']='mutated'
            return dict(passed=True,new_executions=0)
        self.audit.side_effect=changed
        with self.assertRaises(AssertionError):e.stage_cases('repair',allow_new_audit=True)
        self.assertFalse(e.semantic_path('repair').exists())


class PriorGateTests(unittest.TestCase):
    def test_failed_prior_blocks_validation_audit_before_current_outputs_read(self):
        with patch.object(e,'verify_freeze'), \
             patch.object(e,'verify_prior',side_effect=AssertionError('prior failed')), \
             patch.object(e,'read',side_effect=AssertionError('current output must not be read')) as read, \
             patch.object(e,'stage_cases',side_effect=AssertionError('must not collect')) as collect:
            with self.assertRaisesRegex(AssertionError,'prior failed'):e.audit_saved('validation')
            read.assert_not_called();collect.assert_not_called()

    def test_semantic_prior_gate_stops_before_read_tasks_folder_and_pool(self):
        with patch.object(e,'verify_freeze'),patch.object(e,'verify_prior',side_effect=AssertionError('prior failed')), \
             patch.object(e,'read',side_effect=AssertionError('must not read')) as read, \
             patch.object(e,'tasks',side_effect=AssertionError('must not create tasks')) as tasks, \
             patch.object(e,'ProcessPoolExecutor',side_effect=AssertionError('must not reach pool')) as pool:
            with self.assertRaisesRegex(AssertionError,'prior failed'):e.prepare_semantic_proofs('development')
            read.assert_not_called();tasks.assert_not_called();pool.assert_not_called()

    def test_invalid_workers_stop_before_any_dependency_or_artifact(self):
        with patch.object(e,'verify_freeze',side_effect=AssertionError('must not reach freeze')) as frozen:
            for value in (0,-1,True,2.0,'2'):
                with self.subTest(value=value),self.assertRaises(ValueError):e.execute('repair',value)
            frozen.assert_not_called()

    def test_failed_prior_gate_stops_before_pool_and_folder(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            with patch.object(e,'BUILD',Path(directory)),patch.object(e,'verify_freeze'), \
                 patch.object(e,'verify_prior',side_effect=AssertionError('prior failed')), \
                 patch.object(e,'ProcessPoolExecutor',side_effect=AssertionError('pool reached')) as pool:
                with self.assertRaisesRegex(AssertionError,'prior failed'):e.execute('validation')
                pool.assert_not_called()
                self.assertFalse((Path(directory)/'validation-cases').exists())

    def test_prior_verification_never_collects_or_reaudits_raw(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            build=Path(directory);protocol={'source_sha256':{'source.py':'pinned'}}
            report=dict(assessment=dict(passed=True),protocol=protocol,
                        semantic_audit_sha256='semantic',prior_stage_evidence_sha256={})
            (build/'repair.json').write_bytes(e.raw(report));digest=e.file_sha(build/'repair.json')
            (build/'repair-audit.json').write_bytes(e.raw(dict(passed=True,report_sha256=digest,
                source_sha256=protocol['source_sha256'],semantic_audit_sha256='semantic')))
            (build/'repair-lock.json').write_bytes(e.raw(dict(candidate=e.PRIMARY,assessment=report['assessment'],
                protocol=protocol,report_sha256=digest,next_stage_allowed=True)))
            with patch.object(e,'BUILD',build),patch.object(e,'protocol',return_value=protocol), \
                 patch.object(e,'source_hashes',return_value=protocol['source_sha256']), \
                 patch.object(e,'file_sha',side_effect=lambda p:'semantic' if str(p).endswith('-semantic-audit.json') else hashlib.sha256(Path(p).read_bytes()).hexdigest()), \
                 patch.object(e,'verify_semantic_index',return_value={'passed':True}) as semantic, \
                 patch.object(e,'prior_evidence',side_effect=lambda s:{} if s=='repair' else {'repair.json':digest}), \
                 patch.object(e,'stage_cases',side_effect=AssertionError('must not collect')) as collect:
                self.assertEqual(e.verify_prior('development'),{'repair.json':digest})
                semantic.assert_called_once_with('repair');collect.assert_not_called()


class ParentCacheBindingTests(unittest.TestCase):
    """A synthetic complete ledger exercises both inherited and own-parent binds."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.temp.name)
        self.build = self.root/'new'; (self.build/'preservation').mkdir(parents=True)
        self.parent = self.root/'parent'; (self.parent/'repair-cases').mkdir(parents=True)
        (self.parent/'preservation').mkdir()
        entries, cases, manifest, bindings = [], [], [], {}
        for index in range(360):
            origin = 'repair' if index < 240 else 'development'
            local = index if index < 240 else index-240
            count = 4 if local < (107 if origin=='repair' else 55) else 3
            seed = 10000+index
            patients = [patient('P'+str(j), selection=30+j) for j in range(count)]
            scenario = dict(id='unit'+str(index), family='synthetic', seed=seed,
                            horizon_seconds=300, patients=[dict(id=p['id']) for p in patients])
            digest = e.sha(e.raw(scenario)); name=f'{index:03d}_{seed}.json.gz'
            replay = dict(scenario=scenario, arms=[[e.ENVELOPE.split('|')[0],'immediate']],
                initial_snapshot_sha256='snapshot', environment_sha256='environment',
                branches=[dict(policy=e.ENVELOPE.split('|')[0],rule='immediate',
                    snapshot_sha256='snapshot',environment_sha256='environment',patients=patients)])
            body=e.raw(replay);packed=gzip.compress(body,mtime=0)
            path=self.parent/'repair-cases'/name;path.write_bytes(packed)
            meta=dict(raw_sha256=e.sha(body),compressed_sha256=e.sha(packed))
            bindings[path.relative_to(self.root).as_posix()]=dict(meta,compressed_bytes=len(packed))
            branches={e.arm(*pair):dict(patients=deepcopy(patients)) for pair in e.CONTROLS}
            compact=dict(parent_stage=origin,original_origin=origin,kind='mix',seed=seed,
                family='synthetic',scenario_id=scenario['id'],branches=branches)
            cases.append(dict(file=name,compact=compact,**meta))
            entries.append(dict(parent_stage=origin,original_origin=origin,kind='mix',seed=seed,
                family='synthetic',scenario_id=scenario['id'],patient_count=count,
                scenario_sha256=digest,initial_snapshot_sha256='snapshot',environment_sha256='environment',
                branches={name:deepcopy(branches[name]) for name in branches if name!=e.ENVELOPE}))
            manifest.append(dict(stage='repair',kind='mix',seed=seed,sha256=digest))
        self.report=dict(cases=cases,new_branches=360,cached_branches=7560)
        (self.parent/'repair.json').write_bytes(e.raw(self.report))
        report_sha=e.file_sha(self.parent/'repair.json')
        (self.parent/'repair-audit.json').write_bytes(e.raw(dict(passed=True,report_sha256=report_sha)))
        (self.parent/'input-manifest.json').write_bytes(e.raw(dict(entries=manifest)))
        self.inherited=dict(passed=True,environments=360,patients=1242,cached_branches=7560,
            controls=[list(a) for a in e.CONTROLS[:-1]],entries=entries,
            regressions_report_only=dict(old_six=[dict(parent_stage='repair',original_origin='repair',
                kind='mix',seed=10000,patient='P0')]))
        (self.parent/e.LEDGER_FILE).write_bytes(e.raw(self.inherited))
        before=dict(observed_stages={origin:dict(report_sha256=report_sha) for origin in ('repair','development')},
                    immutable_raw_bindings=bindings)
        (self.build/'preservation/before.json').write_bytes(e.raw(before))
        self.stack=ExitStack()
        for name,value in (('ROOT',self.root),('BUILD',self.build),('PARENT',self.parent)):
            self.stack.enter_context(patch.object(e,name,value))
        self.stack.enter_context(patch.object(e,'verify_old',return_value=e.EXPECTED_BEFORE))

    def tearDown(self):
        self.stack.close();self.temp.cleanup()

    def test_parent360_adds_only_pareto_and_preserves_two_slices_and_targets(self):
        self.assertNotIn('patient_count',self.report['cases'][0]['compact'])
        value=e.prepare_cache()
        self.assertEqual(value['cached_branches'],7920)
        self.assertEqual(len(value['entries']),360)
        self.assertEqual(sum(x['patient_count'] for x in value['entries'] if x['parent_stage']=='repair'),827)
        self.assertEqual(sum(x['patient_count'] for x in value['entries'] if x['parent_stage']=='development'),415)
        self.assertEqual(value['regressions_report_only'],self.inherited['regressions_report_only'])
        self.assertEqual(e.control_ledger(),value)
        self.assertTrue(all(len(x['branches'])==22 for x in value['entries']))
        self.assertEqual(value['policy_model_oracle_executions'],0)

    def test_inherited_control_outcome_drift_rejected(self):
        self.inherited['entries'][0]['branches'][e.HEDGE]['patients'][0].update(
            selection_seconds=99,estimated_arrival_seconds=159)
        (self.parent/e.LEDGER_FILE).write_bytes(e.raw(self.inherited))
        with self.assertRaises(AssertionError):e.prepare_cache()
        self.assertFalse((self.build/e.LEDGER_FILE).exists())

    def test_own_pareto_raw_corruption_rejected(self):
        path=self.parent/'repair-cases'/self.report['cases'][1]['file']
        path.write_bytes(path.read_bytes()+b'changed')
        with self.assertRaises(AssertionError):e.prepare_cache()
        self.assertFalse((self.build/e.LEDGER_FILE).exists())


class DocumentationRebindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd());self.root=Path(self.temp.name)
        self.build=self.root/'new';self.build.mkdir();prior=self.root/'parent';prior.mkdir()
        self.initial=dict(parent_sources={'source.py':'sourcehash'},immutable_raw_bindings={'old.gz':'rawhash'},
            strict_reference_sha256={'docs/README.md':'oldindex','evidence.json':'same'})
        self.initial_path=prior/'before-initial.json';self.initial_path.write_bytes(e.raw(self.initial))
        self.initial_sha=e.file_sha(self.initial_path)
        self.previous=deepcopy(self.initial);self.previous['strict_reference_sha256']['docs/README.md']='newindex'
        self.previous.update(initial_preservation_sha256=self.initial_sha,
            non_executable_reference_drift=dict(path='docs/README.md',sealed_reference_sha256='oldindex',
                current_sha256='newindex',reason='An already sealed documentation edit.'))
        self.parent_path=prior/'before.json';self.parent_path.write_bytes(e.raw(self.previous));self.parent_sha=e.file_sha(self.parent_path)
        self.before=deepcopy(self.previous);self.before['parent_sources']['followplan.py']='addedsource'
        self.before['immutable_raw_bindings']['followplan.gz']='addedraw'
        self.before['strict_reference_sha256'].update({'parent/before.json':self.parent_sha,'parent/before-initial.json':self.initial_sha})
        self.before['inherited_reference_drift_provenance']=dict(parent_before_path='parent/before.json',parent_before_sha256=self.parent_sha,
            initial_preservation_path='parent/before-initial.json',initial_preservation_sha256=self.initial_sha)
        immediate=deepcopy(self.before);immediate_path=prior/'immediate.json';immediate_path.write_bytes(e.raw(immediate))
        self.before['strict_reference_sha256']['parent/immediate.json']=e.file_sha(immediate_path)
        self.before['immediate_parent_preservation']=dict(path='parent/immediate.json',sha256=e.file_sha(immediate_path))
        self.stack=ExitStack()
        for name,value in (('ROOT',self.root),('BUILD',self.build),('INITIAL_BEFORE',self.initial_sha),('INDEX_BEFORE','oldindex'),('INDEX_AFTER','newindex')):
            self.stack.enter_context(patch.object(e,name,value))

    def tearDown(self):self.stack.close();self.temp.cleanup()

    def test_sealed_drift_and_added_source_raws_are_accepted_without_new_rebind(self):
        e.verify_reference_rebinding(self.before)

    def test_second_rebind_of_inherited_reference_is_rejected(self):
        self.before['strict_reference_sha256']['evidence.json']='changed'
        with self.assertRaises(AssertionError):e.verify_reference_rebinding(self.before)

    def test_old_source_or_raw_binding_changes_are_rejected(self):
        for key,item in (('parent_sources','source.py'),('immutable_raw_bindings','old.gz')):
            before=deepcopy(self.before);before[key][item]='changed'
            with self.subTest(key=key),self.assertRaises(AssertionError):e.verify_reference_rebinding(before)

    def test_initial_or_prior_archive_corruption_is_rejected(self):
        for path in (self.initial_path,self.parent_path):
            original=path.read_bytes();path.write_bytes(original+b'changed')
            with self.assertRaises(AssertionError):e.verify_reference_rebinding(self.before)
            path.write_bytes(original)


class HistoricalFailureQualificationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path.cwd());self.root=Path(self.temp.name)
        (self.root/'docs').mkdir();(self.root/'docs/README.md').write_bytes(b'current index')
        self.sha=e.file_sha(self.root/'docs/README.md');self.stack=ExitStack()
        self.stack.enter_context(patch.object(e,'ROOT',self.root))
        self.stack.enter_context(patch.object(e,'INDEX_AFTER',self.sha))

    def tearDown(self):
        self.stack.close();self.temp.cleanup()

    def qualified(self):
        ids=list(e.HISTORICAL_REFERENCE_CASE_IDS)
        return dict(passed=False,errors=0,failures=10,skipped=0,
            source_sha256_before={'source.py':'same'},source_sha256_after={'source.py':'same'},
            failure_details=[dict(test_id=pid,traceback='Traceback: verify_old\nAssertionError: docs/README.md\n') for pid in ids],
            eligible_for_candidate_freeze=True,historical_reference_qualification=dict(eligible=True,
                path='docs/README.md',sealed_sha256=e.INDEX_BEFORE,current_sha256=self.sha,
                case_ids=ids,current_before_sha256=self.sha,current_after_sha256=self.sha,
                assertions_unchanged=True,failures_retained=True))

    def test_all_passed_path_requires_no_failure(self):
        value=dict(passed=True,errors=0,failures=0,skipped=0,
            source_sha256_before={'s':'same'},source_sha256_after={'s':'same'})
        e.verify_tests_for_freeze(value)
        value['failures']=1
        with self.assertRaises(AssertionError):e.verify_tests_for_freeze(value)

    def test_exact_ten_failures_remain_failed_and_qualified(self):
        value=self.qualified();before=deepcopy(value);e.verify_tests_for_freeze(value)
        self.assertEqual(value,before);self.assertFalse(value['passed']);self.assertEqual(value['failures'],10)

    def test_missing_unknown_or_different_failure_is_rejected(self):
        for edit in (lambda v:v['failure_details'].pop(),
                     lambda v:v['failure_details'][0].update(test_id='unknown.failure'),
                     lambda v:v['failure_details'][0].update(traceback='verify_old\nAssertionError: changed.py'),
                     lambda v:v.update(failures=11),
                     lambda v:v['historical_reference_qualification'].update(assertions_unchanged=False)):
            value=self.qualified();edit(value)
            with self.assertRaises(AssertionError):e.verify_tests_for_freeze(value)

    def test_current_document_hash_and_source_drift_rejected(self):
        value=self.qualified();value['source_sha256_after']={'source.py':'changed'}
        with self.assertRaises(AssertionError):e.verify_tests_for_freeze(value)
        value=self.qualified();(self.root/'docs/README.md').write_bytes(b'changed again')
        with self.assertRaises(AssertionError):e.verify_tests_for_freeze(value)


if __name__=='__main__':
    unittest.main()
