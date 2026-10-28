"""Independent raw JSON fixtures and mutation checks for stable C1 audit."""
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import unittest

from simulation import partial_information_audit as information
from simulation import responsive_confirmation_audit as responsive
from simulation.h1_online_audit import _diagnostics, _paired_tails
from simulation.stable_c1_audit import (
    COMPARISON_PAIRS, EXPERIMENT_ID, FIXED_SETTINGS, POLICY_IDS, STABILITY_RULE,
    StableC1AuditError, _greedy, audit_branch, audit_report, audit_stability,
    stability_metrics,
)
from test_responsive_confirmation_audit import complete_branch


def _trace(solver, baseline, solver_objective, baseline_objective):
    equal = solver_objective == baseline_objective
    selected = baseline if equal else solver
    applied = selected != solver
    return dict(rule=STABILITY_RULE, solver_assignments=deepcopy(solver),
        baseline_assignments=deepcopy(baseline), solver_objective=list(solver_objective),
        baseline_objective=list(baseline_objective), selected_assignments=deepcopy(selected),
        applied=applied, reason='equal_objective_baseline' if applied else
            'equal_objective_unchanged' if equal else 'solver_strictly_better',
        elapsed_seconds=.0001, optimality_claimed=False)


def stable_branch(code='stable_responsive_C1'):
    stable = code.startswith('stable_')
    prefix, rule = code.removeprefix('stable_').split('_')
    protection = prefix in ('protected', 'early', 'watch', 'responsive')
    scenario, branch = complete_branch(rule, 'observed' if protection else prefix,
                                      protection=protection, prefix=prefix)
    scenario['comparison_id'] = EXPERIMENT_ID
    branch.update(id=code, comparison_id=EXPERIMENT_ID, source_experiment='stable_c1_comparison',
                  stable_ties_enabled=stable)
    branch['policy']['name'] = code
    if stable:
        for record in branch['solver_records']:
            record['stability'] = _trace(record['assignments'], record['assignments'],
                                         record['objective'], record['objective'])
            record['solver_pipeline_wall_seconds'] = .0028
            record['assigned_count'] = sum(hid is not None for hid in record['assignments'].values())
    return scenario, branch


def report_fixture():
    branches, policies, scenario = [], [], None
    for code in POLICY_IDS:
        scenario, branch = stable_branch(code)
        branches.append(branch)
        stable = code.startswith('stable_')
        prefix, rule = code.removeprefix('stable_').split('_')
        protection = prefix in ('protected', 'early', 'watch', 'responsive')
        spec = dict(id=code, label=code, mode='observed' if protection else prefix, rule=rule,
            k=3, wait=2, notify=True, protection=protection,
            prefetch=prefix in ('early', 'responsive'), watch=prefix in ('watch', 'responsive'),
            grace_minutes=3 if protection else None, stable_ties=stable)
        if rule in ('H1', 'C1'):
            spec['time_limit_seconds'] = .05
        if rule == 'H1':
            spec['max_moves'] = 2048
        if rule == 'C1':
            spec.update(seed=0, require_backend=True)
        policies.append(spec)
    by_id = {branch['id']: branch for branch in branches}
    case = dict(scenario=scenario, branches=branches, execution_order=list(POLICY_IDS),
        summaries={b['id']: information.base.raw_branch_metrics(scenario, b) for b in branches},
        information_metrics={b['id']: information.information_metrics(b) for b in branches},
        guard_metrics={b['id']: responsive.guard_metrics(b) for b in branches},
        responsiveness_metrics={b['id']: responsive.responsiveness_metrics(b) for b in branches},
        stability_metrics={b['id']: stability_metrics(b) for b in branches},
        diagnostics={b['id']: _diagnostics(scenario, b) for b in branches},
        paired_comparisons={name:dict(baseline=left, comparison=right,
            metrics=responsive._paired(scenario, by_id[left], by_id[right]),
            arrival_details=_paired_tails(scenario, by_id[left], by_id[right]))
            for name, left, right in COMPARISON_PAIRS})
    return dict(schema_version=1, kind='stable_c1_comparison', experiment_id=EXPERIMENT_ID,
        phase='development', synthetic=True, seeds=[7], families=['fresh_control'],
        settings=dict(deepcopy(FIXED_SETTINGS), policies=policies),
        source_sha256={'fixture.py': 'abc'}, runtime_versions={'python': 'fixture'},
        lock_sha256=None, results=[case])


