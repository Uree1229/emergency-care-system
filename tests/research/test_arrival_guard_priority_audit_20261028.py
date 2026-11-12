"""Recorded JSON corruption tests; no allocation implementation is imported."""
from copy import deepcopy
import subprocess
import sys
import unittest

from simulation import arrival_guard_priority_audit_20261028 as audit
from test_final_assignment_audit_20261028 import fixture as old_fixture


def fixture():
    controls = old_fixture()
    candidate = deepcopy(controls)
    candidate['comparison_id'] = audit.EXPERIMENT_ID
    candidate['execution_order'] = ['G2']
    candidate['scenario'] = deepcopy(candidate['original_scenario'])
    candidate['scenario'].update(online_allocation_comparison=True,
        source_experiment='online_allocation_comparison', comparison_id=audit.EXPERIMENT_ID)
    candidate['scenario'].setdefault('assumptions', []).extend(audit.ASSUMPTION_SUFFIX)
    branch = deepcopy(controls['branches'][2])
    branch.update(id='G2', comparison_id=audit.EXPERIMENT_ID,
        execution_contract=deepcopy(audit.EXECUTION_CONTRACT))
    branch['policy'].update(name='G2', rule='G2', arrival_guard_priority=deepcopy(audit.GUARD_METADATA))
    branch['policy'].pop('heuristic')
    original = deepcopy(branch.pop('heuristic_records')[0])
    original.pop('decision_index')
    decision = branch['allocation_decisions'][0]
    decision.pop('heuristic_record_index')
    decision.update(rule='G2', method='guarded_joint', guard_record_index=0, guard_status='GUARDED_HEURISTIC')
    record = dict(schema_version=1, rule=audit.GUARD_RULE, scope=audit.SCOPE,
        t=decision['t'], decision_index=0, status='GUARDED_HEURISTIC', optimal_proven=False,
        caller_ready_order=['P1', 'P2'], original_h1_record=original,
        original_h1_plan={'P1':'H2', 'P2':'H1'},
        baseline_ready_order_r1_plan={'P1':'H1', 'P2':None}, actual_plan={'P1':'H2', 'P2':'H1'},
        baseline_objective=[4, 8], original_h1_objective=[0, 28], actual_objective=[0, 28],
        baseline_assigned_count=1, original_h1_assigned_count=2, actual_assigned_count=2,
        baseline_priority4_assigned_count=1, original_h1_priority4_assigned_count=2,
        actual_priority4_assigned_count=2, same_current_count_priority4_gain=False,
        strict_current_count_gain=True, baseline_priority_cost_nonincrease=True,
        used_h1_proposal=True, reason='strict_current_count_gain_without_baseline_priority_cost',
        objective_change_disclosed=True, old_H1_local_objective_nonregression_claimed=False,
        whole_episode_nonregression_guaranteed=False, actual_arrival_nonregression_guaranteed=False,
        whole_episode_priority4_nonregression_guaranteed=False,
        parent_h1_source_sha256=audit.PARENT_SOURCE_SHA256,
        parent_h1_max_moves=2048, parent_h1_safety_seconds=10.0, wall_seconds=.0035)
    branch.update(guard_records=[record], heuristic_records=[])
    branch['metrics'].update(heuristic_calls=0, heuristic_wall_seconds=0,
        guard_calls=1, guard_wall_seconds=.0035, parent_h1_proposal_calls=1,
        parent_h1_proposal_wall_seconds=.003,
        optimality_scope='current_same_minute_accepted_offers_only')
    candidate['branches'] = [branch]
    candidate['routing_summary']['by_branch'] = {'G2':deepcopy(controls['routing_summary']['by_branch']['H1'])}
    candidate['duration_minutes'] = max(e['t'] for e in branch['events'])
    return dict(schema_version=1, comparison_id=audit.EXPERIMENT_ID,
        candidate=candidate, controls=controls, input_sha256=candidate['original_scenario_sha256'])


def candidate_and_record(value):
    branch = value['candidate']['branches'][0]
    return branch, branch['guard_records'][0]


