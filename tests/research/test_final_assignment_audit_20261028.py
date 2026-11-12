"""Pure recorded-JSON corruption checks; no simulator or solver is imported."""
from copy import deepcopy
import json
import sys
import unittest

from simulation import final_assignment_audit_20261028 as audit
from test_h1_online_audit import report_fixture


def fixture():
    prior=report_fixture();case=prior['results'][0]
    original=deepcopy(case['scenario']);original.pop('comparison_id')
    r1,h1,_=deepcopy(case['branches'])
    r0=deepcopy(r1);r0['id']='R0';r0['policy'].update(name='R0',rule='R0')
    for decision in r0['allocation_decisions']:
        decision.update(rule='R0',method='first_accept')
    h1['policy']['heuristic']['time_limit_seconds']=10.0
    for record in h1['heuristic_records']:
        record.update(time_limit_seconds=10.0,search_stop_reason='search_complete')
    source_sha=audit.canonical_sha256(original);route_sha='1'*64
    for branch in (r0,r1,h1):
        branch.update(comparison_id=audit.EXPERIMENT_ID,
            original_scenario_sha256=source_sha,routing_input_sha256=route_sha,
            execution_contract=deepcopy(audit.EXECUTION_CONTRACT))
    physical=deepcopy(original)
    physical.update(online_allocation_comparison=True,
        source_experiment='online_allocation_comparison',comparison_id=audit.EXPERIMENT_ID)
    physical.setdefault('assumptions',[]).extend(audit.ASSUMPTION_SUFFIX)
    route=dict(provider='synthetic',scope='scenario',live_traffic=False,ambulance_priority=False,
        road_routes=0,fallback_routes=0,synthetic_routes=2,routes_evaluated=2)
    return dict(schema_version=2,source_experiment='online_allocation_comparison',
        comparison_id=audit.EXPERIMENT_ID,scenario=physical,original_scenario=original,
        input_scenario_sha256=source_sha,original_scenario_sha256=source_sha,
        routing_input_sha256=route_sha,branches=[r0,r1,h1],execution_order=['R0','R1','H1'],
        duration_minutes=max(e['t'] for b in (r0,r1,h1) for e in b['events']),
        routing_summary=dict(provider='synthetic',scope='independent_per_branch',
            live_traffic=False,ambulance_priority=False,shared_mutable_routing_cache=False,
            external_route_provider_allowed=False,
            by_branch={c:deepcopy(route) for c in audit.POLICY_IDS}))


