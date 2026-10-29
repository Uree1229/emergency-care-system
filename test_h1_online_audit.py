"""Raw artifact corruption checks; no simulator or solver is required."""
import copy
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

from simulation.h1_online_audit import (BUDGET_SCOPE, FIXED_SETTINGS, H1OnlineAuditError,
    _greedy, _paired_tails, _p95, audit_h1_branch, audit_report, main)
from simulation.online_allocation_audit import raw_branch_metrics


def fixture():
    scenario = dict(id='audit-tiny', family='scarcity', seed=15101, max_minutes=20,
        comparison_id='h1_online_20261015',
        accept_ttl_minutes=10, patients=[dict(id=pid, occur_t=0, search_start_t=2,
        priority=4, resource_demands={'bed': 1}) for pid in ('P1', 'P2')])
    patients = [dict(id=pid, priority=4, demand={'bed': 1}, required_capabilities=['general'],
        position=[37, 127], request_t=2, options=options,
        accepted_at={h: 3 for h in options}, valid_offer_expiries={h: 13 for h in options})
        for pid, options in [('P1', ['H1', 'H2']), ('P2', ['H1'])]]
    inputs = dict(patients=patients,
        hospitals=[dict(id=hid, capabilities=['general'], capacity={'bed': 1},
            used={'bed': 0}, committed={'bed': 0}, queued={'bed': 0}) for hid in ('H1', 'H2')],
        free_resources=[dict(hospital=hid, resource='bed', free=1) for hid in ('H1', 'H2')],
        travel_minutes=[dict(patient=pid, hospital=hid, minutes=minutes)
            for pid, hid, minutes in [('P1', 'H1', 2), ('P1', 'H2', 5), ('P2', 'H1', 2)]])
    assignment = {'P1': 'H2', 'P2': 'H1'}
    decision = dict(t=4, kind='batch', rule='H1', inputs=inputs, assignments=assignment,
        objective=dict(unassigned_priority_weight=0, priority_weighted_travel_minutes=28),
        scope='current_same_minute_accepted_offers', method='heuristic',
        elapsed_seconds=.004, optimal_proven=False, heuristic_record_index=0)
    record = dict(t=4, decision_index=0, scope='current_same_minute_accepted_offers',
        status='HEURISTIC', optimal_proven=False, assignments=dict(assignment), objective=[0, 28],
        objective_names=['unassigned_priority_weight', 'priority_weighted_travel_minutes'],
        batch_size=2, edge_count=3, assigned_count=2,
        baseline_assignments={'P1': 'H1', 'P2': None}, baseline_objective=[4, 8],
        baseline_assigned_count=1, time_limit_seconds=.05, max_moves=2048,
        time_budget_scope=BUDGET_SCOPE, wall_seconds=.003, preprocessing_seconds=.001,
        search_seconds=.002, evaluated_moves=4, accepted_moves=1,
        accepted_move_types=['scarcity_nearest'],
        improvements=[dict(kind='scarcity_nearest', evaluation=1, objective=[0, 28])])
    accepts = [dict(t=3, type='accept', patient=p['id'], hospital=h)
               for p in patients for h in p['options']]
    events = accepts + [dict(t=4, type='commit', patient=pid, hospital=hid)
                       for pid, hid in assignment.items()]
    for pid, hid, arrival in [('P2', 'H1', 6), ('P1', 'H2', 9)]:
        events.extend([dict(t=arrival, type='arrive', patient=pid, hospital=hid),
                       dict(t=arrival, type='treatment_start', patient=pid, hospital=hid),
                       dict(t=arrival+3, type='treatment_complete', patient=pid, hospital=hid)])
    events.sort(key=lambda e: e['t'])
    outcomes = [dict(id=pid, occur_t=0, search_start_t=2, priority=4, first_commit_t=4,
        final_arrive_t=arrival, treatment_start_t=arrival, treatment_complete_t=arrival+3,
        inquiries=0, redirects=0, transfers=0, state='COMPLETE')
        for pid, arrival in [('P1', 9), ('P2', 6)]]
    policy = dict(name='H1', rule='H1', k=3, wait=2, notify=True,
        near_minutes=None, expand_after=None, heuristic=dict(backend='bounded_joint_heuristic',
        time_limit_seconds=.05, max_moves=2048, time_budget_scope=BUDGET_SCOPE))
    branch = dict(id='H1', policy=policy, source_experiment='online_allocation_comparison',
        comparison_id='h1_online_20261015',
        metrics=dict(observation_end_t=20, whole_episode_optimal_proven=False,
            allocation_decisions=1, allocation_wall_seconds=.004, heuristic_calls=1,
            heuristic_wall_seconds=.003, solver_calls=0, solver_wall_seconds=0,
            solver_fallbacks=0, solver_optimal_batches=0, heuristic_optimal_batches=0),
        events=events, patient_outcomes=outcomes, allocation_decisions=[decision],
        heuristic_records=[record], solver_records=[])
    spec = dict(id='H1', label='H1', rule='H1', k=3, wait=2, notify=True,
                time_limit_seconds=.05, max_moves=2048)
    return scenario, branch, spec


