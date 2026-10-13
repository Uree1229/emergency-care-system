"""V3의 대기 상한·최근 회신 관측·서버 연결·개발/검증 분리 검증.

합성 환자와 고정 시각만 사용한다. 운영 DB, 의료기관, 외부 API를 호출하지 않는다.
"""
import copy
from contextlib import redirect_stdout
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server import db, inquiry_policies as policies, inquiry_workflow as workflow, service
from server import inquiry_experiment as experiment
from server import inquiry_v3
from test_inquiry_policies import PolicyBase
from test_server import T0


def candidate(hid, seconds):
    return dict(hospital_id=hid, travel={'duration_seconds': seconds}, resources_enough=True)


def own(hid, submitted, seconds=60, patient='synthetic-patient'):
    return dict(hospital=hid, patient=patient, submitted_at=submitted, travel_seconds=seconds)


def arguments(now, patient_pending=(), **updates):
    value = dict(now=now, candidates=[candidate('H2', 6000), candidate('H3', 6060)],
                 observations=[], pending=[{k: row[k] for k in ('hospital', 'patient', 'submitted_at')}
                                           for row in patient_pending],
                 patient_pending=list(patient_pending), max_new=10)
    value.update(updates)
    return value


class RecentHistoryAndBoundedWait(unittest.TestCase):
    def test_public_registry_and_internal_variant_contract_are_distinct(self):
        self.assertEqual(policies.POLICY_NAMES,
                         ('fixed_1', 'fixed_3', 'fixed_5', 'observed_v2', 'observed_v3'))
        expected = {f'v3_w{wait}_h{history}' for wait in (45, 60, 90) for history in (600, 900)}
        self.assertEqual(set(policies.V3_EXPERIMENT_NAMES), expected)
        registry = policies.registry()['policies']
        self.assertEqual({item['name'] for item in registry}, set(policies.POLICY_NAMES))
        self.assertTrue(expected.isdisjoint(item['name'] for item in registry))
        public = policies.spec('observed_v3')
        selected = policies.spec(public['source_variant'])
        self.assertEqual(public['config'], selected['config'])
        self.assertEqual(public['review_seconds'], 15)
        for name in expected:
            wait, history = (int(part[1:]) for part in name.split('_')[1:])
            spec = policies.spec(name)
            self.assertEqual(spec['config']['max_wait_seconds'], wait)
            self.assertEqual(spec['config']['history_window_seconds'], history)
        altered = policies.spec('observed_v3')
        altered['config']['max_wait_seconds'] = 1
        self.assertEqual(policies.spec('observed_v3')['config'], selected['config'])

    def test_public_alias_manifest_accepts_only_frozen_registered_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'selection.json'
            with patch.object(inquiry_v3, 'SELECTION_PATH', path):
                self.assertEqual(inquiry_v3._selected_variant(), 'v3_w60_h900')
                valid = dict(schema_version=1, source_policy='v3_w45_h600',
                             development_lock_sha256='a' * 64)
                path.write_text(json.dumps(valid), encoding='utf-8')
                self.assertEqual(inquiry_v3._selected_variant(), 'v3_w45_h600')
                for change in ({'schema_version': True}, {'source_policy': 'observed_v2'},
                               {'development_lock_sha256': 'unlocked'}, {'unexpected': 1}):
                    with self.subTest(change=change):
                        path.write_text(json.dumps(dict(valid, **change)), encoding='utf-8')
                        with self.assertRaises(ValueError):
                            inquiry_v3._selected_variant()

    def test_no_forced_expansion_before_bound_but_exact_bound_sends_worse_candidate(self):
        before = policies.decide('v3_w60_h900', **arguments(T0 + 59, [own('H1', T0)]))
        exact = policies.decide('v3_w60_h900', **arguments(T0 + 60, [own('H1', T0)]))
        baseline = policies.decide('observed_v2', **arguments(T0 + 60, [own('H1', T0)]))
        self.assertEqual(before['send'], [])
        self.assertEqual(baseline['send'], [])
        self.assertEqual(exact['send'], ['H2'])
        self.assertEqual(exact['reason'], 'bounded_wait_expansion')
        self.assertEqual(exact['evidence']['last_contact_age_seconds'], 60)
        self.assertEqual(exact['next_review_at'], T0 + 75)

    def test_new_contact_restarts_wait_instead_of_using_oldest_pending_age(self):
        pending = [own('H1', T0), own('H2', T0 + 60, seconds=6000)]
        at_75 = policies.decide('v3_w60_h900', **arguments(T0 + 75, pending,
                                  candidates=[candidate('H3', 6060)]))
        at_120 = policies.decide('v3_w60_h900', **arguments(T0 + 120, pending,
                                   candidates=[candidate('H3', 6060)]))
        self.assertEqual(at_75['send'], [])
        self.assertEqual(at_75['evidence']['last_contact_age_seconds'], 15)
        self.assertEqual(at_120['send'], ['H3'])
        self.assertEqual(at_120['reason'], 'bounded_wait_expansion')

    def test_closed_recent_contact_also_restarts_bound_and_future_contact_does_not(self):
        args = arguments(T0 + 75, [own('H1', T0)])
        recent = policies.decide('v3_w60_h900', **args, last_contact_at=T0 + 60)
        future = policies.decide('v3_w60_h900', **args, last_contact_at=T0 + 76)
        self.assertEqual(recent['send'], [])
        self.assertEqual(recent['evidence']['last_contact_age_seconds'], 15)
        self.assertEqual(future['send'], ['H2'])
        self.assertEqual(future['evidence']['last_contact_age_seconds'], 75)

    def test_three_live_policy_waits_block_expansion_but_expired_wait_keeps_global_load(self):
        pending = [own('H1', T0), own('H2', T0 + 60), own('H3', T0 + 120)]
        exact = policies.decide('v3_w60_h900', **arguments(T0 + 180, pending,
                                    candidates=[candidate('H4', 6000)]))
        expired = policies.decide('v3_w60_h900', **arguments(T0 + 181, pending,
                                      candidates=[candidate('H4', 6000)]))
        self.assertEqual(exact['send'], [])
        self.assertEqual(exact['reason'], 'concurrency_cap')
        self.assertEqual(expired['send'], ['H4'])
        self.assertEqual(expired['evidence']['patient_pending_count'], 2)
        self.assertEqual(expired['evidence']['unresolved_inquiry_count'], 3)
        self.assertEqual(expired['evidence']['observation_timed_out_pending_count'], 1)

    def test_contact_budget_and_no_candidate_never_force_invalid_inquiry(self):
        pending = [own('H1', T0)]
        no_budget = policies.decide('v3_w60_h900', **arguments(T0 + 60, pending, max_new=0))
        no_candidate = policies.decide('v3_w60_h900', **arguments(T0 + 60, pending, candidates=[]))
        self.assertEqual(no_budget['send'], [])
        self.assertEqual(no_budget['reason'], 'contact_budget')
        self.assertEqual(no_candidate['send'], [])
        self.assertEqual(no_candidate['reason'], 'no_candidates')

    def test_each_registered_bound_is_used_exactly(self):
        for name in policies.V3_EXPERIMENT_NAMES:
            bound = policies.spec(name)['config']['max_wait_seconds']
            with self.subTest(policy=name):
                before = policies.decide(name, **arguments(T0 + bound - 1, [own('H1', T0)]))
                exact = policies.decide(name, **arguments(T0 + bound, [own('H1', T0)]))
                self.assertEqual(before['send'], [])
                self.assertEqual(exact['send'], ['H2'])
                self.assertEqual(exact['reason'], 'bounded_wait_expansion')

    def test_stale_and_future_observations_are_filtered_before_payload_interpretation(self):
        class MustNotInterpret(dict):
            def get(self, key, *args):
                if key in ('hospital', 'outcome'):
                    raise AssertionError('excluded observation payload was interpreted')
                return super().get(key, *args)

        now = T0
        fresh = [dict(hospital='H1', submitted_at=now - 30, observed_at=now - 29, outcome='accept')]
        excluded = [MustNotInterpret(submitted_at=now - 1000, observed_at=now - 900),
                    MustNotInterpret(submitted_at=now + 1, observed_at=now + 2)]
        base = policies.decide('v3_w60_h900', **arguments(now, candidates=[candidate('H1', 60)],
                                                            observations=fresh))
        augmented = policies.decide('v3_w60_h900', **arguments(now, candidates=[candidate('H1', 60)],
                                                               observations=fresh + excluded))
        self.assertEqual(augmented['send'], base['send'])
        self.assertEqual(augmented['evidence']['global_reply_samples'], 1)
        self.assertEqual(augmented['evidence']['history_used_count'], 1)
        self.assertEqual(augmented['evidence']['history_excluded_count'], 1)

    def test_old_history_no_longer_changes_ranking_but_v2_is_unchanged(self):
        history = [dict(hospital='H1', submitted_at=T0 - 1150, observed_at=T0 - 1000,
                        outcome='decline') for _ in range(2)]
        history += [dict(hospital='H2', submitted_at=T0 - 1001, observed_at=T0 - 1000,
                         outcome='accept') for _ in range(2)]
        args = arguments(T0, candidates=[candidate('H1', 60), candidate('H2', 70)], observations=history)
        v2 = policies.decide('observed_v2', **args)
        v3 = policies.decide('v3_w60_h900', **args)
        self.assertEqual(v2['send'], ['H2'])
        self.assertEqual(v3['send'], ['H1'])
        self.assertEqual(v3['evidence']['history_used_count'], 0)
        self.assertEqual(v3['evidence']['history_excluded_count'], 4)

    def test_duplicate_pending_is_not_counted_twice_and_inputs_and_patient_text_are_not_leaked(self):
        secret = 'DO-NOT-EXPOSE-PATIENT-ID'
        args = arguments(T0 + 60, [own('H1', T0, patient=secret)])
        args['patient_pending'][0]['symptoms'] = 'DO-NOT-EXPOSE-SYMPTOMS'
        before = copy.deepcopy(args)
        base = policies.decide('v3_w60_h900', **args)
        self.assertEqual(args, before)
        duplicated = copy.deepcopy(args)
        duplicated['pending'] *= 2
        duplicated['patient_pending'] *= 2
        self.assertEqual(policies.decide('v3_w60_h900', **duplicated), base)
        serialized = json.dumps(base, ensure_ascii=False)
        self.assertNotIn(secret, serialized)
        self.assertNotIn('DO-NOT-EXPOSE-SYMPTOMS', serialized)
        with self.assertRaises(ValueError):
            policies.decide('v3_w60_h900', **dict(args, candidates=[candidate('H2', 60), candidate('H2', 70)]))


