"""Behavioral tests of early resource checks and independent rechecks.

Only small synthetic seeds are used. Patient arrivals and valid acceptance
evidence are checked; the tests do not assume every patient must improve.
"""
import copy
import unittest
from unittest.mock import patch

from simulation.cp_sat_assignment import CPSATDependencyError, _load_cp_model
from simulation.heuristic_assignment import HeuristicAssignmentRule
from simulation.opportunity_scenarios import build_opportunity_scenarios
from simulation.responsive_confirmation_engine import (
    DEFAULT_RESPONSIVE_POLICIES, run_responsive_confirmation_replay,
)
from simulation.responsive_confirmation_scenarios import build_responsive_confirmation_scenarios


def policy(branch_id='responsive_H1', **changes):
    spec = copy.deepcopy(next(s for s in DEFAULT_RESPONSIVE_POLICIES
                              if s['id'] == branch_id))
    spec.update(changes)
    return spec


def run(case, *ids):
    return run_responsive_confirmation_replay(case, [policy(i) for i in ids])


def branch(result, name='responsive_H1'):
    return next(b for b in result['branches'] if b['id'] == name)


def events(value, kind, patient=None, hospital=None):
    return [e for e in value['events'] if e['type'] == kind
            and (patient is None or e.get('patient') == patient)
            and (hospital is None or e.get('hospital') == hospital)]


def arrivals(value):
    return {p['id']: p['final_arrive_t'] for p in value['patient_outcomes']}


def case(family='partial_team_missing', seed=7):
    return build_opportunity_scenarios(seed, [family])[0]


try:
    _load_cp_model()
    HAS_CP_SAT = True
except CPSATDependencyError:
    HAS_CP_SAT = False