def report_fixture():
    scenario, heuristic, hspec = fixture()
    r1 = copy.deepcopy(heuristic)
    r1.update(id='R1', policy=dict(name='R1', rule='R1', k=3, wait=2, notify=True,
                                 near_minutes=None, expand_after=None), heuristic_records=[])
    # A raw R1 trace selects P1 first, exhausting H1 before P2's single decision.
    first = copy.deepcopy(heuristic['allocation_decisions'][0])
    first.update(kind='single', rule='R1', scope='current_patient_accepted_offers',
                 assignments={'P1': 'H1'}, method='nearest_current_offer', elapsed_seconds=.001,
                 objective=dict(unassigned_priority_weight=0, priority_weighted_travel_minutes=8))
    first.pop('heuristic_record_index')
    first['inputs']['patients'] = first['inputs']['patients'][:1]
    first['inputs']['travel_minutes'] = first['inputs']['travel_minutes'][:2]
    second = copy.deepcopy(first)
    second.update(assignments={'P2': None},
        objective=dict(unassigned_priority_weight=4, priority_weighted_travel_minutes=0))
    second['inputs']['patients'] = [copy.deepcopy(heuristic['allocation_decisions'][0]['inputs']['patients'][1])]
    second['inputs']['hospitals'] = second['inputs']['hospitals'][:1]
    second['inputs']['hospitals'][0]['committed']['bed'] = 1
    second['inputs']['free_resources'] = [dict(hospital='H1', resource='bed', free=0)]
    second['inputs']['travel_minutes'] = [dict(patient='P2', hospital='H1', minutes=2)]
    r1['allocation_decisions'] = [first, second]
    r1['events'] = [e for e in r1['events'] if e['type'] == 'accept'] + [
        dict(t=4, type='commit', patient='P1', hospital='H1'),
        dict(t=6, type='arrive', patient='P1', hospital='H1'),
        dict(t=6, type='treatment_start', patient='P1', hospital='H1'),
        dict(t=9, type='treatment_complete', patient='P1', hospital='H1')]
    r1['patient_outcomes'][0].update(final_arrive_t=6, treatment_start_t=6, treatment_complete_t=9)
    r1['patient_outcomes'][1].update(first_commit_t=None, final_arrive_t=None,
        treatment_start_t=None, treatment_complete_t=None, state='UNASSIGNED')
    r1['metrics'].update(allocation_decisions=2, allocation_wall_seconds=.002,
                         heuristic_calls=0, heuristic_wall_seconds=0)
    c1 = copy.deepcopy(heuristic)
    c1.update(id='C1', heuristic_records=[])
    c1['policy'] = dict(name='C1', rule='C1', k=3, wait=2, notify=True,
        near_minutes=None, expand_after=None, solver=dict(backend='ortools_cp_sat',
            time_limit_seconds=.05, seed=0, require_backend=True, num_search_workers=1))
    cd = c1['allocation_decisions'][0]
    cd.update(rule='C1', method='cp_sat', optimal_proven=True, solver_record_index=0)
    cd.pop('heuristic_record_index')
    c1['solver_records'] = [dict(t=4, decision_index=0, scope='current_same_minute_accepted_offers',
        assignments=dict(cd['assignments']), objective=[0,28], batch_size=2, edge_count=3,
        status='OPTIMAL', optimal_proven=True, primary_optimal_proven=True,
        secondary_optimal_proven=True, fallback=False, wall_seconds=.003,
        stages=[dict(status='OPTIMAL', objective=8, best_bound=8, objective_name='assigned_priority_weight'),
                dict(status='OPTIMAL', objective=28, best_bound=28, objective_name='priority_weighted_travel_minutes')])]
    c1['metrics'].update(heuristic_calls=0, heuristic_wall_seconds=0,
                         solver_calls=1, solver_wall_seconds=.003, solver_optimal_batches=1)
    policies = [dict(id='R1', label='R1', rule='R1', k=3, wait=2, notify=True), hspec,
        dict(id='C1', label='C1', rule='C1', k=3, wait=2, notify=True,
             time_limit_seconds=.05, seed=0, require_backend=True)]
    branches = [r1, heuristic, c1]
    groups = {'4': dict(patients=2, unassigned=0, not_arrived=0, untreated=0),
              '2': dict(patients=0, unassigned=0, not_arrived=0, untreated=0),
              '1': dict(patients=0, unassigned=0, not_arrived=0, untreated=0)}
    common_diagnostics = dict(arrival_p95_minutes=9, arrival_denominator=2,
        allocation_wall_p95_ms=4, allocation_wall_max_ms=4,
        allocation_decision_denominator=1, priority_groups=groups)
    r1_diagnostics = copy.deepcopy(common_diagnostics)
    r1_diagnostics.update(arrival_p95_minutes=6, arrival_denominator=1,
        allocation_wall_p95_ms=1, allocation_wall_max_ms=1, allocation_decision_denominator=2)
    r1_diagnostics['priority_groups']['4'].update(unassigned=1, not_arrived=1, untreated=1)
    changed = dict(denominator=1, delayed=1, earlier=0, equal=0,
                   max_delay_minutes=3, p95_difference_minutes=3)
    r1_pair = dict(denominator=1, delayed=0, earlier=0, equal=1,
                   max_delay_minutes=0, p95_difference_minutes=0)
    source = 'simulation/h1_online_audit.py'
    return dict(kind='online_allocation_comparison', experiment_id='h1_online_20261015',
        synthetic=True, phase='development', seeds=[15101], families=['scarcity'],
        runtime_versions={'python':'raw-fixture'}, settings=dict(FIXED_SETTINGS, policies=policies),
        source_sha256={source:hashlib.sha256(Path(source).read_bytes()).hexdigest()},
        results=[dict(scenario=scenario, branches=branches, execution_order=['R1','H1','C1'],
            diagnostics={'R1':r1_diagnostics, 'H1':copy.deepcopy(common_diagnostics),
                         'C1':copy.deepcopy(common_diagnostics)},
            paired_arrival_details={'R1':r1_pair,'H1':copy.deepcopy(changed),'C1':copy.deepcopy(changed)},
            summaries={b['id']:raw_branch_metrics(scenario,b) for b in branches})])