class V3ServerWorkflow(PolicyBase):
    def test_server_starts_one_reviews_at_fifteen_and_adds_contact_at_selected_bound(self):
        self.add_hospitals()
        self.patient(auto_inquiry=True, inquiry_policy='observed_v3')
        bound = self.job()['policy']['config']['max_wait_seconds']
        self.run_worker()
        first = self.inquiries()[0]
        self.assertEqual(len(self.inquiries()), 1)
        self.assertEqual(self.job()['next_evaluation_at'], T0 + 15)
        for review in range(15, bound, 15):
            self.run_worker(T0 + review)
        self.run_worker(T0 + bound - 1)
        self.assertEqual(len(self.inquiries()), 1)
        self.run_worker(T0 + bound)
        self.assertEqual(len(self.inquiries()), 2)
        self.assertEqual(self.job(now=T0 + bound)['latest_decision']['reason'], 'bounded_wait_expansion')
        self.run_worker(T0 + bound + 15)
        self.assertEqual(len(self.inquiries()), 2)
        self.assertEqual(self.inquiries()[0]['id'], first['id'])
        self.assertEqual({q['state'] for q in self.inquiries()}, {'PENDING'})

    def test_server_uses_latest_sent_time_even_after_that_request_is_declined(self):
        self.add_hospitals()
        self.patient(auto_inquiry=True, inquiry_policy='observed_v3')
        bound = self.job()['policy']['config']['max_wait_seconds']
        for review in range(0, bound + 1, 15):
            self.run_worker(T0 + review)
        self.assertEqual(len(self.inquiries()), 2)
        newest = self.inquiries()[-1]
        service.respond(self.conn, T0 + bound + 1, newest['id'], 'decline',
                        'synthetic-nurse', reason='병상')
        self.run_worker(T0 + bound + 15)
        self.assertEqual(len(self.inquiries()), 2)
        decision = self.job(now=T0 + bound + 15)['latest_decision']
        self.assertEqual(decision['hospital_ids'], [])
        self.assertEqual(decision['evidence']['last_contact_age_seconds'], 15)

    def test_late_acceptance_remains_real_acceptance_and_wait_timeout_is_not_decline(self):
        self.add_hospitals()
        self.patient(auto_inquiry=True, inquiry_policy='observed_v3')
        self.run_worker()
        first = self.inquiries()[0]
        for delta in (60, 120, 181):
            self.run_worker(T0 + delta)
        q = next(q for q in self.inquiries() if q['id'] == first['id'])
        self.assertEqual(q['state'], 'PENDING')
        service.respond(self.conn, T0 + 182, first['id'], 'accept', 'synthetic-nurse')
        count = len(self.inquiries())
        self.run_worker(T0 + 183)
        self.assertEqual(len(self.inquiries()), count)
        self.assertEqual(self.job(now=T0 + 183)['state'], 'AWAITING_CONFIRMATION')
        patient = service.patient_view(self.conn, 'A', T0 + 183)
        self.assertIsNone(patient['destination_hospital_id'])
        self.assertEqual(next(q for q in self.inquiries() if q['id'] == first['id'])['state'], 'ACCEPTED')

    def test_stale_frozen_v3_configuration_fails_without_database_changes(self):
        self.patient(auto_inquiry=True, inquiry_policy='observed_v3')
        before_config = policies.spec('observed_v3')
        before_config['config']['max_wait_seconds'] = 999
        self.conn.execute("UPDATE inquiry_workflows SET policy_config=? WHERE patient_id='A'",
                          (db.dumps(before_config),))
        job = tuple(self.conn.execute("SELECT * FROM inquiry_workflows WHERE patient_id='A'").fetchone())
        events = [tuple(r) for r in self.conn.execute('SELECT * FROM events ORDER BY id')]
        error = self.expect_error(409, workflow.start, self.conn, T0 + 1, 'A',
                                  enabled=False, policy_name='observed_v3')
        self.assertEqual(error.code, 'POLICY_VERSION_MISMATCH')
        self.assertEqual(tuple(self.conn.execute("SELECT * FROM inquiry_workflows WHERE patient_id='A'").fetchone()), job)
        self.assertEqual([tuple(r) for r in self.conn.execute('SELECT * FROM events ORDER BY id')], events)
        self.assertEqual(self.inquiries(), [])


