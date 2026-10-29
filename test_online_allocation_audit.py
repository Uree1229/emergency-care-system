import copy
import unittest

from simulation.online_allocation_audit import (
    OnlineAllocationAuditError, audit_branch, audit_report, paired_common_metrics,
    raw_branch_metrics,
)


def endpoint_fixture():
    scenario = dict(id='episode', family='changes', seed=10101, max_minutes=20,
        patients=[dict(id='P1', occur_t=0, search_start_t=2, priority=4,
                       resource_demands={'bed': 1}),
                  dict(id='P2', occur_t=1, search_start_t=3, priority=1,
                       resource_demands={'bed': 1})])
    events = [dict(t=2, type='ask', patient='P1', hospital='H1'),
              dict(t=3, type='ask', patient='P2', hospital='H1'),
              dict(t=4, type='accept', patient='P1', hospital='H1'),
              dict(t=4, type='commit', patient='P1', hospital='H1'),
              dict(t=6, type='notify', patient='P1', hospital='H1'),
              dict(t=7, type='accept', patient='P1', hospital='H2'),
              dict(t=7, type='redirect', patient='P1', hospital='H2', previous='H1'),
              dict(t=7, type='commit', patient='P1', hospital='H2'),
              dict(t=9, type='arrive', patient='P1', hospital='H2'),
              dict(t=9, type='arrival_unable', patient='P1', hospital='H2'),
              dict(t=9, type='no_response', patient='P2', hospital='H1'),
              dict(t=12, type='transfer_depart', patient='P1', hospital='H3', previous='H2'),
              dict(t=12, type='commit', patient='P1', hospital='H3'),
              dict(t=14, type='arrive', patient='P1', hospital='H3'),
              dict(t=14, type='treatment_start', patient='P1', hospital='H3'),
              dict(t=17, type='treatment_complete', patient='P1', hospital='H3')]
    outcomes = [dict(id='P1', occur_t=0, search_start_t=2, priority=4,
                    first_commit_t=4, final_arrive_t=14, treatment_start_t=14,
                    treatment_complete_t=17, inquiries=1, redirects=1, transfers=1, state='COMPLETE'),
                dict(id='P2', occur_t=1, search_start_t=3, priority=1,
                    first_commit_t=None, final_arrive_t=None, treatment_start_t=None,
                    treatment_complete_t=None, inquiries=1, redirects=0, transfers=0, state='UNASSIGNED')]
    branch = dict(id='R1', events=events, patient_outcomes=outcomes,
        metrics={'observation_end_t': 20}, allocation_decisions=[], solver_records=[])
    return scenario, branch


def decision_fixture():
    scenario = dict(id='tiny', family='normal', seed=10101, max_minutes=20,
        accept_ttl_minutes=10,
        patients=[dict(id='P', occur_t=0, search_start_t=2, priority=4,
                       resource_demands={'bed': 1})])
    inputs = dict(patients=[dict(id='P', priority=4, demand={'bed': 1},
        required_capabilities=['general'], position=[37, 127], request_t=2,
        options=['H'], accepted_at={'H': 3}, valid_offer_expiries={'H': 13})],
        hospitals=[dict(id='H', capabilities=['general'], capacity={'bed': 4},
                        used={'bed': 1}, committed={'bed': 1}, queued={'bed': 1})],
        free_resources=[dict(hospital='H', resource='bed', free=1)],
        travel_minutes=[dict(patient='P', hospital='H', minutes=5)])
    decision = dict(t=4, kind='batch', rule='C1', inputs=inputs, assignments={'P': 'H'},
        objective={'unassigned_priority_weight': 0, 'priority_weighted_travel_minutes': 20},
        method='cp_sat', elapsed_seconds=.001, solver_record_index=0)
    record = dict(t=4, decision_index=0, scope='current_same_minute_accepted_offers',
        assignments={'P': 'H'}, objective=[0, 20], batch_size=1, edge_count=1,
        status='OPTIMAL', optimal_proven=True, primary_optimal_proven=True,
        secondary_optimal_proven=True, fallback=False,
        stages=[dict(status='OPTIMAL', objective=4, best_bound=4, objective_name='assigned_priority_weight'),
                dict(status='OPTIMAL', objective=20, best_bound=20, objective_name='priority_weighted_travel_minutes')])
    branch = dict(id='C1', source_experiment='online_allocation_comparison', metrics={'observation_end_t': 20},
        events=[dict(t=3, type='accept', patient='P', hospital='H'),
                dict(t=4, type='commit', patient='P', hospital='H'),
                dict(t=9, type='arrive', patient='P', hospital='H'),
                dict(t=9, type='treatment_start', patient='P', hospital='H'),
                dict(t=12, type='treatment_complete', patient='P', hospital='H')],
        patient_outcomes=[dict(id='P', occur_t=0, search_start_t=2, priority=4,
            first_commit_t=4, final_arrive_t=9, treatment_start_t=9,
            treatment_complete_t=12, inquiries=0, redirects=0, transfers=0, state='COMPLETE')],
        allocation_decisions=[decision], solver_records=[record])
    return scenario, branch