def tie_fixture(*, older_request=False, scarce=False):
    """Only detached input/model records; no simulator used to create ties."""
    patients = [dict(id=pid, occur_t=0, search_start_t=0, priority=4,
        resource_demands={'bed':1}, required_capabilities=['general']) for pid in ('P1', 'P2')]
    if older_request:
        patients[0]['search_start_t'] = 1
    hospitals = [dict(id='H1', capacity={'bed':1}, capabilities=['general'])]
    if scarce:
        hospitals.append(dict(id='H2', capacity={'bed':1}, capabilities=['general']))
    scenario = dict(patients=patients, hospitals=hospitals, accept_ttl_minutes=10)
    inputs = dict(patients=[dict(id=p['id'], priority=p['priority'], request_t=p['search_start_t'],
        demand=deepcopy(p['resource_demands']), required_capabilities=['general'], position=[37,127],
        options=['H1','H2'] if scarce and p['id']=='P1' else ['H1'],
        accepted_at={h['id']:2 for h in hospitals}, valid_offer_expiries={h['id']:12 for h in hospitals})
        for p in patients],
        hospitals=[dict(id=h['id'], capabilities=h['capabilities'], capacity={'bed':1},
                        used={'bed':0}, committed={'bed':0}, queued={'bed':0}) for h in hospitals],
        free_resources=[dict(hospital=h['id'], resource='bed', free=1) for h in hospitals],
        travel_minutes=[dict(patient=p['id'], hospital=h['id'], minutes=2 if h['id']=='H1' else 3)
                        for p in patients for h in hospitals])
    decision = dict(t=3, kind='batch', rule='C1', scope='current_same_minute_accepted_offers',
        inputs=inputs, solver_record_index=0, model_optimal_proven=True, elapsed_seconds=.004)
    ps, free, options, travel = information.base._problem(scenario, decision)
    baseline = _greedy(ps, free, options, travel)
    if scarce:
        solver = {'P1':'H2','P2':'H1'}
    else:
        solver = {'P1':'H1' if older_request else None, 'P2':None if older_request else 'H1'}
    solver_objective = information.base._objective(ps, solver, travel)
    baseline_objective = information.base._objective(ps, baseline, travel)
    trace = _trace(solver, baseline, solver_objective, baseline_objective)
    decision['proposed_assignments'] = deepcopy(trace['selected_assignments'])
    record = dict(t=3, scope=decision['scope'], assignments=deepcopy(trace['selected_assignments']),
        objective=solver_objective, batch_size=2, edge_count=sum(map(len, options.values())),
        assigned_count=sum(hid is not None for hid in trace['selected_assignments'].values()),
        status='OPTIMAL', fallback=False, optimal_proven=True,
        primary_optimal_proven=True, secondary_optimal_proven=True,
        wall_seconds=.003, solver_pipeline_wall_seconds=.0028, stability=trace,
        stages=[dict(status='OPTIMAL', objective_name='assigned_priority_weight',
                     objective=8-solver_objective[0], best_bound=8-solver_objective[0]),
                dict(status='OPTIMAL', objective_name='priority_weighted_travel_minutes',
                     objective=solver_objective[1], best_bound=solver_objective[1])])
    branch = dict(policy={'rule':'C1'}, stable_ties_enabled=True,
                  allocation_decisions=[decision], solver_records=[record])
    return scenario, branch