class DevelopmentSelection(unittest.TestCase):
    def v3(self):
        from server import inquiry_v3_experiment
        return inquiry_v3_experiment

    def row(self, name, success, wait, contacts):
        return dict(policy=name, family_count=8, success_fraction_mean=success,
                    selection_wait_capped_mean=wait, inquiries_per_patient_mean=contacts)

    def test_selection_prioritizes_failed_fraction_before_wait_and_contacts(self):
        names = ('v3_w45_h600', 'v3_w60_h900', 'v3_w90_h900')
        rows = [self.row(names[0], .99, 250, 10), self.row(names[1], .98, 1, 1),
                self.row(names[2], .97, 0, 0)]
        result = self.v3().select_variant(rows, variants=names)
        self.assertEqual(result['name'], names[0])
        self.assertAlmostEqual(result['score']['failed_fraction_mean'], .01)
        self.assertAlmostEqual(result['score_vector'][0], .01)

    def test_selection_uses_wait_then_contacts_then_stable_name_order(self):
        names = ('v3_w45_h600', 'v3_w60_h900', 'v3_w90_h900')
        select = self.v3().select_variant
        rows = [self.row(names[0], 1, 30, 1), self.row(names[1], 1, 20, 8),
                self.row(names[2], 1, 25, 2)]
        self.assertEqual(select(rows, variants=names)['name'], names[1])
        rows = [self.row(names[0], 1, 20, 3), self.row(names[1], 1, 20, 2),
                self.row(names[2], 1, 20, 2)]
        self.assertEqual(select(rows, variants=tuple(reversed(names)))['name'], names[1])
        self.assertEqual(select(list(reversed(rows)), variants=names), select(rows, variants=names))

    def test_family_equal_score_does_not_weight_large_patient_families_more(self):
        rows = []
        for family, count, selected in (('small', 1, 1), ('large', 100, 0)):
            rows.append(dict(family=family, policy='v3_w60_h900', patient_count=count,
                             selected_count=selected, selection_wait_capped_mean=300 if not selected else 10,
                             inquiries_per_patient=2, review_work_seconds_per_patient=20))
        summary = experiment.family_equal_summary(rows)
        self.assertEqual(summary[0]['family_count'], 2)
        self.assertEqual(summary[0]['success_fraction_mean'], .5)
        self.assertEqual(summary[0]['selection_wait_capped_mean'], 155)
        result = self.v3().select_variant(summary, variants=('v3_w60_h900',))
        self.assertEqual(result['score']['failed_fraction_mean'], .5)

    def test_validation_seeds_exclude_all_previous_and_development_seeds(self):
        validate = self.v3().validate_validation_seeds
        self.assertEqual(validate((9002, 9003), development_seeds=(9001,)), (9002, 9003))
        for seeds in ((), (True,), (9002, 9002), (9001,), (1001,), (2020,),
                      (3020,), (4020,), (5020,), (6108,)):
            with self.subTest(seeds=seeds):
                with self.assertRaises(ValueError):
                    validate(seeds, development_seeds=(9001,))
        self.assertTrue(set(self.v3().DEVELOPMENT_SEEDS).isdisjoint(self.v3().VALIDATION_SEEDS))

    def make_lock(self, folder):
        module = self.v3()
        output = Path(folder) / 'development'
        source = module.source_fingerprints()
        scores = [self.row(name, 1, 20, 2) for name in policies.V3_EXPERIMENT_NAMES]
        payload = dict(seeds=[9001], source_sha256=source, family_equal_summary=scores,
                       results=[], summary=[])
        with patch.object(module, 'make_suite', return_value=[]), \
                patch.object(module, '_execute', return_value=payload), \
                redirect_stdout(io.StringIO()):
            module.development(output, seeds=(9001,))
        return output.parent / (output.name + '_lock.json')

    def test_development_lock_rejects_edited_checksum_policy_and_changed_sources(self):
        module = self.v3()
        with tempfile.TemporaryDirectory() as folder:
            path = self.make_lock(folder)
            original = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(module.read_lock(path), original)
            edited = copy.deepcopy(original)
            edited['chosen_policy_spec']['config']['max_wait_seconds'] += 1
            path.write_text(json.dumps(edited), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'integrity'):
                module.read_lock(path)
            edited['sha256'] = experiment.digest({k: v for k, v in edited.items() if k != 'sha256'})
            path.write_text(json.dumps(edited), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'configuration'):
                module.read_lock(path)
            path.write_text(json.dumps(original), encoding='utf-8')
            sources = dict(original['source_sha256'], **{'server/inquiry_v3.py': 'b' * 64})
            with patch.object(module, 'source_fingerprints', return_value=sources):
                with self.assertRaisesRegex(ValueError, 'source'):
                    module.read_lock(path)

    def test_development_never_overwrites_an_existing_report_or_lock(self):
        module = self.v3()
        with tempfile.TemporaryDirectory() as folder:
            path = self.make_lock(folder)
            before = {file.name: file.read_bytes() for file in Path(folder).iterdir()}
            with self.assertRaisesRegex(ValueError, 'already exists'):
                module.development(path.parent / 'development', seeds=(9001,))
            self.assertEqual({file.name: file.read_bytes() for file in Path(folder).iterdir()}, before)

    def test_real_small_development_and_validation_are_independently_audited_with_ten_and_five_branches(self):
        from server import inquiry_experiment_audit as auditor
        from test_server_inquiry_experiment import scenario
        module = self.v3()
        development_source = scenario(1, 1, service_seconds=7)
        development_source.update(family='small-test', seed=9001, description='isolated test')
        validation_source = copy.deepcopy(development_source)
        validation_source.update(id='unit-validation', seed=9002)
        with tempfile.TemporaryDirectory() as folder:
            development_path = Path(folder) / 'development'
            validation_path = Path(folder) / 'validation'
            with patch.object(module, 'make_suite', return_value=[development_source]), \
                    redirect_stdout(io.StringIO()):
                developed = module.development(development_path, seeds=(9001,))
            lock_path = Path(folder) / 'development_lock.json'
            with patch.object(module, 'make_suite', return_value=[validation_source]), \
                    redirect_stdout(io.StringIO()):
                validated = module.validation(validation_path, lock_path, seeds=(9002,))
            self.assertEqual(len(developed['results'][0]['branches']), 10)
            self.assertEqual(len(validated['results'][0]['branches']), 5)
            self.assertEqual(validated['selected_policy'], developed['selected_policy'])
            for output, branch_count in ((development_path, 10), (validation_path, 5)):
                audit = auditor.audit(output.with_suffix('.json'))
                self.assertTrue(audit['passed'], audit['failures'])
                self.assertEqual(audit['counts']['branches'], branch_count)

            # 독립 감사도 검증 결과에서 다시 고르지 않고 개발 잠금의 선택을 요구한다.
            changed = copy.deepcopy(validated)
            alternative = next(name for name in policies.V3_EXPERIMENT_NAMES
                               if name != changed['selected_policy']['name'])
            changed['selected_policy']['name'] = alternative
            validation_path.with_suffix('.json').write_text(json.dumps(changed), encoding='utf-8')
            audit = auditor.audit(validation_path.with_suffix('.json'))
            self.assertFalse(audit['passed'])
            self.assertTrue(any(failure['group'] in ('selection_recomputed', 'coverage', 'lock_integrity')
                                for failure in audit['failures']))


if __name__ == '__main__':
    unittest.main()
