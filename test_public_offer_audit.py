"""Read-only seed-7 full-raw verification and adversarial provenance tests.

Fixtures are the exact saved final-contract diagnostics, bound by compressed
byte SHA256. Tests never import a replay engine, scenario builder or solver.
Earlier diagnostic source revisions remain separately preserved on disk.
"""
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

from simulation import public_offer_audit as audit


ROOT = Path(__file__).resolve().parent
DIAGNOSTICS = ROOT / 'simulation/fixtures/public_offer_20261020'
FIXTURES = {
    'positive': ('public_offer_fresh_shortage-public_offer_H1_public_blocked-ff5e247e44264a74bb27c7ce0ef9309f.json.gz',
        'c50c60dd5c4e464dc311662a42da30406e638848db4b449c232b5f7ce269f836'),
    'available': ('public_offer_fresh_available_closer-public_offer_H1_public_blocked-c6cf39e58ca74a3a80176cf1029e88fc.json.gz',
        '6b81faf359dd1cdd2edb6539f52b435fc40bdb512ab5b4ee688112e278cd5f62'),
    'pending': ('public_offer_pending_closer-public_offer_H1_public_blocked-25540ac55778496bbd71837f107cbe62.json.gz',
        '0bef1df0f927024900139959ca58f21bc4f732ca3ed42d540ec30943187f33f2'),
    'outage': ('public_offer_future_outage-public_offer_H1_public_blocked-27e6d77533164b93a2c205b93391a7df.json.gz',
        'ab8e61928776e3cfdaafd5e1e2084821e3978faabac66c8aec1808bc1a5e72fd'),
    'later': ('public_offer_later_scarce_patient-public_offer_H1_public_blocked-6d298495871541bfa56745dfa7a482bb.json.gz',
        '928bdb8b84546e2ea60a75c60163a9a1c43cb3dd916d894f43b42ce776267a78'),
    'second_resource': ('public_offer_second_required_resource-public_offer_H1_public_blocked-5cb2fd25159d42f3b56fc6bd41247c9c.json.gz',
        'f23c111eaf0e15fc22836090c8f3bb5d845fac801f30322126237817a106ff66'),
    'c1': ('public_offer_fresh_shortage-public_offer_C1_public_blocked-10cb3105f1264977a2a794ca1ff711da.json.gz',
        '6d3ff90317abdb84ddb60780be40d55bffff87314781de5556bd9ccb30fb87db'),
    'independent': ('public_offer_independent_diagnostic-public_offer_H1_public_blocked-80cee32b49b443ffb380f567b86900d0.json.gz',
        '8425f66fb3ae7361252752ae3c5e0eaeb2a0324f70269fb1cd6fc15762ae3b5f'),
    'reference': ('out_of_order-recovery_rank_H1_normal-b558ae698e174bd18624b2de3e941993.json.gz',
        '0d901980ed4cd13408a1f170ebf5311e5817905fa41cfc7a2baa494fd212065d'),
    'mixed_ready': ('050_shared_fifo_burst.json.gz',
        'cd3a0151b8193bc3db87cc1113311c01ac142787cbf4522876ae930cf86bdb8a'),
}


def read_fixture(name):
    filename, expected = FIXTURES[name]
    path = DIAGNOSTICS / filename
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) \
            or len(raw) != after.st_size or hashlib.sha256(raw).hexdigest() != expected:
        raise AssertionError('saved diagnostic bytes differ: ' + filename)
    result = json.loads(gzip.decompress(raw))
    if result['scenario']['seed'] != 7 or len(result['branches']) != (2 if name == 'mixed_ready' else 1):
        raise AssertionError('audit fixtures must remain the declared seed-7 raw runs')
    return result


class PublicOfferRawAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = {name: read_fixture(name) for name in FIXTURES}

    def fixture(self, name='positive'):
        raw = deepcopy(self.raw[name])
        return raw['scenario'], raw['branches'][0]

    def reject(self, scenario, branch, *, component=False):
        with self.assertRaises((audit.PublicOfferAuditError, ValueError, KeyError, TypeError, IndexError)):
            (audit._audit_stage if component else audit.audit_branch)(scenario, branch)

    def event(self, branch, sequence, **changes):
        for rows in (branch['events'], branch['ordered_emissions']):
            next(row for row in rows if row.get('event_sequence') == sequence).update(deepcopy(changes))

    def exchange(self, branch, left, right):
        source = {row['event_sequence']: deepcopy(row) for row in branch['ordered_emissions']}
        for rows in (branch['events'], branch['ordered_emissions']):
            for target, origin in ((left, right), (right, left)):
                row = next(row for row in rows if row.get('event_sequence') == target)
                row.clear(); row.update(deepcopy(source[origin])); row['event_sequence'] = target

    def selected(self, branch):
        return next(row for row in branch['public_offer_records'] if row['decision']['eligible'])

    def marker(self, branch, kind):
        return next(row for row in branch['ordered_emissions'] if row['type'] == kind)

    def test_full_raw_h1_c1_both_modes_and_all_boundaries(self):
        for name in FIXTURES:
            with self.subTest(name=name):
                self.assertTrue(audit.audit_branch(*self.fixture(name))['passed'])

    def test_mixed_ready_regression_keeps_original_prefix_then_new_append_in_h1_and_c1(self):
        raw = deepcopy(self.raw['mixed_ready'])
        for branch in raw['branches']:
            with self.subTest(branch=branch['id']):
                before = deepcopy((raw['scenario'], branch))
                checked = audit.audit_branch(raw['scenario'], branch)
                self.assertTrue(checked['passed'])
                self.assertEqual(checked['counts']['public_offer_ready'], 1)
                workflow = audit._workflow(raw['scenario'])
                for event in audit._indexed_events(branch):
                    if event['event_sequence'] == 776:
                        self.assertEqual((event['type'], event['t']), ('opportunity_guard', 4))
                        self.assertEqual(workflow.public_ready_prefix[4], ['P08'])
                        self.assertEqual(workflow.public_ready_appends[4], [(774, 'P07')])
                        expected = workflow.snapshot(4)
                        actual = branch['protection_records'][event['record_index']]['snapshot']
                        self.assertEqual([row['id'] for row in expected['ready']], ['P08', 'P07'])
                        self.assertEqual(expected, actual)
                        break
                    workflow.apply(event)
                else: self.fail('fixed regression source guard is missing')
                self.assertEqual((raw['scenario'], branch), before)

    def test_sorted_mixed_ready_guard_is_rejected_even_with_the_same_rows(self):
        raw = self.raw['mixed_ready']
        for source in raw['branches']:
            with self.subTest(branch=source['id']):
                branch = deepcopy(source)
                row = branch['protection_records'][5]
                self.assertEqual(row['event_sequence'], 776)
                row['snapshot']['ready'].reverse()
                self.assertEqual([patient['id'] for patient in row['snapshot']['ready']], ['P07', 'P08'])
                with self.assertRaises(audit.PublicOfferAuditError):
                    audit._audit_guard(raw['scenario'], branch)

    def test_mixed_ready_prefix_cannot_be_erased_or_new_append_source_omitted(self):
        scenario, original = self.fixture('mixed_ready')
        for kind in ('prefix', 'append'):
            branch = deepcopy(original)
            if kind == 'prefix': self.event(branch, 766, eligible=False, reason='UNKNOWN')
            else: self.event(branch, 774, type='event_review_cycle')
            self.reject(scenario, branch)

    def test_mixed_ready_appends_do_not_survive_into_the_next_tick(self):
        scenario, branch = self.fixture('mixed_ready')
        workflow = audit._workflow(scenario)
        for event in audit._indexed_events(branch):
            workflow.apply(event)
            if event['type'] == 'public_offer_cycle' and event['t'] == 4.25:
                self.assertEqual(workflow.public_ready_appends[4], [(774, 'P07')])
                self.assertEqual(workflow.public_ready_appends[4.25], [])
                self.assertNotIn('P07', [row['id'] for row in workflow.snapshot(4.25)['ready']])
                break
        else: self.fail('regression next tick is missing')

    def test_c1_keeps_original_actual_and_exact_model_proofs(self):
        checked = audit.audit_branch(*self.fixture('c1'))
        self.assertGreater(checked['counts']['cp_sat_model_proofs'], 0)
        self.assertGreater(checked['counts']['exact_model_rechecks'], 0)

    def test_frozen_normal_reference_uses_exact_frozen_audit(self):
        scenario, branch = self.fixture('reference')
        self.assertEqual(audit.audit_branch(scenario, branch), audit.rank.audit_branch(scenario, branch))

    def test_every_full_raw_input_is_unchanged_after_audit(self):
        for name in ('positive', 'c1', 'independent', 'outage'):
            pair = self.fixture(name); before = deepcopy(pair)
            audit.audit_branch(*pair)
            self.assertEqual(pair, before)

    def test_clean_audit_import_never_imports_engine_or_solver(self):
        command = ('import sys; import simulation.public_offer_audit; '
            'bad=[n for n in sys.modules if n.endswith("_engine") or n.startswith("ortools")]; '
            'assert not bad, bad; print("audit import clean")')
        result = subprocess.run([sys.executable, '-B', '-c', command], cwd=ROOT,
            check=True, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), 'audit import clean')

    def test_real_new_marker_is_backup_source_in_both_work_modes(self):
        for name in ('positive', 'independent'):
            scenario, branch = self.fixture(name)
            checked = audit.audit_branch(scenario, branch)
            marker = self.marker(branch, 'public_offer_ready')
            retained = self.marker(branch, 'backup_retained')
            self.assertEqual(retained['early_ready_event_sequence'], marker['event_sequence'])
            self.assertGreater(checked['counts']['retained_pending_inquiries'], 0)
            self.assertGreater(checked['counts']['backup_replies'], 0)
            indexed = {event['event_sequence']: event for event in branch['ordered_emissions']}
            for handle in retained['pending']:
                ask = indexed[handle['ask_event_sequence']]
                self.assertEqual((handle['submitted_at'], handle['timeout_at']),
                    (ask['submitted_at'], ask['timeout_at']))
                if name == 'positive':
                    work = next(row for row in branch['cooperative_job_ledger']
                        if row['job_id'] == ask['cooperative_job_id'])
                    self.assertEqual(work['clinical_attempt'], ask['clinical_attempt'])

    def test_closer_public_opportunity_and_actual_pending_reject_readiness(self):
        for name, reason in (('available', 'BETTER_STRUCTURAL_NOT_PUBLICLY_BLOCKED'),
                ('pending', 'BETTER_ACTUAL_PENDING')):
            _, branch = self.fixture(name)
            self.assertFalse(any(row['decision']['eligible'] for row in branch['public_offer_records']))
            self.assertTrue(any(row['decision']['reason'] == reason for row in branch['public_offer_records']))

    def test_future_outage_and_unregistered_patient_do_not_enter_current_input(self):
        _, original = self.fixture()
        first = self.selected(original)
        for name in ('outage', 'later'):
            _, twin = self.fixture(name)
            other = self.selected(twin)
            self.assertEqual(other['snapshot'], first['snapshot'])
            self.assertEqual(other['decision'], first['decision'])
        _, later = self.fixture('later')
        cohort = self.selected(later)['snapshot']['original_snapshot']['search_patients']
        self.assertNotIn('P03', [row['id'] for row in cohort])

    def test_other_required_resource_shortage_is_preserved(self):
        _, branch = self.fixture('second_resource')
        selected = self.selected(branch)
        self.assertEqual(selected['decision']['candidate_checks'][0]['shortage_resources'], ['special_team'])
        self.assertEqual(set(selected['snapshot']['structural_resource_views']['H01']), {'ed_bed', 'special_team'})

    def test_independent_rule_rejects_a_previous_commit_even_if_original_scope_still_due(self):
        _, branch = self.fixture()
        snapshot = deepcopy(self.selected(branch)['snapshot'])
        snapshot['first_commit_t'] = 0
        expected = audit._expected(snapshot, 'public_blocked')
        self.assertFalse(expected['eligible'])
        self.assertEqual(expected['reason'], 'OUTSIDE_ORIGINAL_READINESS_SCOPE')

    def test_independent_rule_requires_some_fresh_shortage_at_every_better_candidate(self):
        _, branch = self.fixture('second_resource')
        snapshot = deepcopy(self.selected(branch)['snapshot'])
        closer = snapshot['structural_resource_views']['H01']
        closer['ed_bed'].update(status='unknown', free=None)
        self.assertTrue(audit._expected(snapshot, 'public_blocked')['eligible'])
        closer['special_team'].update(status='stale')
        result = audit._expected(snapshot, 'public_blocked')
        self.assertFalse(result['eligible'])
        self.assertEqual(result['reason'], 'BETTER_STRUCTURAL_NOT_PUBLICLY_BLOCKED')

    def test_independent_rule_keeps_the_registered_shared_capacity_bound(self):
        _, branch = self.fixture()
        snapshot = deepcopy(self.selected(branch)['snapshot'])
        patient = deepcopy(snapshot['original_snapshot']['search_patients'][0])
        patient['id'] = 'REGISTERED-CONTENDER'
        snapshot['original_snapshot']['search_patients'].append(patient)
        result = audit._expected(snapshot, 'public_blocked')
        self.assertFalse(result['eligible'])
        self.assertEqual(result['reason'], 'SHARED_CAPACITY')

    def test_every_actual_namespace_field_is_required(self):
        for key in audit.NAMESPACE_FIELDS:
            with self.subTest(key=key):
                scenario, branch = self.fixture(); del branch[key]
                self.reject(scenario, branch)

    def test_entire_namespace_cannot_be_hidden_under_a_frozen_reference_identity(self):
        scenario, branch = self.fixture()
        for key in audit.NAMESPACE_FIELDS: del branch[key]
        branch['id'] = branch['policy']['name'] = 'recovery_rank_H1_normal'
        branch['comparison_id'] = 'cooperative_probe_20261017'
        self.reject(scenario, branch)

    def test_unknown_namespace_or_inexact_enabled_variant_identity_is_rejected(self):
        for key, value in (('public_offer_hidden_finish', 2), ('public_offer_enabled', 1),
                ('public_offer_variant', 'future-aware'), ('comparison_id', 'cooperative_probe_20261017')):
            with self.subTest(key=key):
                scenario, branch = self.fixture(); branch[key] = value
                self.reject(scenario, branch)

    def test_wrapper_cannot_claim_the_old_class_or_changed_normal_selector(self):
        for key in ('actual_replays', 'actual_normal_selector_changed', 'new_stage'):
            scenario, branch = self.fixture()
            if key == 'actual_replays':
                branch['public_offer_foundation_spec'][key]['cooperative_queue'] = (
                    'simulation.recovery_rank_engine.CooperativeNormalRecoveryRankReplay')
            elif key == 'new_stage': branch['public_offer_foundation_spec'][key] = 'before_original_probe'
            else: branch['public_offer_foundation_spec'][key] = True
            self.reject(scenario, branch)

    def test_inherited_contract_is_exact_and_cannot_masquerade_as_actual_protocol(self):
        for key in ('recovery_rank_selector', 'recovery_rank_spec', 'shortage_probe_spec', 'comparison_id'):
            scenario, branch = self.fixture(); branch['public_offer_base_contract'].pop(key)
            self.reject(scenario, branch)
        scenario, branch = self.fixture()
        branch['shortage_probe_spec'] = deepcopy(branch['public_offer_base_contract']['shortage_probe_spec'])
        self.reject(scenario, branch)

    def test_fixed_readiness_and_normal_foundations_cannot_be_changed(self):
        for key, value in (('name', 'another-execution'), ('wait', 3), ('k', 2), ('rule', 'unknown')):
            scenario, branch = self.fixture(); branch['policy'][key] = value
            self.reject(scenario, branch)
        scenario, branch = self.fixture(); branch['recovery_rank_variant'] = 'protected'
        self.reject(scenario, branch)

    def test_all_snapshot_fields_are_required_and_future_keys_are_rejected(self):
        scenario, original = self.fixture()
        for key in self.selected(original)['snapshot']:
            branch = deepcopy(original); del self.selected(branch)['snapshot'][key]
            self.reject(scenario, branch, component=True)
        for key, value in (('next_service_completion', 2.5), ('future_outage_t', 4), ('future_patient', 'P03')):
            branch = deepcopy(original); self.selected(branch)['snapshot'][key] = value
            self.reject(scenario, branch, component=True)

    def test_snapshot_clocks_state_mode_and_first_commit_are_history_bound(self):
        for key, value in (('evaluated_t', 1.75), ('patient_state', 'TRANSIT'),
                ('mode', 'independent_replies'), ('first_commit_t', 0), ('wait_minutes', 3)):
            scenario, branch = self.fixture(); self.selected(branch)['snapshot'][key] = value
            self.reject(scenario, branch, component=True)

    def test_original_snapshot_structural_cohort_and_condition_are_complete(self):
        for change in ('patient', 'self_cohort', 'unregistered', 'pending', 'structural'):
            scenario, branch = self.fixture()
            original = self.selected(branch)['snapshot']['original_snapshot']
            if change == 'patient': original['patient']['priority'] += 1
            elif change == 'self_cohort': original['search_patients'][0]['demand']['ed_bed'] = 1
            elif change == 'unregistered': original['search_patients'].append(dict(original['search_patients'][0], id='P03'))
            elif change == 'pending': original['pending_hospitals'].clear()
            else: original['structural_candidates'].pop(0)
            self.reject(scenario, branch, component=True)

    def test_every_structural_required_resource_view_is_exact(self):
        for change in ('hospital', 'resource', 'quantity', 'source_time', 'receipt_time', 'packet', 'hidden_future'):
            scenario, branch = self.fixture('second_resource')
            views = self.selected(branch)['snapshot']['structural_resource_views']
            view = views['H01']['special_team']
            if change == 'hospital': views.pop('H02')
            elif change == 'resource': views['H01'].pop('special_team')
            elif change == 'quantity': view['free'] = 1
            elif change == 'source_time': view['observed_at'] = 2
            elif change == 'receipt_time': view['received_at'] = 2
            elif change == 'packet': view['sequence'] += 1
            else: view['future_restore_at'] = 12
            self.reject(scenario, branch, component=True)

    def test_pending_handles_cannot_be_removed_renewed_or_revealed(self):
        for change in ('missing', 'ask', 'attempt', 'submitted', 'timeout', 'finish'):
            scenario, branch = self.fixture()
            pending = self.selected(branch)['snapshot']['pending_inquiries']
            if change == 'missing': pending.clear()
            elif change == 'ask': pending[0]['ask_event_sequence'] += 1
            elif change == 'attempt': pending[0]['attempt'] += 1
            elif change == 'submitted': pending[0]['submitted_at'] = 1
            elif change == 'timeout': pending[0]['timeout_at'] = 4
            else: pending[0]['scheduled_reply_at'] = 2.5
            self.reject(scenario, branch, component=True)

    def test_offer_acceptance_ttl_and_current_quantities_are_exact(self):
        for change in ('accepted_at', 'expires_at', 'travel_minutes', 'resources'):
            scenario, branch = self.fixture()
            offer = self.selected(branch)['snapshot']['original_snapshot']['valid_offers'][0]
            if change == 'resources': offer['resources']['ed_bed']['free'] += 1
            else: offer[change] += .25
            self.reject(scenario, branch, component=True)

    def test_original_shadow_and_independent_decision_cannot_be_forged(self):
        for change in ('shadow', 'eligible', 'candidate_shortage', 'pending_rank', 'unknown'):
            scenario, branch = self.fixture(); row = self.selected(branch)
            if change == 'shadow': row['shadow_decision']['reason'] = 'EARLY_READY'
            elif change == 'eligible': row['decision']['eligible'] = False
            elif change == 'candidate_shortage': row['decision']['candidate_checks'][0]['shortage_resources'] = []
            elif change == 'pending_rank': row['decision']['pending_checks'][0]['at_least_offer_rank'] = False
            else: row['decision']['future_response'] = 'accept'
            self.reject(scenario, branch, component=True)

    def test_missing_reordered_or_orphan_records_are_rejected(self):
        for change in ('missing', 'reversed', 'orphan'):
            scenario, branch = self.fixture('available')
            rows = branch['public_offer_records']
            self.assertGreater(len(rows), 1)
            if change == 'missing': rows.pop()
            elif change == 'reversed': rows.reverse()
            else: rows.append(deepcopy(rows[-1]))
            self.reject(scenario, branch, component=True)

    def test_due_review_cannot_be_erased_even_if_record_is_removed(self):
        scenario, branch = self.fixture(); row = self.selected(branch)
        self.event(branch, row['event_sequence'], type='event_review_cycle')
        branch['public_offer_records'].clear()
        self.reject(scenario, branch, component=True)

    def test_every_original_and_new_cycle_is_required(self):
        for kind in ('early_offer_cycle', 'public_offer_cycle'):
            scenario, branch = self.fixture(); marker = self.marker(branch, kind)
            self.event(branch, marker['event_sequence'], type='event_review_cycle')
            self.reject(scenario, branch, component=True)

    def test_new_stage_must_follow_the_complete_original_early_stage(self):
        scenario, branch = self.fixture(); at = self.selected(branch)['snapshot']['evaluated_t']
        events = [row for row in branch['ordered_emissions'] if row['t'] == at]
        early = next(row for row in events if row['type'] == 'early_offer_cycle')
        new = next(row for row in events if row['type'] == 'public_offer_cycle')
        self.exchange(branch, early['event_sequence'], new['event_sequence'])
        self.reject(scenario, branch, component=True)

    def test_new_stage_must_finish_before_the_opportunity_guard(self):
        scenario, branch = self.fixture(); at = self.selected(branch)['snapshot']['evaluated_t']
        events = [row for row in branch['ordered_emissions'] if row['t'] == at]
        cycle = next(row for row in events if row['type'] == 'public_offer_cycle')
        guard = next(row for row in events if row['type'] == 'opportunity_guard')
        self.exchange(branch, cycle['event_sequence'], guard['event_sequence'])
        self.reject(scenario, branch, component=True)

    def test_positive_marker_and_options_are_unique_immediate_and_exact(self):
        for change in ('missing', 'hospital', 'review', 'options', 'future'):
            scenario, branch = self.fixture(); marker = self.marker(branch, 'public_offer_ready')
            if change == 'missing': delta = dict(type='event_review_cycle')
            elif change == 'hospital': delta = dict(hospital='H02')
            elif change == 'review': delta = dict(review_event_sequence=marker['review_event_sequence']-1)
            elif change == 'options': delta = dict(options=['H02', 'H03'])
            else: delta = dict(scheduled_reply_at=2.5)
            self.event(branch, marker['event_sequence'], **delta)
            self.reject(scenario, branch, component=True)

    def test_negative_review_cannot_create_an_extra_marker(self):
        scenario, branch = self.fixture('available')
        row = branch['public_offer_records'][0]
        self.event(branch, row['event_sequence']+1, type='public_offer_ready', patient='P01',
            hospital='H03', options=['H03'], review_event_sequence=row['event_sequence'])
        self.reject(scenario, branch, component=True)

    def test_current_allocation_check_must_follow_the_actual_new_marker(self):
        for change in ('missing', 'order', 'resources', 'eligible'):
            scenario, branch = self.fixture(); marker = self.marker(branch, 'public_offer_ready')
            sequence = marker['event_sequence']+1
            if change == 'missing': self.event(branch, sequence, type='event_review_cycle')
            elif change == 'order': self.exchange(branch, marker['event_sequence'], sequence)
            elif change == 'eligible': self.event(branch, sequence, eligible=False, reason='UNKNOWN')
            else:
                check = next(row for row in branch['ordered_emissions'] if row['event_sequence'] == sequence)
                resources = deepcopy(check['resources']); resources['ed_bed']['free'] += 1
                self.event(branch, sequence, resources=resources)
            self.reject(scenario, branch, component=True)

    def test_raw_counts_cannot_hide_reviews_cycles_or_actual_readiness(self):
        for key in ('public_offer_cycles', 'public_offer_reviews', 'public_offer_ready'):
            for value in (0, True):
                scenario, branch = self.fixture(); branch['metrics'][key] = value
                self.reject(scenario, branch, component=True)

    def test_ready_snapshot_cannot_omit_the_actual_new_promotion(self):
        scenario, branch = self.fixture()
        branch['protection_records'][0]['snapshot']['ready'].clear()
        self.reject(scenario, branch)

    def test_original_pending_guard_cannot_erase_a_real_pending_opportunity(self):
        scenario, branch = self.fixture()
        branch['pending_guard_records'][0]['snapshot']['ready'][0]['eligible_alternative_hospitals'] = []
        self.reject(scenario, branch)

    def test_backup_reference_must_point_to_the_true_new_readiness_marker(self):
        for name in ('positive', 'independent'):
            scenario, branch = self.fixture(name); marker = self.marker(branch, 'backup_retained')
            self.event(branch, marker['event_sequence'], early_ready_event_sequence=marker['early_ready_event_sequence']-1)
            self.reject(scenario, branch)

    def test_backup_pending_deadline_attempt_and_source_ask_cannot_change(self):
        for key in ('timeout_at', 'attempt', 'ask_event_sequence'):
            scenario, branch = self.fixture(); marker = self.marker(branch, 'backup_retained')
            pending = deepcopy(marker['pending']); pending[0][key] += 1
            self.event(branch, marker['event_sequence'], pending=pending)
            self.reject(scenario, branch)

    def test_backup_retention_arrival_preservation_and_closure_cannot_be_omitted(self):
        for kind in ('backup_retained', 'backup_arrival_preserved', 'backup_closed'):
            scenario, branch = self.fixture(); marker = self.marker(branch, kind)
            self.event(branch, marker['event_sequence'], type='event_review_cycle')
            self.reject(scenario, branch)

    def test_backup_offer_ttl_cannot_be_renewed_at_arrival(self):
        scenario, branch = self.fixture(); marker = self.marker(branch, 'backup_arrival_preserved')
        offers = deepcopy(marker['offers']); offers[0]['expires_at'] += 1
        self.event(branch, marker['event_sequence'], offers=offers)
        self.reject(scenario, branch)

    def test_backup_receipt_cannot_precede_original_clinical_work(self):
        scenario, branch = self.fixture(); marker = self.marker(branch, 'backup_reply_received')
        self.event(branch, marker['event_sequence'], t=marker['t']-.25)
        self.reject(scenario, branch)

    def test_actual_clinical_attempt_and_fifo_work_still_reach_original_auditors(self):
        scenario, branch = self.fixture(); ask = self.marker(branch, 'ask')
        self.event(branch, ask['event_sequence'], clinical_attempt=ask['clinical_attempt']+1)
        self.reject(scenario, branch)
        scenario, branch = self.fixture(); branch['cooperative_job_ledger'][0]['service_minutes'] -= .25
        self.reject(scenario, branch)

    def test_normal_inquiry_evidence_is_not_replaced_by_the_new_readiness_decision(self):
        scenario, branch = self.fixture()
        branch['inquiry_decisions'][0]['decision']['evidence']['policy'] = 'public_offer'
        self.reject(scenario, branch)

    def test_original_allocation_resource_and_physical_gates_are_not_bypassed(self):
        scenario, branch = self.fixture()
        branch['allocation_decisions'][0]['resource_evidence']['H03']['ed_bed']['free'] += 1
        self.reject(scenario, branch)
        scenario, branch = self.fixture(); marker = self.marker(branch, 'confirmation_gate')
        resources = deepcopy(marker['resources']); resources['ed_bed']['free'] += 1
        self.event(branch, marker['event_sequence'], resources=resources)
        self.reject(scenario, branch)

    def test_full_raw_report_source_time_cannot_be_edited_to_justify_a_promotion(self):
        scenario, branch = self.fixture()
        marker = next(row for row in branch['ordered_emissions'] if row['type']=='resource_report_received'
            and row['hospital']=='H01' and row['observed_at']==1)
        self.event(branch, marker['event_sequence'], observed_at=2)
        self.reject(scenario, branch)

    def test_unknown_intervention_event_is_rejected(self):
        scenario, branch = self.fixture(); marker = self.marker(branch, 'public_offer_cycle')
        self.event(branch, marker['event_sequence'], type='public_offer_future_stage')
        self.reject(scenario, branch)

    def test_replay_identity_and_execution_order_are_exact(self):
        scenario, branch = self.fixture()
        result = dict(schema_version=1, experiment_id=audit.EXPERIMENT_ID,
            scenario=scenario, branches=[branch], execution_order=[branch['id']])
        self.assertTrue(audit.audit_public_offer_replay(result)['passed'])
        for change in ('experiment', 'duplicate', 'order'):
            altered = deepcopy(result)
            if change == 'experiment': altered['experiment_id'] = 'cooperative_probe_20261017'
            elif change == 'duplicate': altered['branches'].append(deepcopy(branch))
            else: altered['execution_order'] = []
            with self.assertRaises(audit.PublicOfferAuditError): audit.audit_public_offer_replay(altered)


if __name__ == '__main__':
    unittest.main()