class FinalAssignmentAuditTests(unittest.TestCase):
    def test_complete_raw_case_verifies_without_policy_execution(self):
        value=fixture();result=audit.audit_case(value)
        self.assertTrue(result['passed']);self.assertEqual(result['patients'],2)
        self.assertEqual(result['branch_metrics']['H1']['unassigned_priority_fraction'],0)
        self.assertEqual(result['branch_metrics']['R1']['unassigned_priority_fraction'],.5)
        self.assertFalse(result['H1_algorithm_reexecuted'])
        self.assertFalse(result['full_resource_history_replayed'])

    def test_failure_capped_denominator_does_not_drop_unarrived_patient(self):
        result=audit.audit_case(fixture());metrics=result['branch_metrics']['R1']
        self.assertEqual(metrics['arrival_denominator'],1)
        self.assertEqual(metrics['arrival_observed_denominator'],1)
        self.assertEqual(metrics['arrival_capped_mean_minutes'],13)
        self.assertEqual(metrics['arrival_capped_p95_minutes'],20)
        self.assertEqual(metrics['arrival_p95_minutes'],6)

    def test_more_arrivals_and_delay_are_both_retained(self):
        paired=audit.audit_case(fixture())['paired']['R1']
        self.assertEqual(paired['arrived_gained_ids'],['P2'])
        self.assertEqual(paired['selected_gained_ids'],['P2'])
        self.assertEqual(paired['common_arrival_denominator'],1)
        self.assertEqual(paired['common_arrival_mean_delta_minutes'],3)
        self.assertEqual(paired['maximum_common_arrival_delay_minutes'],3)
        self.assertEqual(paired['unassigned_priority_fraction_delta'],-.5)

    def test_first_reply_rule_cannot_silently_act_as_nearest(self):
        value=fixture();branch=value['branches'][0]
        branch['allocation_decisions'][0]['inputs']['patients'][0]['accepted_at']['H1']=4
        branch['allocation_decisions'][0]['inputs']['patients'][0]['valid_offer_expiries']['H1']=14
        with self.assertRaisesRegex(audit.FinalAssignmentAuditError,'choice mismatch'):
            audit._simple_choice(value['scenario'],branch)

    def test_nearest_rule_cannot_choose_a_farther_valid_offer(self):
        value=fixture();branch=value['branches'][1]
        branch['allocation_decisions'][0]['assignments']={'P1':'H2'}
        with self.assertRaisesRegex(audit.FinalAssignmentAuditError,'choice mismatch'):
            audit._simple_choice(value['scenario'],branch)

    def test_deadline_stop_is_technical_failure_not_an_h1_outcome(self):
        for reason in ('time_limit','time_limit_zero','unknown_stop'):
            value=fixture();value['branches'][2]['heuristic_records'][0]['search_stop_reason']=reason
            with self.assertRaisesRegex(audit.FinalAssignmentAuditError,'deadline'):
                audit.audit_case(value)

    def test_safety_seconds_and_move_limits_are_frozen(self):
        for field,new in [('time_limit_seconds',.05),('max_moves',4096)]:
            value=fixture();value['branches'][2]['policy']['heuristic'][field]=new
            with self.assertRaises(audit.FinalAssignmentAuditError):audit.audit_case(value)

    def test_individual_arm_query_configuration_drift_rejected(self):
        for field,value in [('k',1),('wait',0),('notify',False),('adaptive_inquiry',{}),('inquiry_ranking','recent_load')]:
            replay=fixture();replay['branches'][0]['policy'][field]=value
            with self.assertRaises(audit.FinalAssignmentAuditError):audit.audit_case(replay)

    def test_branch_world_or_routing_fingerprint_mismatch_rejected(self):
        for key in ('original_scenario_sha256','routing_input_sha256'):
            value=fixture();value['branches'][0][key]='0'*64
            with self.assertRaises(audit.FinalAssignmentAuditError):audit.audit_case(value)

    def test_altered_physical_world_after_source_hash_rejected(self):
        value=fixture();value['scenario']['patients'][0]['priority']=1
        with self.assertRaisesRegex(audit.FinalAssignmentAuditError,'physical inputs'):
            audit.audit_case(value)

    def test_original_input_hash_and_manifest_hash_must_both_match(self):
        value=fixture();sha=value['input_scenario_sha256']
        self.assertTrue(audit.audit_case(value,scenario_sha256=sha,source_scenario=value['original_scenario'])['passed'])
        with self.assertRaises(audit.FinalAssignmentAuditError):audit.audit_case(value,scenario_sha256='0'*64)

    def test_arm_order_and_execution_rotation_checked_separately(self):
        value=fixture()
        with self.assertRaises(audit.FinalAssignmentAuditError):audit.audit_case(value,expected_order=['H1','R1','R0'])
        value['branches'].reverse()
        with self.assertRaises(audit.FinalAssignmentAuditError):audit.audit_case(value)

    def test_future_or_hidden_policy_input_is_rejected(self):
        value=fixture();value['branches'][2]['allocation_decisions'][0]['inputs']['future_schedule']=[]
        with self.assertRaisesRegex(audit.FinalAssignmentAuditError,'unknown input'):
            audit.audit_case(value)

    def test_current_resource_overcommit_or_inconsistent_ledger_rejected(self):
        value=fixture()
        value['branches'][2]['allocation_decisions'][0]['inputs']['hospitals'][0]['capacity']['bed']=0
        with self.assertRaises(audit.FinalAssignmentAuditError):audit.audit_case(value)

    def test_commit_event_or_outcome_tamper_rejected(self):
        for kind in ('event','outcome'):
            value=fixture()
            if kind=='event':
                value['branches'][2]['events']=[e for e in value['branches'][2]['events'] if not(e['type']=='commit' and e['patient']=='P2')]
            else:value['branches'][2]['patient_outcomes'][0]['inquiries']=1
            with self.assertRaises(audit.FinalAssignmentAuditError):audit.audit_case(value)

    def test_pair_ids_clocks_and_priority_cannot_be_swapped(self):
        vectors=audit.audit_case(fixture())['patient_vectors']
        for field,new in [('id','OTHER'),('priority',1),('occur_t',1),('observation_end_t',21)]:
            after=deepcopy(vectors['H1']);after[0][field]=new
            with self.assertRaises(audit.FinalAssignmentAuditError):audit.paired_summary(vectors['R1'],after)

    def test_aggregate_keeps_stress_and_main_in_separate_groups(self):
        value=audit.audit_case(fixture());cases=[]
        for track,stratum,seed in [('main','single',1),('main','surge',2),('stress','scarcity',3)]:
            cases.append(dict(track=track,stratum=stratum,seed=seed,scenario_sha256=value['scenario_sha256'],audit=deepcopy(value)))
        result=audit.audit_aggregate(dict(cases=cases))
        self.assertEqual(result['cases'],3);self.assertEqual(result['branches'],9)
        self.assertEqual(len(result['groups']),3)
        self.assertFalse(result['strata_pooled_for_efficacy'])
        self.assertFalse(result['bootstrap_confidence_intervals_independently_checked'])

    def test_aggregate_rejects_duplicate_cases_and_tampered_denominators(self):
        value=audit.audit_case(fixture())
        case=dict(track='main',stratum='single',seed=1,scenario_sha256=value['scenario_sha256'],audit=value)
        with self.assertRaises(audit.FinalAssignmentAuditError):audit.audit_aggregate(dict(cases=[case,case]))
        bad=deepcopy(case);bad['audit']['branch_metrics']['H1']['unassigned']=1
        with self.assertRaises(audit.FinalAssignmentAuditError):audit.audit_aggregate(dict(cases=[bad]))

    def test_audit_does_not_mutate_or_import_model_or_simulator(self):
        before=set(sys.modules);value=fixture();copy=deepcopy(value)
        audit.audit_case(value)
        self.assertEqual(value,copy)
        newly=set(sys.modules)-before
        self.assertFalse(any(name.startswith('ortools') or name in ('simulation.sim','simulation.heuristic_assignment') for name in newly))

    def test_json_roundtrip_preserves_vectors_and_all_counts(self):
        result=audit.audit_case(fixture())
        self.assertEqual(json.loads(json.dumps(result,allow_nan=False)),result)
        self.assertEqual(sum(result['patient_vectors']['R1'][i]['arrival_capped_minutes'] for i in range(2)),26)


if __name__=='__main__':unittest.main(verbosity=2)