def recorded_guard_problem(*, priority_cost_increase=False, priority4_gain=False):
    """Small recorded current snapshot; never emits simulated outcome events."""
    value=fixture(); scenario=value['candidate']['scenario']
    branch,record=candidate_and_record(value); decision=branch['allocation_decisions'][0]
    if priority_cost_increase:
        # Caller serves both priority4 patients. Priority/id greedy serves A,
        # then two priority1 patients: more people but worse priority miss.
        specs=[('B',4,2,['H1']),('A',4,2,['H1','H2']),
               ('C',1,1,['H2']),('D',1,1,['H2'])]
        capacities={'H1':2,'H2':2}
        baseline={'B':'H1','A':'H2','C':None,'D':None}
        parent={'B':None,'A':'H1','C':'H2','D':'H2'}
        bobj,hobj=[2,16],[4,12]
        reason='rejected_current_admission_gain_with_baseline_priority_cost'
        actual=baseline;actual_obj=bobj;use_parent=False;b4,h4,a4=2,1,2;same_top4_gain=False
    else:
        # Equal cardinality: priority-only replacement must retain caller plan.
        top_priority=4 if priority4_gain else 2
        specs=[('P1',1,1,['H1']),('P2',top_priority,1,['H1'])]
        capacities={'H1':1}
        baseline={'P1':'H1','P2':None}; parent={'P1':None,'P2':'H1'}
        bobj,hobj=[top_priority,2],[1,top_priority*2]
        reason=('same_current_count_priority4_gain_without_baseline_priority_cost' if priority4_gain
            else 'preserve_caller_nearest_without_count_or_priority4_gain')
        actual=parent if priority4_gain else baseline;actual_obj=hobj if priority4_gain else bobj
        use_parent=priority4_gain;b4=0;h4=int(priority4_gain);a4=int(priority4_gain);same_top4_gain=priority4_gain
    scenario['patients']=[dict(id=pid,occur_t=0,search_start_t=2,priority=priority,
        resource_demands={'bed':demand}) for pid,priority,demand,_ in specs]
    inputs=decision['inputs']
    inputs['patients']=[dict(id=pid,priority=priority,demand={'bed':demand},
        required_capabilities=['general'],position=[37,127],request_t=2,options=options,
        accepted_at={hid:3 for hid in options},valid_offer_expiries={hid:13 for hid in options})
        for pid,priority,demand,options in specs]
    inputs['hospitals']=[dict(id=hid,capabilities=['general'],capacity={'bed':cap},
        used={'bed':0},committed={'bed':0},queued={'bed':0}) for hid,cap in capacities.items()]
    inputs['free_resources']=[dict(hospital=hid,resource='bed',free=cap) for hid,cap in capacities.items()]
    inputs['travel_minutes']=[dict(patient=pid,hospital=hid,minutes=2)
        for pid,_,_,options in specs for hid in options]
    bc=sum(h is not None for h in baseline.values());hc=sum(h is not None for h in parent.values())
    record.update(caller_ready_order=[p[0] for p in specs],baseline_ready_order_r1_plan=baseline,
        original_h1_plan=parent,actual_plan=actual,baseline_objective=bobj,
        original_h1_objective=hobj,actual_objective=actual_obj,
        baseline_assigned_count=bc,original_h1_assigned_count=hc,actual_assigned_count=bc,
        baseline_priority4_assigned_count=b4,original_h1_priority4_assigned_count=h4,
        actual_priority4_assigned_count=a4,same_current_count_priority4_gain=same_top4_gain,
        strict_current_count_gain=hc>bc,baseline_priority_cost_nonincrease=hobj[0]<=bobj[0],
        used_h1_proposal=use_parent,reason=reason)
    original=record['original_h1_record']
    original.update(assignments=parent,objective=hobj,batch_size=len(specs),
        edge_count=sum(len(p[3]) for p in specs),assigned_count=hc,
        baseline_assignments=parent,baseline_objective=hobj,baseline_assigned_count=hc,
        evaluated_moves=0,accepted_moves=0,accepted_move_types=[],improvements=[])
    decision.update(assignments=actual,objective=dict(unassigned_priority_weight=actual_obj[0],
        priority_weighted_travel_minutes=actual_obj[1]))
    return scenario,branch