class StableC1AuditTests(unittest.TestCase):
    def test_seventeen_branch_report_passes(self):
        result = audit_report(report_fixture())
        self.assertEqual(result['branches'], 17)
        self.assertEqual(result['stable_batches'], 3)
        self.assertEqual(result['stable_changed_batches'], 0)
        self.assertFalse(result['actual_optimality_claimed'])

    def test_stable_full_branch_passes_raw_physics_and_proof(self):
        scenario, branch = stable_branch()
        result = audit_branch(scenario, branch)
        self.assertEqual(result['counts']['physical_gate_passes'], 1)
        self.assertEqual(result['counts']['stable_solver_original_proofs'], 1)

    def test_equal_objective_patient_identity_tie_is_replaced(self):
        scenario, branch = tie_fixture()
        result = audit_stability(scenario, branch)
        self.assertEqual(result['stable_changed_batches'], 1)
        self.assertEqual(branch['solver_records'][0]['assignments'], {'P1':'H1','P2':None})

    def test_older_request_precedes_lexical_patient_identity(self):
        scenario, branch = tie_fixture(older_request=True)
        result = audit_stability(scenario, branch)
        self.assertEqual(result['stable_changed_batches'], 1)
        self.assertEqual(branch['solver_records'][0]['assignments'], {'P1':None,'P2':'H1'})

    def test_strictly_better_solver_plan_preserved(self):
        scenario, branch = tie_fixture(scarce=True)
        result = audit_stability(scenario, branch)
        self.assertEqual(result['stable_equal_objective_batches'], 0)
        self.assertEqual(branch['solver_records'][0]['assignments'], {'P1':'H2','P2':'H1'})

    def test_greedy_priority_precedes_request_and_identity(self):
        patients = [dict(id='A', priority=1, request_t=0, demand={'bed':1}),
                    dict(id='B', priority=4, request_t=3, demand={'bed':1})]
        result = _greedy(patients, {('H','bed'):1}, {'A':['H'],'B':['H']},
                         {('A','H'):1,('B','H'):1})
        self.assertEqual(result, {'B':'H','A':None})

    def test_greedy_hospital_identity_breaks_equal_distance_tie(self):
        p = dict(id='P', priority=4, request_t=0, demand={'bed':1})
        result = _greedy([p], {('Z','bed'):1,('A','bed'):1}, {'P':['Z','A']},
                         {('P','Z'):2,('P','A'):2})
        self.assertEqual(result, {'P':'A'})

    def test_greedy_multiresource_capacity_is_joint(self):
        ps = [dict(id=pid, priority=4, request_t=0, demand={'bed':1,'team':1}) for pid in ('A','B')]
        result = _greedy(ps, {('H','bed'):2,('H','team'):1}, {'A':['H'],'B':['H']},
                         {('A','H'):1,('B','H'):1})
        self.assertEqual(result, {'A':'H','B':None})

    def test_mutated_greedy_baseline_rejected(self):
        scenario, branch = tie_fixture()
        branch['solver_records'][0]['stability']['baseline_assignments'] = {'P1':None,'P2':'H1'}
        with self.assertRaisesRegex(StableC1AuditError, 'independently rebuilt'):
            audit_stability(scenario, branch)

    def test_infeasible_original_solver_rejected(self):
        scenario, branch = tie_fixture()
        branch['solver_records'][0]['stability']['solver_assignments'] = {'P1':'H1','P2':'H1'}
        with self.assertRaisesRegex(StableC1AuditError, 'original CP-SAT plan'):
            audit_stability(scenario, branch)

    def test_mutated_objective_rejected(self):
        scenario, branch = tie_fixture()
        branch['solver_records'][0]['stability']['solver_objective'] = [0,8]
        with self.assertRaisesRegex(StableC1AuditError, 'stability objective'):
            audit_stability(scenario, branch)

    def test_different_objective_cannot_be_replaced(self):
        scenario, branch = tie_fixture(scarce=True)
        trace = branch['solver_records'][0]['stability']
        trace['selected_assignments'] = deepcopy(trace['baseline_assignments'])
        with self.assertRaisesRegex(StableC1AuditError, 'final proposal'):
            audit_stability(scenario, branch)

    def test_false_applied_flag_rejected(self):
        scenario, branch = tie_fixture()
        branch['solver_records'][0]['stability']['applied'] = False
        with self.assertRaisesRegex(StableC1AuditError, 'applied flag'):
            audit_stability(scenario, branch)

    def test_false_reason_rejected(self):
        scenario, branch = tie_fixture()
        branch['solver_records'][0]['stability']['reason'] = 'solver_strictly_better'
        with self.assertRaisesRegex(StableC1AuditError, 'reason'):
            audit_stability(scenario, branch)

    def test_stability_has_no_third_optimum_claim(self):
        scenario, branch = tie_fixture()
        branch['solver_records'][0]['stability']['optimality_claimed'] = True
        with self.assertRaisesRegex(StableC1AuditError, 'additional optimum'):
            audit_stability(scenario, branch)

    def test_global_canonical_claim_rejected(self):
        for field in ('canonical_optimal_proven', 'tie_optimal_proven', 'actual_optimal_proven',
                      'whole_episode_optimal_proven', 'global_optimal_proven'):
            with self.subTest(field=field):
                scenario, branch = tie_fixture()
                branch['solver_records'][0][field] = True
                with self.assertRaisesRegex(StableC1AuditError, 'overstated'):
                    audit_stability(scenario, branch)

    def test_original_two_stage_proof_checked_separately(self):
        scenario, branch = tie_fixture()
        branch['solver_records'][0]['stages'][0]['best_bound'] += 1
        with self.assertRaisesRegex(StableC1AuditError, 'bound'):
            audit_stability(scenario, branch)

    def test_postselection_overhead_included(self):
        scenario, branch = tie_fixture()
        branch['solver_records'][0]['wall_seconds'] = .0028
        with self.assertRaisesRegex(StableC1AuditError, 'overhead omitted'):
            audit_stability(scenario, branch)

    def test_nonfinite_overhead_rejected(self):
        scenario, branch = tie_fixture()
        branch['solver_records'][0]['stability']['elapsed_seconds'] = float('nan')
        with self.assertRaisesRegex(StableC1AuditError, 'overhead'):
            audit_stability(scenario, branch)

    def test_postselection_wall_time_in_allocation_metrics(self):
        scenario, branch = tie_fixture()
        branch['allocation_decisions'][0]['elapsed_seconds'] = .002
        with self.assertRaisesRegex(StableC1AuditError, 'omitted from allocation'):
            audit_stability(scenario, branch)

    def test_no_third_solver_stage(self):
        scenario, branch = tie_fixture()
        branch['solver_records'][0]['stages'].append(deepcopy(branch['solver_records'][0]['stages'][-1]))
        with self.assertRaisesRegex(StableC1AuditError, 'too many CP-SAT stages'):
            audit_stability(scenario, branch)

    def test_selected_assigned_count_checked(self):
        scenario, branch = tie_fixture()
        branch['solver_records'][0]['assigned_count'] = 2
        with self.assertRaisesRegex(StableC1AuditError, 'assigned count'):
            audit_stability(scenario, branch)

    def test_frozen_baseline_cannot_have_stability_trace(self):
        scenario, branch = tie_fixture()
        branch['stable_ties_enabled'] = False
        with self.assertRaisesRegex(StableC1AuditError, 'frozen baseline'):
            audit_stability(scenario, branch)

    def test_rule_only_applies_to_C1(self):
        scenario, branch = tie_fixture()
        branch['policy']['rule'] = 'H1'
        with self.assertRaisesRegex(StableC1AuditError, 'non-C1'):
            audit_stability(scenario, branch)

    def test_unknown_stability_trace_field_rejected(self):
        scenario, branch = tie_fixture()
        branch['solver_records'][0]['stability']['future_patient'] = 'P3'
        with self.assertRaisesRegex(StableC1AuditError, 'unknown stability trace'):
            audit_stability(scenario, branch)

    def test_report_metrics_recomputed(self):
        report = report_fixture()
        report['results'][0]['stability_metrics']['stable_responsive_C1']['stability_tie_replacements'] += 1
        with self.assertRaisesRegex(StableC1AuditError, 'raw metric mismatch'):
            audit_report(report)

    def test_paired_tail_recomputed(self):
        report = report_fixture()
        pair = report['results'][0]['paired_comparisons'][COMPARISON_PAIRS[-1][0]]
        pair['arrival_details']['max_delay_minutes'] = 37
        with self.assertRaisesRegex(StableC1AuditError, 'paired arrival tail'):
            audit_report(report)

    def test_source_hash_expectation_enforced(self):
        with self.assertRaisesRegex(StableC1AuditError, 'source hashes'):
            audit_report(report_fixture(), expected_source_sha256={'fixture.py':'wrong'})

    def test_source_hash_expectation_passes_exact_manifest(self):
        result = audit_report(report_fixture(), expected_source_sha256={'fixture.py':'abc'})
        self.assertTrue(result['provenance_checked'])

    def test_lock_source_mismatch_rejected_before_execution(self):
        report = report_fixture()
        lock = dict(kind='stable_c1_lock', experiment_id=EXPERIMENT_ID,
            source_sha256={'fixture.py':'changed'}, runtime_versions=report['runtime_versions'],
            settings=report['settings'], families=report['families'])
        with self.assertRaisesRegex(StableC1AuditError, 'differs from lock: source_sha256'):
            audit_report(report, lock=lock)

    def test_lock_cannot_choose_consumed_or_overlapping_seed_lists(self):
        report = report_fixture()
        lock = dict(kind='stable_c1_lock', experiment_id=EXPERIMENT_ID,
            **{key:deepcopy(report[key]) for key in ('source_sha256','runtime_versions','settings','families')},
            development_seeds=[7], validation_seeds=[7])
        with self.assertRaisesRegex(StableC1AuditError, 'preregistered stability seeds'):
            audit_report(report, lock=lock)

    def test_validation_lock_digest_required(self):
        from simulation.stable_c1_audit import DEVELOPMENT_SEEDS, VALIDATION_SEEDS, FAMILIES
        report = report_fixture()
        report.update(phase='validation', seeds=list(VALIDATION_SEEDS), families=list(FAMILIES), lock_sha256='wrong')
        lock = dict(kind='stable_c1_lock', experiment_id=EXPERIMENT_ID,
            **{key:deepcopy(report[key]) for key in ('source_sha256','runtime_versions','settings','families')},
            development_seeds=list(DEVELOPMENT_SEEDS), validation_seeds=list(VALIDATION_SEEDS))
        with self.assertRaisesRegex(StableC1AuditError, 'validation lock digest'):
            audit_report(report, lock=lock)

    def test_branch_stability_configuration_enforced(self):
        report = report_fixture()
        report['settings']['policies'][-1]['stable_ties'] = False
        with self.assertRaisesRegex(StableC1AuditError, 'policy fields'):
            audit_report(report)

    def test_seventeen_branches_required(self):
        report = report_fixture()
        report['results'][0]['branches'].pop()
        with self.assertRaisesRegex(StableC1AuditError, 'branch output'):
            audit_report(report)

    def test_missing_case_rejected(self):
        report = report_fixture()
        report['results'] = []
        with self.assertRaisesRegex(StableC1AuditError, 'missing family'):
            audit_report(report)

    def test_execution_rotation_enforced(self):
        report = report_fixture()
        report['results'][0]['execution_order'].reverse()
        with self.assertRaisesRegex(StableC1AuditError, 'execution order'):
            audit_report(report)

    def test_whole_episode_optimality_rejected(self):
        report = report_fixture()
        report['results'][0]['branches'][-1]['metrics']['whole_episode_optimal_proven'] = True
        with self.assertRaisesRegex(StableC1AuditError, 'whole episode'):
            audit_report(report)

    def test_malformed_report_wrapped_as_audit_error(self):
        with self.assertRaises(StableC1AuditError):
            audit_report(dict(kind='stable_c1_comparison', experiment_id=EXPERIMENT_ID, synthetic=True))

    def test_auditor_does_not_import_implementation(self):
        script = '''import importlib.abc, sys
class Reject(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked=('simulation.stable_cp_sat_assignment','simulation.cp_sat_assignment',
                 'simulation.stable_c1_engine','simulation.stable_c1_experiment',
                 'simulation.stable_c1_scenarios','simulation.responsive_confirmation_engine',
                 'simulation.responsive_confirmation_experiment','simulation.responsive_confirmation_scenarios',
                 'simulation.opportunity_guard','simulation.opportunity_engine','simulation.opportunity_scenarios',
                 'simulation.partial_information_engine','simulation.information_observations',
                 'simulation.heuristic_assignment','simulation.sim','ortools')
        if any(fullname == name or fullname.startswith(name+'.') for name in blocked):
            raise RuntimeError('forbidden implementation import: '+fullname)
sys.meta_path.insert(0, Reject())
import simulation.stable_c1_audit
print('independent')
'''
        completed = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True,
                                   cwd=Path(__file__).resolve().parent)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), 'independent')


if __name__ == '__main__':
    unittest.main()