class H1OnlineAuditTests(unittest.TestCase):
    def test_raw_three_rule_report_passes_without_solver_import(self):
        result = audit_report(report_fixture())
        self.assertEqual(result['heuristic_batches'], 1)
        self.assertEqual(result['heuristic_baselines_checked'], 1)
        self.assertEqual(result['branches'], 3)
        self.assertFalse(result['heuristic_optimality_claimed'])

    def test_greedy_reconstruction_uses_priority_then_request_then_id(self):
        scenario, branch, spec = fixture()
        branch['allocation_decisions'][0]['inputs']['patients'].reverse()
        self.assertEqual(audit_h1_branch(scenario, branch, spec)['heuristic_batches'], 1)
        patients = [dict(id=pid, priority=priority, request_t=request, demand={'bed':1})
                    for pid, priority, request in [('A',2,0),('B',4,2),('D',4,1),('C',4,1)]]
        options={p['id']:['Z','H'] for p in patients}
        travel={(p['id'],hid):2 for p in patients for hid in ('Z','H')}
        self.assertEqual(_greedy(patients,{('H','bed'):1,('Z','bed'):1},options,travel),
                         {'C':'H','D':'Z','B':None,'A':None})

    def test_valid_assignment_worse_than_baseline_is_rejected(self):
        scenario, branch, spec = fixture()
        decision=branch['allocation_decisions'][0]; record=branch['heuristic_records'][0]
        decision.update(assignments={'P1':None,'P2':None})
        record.update(assignments=dict(decision['assignments']), objective=[8,0],assigned_count=0)
        with self.assertRaisesRegex(H1OnlineAuditError,'worsens'):
            audit_h1_branch(scenario,branch,spec)

    def test_corrupt_heuristic_baseline_and_objective_are_rejected(self):
        for field, value in [('baseline_assignments', {'P1':'H2','P2':'H1'}),
                             ('baseline_objective',[0,28]), ('objective',[0,27]),
                             ('assigned_count',1), ('edge_count',2)]:
            with self.subTest(field=field):
                report = report_fixture()
                report['results'][0]['branches'][1]['heuristic_records'][0][field] = value
                with self.assertRaises(H1OnlineAuditError): audit_report(report)

    def test_proposed_assignments_cannot_disagree_with_actual_commits(self):
        report = report_fixture()
        report['results'][0]['branches'][1]['heuristic_records'][0]['assignments']['P1'] = 'H1'
        with self.assertRaisesRegex(H1OnlineAuditError, 'actual commits'): audit_report(report)

    def test_optional_proposal_field_is_checked(self):
        report = report_fixture()
        record = report['results'][0]['branches'][1]['heuristic_records'][0]
        record['proposed_assignments'] = {'P1':'H1','P2':None}
        with self.assertRaisesRegex(H1OnlineAuditError, 'actual commits'): audit_report(report)

    def test_orphan_missing_and_cross_linked_heuristic_records_are_rejected(self):
        for change in ('orphan','missing','crosslink','boolean_index'):
            with self.subTest(change=change):
                report = report_fixture(); branch=report['results'][0]['branches'][1]
                if change=='orphan': branch['heuristic_records'].append(copy.deepcopy(branch['heuristic_records'][0]))
                elif change=='missing': branch['allocation_decisions'][0].pop('heuristic_record_index')
                elif change=='crosslink': branch['heuristic_records'][0]['decision_index']=1
                else: branch['allocation_decisions'][0]['heuristic_record_index']=False
                with self.assertRaises(H1OnlineAuditError): audit_report(report)

    def test_heuristic_record_cannot_be_reused(self):
        scenario, branch, spec = fixture()
        branch['allocation_decisions'].append(copy.deepcopy(branch['allocation_decisions'][0]))
        with self.assertRaisesRegex(H1OnlineAuditError,'reused'): audit_h1_branch(scenario,branch,spec)

    def test_heuristic_optimality_and_method_claims_are_rejected(self):
        for target,key,value in [('record','status','OPTIMAL'),('record','optimal_proven',True),
                                 ('decision','optimal_proven',True),('decision','method','cp_sat')]:
            report=report_fixture(); branch=report['results'][0]['branches'][1]
            item=branch['heuristic_records'][0] if target=='record' else branch['allocation_decisions'][0]
            item[key]=value
            with self.subTest(target=target,key=key), self.assertRaises(H1OnlineAuditError): audit_report(report)

    def test_move_limit_and_improvement_trace_corruptions_are_rejected(self):
        for field,value in [('evaluated_moves',2049),('accepted_moves',5),('accepted_moves',True),
                            ('improvements',[]),('accepted_move_types',['wrong'])]:
            report=report_fixture()
            report['results'][0]['branches'][1]['heuristic_records'][0][field]=value
            with self.subTest(field=field), self.assertRaises(H1OnlineAuditError): audit_report(report)

    def test_nonfinite_negative_and_inconsistent_timings_are_rejected(self):
        for field,value in [('wall_seconds',math.nan),('search_seconds',math.inf),
                            ('preprocessing_seconds',-.1),('wall_seconds',.001),('wall_seconds',True)]:
            report=report_fixture()
            report['results'][0]['branches'][1]['heuristic_records'][0][field]=value
            with self.subTest(field=field,value=value), self.assertRaises(H1OnlineAuditError): audit_report(report)
        report=report_fixture(); record=report['results'][0]['branches'][1]['heuristic_records'][0]
        record.update(wall_seconds=.006,search_seconds=.005)
        report['results'][0]['branches'][1]['metrics']['heuristic_wall_seconds']=.006
        with self.assertRaisesRegex(H1OnlineAuditError,'enclosing decision'): audit_report(report)

    def test_cooperative_budget_is_not_a_hard_wall_time_bound(self):
        report=report_fixture(); branch=report['results'][0]['branches'][1]
        record=branch['heuristic_records'][0]
        record.update(wall_seconds=.06, preprocessing_seconds=.01, search_seconds=.05)
        branch['allocation_decisions'][0]['elapsed_seconds']=.061
        branch['metrics'].update(heuristic_wall_seconds=.06, allocation_wall_seconds=.061)
        report['results'][0]['diagnostics']['H1'].update(allocation_wall_p95_ms=61,
                                                      allocation_wall_max_ms=61)
        self.assertEqual(audit_report(report)['heuristic_batches'],1)

    def test_false_runtime_totals_are_rejected(self):
        for key,value in [('heuristic_calls',0),('heuristic_wall_seconds',.1),
                          ('allocation_decisions',9),('solver_calls',1),
                          ('heuristic_optimal_batches',1),('heuristic_calls',True)]:
            report=report_fixture(); report['results'][0]['branches'][1]['metrics'][key]=value
            with self.subTest(key=key), self.assertRaisesRegex(H1OnlineAuditError,'runtime metric'): audit_report(report)

    def test_policy_and_record_settings_must_match_manifest(self):
        for location in ('settings','policy','record'):
            report=report_fixture(); branch=report['results'][0]['branches'][1]
            if location=='settings': target=report['settings']['policies'][1]
            elif location=='policy': target=branch['policy']['heuristic']
            else: target=branch['heuristic_records'][0]
            target['max_moves']=256
            with self.subTest(location=location), self.assertRaises(H1OnlineAuditError): audit_report(report)

    def test_base_audit_still_checks_raw_capacity_and_cp_sat_proofs(self):
        report=report_fixture()
        report['results'][0]['branches'][1]['allocation_decisions'][0]['inputs']['hospitals'][0]['committed']['bed']=1
        with self.assertRaisesRegex(H1OnlineAuditError,'free resources'): audit_report(report)
        report=report_fixture()
        report['results'][0]['branches'][2]['solver_records'][0]['stages'][1]['best_bound']=27
        with self.assertRaisesRegex(H1OnlineAuditError,'matching bound'): audit_report(report)

    def test_report_identity_fixed_settings_and_rotation_are_checked(self):
        for change in ('scenario','branch','order','settings','extra_policy'):
            report=report_fixture(); case=report['results'][0]
            if change=='scenario': case['scenario']['comparison_id']='old'
            elif change=='branch': case['branches'][1]['comparison_id']='old'
            elif change=='order': case['execution_order']=['H1','R1','C1']
            elif change=='settings': report['settings']['simulation_unit']='seconds'
            else: report['settings']['policies'][1]['observed_inquiry']={}
            with self.subTest(change=change), self.assertRaises(H1OnlineAuditError): audit_report(report)

    def test_raw_distribution_denominators_percentiles_and_priority_groups_are_checked(self):
        for change in ('arrival_p95_minutes','arrival_denominator','allocation_wall_p95_ms',
                       'allocation_wall_max_ms','allocation_decision_denominator','priority_groups'):
            report=report_fixture(); diagnostic=report['results'][0]['diagnostics']['H1']
            if change=='priority_groups': diagnostic[change]['4']['patients']=1
            else: diagnostic[change]=999
            with self.subTest(change=change), self.assertRaisesRegex(H1OnlineAuditError,'diagnostic'):
                audit_report(report)

    def test_raw_paired_tail_counts_delays_and_denominator_are_checked(self):
        for field in ('denominator','delayed','earlier','equal','max_delay_minutes','p95_difference_minutes'):
            report=report_fixture(); report['results'][0]['paired_arrival_details']['H1'][field]=999
            with self.subTest(field=field), self.assertRaisesRegex(H1OnlineAuditError,'paired arrival tail'):
                audit_report(report)

    def test_percentile_and_all_earlier_pair_preserve_direction(self):
        self.assertEqual(_p95(list(range(1,22))),20)
        self.assertIsNone(_p95([]))
        case=report_fixture()['results'][0]
        tails=_paired_tails(case['scenario'],case['branches'][1],case['branches'][0])
        self.assertEqual(tails,dict(denominator=1,delayed=0,earlier=1,equal=0,
                                   max_delay_minutes=0,p95_difference_minutes=-3))

    def test_two_case_execution_order_rotates_by_family_index(self):
        report=report_fixture(); case=copy.deepcopy(report['results'][0])
        case['scenario'].update(id='audit-tiny-2',family='second')
        case['execution_order']=['H1','C1','R1']
        report['families'].append('second'); report['results'].append(case)
        self.assertEqual(audit_report(report)['cases'],2)
        case['execution_order']=['R1','H1','C1']
        with self.assertRaisesRegex(H1OnlineAuditError,'rotation'): audit_report(report)

    def test_cli_writes_audited_json_and_preserves_input(self):
        report=report_fixture()
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            source=Path(directory)/'report.json'; output=Path(directory)/'audit.json'
            source.write_text(json.dumps(report),encoding='utf-8'); original=source.read_bytes()
            with redirect_stdout(StringIO()): main([str(source),'--output',str(output)])
            self.assertTrue(json.loads(output.read_text())['passed'])
            self.assertEqual(source.read_bytes(),original)
            with redirect_stdout(StringIO()), self.assertRaisesRegex(H1OnlineAuditError,'overwrite'):
                main([str(source),'--output',str(source)])
            old_output=output.read_bytes()
            with redirect_stdout(StringIO()), self.assertRaisesRegex(H1OnlineAuditError,'already exists'):
                main([str(source),'--output',str(output)])
            self.assertEqual(output.read_bytes(),old_output)


if __name__=='__main__': unittest.main()
