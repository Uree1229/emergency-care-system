"""Latest server routes over real full-mode HTTP/background workers.

All patients, hospital resources and locations are synthetic and private. These
tests exercise API role calls, not a microphone, browser UI or clinical effect.
No worker is invoked manually and no solver result is mocked.
"""
from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import Request, urlopen

from server.team_backup import backup_database, inspect_state, restore_database
from server.team_runtime import CommonRuntime


SERVER_INTEGRATION_EVIDENCE = {}
REPORT = ('47세 남성. 흉통. 의식 명료. 호흡 정상. 케이타스 2. 심전도 정상. '
          '혈압 120에 80. 맥박 90회. 산소포화도 98퍼센트.')


def safe_route(path):
    """Record route contracts without generated patient/session IDs or queries."""
    url = urlsplit(path)
    parts = url.path.split('/')
    if parts[1:4] == ['api', 'voice', 'sessions'] and len(parts) > 4:
        parts[4] = '{session}'
    elif len(parts) > 3 and parts[1:3] == ['api', 'patients']:
        parts[3] = '{patient}'
    elif len(parts) > 3 and parts[1:3] == ['api', 'hospitals']:
        parts[3] = '{hospital}'
    elif len(parts) > 3 and parts[1:3] == ['api', 'inquiries']:
        parts[3] = '{inquiry}'
    for index, part in enumerate(parts[:-1]):
        if part == 'transport-change-reviews':
            parts[index+1] = '{review}'
    return '/'.join(parts), sorted({key for key, _ in parse_qsl(url.query, keep_blank_values=True)})


class LatestServerIntegrationTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[2]/'.codex-build/latest-server-integration/tmp'
        parent.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='http-', dir=parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root/'synthetic.db'
        self.runtimes = []
        self.addCleanup(self.close_all)
        self.actor = 'SYNTHETIC-INTEGRATION-CREW'
        self.counter = 0
        self.requests, self.checks, self.publications = [], [], []
        self.started = time.monotonic()
        self.start_runtime(self.path)
        self.start_health = self.wait_health()

    def close_all(self):
        for runtime in reversed(self.runtimes):
            runtime.close()

    def start_runtime(self, path):
        runtime = CommonRuntime(path, port=0, mode='full')
        self.runtimes.append(runtime)
        runtime.start()
        self.active, self.path = runtime, Path(path)
        return runtime

    def tearDown(self):
        end = self.api('GET', '/api/system/status') if self.active.http is not None else None
        self.close_all()
        SERVER_INTEGRATION_EVIDENCE[self.id()] = dict(
            synthetic=True, actual_hospital_requests=0, actual_speech_count=0,
            manual_worker_calls=0, mocked_solver_results=0,
            worker_mode='actual_background_threads', runtime_starts=len(self.runtimes),
            requests=list(self.requests), checks=list(self.checks), plan_publications=list(self.publications),
            workers_start={key: self.start_health['workers'][key]['state'] for key in ('planning', 'inquiry')},
            workers_end={key: end['workers'][key]['state'] for key in ('planning', 'inquiry')} if end else None,
            all_workers_stopped=all(not worker.thread.is_alive() for runtime in self.runtimes
                for worker in (runtime.planner, runtime.inquiry_worker)),
            elapsed_seconds=round(time.monotonic()-self.started, 3))

    def check(self, label, condition):
        self.assertTrue(condition, label)
        self.checks.append(label)

    def api(self, method, path, body=None, *, status=200, error=None):
        request = Request(self.active.base_url+path, method=method,
            data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
            headers={'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=8) as response:
                actual, data = response.status, json.load(response)
        except HTTPError as response:
            with response:
                actual, data = response.code, json.load(response)
        route, keys = safe_route(path)
        self.requests.append(dict(method=method, path=route, query_keys=keys, status=actual))
        self.assertEqual(actual, status, method+' '+route+' status')
        if error is not None:
            self.assertEqual(data.get('error'), error, method+' '+route+' error contract')
        return data

    def wait(self, getter, condition, label, timeout=20):
        until = time.monotonic()+timeout
        while time.monotonic() < until:
            result = getter()
            if condition(result):
                return result
            time.sleep(.05)
        self.fail(label)

    def wait_health(self):
        return self.wait(lambda: self.api('GET', '/api/system/status'),
            lambda status: all(status['workers'][key]['state'] == 'RUNNING' for key in ('planning', 'inquiry')),
            'The actual background workers did not become RUNNING')

    def common(self, session=None, *, actor=None, **body):
        self.counter += 1
        return dict(actor_id=actor or self.actor, client_request_id='integration-'+str(self.counter),
                    **({'expected_version': session['version']} if session else {}), **body)

    def patient(self):
        return self.api('GET', f'/api/patients/{self.pid}')

    def fresh_plan(self, *, after_revision=-1, hospital=None):
        result = self.wait(lambda: self.api('GET', f'/api/patients/{self.pid}/recommendation'),
            lambda item: item['status'] == 'READY' and item['revision'] > after_revision and
                item['expires_at'] > int(time.time()) and isinstance(item['result'].get('solver'), dict) and
                (hospital is None or item['result']['hospital_id'] == hospital),
            'The actual planner did not publish the expected fresh recommendation')
        completed = self.wait(lambda: self.api('GET', '/api/system/status'),
            lambda health: not health['planning']['pending'] and
                health['planning']['revision'] == health['planning']['completed_revision'] and
                health['planning']['result_counts']['fresh'] > 0,
            'The actual planner did not complete the latest revision')
        self.publications.append(dict(revision=result['revision'], created_at=result['created_at'],
            expires_at=result['expires_at'], solver_status=result['result']['solver'].get('status'),
            result_status=result['result']['status'], destination_locked=result['result']['destination_locked'],
            completed_revision=completed['planning']['completed_revision'],
            fresh_count=completed['planning']['result_counts']['fresh']))
        return result

    def accept(self, inquiry):
        return self.api('POST', f'/api/inquiries/{inquiry["id"]}/respond',
            dict(action='accept', handled_by='SYNTHETIC-DESK', expected_version=inquiry['version']))['inquiry']

    def setup_transport(self):
        session = self.api('POST', '/api/voice/sessions', self.common())['session']
        session = self.api('POST', f'/api/voice/sessions/{session["id"]}/transcripts',
            self.common(session, text=REPORT, mode='auto', is_final=True,
                        utterance_id='integration-synthetic-report'))['session']
        session = self.api('POST', f'/api/voice/sessions/{session["id"]}/confirm',
                           self.common(session, confirmed=True))['session']
        result = self.api('POST', f'/api/voice/sessions/{session["id"]}/submit',
            self.common(session, start_inquiry=True, inquiry_policy='fixed_3',
                context=dict(consent=True, lat=35.1355, lon=126.86,
                             required_capabilities=['응급의학'], resource_demands={'ed_bed': 1})))
        self.pid = result['patient']['id']
        first = self.wait(self.patient, lambda patient: len(patient['inquiries']) == 3,
                          'The actual inquiry worker did not send the first batch')
        self.hid = 'H04'  # Nearby general ED; deliberately lacks cardiac intervention.
        self.check('voice_patient_receives_three_real_background_inquiries',
                   len(first['inquiries']) == 3 and any(q['hospital_id'] == self.hid for q in first['inquiries']))
        inquiry = self.accept(next(q for q in first['inquiries'] if q['hospital_id'] == self.hid))
        initial = self.fresh_plan(hospital=self.hid)
        self.check('accepted_hospital_is_recommended_without_automatic_confirmation',
                   initial['result']['confirmable_now'] and self.patient()['destination_hospital_id'] is None)
        self.api('POST', f'/api/patients/{self.pid}/confirm', {'inquiry_id': inquiry['id']})
        self.api('POST', f'/api/patients/{self.pid}/transport-review', {'enabled': True})
        departed = self.api('POST', f'/api/patients/{self.pid}/depart', {})
        self.check('explicit_crew_calls_confirm_and_start_transport',
                   departed['state'] == 'IN_TRANSIT' and departed['destination_hospital_id'] == self.hid)
        return self.fresh_plan(after_revision=initial['revision'], hospital=self.hid)

    def draft(self, changes):
        session = self.api('POST', '/api/voice/sessions', self.common(patient_id=self.pid))['session']
        session = self.api('PATCH', f'/api/voice/sessions/{session["id"]}/fields',
            self.common(session, changes=changes, change_kind='observation'))['session']
        return self.api('POST', f'/api/voice/sessions/{session["id"]}/confirm',
                        self.common(session, confirmed=True))['session']

    def request_review(self, session):
        path = f'/api/voice/sessions/{session["id"]}/transport-change-review'
        preview = self.api('GET', path+'?'+urlencode({'actor_id': self.actor}))['review']
        selected = self.api('POST', path+'/preview',
                            self.common(session, selection=preview['selection']))['review']
        self.check('get_and_explicit_selection_preview_preserve_the_proposal_contract',
                   preview['token'] == selected['token'] and selected['can_request'] and
                   not selected['automatic_destination_change'])
        body = self.common(session, expected_patient_version=selected['patient_version'],
            review_token=selected['token'], selection=selected['selection'], confirmed=True,
            allow_reduction=True, reason='Synthetic crew verified observations')
        before = self.patient()
        result = self.api('POST', path+'/request', body)
        duplicate = self.api('POST', path+'/request', copy.deepcopy(body))
        after = self.patient()
        self.check('request_and_lost_response_retry_leave_patient_and_destination_unchanged',
                   duplicate.get('duplicate') is True and result['review']['id'] == duplicate['review']['id'] and
                   before['version'] == after['version'] and before['extra'] == after['extra'] and
                   before['destination_hospital_id'] == after['destination_hospital_id'] and
                   result['review']['state'] == 'PENDING')
        return result['review']

    def listed(self, rid):
        patient_items = self.api('GET', f'/api/patients/{self.pid}/transport-change-reviews')['reviews']
        hospital_items = self.api('GET', f'/api/hospitals/{self.hid}/transport-change-reviews')['reviews']
        own = next(item for item in patient_items if item['id'] == rid)
        other = next(item for item in hospital_items if item['id'] == rid)
        self.check('patient_and_current_hospital_see_the_same_scoped_review', own == other)
        return own

    def decision_body(self, review, decision):
        return self.common(actor='SYNTHETIC-HOSPITAL-DESK', expected_review_version=review['version'],
            expected_patient_version=review['current_patient_version'], decision_token=review['decision_token'],
            decision=decision, confirmed=True, reason='Synthetic current hospital reviewed change')

    def test_provenance_routes_are_read_only_with_real_workers_and_strict_queries(self):
        before = inspect_state(self.path)
        inventory = self.api('GET', '/api/data-provenance')
        hospital = self.api('GET', '/api/hospitals/H04/data-provenance')
        scope = self.api('GET', '/api/resource-model-scope')
        self.check('all_provenance_routes_report_saved_synthetic_scope_without_external_calls',
                   inventory['read_only'] and inventory['external_api_calls'] == 0 and
                   len(inventory['hospitals']) == 10 and len(hospital['hospitals']) == 1 and
                   hospital['hospitals'][0]['hospital_id'] == 'H04' and
                   scope['model_resources'] == ['ed_bed', 'special_team'] and
                   not scope['public_observation_to_operating_capacity'] and
                   not scope['public_observation_confirms_patient_acceptance'])
        for path in ('/api/data-provenance', '/api/hospitals/H04/data-provenance', '/api/resource-model-scope'):
            self.api('GET', path+'?unexpected=', status=400, error='INVALID')
            self.api('GET', path+'?unexpected=1&unexpected=2', status=400, error='INVALID')
        after = inspect_state(self.path)
        self.check('quiescent_business_rows_are_unchanged_by_provenance_gets_and_query_rejections',
                   before['schema_sha256'] == after['schema_sha256'] and
                   before['business_sha256'] == after['business_sha256'])

    def test_enroute_keep_validates_scope_tokens_retries_and_replans_after_hospital_apply(self):
        previous_plan = self.setup_transport()
        session = self.draft({'pulse': 105})
        review = self.request_review(session)
        current = self.listed(review['id'])
        path = f'/api/hospitals/{self.hid}/transport-change-reviews/{review["id"]}/decision'
        body = self.decision_body(current, 'keep')
        negatives = (
            ('GET', f'/api/voice/sessions/{session["id"]}/transport-change-review?actor_id=x&actor_id=y', None),
            ('POST', f'/api/voice/sessions/{session["id"]}/transport-change-review/preview?unexpected=', {}),
            ('POST', f'/api/voice/sessions/{session["id"]}/transport-change-review/request?unexpected=', {}),
            ('GET', f'/api/patients/{self.pid}/transport-change-reviews?unexpected=', None),
            ('GET', f'/api/hospitals/{self.hid}/transport-change-reviews?unexpected=', None),
            ('POST', path+'?unexpected=', body))
        for method, query, payload in negatives:
            self.api(method, query, payload, status=400, error='INVALID')
        self.api('POST', path.replace('/H04/', '/H01/'), body, status=404, error='NOT_FOUND')
        self.api('POST', path, dict(body, decision_token='0'*64), status=409, error='ENROUTE_REVIEW_STALE')
        self.check('invalid_queries_wrong_hospital_and_stale_token_do_not_apply_the_proposal',
                   self.patient()['extra']['vitals']['hr'] == 90 and self.listed(review['id'])['state'] == 'PENDING')
        applied = self.api('POST', path, body)
        retried = self.api('POST', path, copy.deepcopy(body))
        self.api('POST', path.replace('/H04/', '/H01/'), body, status=404, error='NOT_FOUND')
        patient = self.patient()
        self.check('hospital_keep_applies_once_and_preserves_the_current_transport_destination',
                   applied['review']['state'] == 'APPLIED_KEEP' and retried.get('duplicate') is True and
                   patient['extra']['vitals']['hr'] == 105 and patient['state'] == 'IN_TRANSIT' and
                   patient['destination_hospital_id'] == self.hid and not patient['needs_adjustment'] and
                   patient['version'] == applied['patient']['version'])
        resumed = self.fresh_plan(after_revision=previous_plan['revision'], hospital=self.hid)
        self.check('actual_planner_republishes_updated_patient_with_destination_held',
                   resumed['result']['destination_locked'] and resumed['result']['status'] == 'DESTINATION_HELD')

    def test_pending_review_backup_restore_then_adjust_requires_new_acceptance_and_manual_reroute(self):
        self.setup_transport()
        def saved_patient():
            with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True)) as conn:
                return conn.execute('SELECT * FROM patients WHERE id=?', (self.pid,)).fetchone()
        before_request = saved_patient()
        session = self.draft({'stemi_suspected': 'yes'})
        review = self.request_review(session)
        self.check('changed_conditions_are_proposed_without_silently_replacing_the_patient',
                   review['proposal']['proposed']['treatment_profile_id'] == 'TR_CARDIAC_INTERVENTION' and
                   saved_patient() == before_request)
        backup = backup_database(self.path, self.root/'pending-backup')
        source_runtime = self.active
        source_runtime.close()
        restored_path = self.root/'pending-restored.db'
        restored = restore_database(self.root/'pending-backup', restored_path)
        snapshot_state = inspect_state(Path(backup['snapshot_path']))
        restored_state = inspect_state(restored_path)
        with closing(sqlite3.connect(restored_path.resolve().as_uri()+'?mode=ro', uri=True)) as conn:
            pending_state = conn.execute('SELECT state FROM enroute_patient_reviews WHERE id=?', (review['id'],)).fetchone()[0]
        self.check('verified_restore_preserves_pending_review_and_all_snapshot_business_rows',
                   restored['business_state_preserved'] and pending_state == 'PENDING' and
                   snapshot_state['schema_sha256'] == restored_state['schema_sha256'] and
                   snapshot_state['business_sha256'] == restored_state['business_sha256'])
        self.start_runtime(restored_path)
        self.wait_health()
        current = self.listed(review['id'])
        self.check('restored_current_hospital_can_resume_the_pending_review',
                   current['state'] == 'PENDING' and current['can_decide'] and
                   self.patient()['destination_hospital_id'] == self.hid)
        path = f'/api/hospitals/{self.hid}/transport-change-reviews/{review["id"]}/decision'
        self.api('POST', path, self.decision_body(current, 'keep'),
                 status=409, error='ENROUTE_KEEP_UNSUPPORTED')
        current = self.listed(review['id'])
        adjusted = self.api('POST', path, self.decision_body(current, 'adjust'))
        self.check('hospital_adjust_applies_changed_conditions_but_does_not_reroute',
                   adjusted['review']['state'] == 'APPLIED_ADJUST' and
                   adjusted['patient']['extra']['treatment_profile_id'] == 'TR_CARDIAC_INTERVENTION' and
                   adjusted['patient']['needs_adjustment'] and
                   adjusted['patient']['destination_hospital_id'] == self.hid)
        patient = self.wait(self.patient,
            lambda item: any(q['hospital_id'] != self.hid and q['state'] == 'PENDING' and
                            q['patient_version'] == item['version'] for q in item['inquiries']),
            'The real restored inquiry worker did not search for a condition-compatible alternative')
        alternative = next(q for q in patient['inquiries'] if q['hospital_id'] != self.hid and
                           q['state'] == 'PENDING' and q['patient_version'] == patient['version'])
        location = {'lat': patient['lat'], 'lon': patient['lon']}
        self.api('POST', f'/api/patients/{self.pid}/change-destination',
                 dict(location, inquiry_id=alternative['id']), status=409, error='NOT_ACCEPTED')
        self.accept(alternative)
        self.wait(lambda: self.api('GET', f'/api/patients/{self.pid}/transport-review'),
            lambda item: item['workflow']['state'] == 'AWAITING_CHANGE',
            'The actual inquiry worker did not observe the new hospital acceptance')
        self.check('new_acceptance_still_waits_for_explicit_crew_destination_change',
                   self.patient()['destination_hospital_id'] == self.hid)
        changed = self.api('POST', f'/api/patients/{self.pid}/change-destination',
                           dict(location, inquiry_id=alternative['id']))
        self.check('explicit_crew_change_uses_the_new_acceptance_and_preserves_in_transit_state',
                   changed['destination_hospital_id'] == alternative['hospital_id'] and
                   changed['state'] == 'IN_TRANSIT' and not changed['needs_adjustment'])
        final = self.fresh_plan(hospital=alternative['hospital_id'])
        self.check('actual_planner_holds_the_new_destination_after_recovery_and_rerouting',
                   final['result']['destination_locked'] and final['result']['status'] == 'DESTINATION_HELD')


if __name__ == '__main__':
    unittest.main()
