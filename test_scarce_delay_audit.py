"""Raw provenance mutations for scarce supplemental inquiries; seeds 7 and 8 only."""
from copy import deepcopy
import subprocess
import sys
import unittest

from simulation import scarce_delay_audit as audit
from simulation.joint_policy_scenarios import make_scenario
from simulation.scarce_delay_engine import run_scarce_delay_branch, run_scarce_delay_replay


class ScarceDelayRawAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenario = make_scenario(7, 'scarce_options_staggered_replies')
        cls.branch = run_scarce_delay_branch(cls.scenario)
        cls.baseline = run_scarce_delay_branch(cls.scenario, cohort_registration=False, scarce_routing=False)
        cls.cohort = run_scarce_delay_branch(cls.scenario, cohort_registration=True, scarce_routing=False)

    def fixture(self):
        return deepcopy(self.scenario), deepcopy(self.branch)

    def event(self, branch, kind, *, match=None, **updates):
        event = next(row for row in branch['ordered_emissions'] if row['type'] == kind
                     and (match is None or match(row)))
        seq = event['event_sequence']
        event.update(deepcopy(updates))
        next(row for row in branch['events'] if row.get('event_sequence') == seq).update(deepcopy(updates))
        return event

    def reject(self, scenario, branch):
        with self.assertRaises((audit.ScarceDelayAuditError, KeyError, IndexError, TypeError)):
            audit.audit_branch(scenario, branch)

    def record(self, branch):
        return next(row for row in branch['scarce_supplement_records'] if row['result']['send'])

    def test_actual_supplement_baseline_and_cohort_pass(self):
        for branch in (self.branch, self.baseline, self.cohort):
            with self.subTest(branch=branch['id']):
                checked = audit.audit_branch(deepcopy(self.scenario), deepcopy(branch))
                self.assertTrue(checked['passed'])
                self.assertEqual(checked['assignment_constraint_violations'], 0)
                self.assertFalse(checked['actual_optimality_claimed'])
        checked = audit.audit_branch(deepcopy(self.scenario), deepcopy(self.branch))
        self.assertEqual(checked['counts']['scarce_supplement_sent'], 1)
        self.assertEqual([row['treatment_start_t'] for row in self.branch['patient_outcomes']], [5.5, 3.5])
        self.assertEqual(checked['waiting']['total_wait_sum_minutes'], 1)

    def test_seed_eight_c1_and_v3_are_auditable(self):
        for inquiry, allocation in (('v4', 'C1'), ('v3', 'H1')):
            scenario = make_scenario(8, 'scarce_options_staggered_replies')
            branch = run_scarce_delay_branch(scenario, inquiry=inquiry, allocation=allocation)
            self.assertTrue(audit.audit_branch(scenario, branch)['passed'])

    def test_eight_branch_replay_counts_and_order_pass(self):
        result = run_scarce_delay_replay(deepcopy(self.scenario))
        checked = audit.audit_scarce_delay_replay(result)
        self.assertEqual(checked['branches'], 8)
        self.assertEqual(checked['counts']['scarce_supplement_sent'], 2)

    def test_unregistered_future_patient_is_not_in_snapshot(self):
        scenario = make_scenario(8, 'future_patient_not_yet_active')
        branch = run_scarce_delay_branch(scenario)
        self.assertTrue(audit.audit_branch(scenario, branch)['passed'])
        for record in branch['scarce_supplement_records']:
            snap = record['inputs']['opportunity_snapshot']
            self.assertTrue(all(row['request_t'] <= snap['t'] for row in snap['registered_search_patients']))

    def test_missing_information_and_outage_physics_pass_unchanged(self):
        for family in ('unverified_backup_resources', 'outage_at_arrival_boundary'):
            with self.subTest(family=family):
                scenario = make_scenario(8, family)
                branch = run_scarce_delay_branch(scenario)
                self.assertTrue(audit.audit_branch(scenario, branch)['passed'])

    def test_silent_alternative_retains_original_actual_inquiry(self):
        scenario = deepcopy(self.scenario)
        scenario['response_matrix']['*']['H02']['silent'] = True
        branch = run_scarce_delay_branch(scenario)
        self.assertTrue(audit.audit_branch(scenario, branch)['passed'])
        ask = next(row for row in branch['events'] if row['type'] == 'ask' and row['patient'] == 'P01')
        reply = next(row for row in branch['events'] if row['type'] == 'accept' and row['patient'] == 'P01')
        self.assertEqual((ask['hospital'], ask['attempt'], ask['t']), ('H01', 1, 0))
        self.assertEqual((reply['hospital'], reply['t']), ('H01', .25))

    def test_engine_solver_store_imports_forbidden(self):
        code = """
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname.startswith('ortools') or (fullname.startswith('simulation.') and (
      fullname.endswith('_engine') or fullname.endswith('_assignment') or fullname in (
      'simulation.sim','simulation.map_replay','simulation.inquiry_queue','simulation.observation_store'))):
   raise AssertionError('forbidden execution import '+fullname)
sys.meta_path.insert(0,Block())
from simulation import scarce_delay_audit
scarce_delay_audit._selector_reproduce('v4',dict(now=86400,candidates=[],max_new=0))
print('PASS')
"""
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'PASS')

    def test_original_selector_output_cannot_claim_supplement(self):
        scenario, branch = self.fixture()
        branch['inquiry_decisions'][0]['decision']['send'].append('H02')
        self.reject(scenario, branch)

    def test_original_selector_candidate_pool_cannot_exclude_scarce_hospital(self):
        scenario, branch = self.fixture()
        branch['inquiry_decisions'][0]['inputs']['candidates'] = [dict(hospital_id='H02',travel=dict(duration_seconds=300))]
        self.reject(scenario, branch)

    def test_original_ask_attempt_cannot_be_replaced(self):
        scenario, branch = self.fixture()
        self.event(branch, 'ask', match=lambda row:row['patient']=='P01' and row['hospital']=='H01', attempt=2)
        self.reject(scenario, branch)

    def test_original_pending_timeout_cannot_extend(self):
        scenario, branch = self.fixture()
        self.event(branch, 'ask', match=lambda row:row['patient']=='P01' and row['hospital']=='H01', timeout_at=100)
        self.reject(scenario, branch)

    def test_supplement_actual_target_cannot_change(self):
        scenario, branch = self.fixture()
        self.event(branch, 'ask', match=lambda row:row['patient']=='P01' and row['hospital']=='H02', hospital='H03')
        self.reject(scenario, branch)

    def test_supplement_clock_cannot_be_earlier(self):
        scenario, branch = self.fixture()
        self.event(branch, 'scarce_supplement_review', match=lambda row:row['patient']=='P01', t=.25)
        self.reject(scenario, branch)

    def test_cohort_cannot_omit_registered_patient(self):
        scenario, branch = self.fixture()
        branch['cohort_registration_records'][0]['patients'].pop()
        self.reject(scenario, branch)

    def test_cohort_cannot_claim_future_request_sequence(self):
        scenario, branch = self.fixture()
        rows = deepcopy(branch['cohort_registration_records'][0]['patients'])
        rows[1]['request_event_sequence'] = 99999
        branch['cohort_registration_records'][0]['patients'] = deepcopy(rows)
        self.event(branch, 'request_cohort_registered', patients=rows)
        self.reject(scenario, branch)

    def test_cohort_raw_marker_unknown_field_rejected(self):
        scenario, branch = self.fixture()
        self.event(branch, 'request_cohort_registered', future_capacity=2)
        self.reject(scenario, branch)

    def test_enabled_flag_cannot_hide_new_intervention(self):
        scenario, branch = self.fixture()
        branch['scarce_routing_enabled'] = False
        self.reject(scenario, branch)

    def test_cohort_mode_cannot_be_disabled_under_supplement(self):
        scenario, branch = self.fixture()
        branch['cohort_registration_enabled'] = False
        self.reject(scenario, branch)

    def test_fixed_protocol_cannot_raise_budget(self):
        scenario, branch = self.fixture()
        branch['scarce_delay_spec']['maximum_supplements_per_patient'] = 2
        self.reject(scenario, branch)

    def test_unobserved_patient_cannot_leave_denominator(self):
        scenario, branch = self.fixture()
        branch['patient_outcomes'].pop()
        self.reject(scenario, branch)

    def test_offer_ttl_cannot_be_renewed(self):
        scenario, branch = self.fixture()
        self.event(branch, 'confirmation_gate', offer_expires_at=100)
        self.reject(scenario, branch)

    def test_report_quantity_cannot_be_invented(self):
        scenario, branch = self.fixture()
        report = next(row for row in branch['events'] if row['type']=='resource_report_received')
        resources = deepcopy(report['resources']); resources['ed_bed']['capacity'] += 1
        self.event(branch, 'resource_report_received', resources=resources)
        self.reject(scenario, branch)

    def test_additional_resource_source_rejected(self):
        scenario, branch = self.fixture()
        self.event(branch, 'resource_report_received', publication_id=1)
        self.reject(scenario, branch)

    def test_transfer_move_cannot_be_shortened(self):
        scenario, branch = self.fixture()
        branch['trajectories'][0]['legs'][1]['estimated_minutes'] -= 1
        self.reject(scenario, branch)

    def test_waiting_metric_cannot_hide_other_patient(self):
        scenario, branch = self.fixture()
        branch['patient_outcomes'][1]['treatment_start_t'] += 1
        self.reject(scenario, branch)

    def test_replay_branch_order_cannot_omit_a_branch(self):
        result = dict(schema_version=1,experiment_id=audit.EXPERIMENT_ID,scenario=deepcopy(self.scenario),
            branches=[deepcopy(self.branch)],execution_order=[])
        with self.assertRaises(audit.ScarceDelayAuditError): audit.audit_scarce_delay_replay(result)

    def test_required_no_action_review_cannot_disappear(self):
        scenario, branch = self.fixture()
        record = next(row for row in branch['scarce_supplement_records'] if not row['result']['send'])
        seq = record['event_sequence']
        self.event(branch, 'scarce_supplement_review', match=lambda row:row['event_sequence']==seq,
                   type='unused_diagnostic')
        branch['scarce_supplement_records'].remove(record)
        branch['metrics']['scarce_supplement_reviews'] -= 1
        with self.assertRaises(audit.ScarceDelayAuditError): audit.audit_supplements(scenario, branch)

    def test_repeated_review_cannot_renew_one_minute_claim(self):
        scenario, branch = self.fixture()
        branch['scarce_supplement_records'].append(deepcopy(self.record(branch)))
        self.reject(scenario, branch)

    def test_supplement_marker_unknown_oracle_field_rejected(self):
        scenario, branch = self.fixture()
        self.event(branch, 'scarce_supplement_review', future_reply_time=.5)
        self.reject(scenario, branch)

    def test_read_only_audit_preserves_scenario_and_branch(self):
        scenario, branch = self.fixture()
        before_scenario, before_branch = deepcopy(scenario), deepcopy(branch)
        audit.audit_branch(scenario, branch)
        self.assertEqual(scenario, before_scenario)
        self.assertEqual(branch, before_branch)

    def test_arrival_capability_reassessment_is_explicit_frozen_audit_limit(self):
        scenario = deepcopy(self.scenario)
        scenario['patients'][0]['arrival_assessment_adds_capabilities'] = list(
            scenario['patients'][1]['required_capabilities'])
        branch = run_scarce_delay_branch(scenario)
        before = deepcopy(scenario)
        # This valid extra environment is outside the locked 67-family scope.
        # The frozen model audit compares declared initial capabilities. Do
        # not project events or weaken that audit to manufacture a pass.
        self.assertEqual(audit.audit_supplements(scenario, branch)['scarce_supplement_sent'], 1)
        with self.assertRaisesRegex(audit.ScarceDelayAuditError, 'model patient capabilities'):
            audit.audit_branch(scenario, branch)
        self.assertEqual(scenario, before)