class OnlineAllocationAuditTests(unittest.TestCase):
    def test_first_selection_is_not_counted_again_for_redirect_and_transfer(self):
        scenario, branch = endpoint_fixture()
        result = audit_branch(scenario, branch)
        self.assertEqual(result['counts']['first_selections'], 1)
        self.assertEqual(result['counts']['all_commit_events'], 3)
        self.assertEqual(result['metrics']['selection_mean_minutes'], 2)
        self.assertEqual(result['metrics']['selection_capped_mean_minutes'], 9.5)

    def test_arrival_means_include_failed_case_in_capped_denominator(self):
        scenario, branch = endpoint_fixture()
        metrics = raw_branch_metrics(scenario, branch)
        self.assertEqual(metrics['arrival_denominator'], 1)
        self.assertEqual(metrics['arrival_mean_minutes'], 14)
        self.assertEqual(metrics['arrival_capped_mean_minutes'], 16.5)
        self.assertEqual(metrics['untreated'], 1)

    def test_notification_lag_and_unresolved_notifications_are_distinct(self):
        scenario, branch = endpoint_fixture()
        branch['events'].append(dict(t=18, type='notify', patient='P1', hospital='H3'))
        metrics = raw_branch_metrics(scenario, branch)
        self.assertEqual(metrics['notifications'], 2)
        self.assertEqual(metrics['notification_redirect_pairs'], 1)
        self.assertEqual(metrics['notification_without_redirect'], 1)
        self.assertEqual(metrics['notify_to_redirect_mean_minutes'], 1)

    def test_common_patient_ids_do_not_compare_survivor_averages(self):
        scenario, baseline = endpoint_fixture()
        comparison = copy.deepcopy(baseline)
        comparison['events'] = [e for e in comparison['events'] if e['type'] != 'treatment_complete']
        for event in comparison['events']:
            if event['type'] in ('arrive', 'treatment_start') and event['t'] == 14:
                event['t'] = 16
        comparison['patient_outcomes'][0].update(final_arrive_t=16,
            treatment_start_t=16, treatment_complete_t=None, state='INCOMPLETE')
        metrics = paired_common_metrics(scenario, baseline, comparison)
        self.assertEqual(metrics['arrival']['patient_ids'], ['P1'])
        self.assertEqual(metrics['arrival']['denominator'], 1)
        self.assertEqual(metrics['arrival']['mean_difference_minutes'], 2)

    def test_outcomes_cannot_override_raw_events(self):
        scenario, branch = endpoint_fixture()
        branch['patient_outcomes'][0]['final_arrive_t'] = 9
        with self.assertRaisesRegex(OnlineAllocationAuditError, 'outcome/event'):
            raw_branch_metrics(scenario, branch)

    def test_failed_arrival_is_not_final_arrival(self):
        scenario, branch = endpoint_fixture()
        branch['events'] = [e for e in branch['events'] if e['t'] < 12]
        branch['patient_outcomes'][0].update(final_arrive_t=None, treatment_start_t=None,
            treatment_complete_t=None, transfers=0, state='NOT_TREATED')
        self.assertEqual(raw_branch_metrics(scenario, branch)['arrival_denominator'], 0)

    def test_independent_ledger_and_current_batch_optimum(self):
        scenario, branch = decision_fixture()
        result = audit_branch(scenario, branch)
        self.assertEqual(result['counts']['cp_sat_two_stage_proofs'], 1)
        self.assertEqual(result['counts']['exact_rechecks'], 1)
        self.assertFalse(result['counts']['whole_trajectory_optimality_claimed'])

    def test_saved_free_cannot_ignore_committed_or_queued_demand(self):
        scenario, branch = decision_fixture()
        branch['allocation_decisions'][0]['inputs']['hospitals'][0]['queued']['bed'] = 2
        with self.assertRaisesRegex(OnlineAllocationAuditError, 'capacity - used'):
            audit_branch(scenario, branch)

    def test_both_free_and_assignment_cannot_hide_overallocation(self):
        scenario, branch = decision_fixture()
        inputs = branch['allocation_decisions'][0]['inputs']
        inputs['hospitals'][0]['queued']['bed'] = 2
        inputs['free_resources'][0]['free'] = 0
        with self.assertRaisesRegex(OnlineAllocationAuditError, 'resource ledger'):
            audit_branch(scenario, branch)

    def test_future_and_expired_acceptances_rejected(self):
        for accepted in (5, -7):
            scenario, branch = decision_fixture()
            branch['allocation_decisions'][0]['inputs']['patients'][0]['accepted_at']['H'] = accepted
            with self.assertRaisesRegex(OnlineAllocationAuditError, 'expired acceptance'):
                audit_branch(scenario, branch)

    def test_future_release_fields_cannot_enter_current_solver_view(self):
        scenario, branch = decision_fixture()
        branch['allocation_decisions'][0]['inputs']['hospitals'][0]['release_at'] = 6
        with self.assertRaisesRegex(OnlineAllocationAuditError, 'snapshot field'):
            audit_branch(scenario, branch)

    def test_false_optimal_proof_and_future_trajectory_claims_rejected(self):
        scenario, branch = decision_fixture()
        branch['solver_records'][0]['stages'][1]['status'] = 'FEASIBLE'
        with self.assertRaisesRegex(OnlineAllocationAuditError, 'proof flag'):
            audit_branch(scenario, branch)
        scenario, branch = decision_fixture()
        branch['metrics']['whole_episode_optimal_proven'] = True
        with self.assertRaisesRegex(OnlineAllocationAuditError, 'whole trajectory'):
            audit_branch(scenario, branch)

    def test_unreceived_offer_cannot_be_fabricated(self):
        scenario, branch = decision_fixture()
        branch['events'] = [e for e in branch['events'] if e['type'] != 'accept']
        with self.assertRaisesRegex(OnlineAllocationAuditError, 'received acceptance'):
            audit_branch(scenario, branch)

    def test_all_commits_need_exactly_one_decision_record(self):
        scenario, branch = decision_fixture()
        branch['allocation_decisions'] = []
        branch['solver_records'] = []
        with self.assertRaisesRegex(OnlineAllocationAuditError, 'covered exactly once'):
            audit_branch(scenario, branch)

    def test_report_checks_paired_comparison_and_family_aggregate(self):
        scenario, branch = decision_fixture()
        branch['id'] = 'R1'
        metrics = raw_branch_metrics(scenario, branch)
        common = paired_common_metrics(scenario, branch, branch)
        pared = {label: {k: v for k, v in value.items() if k not in ('patient_ids', 'total_difference_minutes')}
                 for label, value in common.items()}
        fields = ('unassigned', 'not_arrived', 'untreated', 'inquiries_per_patient',
            'selection_capped_mean_minutes', 'arrival_capped_mean_minutes', 'treatment_capped_mean_minutes',
            'redirects', 'transfers', 'notifications', 'notification_without_redirect')
        report = dict(kind='online_allocation_comparison', synthetic=True, phase='development',
            seeds=[10101], families=['normal'], source_sha256={'source.py': 'x'}, settings={},
            results=[dict(scenario=scenario, branches=[branch], summaries={'R1': metrics}, paired_vs_r1={'R1': pared})],
            summary=[dict(family='normal', policies={'R1': dict(cases=1, **{k: metrics[k] for k in fields})})])
        audit_report(report)
        broken = copy.deepcopy(report)
        broken['results'][0]['paired_vs_r1']['R1']['arrival']['denominator'] = 0
        with self.assertRaisesRegex(OnlineAllocationAuditError, 'common-patient'):
            audit_report(broken)
        broken = copy.deepcopy(report)
        broken['summary'][0]['policies']['R1']['arrival_capped_mean_minutes'] = 2
        with self.assertRaisesRegex(OnlineAllocationAuditError, 'case means'):
            audit_report(broken)

    def test_report_checks_full_seed_coverage_and_raw_summary(self):
        scenario, branch = decision_fixture()
        report = dict(kind='online_allocation_comparison', synthetic=True, phase='development',
            seeds=[10101], families=['normal'], source_sha256={'source.py': 'x'}, settings={},
            results=[dict(scenario=scenario, branches=[branch],
                          summaries={'C1': raw_branch_metrics(scenario, branch)})])
        self.assertEqual(audit_report(report)['cases'], 1)
        broken = copy.deepcopy(report)
        broken['results'][0]['summaries']['C1']['arrival_capped_mean_minutes'] = 1
        with self.assertRaisesRegex(OnlineAllocationAuditError, 'full-flow summary'):
            audit_report(broken)
        report['seeds'].append(10102)
        with self.assertRaisesRegex(OnlineAllocationAuditError, 'family/seed'):
            audit_report(report)


if __name__ == '__main__':
    unittest.main()
