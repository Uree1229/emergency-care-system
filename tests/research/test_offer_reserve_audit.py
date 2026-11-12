"""Independent arithmetic and saved whole-raw cohort-reserve audit tests.

No producer policy, engine, scenario builder or solver is imported.  New raw
fixtures are actual seed-7 executions, never relabelled old public branches.
"""
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

from simulation import offer_reserve_audit as audit


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / 'simulation/fixtures/offer_reserve_20261020'
# Exact hashes are filled from saved actual runs before the source freeze.
FIXTURE_SHA256 = {
    'ample_c1.json.gz': 'eb36b8cbedf6f25f98045b0e214d21677536de13efd325fa6107103dfc470617',
    'ample_h1.json.gz': '8af81d5574d9f161c1735501a5ca616532aa41662dda4309f0f92bf38b245a78',
    'ample_independent.json.gz': 'fbc7a97af467d4612f8ef286ee99235441780063ec83d7856aca69de3a467ccc',
    'later_refused.json.gz': '150c02d98fd2fc062413691125c8bc91faeadc31ec81adb067c48d5d566ccea9',
    'mixed_ready.json.gz': 'e9b4e35d3cd0f541894891ac78a4f338a1459d2fc4c3b561ae72fb95d048fe14',
}


def read_fixtures():
    paths = {path.name: path for path in FIXTURE_DIR.glob('*.json.gz')}
    if set(paths) != set(FIXTURE_SHA256):
        raise AssertionError('complete fixed actual reserve fixture inventory differs')
    result = {}
    for name, path in paths.items():
        before = path.stat(); compressed = path.read_bytes(); after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) \
                or len(compressed) != after.st_size \
                or hashlib.sha256(compressed).hexdigest() != FIXTURE_SHA256[name]:
            raise AssertionError('actual reserve fixture bytes changed: ' + name)
        raw = json.loads(gzip.decompress(compressed))
        if raw['scenario']['seed'] != 7 or len(raw['branches']) != 1 \
                or raw['branches'][0]['comparison_id'] != audit.EXPERIMENT_ID:
            raise AssertionError('fixture is not the declared actual seed-7 reserve run')
        result[name[:-8]] = raw
    return result


def view(free=3, *, status='fresh', committed=0, queued=0):
    return dict(capacity=free + committed + queued, used=0, committed=committed,
        queued=queued, free=free, observed_at=1, received_at=1, sequence=10,
        source='feed', status=status, age_minutes=0)


def snapshot(free=3):
    demand = {'ed_bed': 1}
    patient = dict(id='P', priority=4, request_t=0, demand=demand, required_capabilities=[])
    resources = {'A': {'ed_bed': view(0)}, 'B': {'ed_bed': view(free)},
        'C': {'ed_bed': view(8)}}
    original = dict(t=1, patient=patient, first_valid_t=.75, pending_hospitals=['C'],
        valid_offers=[dict(hospital='B', accepted_at=.75, expires_at=10,
            resources=deepcopy(resources['B']), travel_minutes=3)],
        structural_candidates=[dict(hospital='A', travel_minutes=1),
            dict(hospital='B', travel_minutes=3), dict(hospital='C', travel_minutes=5)],
        search_patients=[dict(deepcopy(patient), compatible_hospitals=['A', 'B', 'C'])])
    return dict(schema_version=1, evaluated_t=1, mode='independent_replies',
        patient_state='SEARCH', first_commit_t=None, wait_minutes=2,
        original_snapshot=original,
        pending_inquiries=[dict(hospital='C', attempt=1, submitted_at=0,
            timeout_at=3, ask_event_sequence=3)], structural_resource_views=resources)


def set_resource(value, hospital, resource, resource_view):
    value['structural_resource_views'][hospital][resource] = deepcopy(resource_view)
    for offer in value['original_snapshot']['valid_offers']:
        if offer['hospital'] == hospital:
            offer['resources'][resource] = deepcopy(resource_view)