def _mutate_record(case):
    def test(self):
        scenario, branch = self.fixture()
        record = self.record(branch)
        case(record)
        self.reject(scenario, branch)
    return test


_MUTATIONS = {
 'oracle_field':lambda r:r['inputs'].__setitem__('response_delay',.5),
 'future_original_ask':lambda r:r['inputs'].__setitem__('original_ask_event_sequence',99999),
 'future_original_selector':lambda r:r['inputs'].__setitem__('original_selector_event_sequence',99999),
 'hidden_contact_budget':lambda r:r['inputs'].__setitem__('episode_contacts',0),
 'hidden_pending_count':lambda r:r['inputs'].__setitem__('open_inquiries',0),
 'increased_new_budget':lambda r:r['inputs'].__setitem__('max_new',3),
 'unverified_eligible_hospital':lambda r:r['inputs']['eligible_new_hospitals'].append('H99'),
 'future_snapshot_time':lambda r:r['inputs']['opportunity_snapshot'].__setitem__('t',1),
 'future_restoration':lambda r:r['inputs']['opportunity_snapshot'].__setitem__('until',2),
 'unregistered_scarce_patient':lambda r:r['inputs']['opportunity_snapshot']['registered_search_patients'][1].__setitem__('request_event_sequence',99999),
 'higher_own_priority':lambda r:r['inputs']['opportunity_snapshot']['patient'].__setitem__('priority',9),
 'lower_scarce_priority':lambda r:r['inputs']['opportunity_snapshot']['registered_search_patients'][1].__setitem__('priority',1),
 'unobserved_second_hospital':lambda r:r['inputs']['opportunity_snapshot']['registered_search_patients'][1]['compatible_hospitals'].append('H02'),
 'missing_original_pending':lambda r:r['inputs']['opportunity_snapshot']['patient']['pending_inquiries'].clear(),
 'renewed_original_pending_deadline':lambda r:r['inputs']['opportunity_snapshot']['patient']['pending_inquiries'][0].__setitem__('timeout_at',100),
 'fake_scarce_new_registration':lambda r:r['inputs']['opportunity_snapshot']['registered_search_patients'][1].__setitem__('inquiry_count',1),
 'fake_fresh_alternative':lambda r:r['inputs']['opportunity_snapshot']['candidates'][1]['resources']['ed_bed'].__setitem__('status','unknown'),
 'stale_scarce_resource':lambda r:r['inputs']['opportunity_snapshot']['registered_search_patients'][1]['resource_views']['H01']['ed_bed'].__setitem__('status','stale'),
 'unknown_special_resource':lambda r:r['inputs']['opportunity_snapshot']['registered_search_patients'][1]['resource_views']['H01']['special_team'].__setitem__('status','unknown'),
 'fake_received_free':lambda r:r['inputs']['opportunity_snapshot']['candidates'][0]['resources']['ed_bed'].__setitem__('free',2),
 'fake_queue_deduction':lambda r:r['inputs']['opportunity_snapshot']['candidates'][0]['resources']['ed_bed'].__setitem__('queued',1),
 'fake_committed_deduction':lambda r:r['inputs']['opportunity_snapshot']['candidates'][0]['resources']['ed_bed'].__setitem__('committed',1),
 'fake_shorter_route':lambda r:r['inputs']['opportunity_snapshot']['candidates'][1].__setitem__('travel_minutes',3),
 'supplement_wrong_hospital':lambda r:r['result'].__setitem__('send',['H03']),
 'supplement_extra_hospital':lambda r:r['result'].__setitem__('send',['H02','H03']),
 'wrong_claim_deadline':lambda r:r['result']['opportunity_result']['blocked'][0]['waiters'][0].__setitem__('deadline',100),
 'wrong_claim_patient':lambda r:r['result']['opportunity_result']['blocked'][0]['waiters'][0].__setitem__('patient','FUTURE'),
 'wrong_claim_demand':lambda r:r['result']['opportunity_result']['blocked'][0].__setitem__('waiting_demand',0),
}
for _name,_case in _MUTATIONS.items():
    setattr(ScarceDelayRawAudit,'test_raw_'+_name+'_rejected',_mutate_record(_case))


if __name__ == '__main__':
    unittest.main()