class ResponsiveConfirmationEngineTests(unittest.TestCase):
    def test_one_minute_confirmation_keeps_scarce_opportunity_and_saves_a_minute(self):
        result = run(case(), 'protected_H1', 'responsive_H1')
        before, after = branch(result, 'protected_H1'), branch(result)
        for pid in ('P02', 'P03'):
            self.assertEqual(arrivals(after)[pid], arrivals(before)[pid] - 1)
        self.assertEqual(arrivals(after)['P01'], arrivals(before)['P01'])
        self.assertEqual({e['patient']: e['hospital'] for e in events(after, 'commit')},
                         {'P01': 'NEAR', 'P02': 'FAR', 'P03': 'NEAR'})
        self.assertEqual(events(after, 'resource_confirmation_request')[0]['t'], 0)
        self.assertFalse(events(after, 'opportunity_hold'))

    def test_two_minute_confirmation_preserves_fast_case_gain_with_one_hold(self):
        result = run(case('delayed_team_confirmation'), 'protected_H1', 'responsive_H1')
        before, after = branch(result, 'protected_H1'), branch(result)
        for pid in ('P02', 'P03'):
            self.assertEqual(arrivals(after)[pid], arrivals(before)[pid] - 1)
        holds = events(after, 'opportunity_hold', 'P02')
        self.assertEqual([e['t'] for e in holds], [1])
        self.assertEqual({e['waiting_since'] for e in holds}, {0})
        self.assertEqual({e['deadline'] for e in holds}, {3})

    def test_five_minute_confirmation_reduces_flexible_delay_but_does_not_claim_zero(self):
        result = run(case('confirmation_beyond_grace'),
                     'observed_H1', 'protected_H1', 'responsive_H1')
        baseline, old, new = (branch(result, name) for name in
                             ('observed_H1', 'protected_H1', 'responsive_H1'))
        self.assertEqual(arrivals(old)['P02'] - arrivals(baseline)['P02'], 3)
        self.assertEqual(arrivals(new)['P02'] - arrivals(baseline)['P02'], 2)
        self.assertLess(arrivals(new)['P03'], arrivals(old)['P03'])
        self.assertEqual([e['t'] for e in events(new, 'opportunity_hold', 'P02')], [1, 2])

    def test_three_minute_confirmation_keeps_old_guard_scarce_opportunity(self):
        scenario = build_responsive_confirmation_scenarios(7, ['slow_confirmation_three'])[0]
        result = run(scenario, 'protected_H1', 'responsive_H1')
        old, new = branch(result, 'protected_H1'), branch(result)
        self.assertEqual(arrivals(new)['P03'], arrivals(old)['P03'] - 1)
        self.assertEqual(arrivals(new)['P02'], arrivals(old)['P02'] - 1)
        self.assertEqual(events(new, 'commit', 'P03')[0]['hospital'], 'NEAR')
        self.assertEqual(events(new, 'commit', 'P03')[0]['t'], 3)
        self.assertEqual([e['t'] for e in events(new, 'opportunity_hold', 'P02')], [1, 2])

    def test_incomplete_reconfirmation_never_guesses_the_missing_team(self):
        result = run(case('partial_team_unresolved'),
                     'observed_H1', 'protected_H1', 'responsive_H1')
        baseline, old, new = (branch(result, name) for name in
                             ('observed_H1', 'protected_H1', 'responsive_H1'))
        self.assertLess(arrivals(new)['P02'], arrivals(old)['P02'])
        self.assertEqual(arrivals(new)['P02'], arrivals(baseline)['P02'])
        self.assertFalse(events(new, 'opportunity_hold'))
        self.assertIsNone(arrivals(new)['P03'])
        self.assertFalse(events(new, 'commit', 'P03'))
        for d in new['allocation_decisions']:
            self.assertNotIn('P03', {p['id'] for p in d['inputs']['patients']})

    def test_silent_confirmation_stays_bounded_and_never_allocates_unknown(self):
        scenario = case()
        scenario['observation_config']['confirmation_silent'] = True
        scenario['max_minutes'] = 25
        value = branch(run(scenario, 'responsive_H1'))
        self.assertEqual(events(value, 'commit', 'P02')[0]['t'], 3)
        self.assertFalse(events(value, 'commit', 'P03'))
        self.assertTrue(events(value, 'resource_confirmation_timeout'))
        self.assertTrue(all(e['t'] < e['deadline'] for e in events(value, 'opportunity_hold')))

    def test_prefetch_has_received_missing_evidence_before_request_and_before_acceptance(self):
        value = branch(run(case(), 'responsive_H1'))
        request = events(value, 'resource_confirmation_request')[0]
        prior = [e for e in value['events'] if e.get('event_sequence', -1)
                 < request['event_sequence']]
        checks = [e for e in prior if e['type'] == 'information_check'
                  and e['patient'] == request['patient']
                  and e['hospital'] == request['hospital']
                  and e['purpose'] == 'allocation' and not e['eligible']]
        self.assertTrue(checks)
        self.assertEqual(checks[-1]['reason'], 'UNKNOWN')
        self.assertEqual(checks[-1]['resources']['special_team']['free'], None)
        self.assertFalse([e for e in prior if e['type'] == 'accept'])

    def test_prefetch_does_not_override_a_fresh_reported_shortage(self):
        scenario = case()
        for report in scenario['observation_config']['initial_reports']:
            if report['hospital'] == 'NEAR':
                report['resources']['ed_bed']['used'] = 2
        value = branch(run(scenario, 'responsive_H1'))
        self.assertFalse([e for e in events(value, 'resource_confirmation_request')
                          if e['t'] == 0 and e['hospital'] == 'NEAR'])
        self.assertFalse([e for e in events(value, 'ask', 'P03') if e['t'] == 0])

    def test_missing_or_stale_counts_cannot_enter_model_before_confirmation(self):
        value = branch(run(case('delayed_team_confirmation'), 'responsive_H1'))
        first = min(e['t'] for e in events(value, 'resource_report_received')
                    if e['source'] == 'confirmation')
        for decision in value['allocation_decisions']:
            rows = decision['inputs']['patients']
            if decision['t'] < first:
                self.assertNotIn('P03', {p['id'] for p in rows})
            self.assertTrue(all(r['free'] is not None
                                for r in decision['inputs']['free_resources']))
        self.assertGreaterEqual(events(value, 'commit', 'P03')[0]['t'], first)

    def test_acceptance_does_not_refresh_a_stale_resource_source_clock(self):
        scenario = case('delayed_team_confirmation')
        scenario['observation_config']['initial_reports'] = [
            report for hid in ('NEAR', 'FAR') for report in (
                dict(hospital=hid, observed_at=-11,
                     resources={'special_team': {'capacity': 1, 'used': 0}}),
                dict(hospital=hid, observed_at=0,
                     resources={'ed_bed': {'capacity': 2, 'used': 0}}),
            )
        ]
        value = branch(run(scenario, 'responsive_H1'))
        checks = [e for e in events(value, 'information_check', 'P03', 'NEAR')
                  if e['t'] == 1 and e['purpose'] == 'allocation']
        self.assertTrue(checks)
        self.assertEqual(checks[-1]['reason'], 'STALE')
        self.assertEqual(checks[-1]['resources']['special_team']['observed_at'], -11)
        self.assertEqual(checks[-1]['resources']['special_team']['age_minutes'], 12)
        self.assertEqual(events(value, 'accept', 'P03')[0]['t'], 1)
        self.assertEqual(events(value, 'commit', 'P03')[0]['t'], 2)

    def test_entirely_unknown_bed_counts_cannot_protect_a_reported_opportunity(self):
        scenario = case('delayed_team_confirmation')
        scenario['observation_config']['initial_reports'] = []
        value = branch(run(scenario, 'responsive_H1'))
        self.assertFalse(events(value, 'opportunity_hold'))
        self.assertTrue(all(d['t'] >= 2 for d in value['allocation_decisions']))

    def test_short_acceptance_expiry_releases_last_commit_opportunity(self):
        scenario = case('confirmation_beyond_grace')
        scenario['accept_ttl_minutes'] = 1
        value = branch(run(scenario, 'responsive_H1'))
        self.assertFalse(events(value, 'opportunity_hold'))
        self.assertEqual(events(value, 'commit', 'P02')[0]['t'], 1)

    def test_late_older_report_never_triggers_a_fresh_information_wake(self):
        value = branch(run(case('out_of_order'), 'responsive_H1'))
        ignored = {e['event_sequence'] for e in events(value, 'resource_report_received')
                   if not e['applied']}
        self.assertTrue(ignored)
        self.assertFalse([e for e in events(value, 'report_driven_wake')
                          if e['report_event_sequence'] in ignored])

    def test_same_hospital_shared_request_is_deduplicated_and_respects_cooldown(self):
        scenario = case('multiple_waiting_patients')
        scenario['observation_config']['confirmation_delay_minutes'] = 5
        value = branch(run(scenario, 'responsive_H1'))
        requests = events(value, 'resource_confirmation_request', hospital='NEAR')
        self.assertEqual([e['t'] for e in requests if e['t'] == 0], [0])
        self.assertTrue(all(right['t'] - left['t'] >= 8
                            for left, right in zip(requests, requests[1:])))

    def test_watch_rechecks_after_acceptance_expiry_without_reviving_that_offer(self):
        scenario = case('confirmation_beyond_grace')
        scenario['accept_ttl_minutes'] = 2
        value = branch(run(scenario, 'responsive_H1'))
        expiry = events(value, 'expire', 'P03')[0]['t']
        self.assertTrue([e for e in events(value, 'resource_confirmation_request')
                         if e['t'] > expiry])
        for d in value['allocation_decisions']:
            for p in d['inputs']['patients']:
                self.assertTrue(all(p['valid_offer_expiries'][h] > d['t']
                                    for h in p['options']))
                if p['id'] == 'P03' and d['t'] >= expiry:
                    self.assertTrue(all(p['accepted_at'][h] > 1 for h in p['options']))

    def test_independent_watch_request_traffic_is_bounded_under_incomplete_replies(self):
        scenario = case('partial_team_unresolved')
        scenario['max_minutes'] = 25
        value = branch(run(scenario, 'responsive_H1'))
        requests = events(value, 'resource_confirmation_request', hospital='NEAR')
        self.assertLessEqual(len(requests), 7)
        self.assertTrue(all(b['t'] - a['t'] >= 4 for a, b in zip(requests, requests[1:])))

    def test_new_sufficient_report_wakes_inquiry_without_auto_accepting_patient(self):
        scenario = case()
        scenario['retry_minutes'] = 20
        scenario['observation_config']['confirmation_delay_minutes'] = 5
        scenario['hospitals'][0]['initial_occupancy'] = [
            {'until': 4, 'demand': {'ed_bed': 2, 'special_team': 1}},
        ]
        value = branch(run(scenario, 'responsive_H1'))
        wakes = events(value, 'report_driven_wake', 'P03')
        self.assertTrue(wakes)
        self.assertEqual(wakes[0]['t'], 5)
        self.assertEqual([e['t'] for e in events(value, 'ask', 'P03')][:2], [0, 5])
        self.assertEqual(events(value, 'accept', 'P03')[0]['t'], 6)
        self.assertGreaterEqual(events(value, 'commit', 'P03')[0]['t'], 6)
        report = next(e for e in events(value, 'resource_report_received')
                      if e['event_sequence'] == wakes[0]['report_event_sequence'])
        self.assertTrue(report['applied'])
        self.assertEqual(report['t'], 5)

    def test_future_patient_cannot_start_resource_watch_early(self):
        scenario = case()
        scenario['patients'][2].update(occur_t=12, dispatch_t=12,
                                       ambulance_arrive_t=12, search_start_t=12)
        value = branch(run(scenario, 'responsive_H1'))
        self.assertFalse([e for e in events(value, 'resource_confirmation_request') if e['t'] < 12])
        self.assertFalse([e for e in events(value, 'opportunity_hold') if e['t'] < 12])

    def test_higher_priority_ready_patient_keeps_its_commitment(self):
        scenario = case('waiting_priority_low')
        value = branch(run(scenario, 'responsive_H1'))
        self.assertFalse(events(value, 'opportunity_hold'))
        self.assertEqual([e['t'] for e in events(value, 'commit') if e['patient'] in ('P01', 'P02')], [1, 1])

    def test_no_accepted_waiting_offer_cannot_hold_ready_patients(self):
        scenario = case()
        scenario['response_environment']['P03'] = {'NEAR': {'delay': 5}}
        value = branch(run(scenario, 'responsive_H1'))
        self.assertFalse(events(value, 'opportunity_hold'))
        self.assertEqual([e['t'] for e in events(value, 'commit') if e['patient'] in ('P01', 'P02')], [1, 1])

    def test_fresh_control_has_same_patient_flow_and_no_resource_prefetch(self):
        result = run(case('fresh_control'), 'observed_H1', 'responsive_H1')
        before, after = branch(result, 'observed_H1'), branch(result)
        self.assertEqual(before['patient_outcomes'], after['patient_outcomes'])
        self.assertFalse(events(after, 'resource_confirmation_request'))
        self.assertFalse(events(after, 'opportunity_hold'))

    def test_h1_responsive_path_does_not_use_cp_sat_backend(self):
        with patch('simulation.cp_sat_assignment._load_cp_model',
                   side_effect=AssertionError('H1 must not import the CP-SAT backend')):
            value = branch(run(case('delayed_team_confirmation'), 'responsive_H1'))
        self.assertFalse(value['solver_records'])
        self.assertTrue(value['heuristic_records'])

    def test_solver_receives_only_detached_current_accepted_offers(self):
        original = HeuristicAssignmentRule.decide_batch
        calls = []

        def checked(rule, view, ready, t):
            self.assertEqual(set(vars(view)), {'hospitals', 'position', 'travel'})
            for p, offers in ready:
                self.assertEqual(set(vars(p)), {'id', 'priority', 'demand', 'caps', 't_req', 'loc'})
                self.assertLessEqual(p.t_req, t)
                self.assertTrue(offers)
            calls.append(t)
            return original(rule, view, ready, t)

        with patch.object(HeuristicAssignmentRule, 'decide_batch', checked):
            value = branch(run(case(), 'responsive_H1'))
        self.assertTrue(calls)
        self.assertFalse(value['metrics']['whole_episode_optimal_proven'])

    def test_input_policy_and_scenario_are_not_mutated(self):
        scenario = case(); before = copy.deepcopy(scenario)
        specs = [policy('protected_H1'), policy()]; specs_before = copy.deepcopy(specs)
        run_responsive_confirmation_replay(scenario, specs)
        self.assertEqual(scenario, before)
        self.assertEqual(specs, specs_before)

    def test_branch_rotation_keeps_actual_patient_flow(self):
        scenario = case('delayed_team_confirmation', seed=8)
        specs = [policy('protected_H1'), policy()]
        ids = [p['id'] for p in specs]
        a = run_responsive_confirmation_replay(scenario, specs, execution_order=ids)
        b = run_responsive_confirmation_replay(scenario, specs, execution_order=ids[::-1])
        for left, right in zip(a['branches'], b['branches']):
            self.assertEqual(left['patient_outcomes'], right['patient_outcomes'])
            self.assertEqual(left['events'], right['events'])


