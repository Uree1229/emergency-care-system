"""Adversarial complete-raw recovery checks on structural seeds 7 and 8."""
from copy import deepcopy
import json
import subprocess
import sys
import unittest

from simulation import recovery_dispatch_audit as audit
from simulation.recovery_dispatch_engine import run_recovery_dispatch_branch
from simulation.retry_dispatch_engine import run_retry_dispatch_branch
from simulation.recovery_dispatch_scenarios import make_scenario


class RecoveryRawAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {}
        for family in ('retry_dispatch_decline_then_recovery', 'fresh_control',
                       'shortage_probe_later_patient_fifo', 'cooperative_probe_busy_early_report',
                       'delayed_pessimistic_recovery', 'scarce_contact_concurrency_limits',
                       'watch_outage_recovery', 'recovery_dispatch_duplicate_recovery_feeds'):
            for variant in audit.VARIANTS:
                scenario = make_scenario(7, family)
                cls.cases[family, variant] = scenario, run_recovery_dispatch_branch(scenario, variant=variant)

    def fixture(self, variant='eager', family='retry_dispatch_decline_then_recovery'):
        return deepcopy(self.cases[family, variant])

    def reject(self, scenario, branch):
        with self.assertRaises((audit.RecoveryDispatchAuditError, KeyError, ValueError, TypeError, IndexError)):
            audit.audit_branch(scenario, branch)

    def event(self, branch, kind, **changes):
        event = next(row for row in branch['ordered_emissions'] if row['type'] == kind)
        event.update(deepcopy(changes))
        next(row for row in branch['events'] if row.get('event_sequence') == event['event_sequence']).update(deepcopy(changes))
        return event

    def eligible(self, branch):
        return next(row for row in branch['recovery_dispatch_reviews'] if row['decision']['hospital'] is not None)

    def test_complete_modes_and_workflows(self):
        for key, pair in self.cases.items():
            with self.subTest(key=key):
                self.assertTrue(audit.audit_branch(*deepcopy(pair))['passed'])

    def test_c1_actual_allocation_and_seed8(self):
        s = make_scenario(8, 'retry_dispatch_decline_then_recovery')
        for variant in audit.VARIANTS:
            b = run_recovery_dispatch_branch(s, allocation='C1', variant=variant)
            self.assertTrue(audit.audit_branch(s, b)['passed'])

    def test_read_only(self):
        s, b = self.fixture(); before = deepcopy((s, b))
        audit.audit_branch(s, b)
        self.assertEqual((s, b), before)

    def test_full_frozen_reference_delegates_without_rewriting(self):
        s = make_scenario(8, 'fresh_control'); b = run_retry_dispatch_branch(s, variant='frontier')
        self.assertEqual(audit.audit_branch(s, b), audit.parent.audit_branch(s, b))

    def test_complete_new_namespace_required(self):
        for field in audit.NAMESPACE_FIELDS:
            s, b = self.fixture(); del b[field]; self.reject(s, b)

    def test_entire_namespace_cannot_be_hidden(self):
        s, b = self.fixture()
        for field in audit.NAMESPACE_FIELDS: del b[field]
        self.reject(s, b)

    def test_unknown_namespace_or_future_input(self):
        s, b = self.fixture(); b['recovery_dispatch_future_finish'] = 4; self.reject(s, b)
        s, b = self.fixture(); self.eligible(b)['snapshot']['future_response'] = .5; self.reject(s, b)

    def test_false_unchanged_frontier_subclass_rejected(self):
        s, b = self.fixture(); b['retry_dispatch_foundation_spec'] = {}; self.reject(s, b)
        s, b = self.fixture(); b['recovery_dispatch_foundation_spec']['eligibility_changed'] = False; self.reject(s, b)

    def test_protocol_relaxation_rejected(self):
        for key in ('actual_clinical_decline_required', 'latched_requires_only_target_normal_candidate',
                    'required_resources_in_same_recovery_report', 'episode_transition_and_contact_budget_unchanged'):
            s, b = self.fixture(); b['recovery_dispatch_spec'][key] = False; self.reject(s, b)

    def test_identity_and_frontier_cannot_change(self):
        s, b = self.fixture(); b['policy']['name'] = 'renamed'; self.reject(s, b)
        s, b = self.fixture(); b['retry_dispatch_selector'] = 'paced'; self.reject(s, b)

    def test_all_report_history_retained(self):
        s, b = self.fixture(); b['recovery_dispatch_resource_transitions'].pop(0); self.reject(s, b)

    def test_transition_quantities_reproduced(self):
        s, b = self.fixture()
        row = b['recovery_dispatch_resource_transitions'][-1]
        row['after_resources'][next(iter(row['after_resources']))]['used'] += 1
        self.reject(s, b)

    def test_report_before_refs_reproduced(self):
        s, b = self.fixture(); row = b['recovery_dispatch_resource_transitions'][-1]
        row['before_report_event_sequences'][next(iter(row['before_report_event_sequences']))] = 9999
        self.reject(s, b)

    def test_report_source_clock_reproduced(self):
        s, b = self.fixture(); row = b['recovery_dispatch_resource_transitions'][-1]
        row['after_resources'][next(iter(row['after_resources']))]['observed_at'] = row['t'] + 1
        self.reject(s, b)

    def test_ignored_report_cannot_become_applied_recovery(self):
        s, b = self.fixture(); row = self.eligible(b)['snapshot']['transitions'][-1]
        row['report_event_sequence'] = 9999; self.reject(s, b)

    def test_actual_decline_cannot_be_dropped(self):
        s, b = self.fixture(); b['recovery_dispatch_declines'].clear(); self.reject(s, b)

    def test_actual_decline_attempt_and_condition_bound(self):
        for key, value in (('ask_event_sequence', 9999), ('attempt', 7), ('condition_fingerprint', 'fake')):
            s, b = self.fixture(); b['recovery_dispatch_declines'][0][key] = value; self.reject(s, b)

    def test_snapshot_decline_not_future_or_timeout(self):
        s, b = self.fixture(); row = self.eligible(b)['snapshot']['declines'][0]
        row['t'] += 100; self.reject(s, b)

    def test_pending_original_handles_bound(self):
        s, b = self.fixture(); self.eligible(b)['snapshot']['pending_inquiries'].clear(); self.reject(s, b)

    def test_current_eligible_universe_complete(self):
        s, b = self.fixture(); self.eligible(b)['snapshot']['candidates'].pop(); self.reject(s, b)

    def test_contact_history_never_erased(self):
        s, b = self.fixture(); self.eligible(b)['snapshot']['contacts'].pop(0); self.reject(s, b)

    def test_last_contact_clock_bound(self):
        s, b = self.fixture(); self.eligible(b)['snapshot']['scheduling']['last_contact_at'] = None; self.reject(s, b)

    def test_episode_budget_bound(self):
        s, b = self.fixture(); self.eligible(b)['snapshot']['scheduling']['episode_contacts'] = 0; self.reject(s, b)

    def test_review_deadline_bound(self):
        s, b = self.fixture(); self.eligible(b)['snapshot']['scheduling']['next_policy_review_at'] += 1; self.reject(s, b)

    def test_original_tried_set_bound(self):
        s, b = self.fixture(); self.eligible(b)['snapshot']['tried_hospitals'].pop(); self.reject(s, b)

    def test_review_order_and_source_markers_bound(self):
        s, b = self.fixture(); self.event(b, 'recovery_reopen_review', record_index=999); self.reject(s, b)

    def test_decision_token_not_replaceable(self):
        s, b = self.fixture(); self.eligible(b)['decision']['token_id'] = 'invented'; self.reject(s, b)

    def test_reopen_raw_marker_bound(self):
        s, b = self.fixture(); self.event(b, 'recovery_reopen', review_event_sequence=9999); self.reject(s, b)

    def test_real_ask_required_for_consumption(self):
        s, b = self.fixture(); b['recovery_dispatch_consumptions'][0]['ask_event_sequence'] = 9999; self.reject(s, b)

    def test_consumption_condition_once_token_bound(self):
        s, b = self.fixture(); b['recovery_dispatch_consumptions'][0]['condition_fingerprint'] = 'changed'; self.reject(s, b)

    def test_no_duplicate_actual_consumption(self):
        s, b = self.fixture(); b['recovery_dispatch_consumptions'] *= 2; self.reject(s, b)

    def test_unsent_restore_flag_and_actual_ask_bound(self):
        s, b = self.fixture(); self.event(b, 'recovery_reopen_result', target_restored=True); self.reject(s, b)

    def test_original_episode_not_reset(self):
        s, b = self.fixture(); b['recovery_dispatch_reopens'][0]['after_episode_id'] += 1; self.reject(s, b)

    def test_exhaustion_resets_episode_only_at_the_next_actual_normal_call(self):
        s, b = self.fixture('latched', 'watch_outage_recovery')
        rows = b['recovery_dispatch_reviews']
        self.assertEqual(rows[0]['snapshot']['scheduling']['episode_id'], 0)
        self.assertEqual(rows[1]['snapshot']['scheduling']['episode_id'], 0)
        self.assertEqual(rows[1]['snapshot']['tried_hospitals'], [])
        self.assertEqual(rows[2]['snapshot']['scheduling']['episode_id'], 1)
        self.assertTrue(audit.audit_branch(s, b)['passed'])

    def test_unlogged_no_token_extra_base_call_cannot_advance_episode(self):
        s, b = self.fixture('latched', 'watch_outage_recovery')
        b['recovery_dispatch_reviews'][1]['snapshot']['scheduling'].update(episode_id=1, episode_contacts=0)
        self.reject(s, b)

    def test_actual_report_wake_reenters_original_episode_before_duplicate_review_guard(self):
        s, b = self.fixture('eager', 'recovery_dispatch_duplicate_recovery_feeds')
        row = next(r for r in b['recovery_dispatch_reviews'] if r['snapshot']['t'] == 11.25)
        self.assertEqual(row['snapshot']['scheduling']['episode_id'], 2)
        source = next(e for e in b['ordered_emissions'] if e['t'] == 11 and e['type'] == 'resource_event_review')
        self.assertEqual(source['previous_retry_at'], 14)
        self.assertTrue(audit.audit_branch(s, b)['passed'])

    def test_duplicate_report_wake_does_not_authorize_an_extra_episode(self):
        s, b = self.fixture('eager', 'recovery_dispatch_duplicate_recovery_feeds')
        row = next(r for r in b['recovery_dispatch_reviews'] if r['snapshot']['t'] == 11.25)
        row['snapshot']['scheduling']['episode_id'] += 1
        self.reject(s, b)

    def test_original_report_wake_source_is_required(self):
        s, b = self.fixture('eager', 'recovery_dispatch_duplicate_recovery_feeds')
        wake = next(e for e in b['ordered_emissions'] if e['t'] == 11 and e['type'] == 'resource_event_review')
        for stream in (b['events'], b['ordered_emissions']):
            next(e for e in stream if e.get('event_sequence') == wake['event_sequence'])['report_event_sequence'] = 9999
        self.reject(s, b)

    def test_other_tried_hospitals_not_erased(self):
        s, b = self.fixture(); b['recovery_dispatch_reopens'][0]['after_tried'].pop(); self.reject(s, b)

    def test_actual_new_ask_list_bound(self):
        s, b = self.fixture(); b['recovery_dispatch_reopens'][0]['actual_new_ask_event_sequences'] = []; self.reject(s, b)

    def test_original_inquiry_records_cannot_be_projected(self):
        s, b = self.fixture(); b['inquiry_decisions'].pop(); self.reject(s, b)

    def test_latched_retry_wake_and_source_clock_bound(self):
        s, b = self.fixture('latched'); b['recovery_dispatch_reopens'][0]['original_retry_at'] = 99; self.reject(s, b)

    def test_physical_actual_fifo_service_unmodified(self):
        s, b = self.fixture(); event = next(row for row in b['ordered_emissions'] if row['type'] == 'cooperative_job_completed')
        self.event(b, 'cooperative_job_completed', t=event['t'] + .25); self.reject(s, b)

    def test_metrics_cannot_claim_unperformed_recovery(self):
        s, b = self.fixture(); b['metrics']['recovery_reopen_sent'] += 1; self.reject(s, b)

    def test_engine_and_solver_free_import(self):
        code = ('import sys,json; import simulation.recovery_dispatch_audit; '
                'print(json.dumps([n for n in sys.modules if n.startswith("simulation.") and n.endswith("_engine") '
                'or n.startswith("ortools")]))')
        result = subprocess.run([sys.executable, '-B', '-c', code], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), [])


if __name__ == '__main__':
    unittest.main()