class ArrivalGuardAuditTests(unittest.TestCase):
    def test_same_count_priority4_gain_still_requires_weighted_miss_nonincrease(self):
        # Four weight2 patients consume both resources in caller order. The
        # parent instead admits weight4 plus three weight1 patients: same count
        # and more weight4, but total admitted weight falls 8 -> 7.
        value=fixture();scenario=value['candidate']['scenario']
        branch,record=candidate_and_record(value);decision=branch['allocation_decisions'][0]
        specs=[(f'B{i}',2,{'bed':1,'oxygen':1}) for i in range(4)]+[('H',4,{'oxygen':4})]+[
            (f'L{i}',1,{'bed':1}) for i in range(3)]
        baseline={pid:('H1' if pid.startswith('B') else None) for pid,_,_ in specs}
        parent={pid:(None if pid.startswith('B') else 'H1') for pid,_,_ in specs}
        scenario['patients']=[dict(id=pid,occur_t=0,search_start_t=2,priority=priority,
            resource_demands=deepcopy(demand)) for pid,priority,demand in specs]
        inputs=decision['inputs']
        inputs['patients']=[dict(id=pid,priority=priority,demand=demand,required_capabilities=['general'],
            position=[37,127],request_t=2,options=['H1'],accepted_at={'H1':3},
            valid_offer_expiries={'H1':13}) for pid,priority,demand in specs]
        inputs['hospitals']=[dict(id='H1',capabilities=['general'],capacity={'bed':4,'oxygen':4},
            used={'bed':0,'oxygen':0},committed={'bed':0,'oxygen':0},queued={'bed':0,'oxygen':0})]
        inputs['free_resources']=[dict(hospital='H1',resource=r,free=4) for r in ('bed','oxygen')]
        inputs['travel_minutes']=[dict(patient=pid,hospital='H1',minutes=2) for pid,_,_ in specs]
        record.update(caller_ready_order=[pid for pid,_,_ in specs],baseline_ready_order_r1_plan=baseline,
            original_h1_plan=parent,actual_plan=baseline,baseline_objective=[7,16],
            original_h1_objective=[8,14],actual_objective=[7,16],
            baseline_assigned_count=4,original_h1_assigned_count=4,actual_assigned_count=4,
            baseline_priority4_assigned_count=0,original_h1_priority4_assigned_count=1,actual_priority4_assigned_count=0,
            strict_current_count_gain=False,same_current_count_priority4_gain=True,
            baseline_priority_cost_nonincrease=False,used_h1_proposal=False,
            reason='rejected_current_admission_gain_with_baseline_priority_cost')
        record['original_h1_record'].update(assignments=parent,objective=[8,14],batch_size=8,edge_count=8,
            assigned_count=4,baseline_assignments=parent,baseline_objective=[8,14],baseline_assigned_count=4,
            evaluated_moves=0,accepted_moves=0,accepted_move_types=[],improvements=[])
        decision.update(assignments=baseline,objective=dict(unassigned_priority_weight=7,priority_weighted_travel_minutes=16))
        self.assertEqual(audit._guard_decisions(scenario,branch)['guard_baselines_used'],1)
        record['used_h1_proposal']=True
        with self.assertRaisesRegex(audit.ArrivalGuardAuditError,'guard boolean'):
            audit._guard_decisions(scenario,branch)

    def test_equal_count_priority4_gain_is_used_and_old_proposal_remains_separate(self):
        scenario,branch=recorded_guard_problem(priority4_gain=True)
        totals=audit._guard_decisions(scenario,branch)
        self.assertEqual(totals['guard_parent_proposals_used'],1)
        self.assertEqual(branch['allocation_decisions'][0]['assignments'],{'P1':None,'P2':'H1'})
        self.assertEqual(branch['guard_records'][0]['baseline_ready_order_r1_plan'],{'P1':'H1','P2':None})
        branch['allocation_decisions'][0]['assignments']={'P1':'H1','P2':None}
        with self.assertRaisesRegex(audit.ArrivalGuardAuditError,'actual G2 plan'):
            audit._guard_decisions(scenario,branch)

    def test_priority_only_parent_swap_retains_caller_nearest(self):
        scenario,branch=recorded_guard_problem()
        totals=audit._guard_decisions(scenario,branch)
        self.assertEqual(totals['guard_baselines_used'],1)
        self.assertEqual(branch['allocation_decisions'][0]['assignments'],{'P1':'H1','P2':None})
        branch['allocation_decisions'][0]['assignments']={'P1':None,'P2':'H1'}
        with self.assertRaisesRegex(audit.ArrivalGuardAuditError,'actual G2 plan'):
            audit._guard_decisions(scenario,branch)

    def test_more_people_with_worse_priority_cost_is_rejected(self):
        scenario,branch=recorded_guard_problem(priority_cost_increase=True)
        totals=audit._guard_decisions(scenario,branch)
        self.assertEqual(totals['guard_baselines_used'],1)
        self.assertEqual(branch['guard_records'][0]['original_h1_assigned_count'],3)
        self.assertEqual(branch['guard_records'][0]['actual_assigned_count'],2)
        branch['guard_records'][0]['used_h1_proposal']=True
        with self.assertRaisesRegex(audit.ArrivalGuardAuditError,'guard boolean'):
            audit._guard_decisions(scenario,branch)

    def test_full4_preserves_old_h1_and_independently_checks_guard(self):
        value = fixture(); before = deepcopy(value)
        result = audit.audit_bundle(value)
        self.assertEqual(value, before)
        self.assertTrue(result['passed'])
        self.assertEqual(list(result['branch_metrics']), ['R0', 'R1', 'H1', 'G2'])
        self.assertEqual(set(result['paired']), {'R0', 'R1', 'H1'})
        self.assertEqual(result['paired']['H1']['arrived_lost_ids'], [])
        self.assertEqual(result['paired']['R1']['selected_gained_ids'], ['P2'])
        self.assertEqual(result['candidate_audit']['counters']['guard_batches_checked'], 1)
        self.assertFalse(result['H1_algorithm_reexecuted'])
        self.assertFalse(result['full_resource_history_replayed'])
        self.assertEqual(result['current_phase_raw_branches_audited'], 4)

    def test_candidate_only_has_complete_endpoint_denominator(self):
        value = fixture()
        result = audit.audit_candidate(value['candidate'])
        self.assertEqual(result['patients'], 2)
        self.assertEqual(result['branch_metrics']['G2']['arrival_denominator'], 2)
        self.assertEqual(len(result['patient_vectors']['G2']), 2)
        self.assertTrue(result['original_H1_proposal_record_checked_separately'])
        self.assertFalse(result['original_H1_proposal_treated_as_actual'])

    def test_guard_count_tamper_is_rejected(self):
        for field in ('baseline_assigned_count', 'original_h1_assigned_count', 'actual_assigned_count',
            'baseline_priority4_assigned_count','original_h1_priority4_assigned_count','actual_priority4_assigned_count'):
            with self.subTest(field=field):
                value=fixture(); _, record=candidate_and_record(value); record[field]+=1
                with self.assertRaisesRegex(audit.ArrivalGuardAuditError, 'guard mathematics'):
                    audit.audit_candidate(value['candidate'])

    def test_guard_boolean_gate_cannot_be_inverted(self):
        for field in ('strict_current_count_gain', 'same_current_count_priority4_gain',
            'baseline_priority_cost_nonincrease', 'used_h1_proposal'):
            with self.subTest(field=field):
                value=fixture(); _, record=candidate_and_record(value); record[field]=not record[field]
                with self.assertRaisesRegex(audit.ArrivalGuardAuditError, 'guard boolean'):
                    audit.audit_candidate(value['candidate'])

    def test_wrong_caller_order_and_false_baseline_are_rejected(self):
        for mutation in ('order', 'plan', 'objective', 'reason'):
            value=fixture(); _,record=candidate_and_record(value)
            if mutation=='order':record['caller_ready_order'].reverse()
            if mutation=='plan':record['baseline_ready_order_r1_plan']={'P1':'H2', 'P2':'H1'}
            if mutation=='objective':record['baseline_objective']=[0, 28]
            if mutation=='reason':record['reason']='prefer_later_episode_arrival'
            with self.subTest(mutation=mutation), self.assertRaises(audit.ArrivalGuardAuditError):
                audit.audit_candidate(value['candidate'])

    def test_original_h1_proposal_remains_separate_and_checked(self):
        for mutation in ('assignment', 'parent_baseline', 'index', 'trace'):
            value=fixture(); _,record=candidate_and_record(value)
            original=record['original_h1_record']
            if mutation=='assignment':original['assignments']['P1']='H1'
            if mutation=='parent_baseline':original['baseline_assignments']['P1']='H2'
            if mutation=='index':original['decision_index']=0
            if mutation=='trace':original['improvements'][0]['objective']=[0, 27]
            with self.subTest(mutation=mutation), self.assertRaises(audit.ArrivalGuardAuditError):
                audit.audit_candidate(value['candidate'])

    def test_parent_budget_source_and_deadline_are_enforced(self):
        for mutation in ('source', 'max_moves', 'safety', 'deadline', 'search_time'):
            value=fixture(); _,record=candidate_and_record(value)
            if mutation=='source':record['parent_h1_source_sha256']='0'*64
            if mutation=='max_moves':record['original_h1_record']['max_moves']=4096
            if mutation=='safety':record['parent_h1_safety_seconds']=.05
            if mutation=='deadline':record['original_h1_record']['search_stop_reason']='time_limit'
            if mutation=='search_time':record['original_h1_record']['search_seconds']=10.0
            with self.subTest(mutation=mutation), self.assertRaises(audit.ArrivalGuardAuditError):
                audit.audit_candidate(value['candidate'])

    def test_infeasible_original_proposal_is_rejected_before_objective(self):
        value=fixture(); _,record=candidate_and_record(value)
        record['original_h1_plan']={'P1':'H1', 'P2':'H1'}
        with self.assertRaisesRegex(audit.ArrivalGuardAuditError, 'proposal infeasible'):
            audit.audit_candidate(value['candidate'])

    def test_actual_commits_cannot_be_relabelled_as_parent(self):
        value=fixture(); branch,_=candidate_and_record(value)
        branch['allocation_decisions'][0]['assignments']={'P1':'H1', 'P2':None}
        with self.assertRaises(audit.ArrivalGuardAuditError):audit.audit_candidate(value['candidate'])

    def test_current_resource_and_offer_checks_are_still_active(self):
        for mutation in ('ledger', 'future', 'hidden'):
            value=fixture(); branch,_=candidate_and_record(value)
            inputs=branch['allocation_decisions'][0]['inputs']
            if mutation=='ledger':inputs['hospitals'][0]['committed']['bed']=1
            if mutation=='future':inputs['patients'][0]['accepted_at']['H1']=5
            if mutation=='hidden':inputs['future_acceptance_schedule']={}
            with self.subTest(mutation=mutation), self.assertRaises(audit.ArrivalGuardAuditError):
                audit.audit_candidate(value['candidate'])

    def test_orphan_reused_and_misdirected_guard_records_fail(self):
        for mutation in ('orphan', 'missing', 'index', 'time'):
            value=fixture(); branch,record=candidate_and_record(value)
            if mutation=='orphan':branch['guard_records'].append(deepcopy(record))
            if mutation=='missing':branch['allocation_decisions'][0]['guard_record_index']=1
            if mutation=='index':record['decision_index']=1
            if mutation=='time':record['t']+=1
            with self.subTest(mutation=mutation), self.assertRaises(audit.ArrivalGuardAuditError):
                audit.audit_candidate(value['candidate'])

    def test_runtime_and_optimality_claims_are_checked(self):
        for mutation in ('runtime', 'guard_wall', 'parent_wall', 'proof', 'guarantee', 'priority4_guarantee'):
            value=fixture(); branch,record=candidate_and_record(value)
            if mutation=='runtime':branch['metrics']['parent_h1_proposal_calls']=2
            if mutation=='guard_wall':record['wall_seconds']=.005
            if mutation=='parent_wall':record['wall_seconds']=.001
            if mutation=='proof':record['optimal_proven']=True
            if mutation=='guarantee':record['actual_arrival_nonregression_guaranteed']=True
            if mutation=='priority4_guarantee':record['whole_episode_priority4_nonregression_guaranteed']=True
            with self.subTest(mutation=mutation), self.assertRaises(audit.ArrivalGuardAuditError):
                audit.audit_candidate(value['candidate'])

    def test_input_source_physical_augmentation_and_hash_are_bound(self):
        for mutation in ('hash', 'physical', 'source', 'bundle'):
            value=fixture()
            if mutation=='hash':value['candidate']['original_scenario_sha256']='0'*64
            if mutation=='physical':value['candidate']['scenario']['patients'][0]['priority']=1
            if mutation=='source':value['candidate']['original_scenario']['patients'][0]['priority']=1
            if mutation=='bundle':value['input_sha256']='0'*64
            with self.subTest(mutation=mutation), self.assertRaises(audit.ArrivalGuardAuditError):audit.audit_bundle(value)

    def test_controls_reaudited_and_not_mixed_with_candidate_routing(self):
        for mutation in ('controls', 'routing', 'order'):
            value=fixture()
            if mutation=='controls':value['controls']['branches'][1]['allocation_decisions'][0]['method']='first_accept'
            if mutation=='routing':value['controls']['routing_input_sha256']='0'*64
            kwargs={'expected_control_order':['H1','R1','R0']} if mutation=='order' else {}
            with self.subTest(mutation=mutation), self.assertRaises(audit.ArrivalGuardAuditError):audit.audit_bundle(value, **kwargs)

    def test_cached_known_requires_id_and_exact_source(self):
        value=fixture(); value['controls']=None
        with self.assertRaisesRegex(audit.ArrivalGuardAuditError, 'known reference'):audit.audit_bundle(value)
        with self.assertRaisesRegex(audit.ArrivalGuardAuditError, 'unknown known'):
            audit.audit_bundle(value, known_case_id='not_a_case',known_reference='original')
        first=next(iter(audit.load_known_references()))
        with self.assertRaisesRegex(audit.ArrivalGuardAuditError, 'known control source hash'):
            audit.audit_bundle(value, known_case_id=first,known_reference='original')
        with self.assertRaisesRegex(audit.ArrivalGuardAuditError,'known reference'):
            audit.audit_bundle(value,known_case_id=first,known_reference='unregistered')
        g1case=next(iter(audit.load_known_references('g1validation')))
        with self.assertRaisesRegex(audit.ArrivalGuardAuditError,'known control source hash'):
            audit.audit_bundle(value,known_case_id=g1case,known_reference='g1validation')

    def test_known_reference_loader_is_hash_bound_and_returns_deepcopy(self):
        import hashlib
        for reference,(path,digest,_) in audit.KNOWN_REPORTS.items():
            refs=audit.load_known_references(reference); self.assertEqual(len(refs),345)
            key=next(iter(refs)); refs[key]['audit']['patient_vectors']['H1'].clear()
            again=audit.load_known_references(reference)
            self.assertGreater(len(again[key]['audit']['patient_vectors']['H1']),0)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),digest)
            self.assertEqual(set(again[key]['audit']['patient_vectors']),
                {'R0','R1','H1'} if reference=='original' else {'R0','R1','H1','G1'})

    def test_no_simulator_policy_or_solver_import_in_clean_process(self):
        script = ('import sys; from simulation import arrival_guard_priority_audit_20261028; '
            'forbidden={"simulation.sim","simulation.map_replay","simulation.heuristic_assignment",'
            '"simulation.cp_sat_assignment","simulation.arrival_guard_policy_20261028",'
            '"simulation.arrival_guard_engine_20261028"}; '
            'assert not forbidden.intersection(sys.modules)')
        result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_caller_order_math_differs_from_priority_order_and_consumes_resources(self):
        patients=[dict(id='low',priority=1,demand={'bed':1},request_t=0),
            dict(id='high',priority=4,demand={'bed':1},request_t=0)]
        options={'low':['B','A'],'high':['A']}
        travel={('low','B'):2,('low','A'):2,('high','A'):2}
        free={('A','bed'):1,('B','bed'):0}
        self.assertEqual(audit._nearest(patients,free,options,travel),{'low':'A','high':None})
        self.assertEqual(audit.inherited._greedy(patients,free,options,travel),{'high':'A','low':None})


if __name__=='__main__':unittest.main()