@unittest.skipUnless(HAS_CP_SAT, 'optional OR-Tools backend is not installed')
class ResponsiveConfirmationCPSATTests(unittest.TestCase):
    def test_responsive_c1_keeps_model_proof_separate_from_episode_claim(self):
        value = branch(run(case(), 'responsive_C1'), 'responsive_C1')
        self.assertTrue(value['solver_records'])
        self.assertFalse(value['heuristic_records'])
        self.assertFalse(value['metrics']['whole_episode_optimal_proven'])
        self.assertFalse(any(d['actual_optimal_proven'] for d in value['allocation_decisions']))

    def test_default_fourteen_conditions_keep_ablation_flags_explicit(self):
        specs = DEFAULT_RESPONSIVE_POLICIES
        self.assertEqual(len(specs), 14)
        self.assertEqual(len({s['id'] for s in specs}), 14)
        for name, flags in (
            ('observed_H1', (False, False, False, None)),
            ('protected_H1', (True, False, False, 3)),
            ('early_H1', (True, True, False, 3)),
            ('watch_H1', (True, False, True, 3)),
            ('responsive_H1', (True, True, True, 3)),
        ):
            s = next(s for s in specs if s['id'] == name)
            self.assertEqual(tuple(s[k] for k in ('protection', 'prefetch', 'watch', 'grace_minutes')), flags)


if __name__ == '__main__':
    unittest.main()