class OfferReserveIndependentArithmeticTests(unittest.TestCase):
    def decide(self, value):
        return audit._expected(value, 'cohort_reserve')

    def test_single_patient_exact_reserve_boundary_and_declared_fixed_coefficient(self):
        self.assertTrue(audit.PUBLIC_OFFER_SPEC['new_threshold'])
        self.assertEqual(audit.PUBLIC_OFFER_SPEC['reserve_own_demand_copies'], 1)
        for free, eligible in ((1, False), (2, True), (3, True)):
            with self.subTest(free=free):
                result = self.decide(snapshot(free))
                self.assertTrue(result['base_decision']['eligible'])
                self.assertEqual(result['eligible'], eligible)
                evidence = result['option_checks'][0]['resources']['ed_bed']
                self.assertEqual(evidence, dict(cohort_demand=1, own_demand=1,
                    free=free, required_free=2, residual_after_cohort=free-1, passed=eligible))

    def test_compatible_cohort_includes_self_and_every_priority(self):
        for priority in (1, 2, 4):
            value = snapshot(3)
            other = deepcopy(value['original_snapshot']['search_patients'][0])
            other.update(id='Q', priority=priority, demand={'ed_bed':2})
            value['original_snapshot']['search_patients'].append(other)
            result = self.decide(value)
            self.assertTrue(result['base_decision']['eligible'])
            self.assertFalse(result['eligible'])
            self.assertEqual(result['option_checks'][0]['resources']['ed_bed']['required_free'], 4)

    def test_incompatible_search_patient_does_not_consume_this_hospital_reserve(self):
        value = snapshot(2)
        other = deepcopy(value['original_snapshot']['search_patients'][0])
        other.update(id='Q', demand={'ed_bed':100}, compatible_hospitals=['C'])
        value['original_snapshot']['search_patients'].append(other)
        result = self.decide(value)
        self.assertTrue(result['eligible'])
        self.assertEqual(result['option_checks'][0]['resources']['ed_bed']['cohort_demand'], 1)

    def test_actual_options_all_checked_in_snapshot_order_not_only_the_best(self):
        value = snapshot(4)
        original = value['original_snapshot']
        original['structural_candidates'].append(dict(hospital='D', travel_minutes=4))
        original['search_patients'][0]['compatible_hospitals'].append('D')
        value['structural_resource_views']['D'] = {'ed_bed': view(1)}
        original['valid_offers'].append(dict(hospital='D', accepted_at=.5, expires_at=9,
            resources=deepcopy(value['structural_resource_views']['D']), travel_minutes=4))
        result = self.decide(value)
        self.assertTrue(result['base_decision']['eligible'])
        self.assertEqual(result['base_decision']['hospital'], 'B')
        self.assertEqual(result['usable_options'], ['B', 'D'])
        self.assertEqual([row['hospital'] for row in result['option_checks']], ['B', 'D'])
        self.assertFalse(result['eligible'])
        self.assertTrue(result['option_checks'][0]['passed'])
        self.assertFalse(result['option_checks'][1]['passed'])

    def test_every_required_resource_gets_its_own_cohort_and_own_demand(self):
        value = snapshot(4)
        original = value['original_snapshot']
        original['patient']['demand']['team'] = 2
        original['search_patients'][0]['demand']['team'] = 2
        for hospital in value['structural_resource_views']:
            set_resource(value, hospital, 'team', view(4 if hospital != 'B' else 3))
        result = self.decide(value)
        self.assertTrue(result['base_decision']['eligible'])
        self.assertFalse(result['eligible'])
        self.assertEqual(set(result['option_checks'][0]['resources']), {'ed_bed', 'team'})
        team = result['option_checks'][0]['resources']['team']
        self.assertEqual((team['cohort_demand'], team['own_demand'], team['required_free']), (2, 2, 4))

    def test_known_commitments_and_queue_cannot_be_counted_as_free_capacity(self):
        value = snapshot(1)
        set_resource(value, 'B', 'ed_bed', view(1, committed=5, queued=7))
        result = self.decide(value)
        self.assertTrue(result['base_decision']['eligible'])
        self.assertFalse(result['eligible'])
        self.assertEqual(result['option_checks'][0]['resources']['ed_bed']['free'], 1)

    def test_unknown_or_stale_only_offer_cannot_prove_any_reserve(self):
        for status in ('unknown', 'stale'):
            value = snapshot(99)
            resource = view(99, status=status)
            if status == 'unknown':
                resource.update(capacity=None, used=None, free=None, observed_at=None,
                    received_at=None, sequence=None, source=None, age_minutes=None)
            set_resource(value, 'B', 'ed_bed', resource)
            result = self.decide(value)
            self.assertFalse(result['eligible'])
            self.assertFalse(result['base_decision']['eligible'])
            self.assertEqual(result['option_checks'], [])
            self.assertEqual(result['usable_options'], [])

    def test_complete_cohort_demand_prevents_two_single_patient_checks_reusing_same_spare(self):
        value = snapshot(4)
        original = value['original_snapshot']
        original['patient']['demand']['ed_bed'] = 2
        original['search_patients'][0]['demand']['ed_bed'] = 2
        other = deepcopy(original['search_patients'][0]); other['id'] = 'Q'
        original['search_patients'].append(other)
        result = self.decide(value)
        self.assertTrue(result['base_decision']['eligible'])
        self.assertFalse(result['eligible'])
        self.assertEqual(result['option_checks'][0]['resources']['ed_bed']['required_free'], 6)
        set_resource(value, 'B', 'ed_bed', view(6))
        self.assertTrue(self.decide(value)['eligible'])

    def test_no_actual_pending_or_previous_commit_or_base_success_cannot_create_new_ready(self):
        for kind in ('pending', 'committed', 'original_ready'):
            value = snapshot()
            if kind == 'pending':
                value['pending_inquiries'] = []
                value['original_snapshot']['pending_hospitals'] = []
            elif kind == 'committed': value['first_commit_t'] = .5
            else: value['original_snapshot']['structural_candidates'][0]['travel_minutes'] = 8
            result = self.decide(value)
            self.assertFalse(result['eligible'])
            self.assertEqual(result['reason'], 'BASE_PUBLIC_OFFER_INELIGIBLE')
            self.assertEqual(result['option_checks'], [])

    def test_independent_result_does_not_mutate_snapshot_or_frozen_base(self):
        value = snapshot(); before = deepcopy(value)
        old = audit.prior._expected(value, 'public_blocked')
        result = self.decide(value)
        self.assertEqual(result['base_decision'], old)
        result['option_checks'].clear()
        self.assertEqual(value, before)

    def test_auditor_import_never_loads_replay_engine_solver_or_producer_policy(self):
        command = ('import sys; import simulation.offer_reserve_audit; '
            'bad=[n for n in sys.modules if n.endswith("_engine") or n.startswith("ortools") '
            'or n in ("simulation.offer_reserve_policy","simulation.public_offer_policy")]; '
            'assert not bad,bad; print("independent audit import clean")')
        result = subprocess.run([sys.executable, '-B', '-c', command], cwd=ROOT,
            check=True, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), 'independent audit import clean')


class OfferReserveWholeRawAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = read_fixtures()

    def fixture(self, name='ample_h1'):
        raw = deepcopy(self.raw[name])
        return raw['scenario'], raw['branches'][0]

    def review(self, branch, *, eligible=True):
        return next(row for row in branch['public_offer_records']
            if row['decision']['eligible'] is eligible)

    def marker(self, branch, kind):
        return next(event for event in branch['ordered_emissions'] if event['type'] == kind)

    def event(self, branch, sequence, **changes):
        for rows in (branch['events'], branch['ordered_emissions']):
            next(row for row in rows if row.get('event_sequence') == sequence).update(deepcopy(changes))

    def reject(self, scenario, branch, *, component=False):
        with self.assertRaises((audit.OfferReserveAuditError, ValueError, KeyError, TypeError, IndexError)):
            (audit._audit_stage if component else audit.audit_branch)(scenario, branch)

    def test_all_actual_saved_branches_pass_without_mutation(self):
        modes, allocations = set(), set()
        for name in self.raw:
            with self.subTest(name=name):
                pair = self.fixture(name); before = deepcopy(pair)
                self.assertTrue(audit.audit_branch(*pair)['passed'])
                self.assertEqual(pair, before)
                modes.add(pair[1]['public_offer_foundation_spec']['mode'])
                allocations.add(pair[1]['policy']['rule'])
        self.assertEqual(modes, {'independent_replies', 'cooperative_queue'})
        self.assertEqual(allocations, {'H1', 'C1'})

    def test_actual_positive_has_original_base_and_every_checked_option(self):
        for name in ('ample_h1', 'ample_c1', 'ample_independent'):
            scenario, branch = self.fixture(name)
            record = self.review(branch)
            self.assertEqual(record['decision'], audit._expected(record['snapshot'], 'cohort_reserve'))
            self.assertEqual(record['decision']['base_decision'],
                audit.prior._expected(record['snapshot'], 'public_blocked'))
            self.assertTrue(all(row['passed'] for row in record['decision']['option_checks']))
            ready = self.marker(branch, 'public_offer_ready')
            self.assertEqual(set(ready['options']), set(record['decision']['usable_options']))

    def test_later_counterexample_is_explicitly_blocked_without_seeing_future_patient(self):
        _, branch = self.fixture('later_refused')
        first = branch['public_offer_records'][0]
        self.assertTrue(first['decision']['base_decision']['eligible'])
        self.assertFalse(first['decision']['eligible'])
        self.assertEqual(first['decision']['reason'], 'COHORT_RESERVE_INSUFFICIENT')
        cohort = first['snapshot']['original_snapshot']['search_patients']
        self.assertNotIn('P03', [row['id'] for row in cohort])
        self.assertFalse(any(event['type'] == 'public_offer_ready' for event in branch['ordered_emissions']))

    def test_c1_retains_original_actual_and_exact_model_proofs(self):
        checked = audit.audit_branch(*self.fixture('ample_c1'))
        self.assertGreater(checked['counts']['cp_sat_model_proofs'], 0)
        self.assertGreater(checked['counts']['exact_model_rechecks'], 0)

    def test_mixed_ready_original_prefix_then_new_append_order_is_retained(self):
        scenario, branch = self.fixture('mixed_ready')
        workflow = audit._workflow(scenario)
        for event in audit._indexed_events(branch):
            if event['event_sequence'] == 776:
                self.assertEqual((event['type'], event['t']), ('opportunity_guard', 4))
                self.assertEqual(workflow.public_ready_prefix[4], ['P08'])
                self.assertEqual(workflow.public_ready_appends[4], [(774, 'P07')])
                self.assertEqual([row['id'] for row in workflow.snapshot(4)['ready']], ['P08', 'P07'])
                break
            workflow.apply(event)
        else: self.fail('saved actual mixed-ready guard missing')

    def test_sorted_mixed_guard_with_identical_members_is_rejected(self):
        scenario, branch = self.fixture('mixed_ready')
        guard = next(row for row in branch['protection_records'] if row['event_sequence'] == 776)
        guard['snapshot']['ready'].reverse()
        with self.assertRaises(audit.OfferReserveAuditError):
            audit.prior._audit_guard(scenario, branch)

    def test_every_new_namespace_field_is_required(self):
        for key in audit.NAMESPACE_FIELDS:
            scenario, branch = self.fixture(); del branch[key]
            self.reject(scenario, branch)

    def test_unknown_namespace_and_hidden_actual_identity_are_rejected(self):
        for key, value in (('offer_reserve_hidden_patient', 'P03'),
                ('public_offer_variant', 'public_blocked'), ('comparison_id', 'public_offer_20261020')):
            scenario, branch = self.fixture(); branch[key] = value
            self.reject(scenario, branch)

    def test_fixed_reserve_coefficient_and_no_forecast_claim_are_literal(self):
        for key, value in (('reserve_own_demand_copies', 0), ('new_threshold', False),
                ('unregistered_patient_nonworsening_guarantee', True), ('actual_options_unchanged', False)):
            scenario, branch = self.fixture(); branch['public_offer_spec'][key] = value
            self.reject(scenario, branch)

    def test_actual_wrapper_and_nested_frozen_descriptors_cannot_be_interchanged(self):
        for kind in ('actual', 'nested', 'coefficient'):
            scenario, branch = self.fixture()
            if kind == 'actual':
                branch['public_offer_foundation_spec']['actual_replays']['cooperative_queue'] = (
                    'simulation.public_offer_engine.CooperativePublicOfferReplay')
            elif kind == 'nested': branch['offer_reserve_base_contract']['base_variant'] = 'cohort_reserve'
            else: branch['public_offer_foundation_spec']['fixed_current_cohort_reserve_own_demand_copies'] = 2
            self.reject(scenario, branch)

    def test_complete_new_decision_schema_and_all_quantity_evidence_are_exact(self):
        _, original = self.fixture()
        for key in self.review(original)['decision']:
            scenario, branch = self.fixture(); del self.review(branch)['decision'][key]
            self.reject(scenario, branch, component=True)
        resource = self.review(original)['decision']['option_checks'][0]['resources']['ed_bed']
        for key in resource:
            scenario, branch = self.fixture()
            row = self.review(branch)['decision']['option_checks'][0]['resources']['ed_bed']
            row[key] = not row[key] if key == 'passed' else row[key] + 1
            self.reject(scenario, branch, component=True)

    def test_usable_option_or_resource_proof_cannot_be_omitted(self):
        for kind in ('options', 'option_check', 'resource', 'hospital', 'passed'):
            scenario, branch = self.fixture(); decision = self.review(branch)['decision']
            if kind == 'options': decision['usable_options'].clear()
            elif kind == 'option_check': decision['option_checks'].clear()
            elif kind == 'resource': decision['option_checks'][0]['resources'].clear()
            elif kind == 'hospital': decision['option_checks'][0]['hospital'] = 'H02'
            else: decision['option_checks'][0]['passed'] = False
            self.reject(scenario, branch, component=True)

    def test_underlying_public_decision_and_original_shadow_are_not_relabelled(self):
        for target in ('base_decision', 'shadow'):
            scenario, branch = self.fixture(); record = self.review(branch)
            if target == 'base_decision': record['decision']['base_decision']['eligible'] = False
            else: record['shadow_decision']['reason'] = 'EARLY_READY'
            self.reject(scenario, branch, component=True)

    def test_future_fields_and_unregistered_cohort_members_are_rejected(self):
        for kind in ('top', 'report', 'cohort', 'resource_future'):
            scenario, branch = self.fixture(); record = self.review(branch)
            snapshot = record['snapshot']
            if kind == 'top': snapshot['future_patient'] = 'P03'
            elif kind == 'report': snapshot['structural_resource_views']['H03']['ed_bed']['received_at'] = 99
            elif kind == 'resource_future': snapshot['structural_resource_views']['H03']['ed_bed']['next_release_at'] = 2
            else:
                patient = deepcopy(snapshot['original_snapshot']['search_patients'][0]); patient['id'] = 'UNREGISTERED'
                snapshot['original_snapshot']['search_patients'].append(patient)
            self.reject(scenario, branch, component=True)

    def test_current_free_committed_queued_and_cohort_demand_are_raw_history_bound(self):
        for key in ('capacity', 'used', 'committed', 'queued', 'free', 'status'):
            scenario, branch = self.fixture(); record = self.review(branch)
            resource = record['snapshot']['structural_resource_views']['H03']['ed_bed']
            resource[key] = 'stale' if key == 'status' else resource[key] + 1
            self.reject(scenario, branch, component=True)
        scenario, branch = self.fixture()
        self.review(branch)['snapshot']['original_snapshot']['search_patients'][0]['demand']['ed_bed'] += 1
        self.reject(scenario, branch, component=True)

    def test_rejected_reserve_cannot_be_changed_to_a_promotion(self):
        scenario, branch = self.fixture('later_refused')
        first = branch['public_offer_records'][0]
        first['decision'].update(eligible=True, hospital='H03', reason='COHORT_RESERVE_READY')
        self.reject(scenario, branch, component=True)

    def test_marker_option_identity_and_allocation_check_cannot_bypass_full_proof(self):
        for kind in ('option', 'hospital', 'check'):
            scenario, branch = self.fixture()
            marker = self.marker(branch, 'public_offer_ready')
            if kind == 'option': self.event(branch, marker['event_sequence'], options=['H02'])
            elif kind == 'hospital': self.event(branch, marker['event_sequence'], hospital='H02')
            else: self.event(branch, marker['event_sequence'] + 1, eligible=False, reason='UNKNOWN')
            self.reject(scenario, branch, component=True)

    def test_real_new_marker_retains_original_backup_identity_and_deadlines(self):
        for name in ('ample_h1', 'ample_independent'):
            scenario, branch = self.fixture(name)
            marker = self.marker(branch, 'public_offer_ready')
            backup = self.marker(branch, 'backup_retained')
            self.assertEqual(backup['early_ready_event_sequence'], marker['event_sequence'])
            self.assertTrue(backup['pending'])
            indexed = {row['event_sequence']: row for row in branch['ordered_emissions']}
            for handle in backup['pending']:
                ask = indexed[handle['ask_event_sequence']]
                self.assertEqual((handle['submitted_at'], handle['timeout_at']),
                    (ask['submitted_at'], ask['timeout_at']))

    def test_backup_deadline_and_ready_lineage_cannot_be_rewritten(self):
        for kind in ('deadline', 'lineage'):
            scenario, branch = self.fixture(); backup = self.marker(branch, 'backup_retained')
            if kind == 'deadline':
                pending = deepcopy(backup['pending']); pending[0]['timeout_at'] += .25
                self.event(branch, backup['event_sequence'], pending=pending)
            else: self.event(branch, backup['event_sequence'], early_ready_event_sequence=0)
            self.reject(scenario, branch)

    def test_original_report_and_physical_evidence_are_still_audited(self):
        scenario, branch = self.fixture()
        check = next(row for row in branch['ordered_emissions'] if row['type'] == 'information_check')
        self.event(branch, check['event_sequence'], resources={})
        self.reject(scenario, branch)

    def test_replay_audit_requires_explicit_actual_identity_and_unique_execution_order(self):
        scenario, branch = self.fixture()
        result = dict(schema_version=1, experiment_id=audit.EXPERIMENT_ID, scenario=scenario,
            branches=[branch], execution_order=[branch['id']])
        self.assertTrue(audit.audit_offer_reserve_replay(result)['passed'])
        for kind in ('experiment', 'duplicate'):
            altered = deepcopy(result)
            if kind == 'experiment': altered['experiment_id'] = 'public_offer_20261020'
            else: altered['execution_order'].append(branch['id'])
            with self.assertRaises(audit.OfferReserveAuditError): audit.audit_offer_reserve_replay(altered)


if __name__ == '__main__':
    unittest.main()
