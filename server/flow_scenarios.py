"""Frontend-free rehearsals through the existing local JSON API.

The script emulates explicitly labelled crew/hospital actions. It neither
implements a second workflow nor makes clinical or destination decisions.
Only loopback HTTP is accepted; requests bypass proxies and never follow
redirects. Reports contain checks and identifiers, not clinical/chat text.
"""
import ipaddress
import json
import math
import re
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlencode, urlsplit


SCENARIOS = ('normal', 'amended', 'change')
H1, H2 = 'FLOW-H1', 'FLOW-H2'
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class ScenarioError(RuntimeError):
    def __init__(self, message, report=None):
        super().__init__(message)
        self.report = report


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def local_origin(value):
    """Return a numeric loopback origin without DNS, credentials or path data."""
    if not isinstance(value, str) or any(c.isspace() for c in value):
        raise ValueError('base_url must be a loopback HTTP origin')
    parsed = urlsplit(value)
    try:
        port = parsed.port
        host = '127.0.0.1' if parsed.hostname == 'localhost' else parsed.hostname
        address = ipaddress.ip_address(host)
    except (ValueError, TypeError):
        raise ValueError('base_url must use a numeric loopback address or localhost') from None
    if (parsed.scheme != 'http' or not address.is_loopback or
            parsed.username is not None or parsed.password is not None or
            parsed.path not in ('', '/') or parsed.query or parsed.fragment or
            port is None or not 1 <= port <= 65535):
        raise ValueError('base_url must be http://loopback:port with no path or credentials')
    rendered = f'[{address}]' if address.version == 6 else str(address)
    return f'http://{rendered}:{port}'


class _Rehearsal:
    def __init__(self, base_url, scenario, run_id, timeout):
        self.base = local_origin(base_url)
        if scenario not in SCENARIOS:
            raise ValueError('scenario must be normal, amended or change')
        run_id = uuid.uuid4().hex[:12] if run_id is None else run_id
        if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,47}', run_id):
            raise ValueError('run_id must contain 1-48 ASCII letters, numbers, _ or -')
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise ValueError('timeout must be greater than zero and at most 120 seconds')
        self.timeout = timeout
        self.pid = f'FLOW-{run_id}-{scenario.upper()}'
        self.p = f'/api/patients/{self.pid}'
        self.crew = f'crew-{run_id}'
        self.desk = f'desk-{run_id}'
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        self.report = dict(schema_version=1, scenario=scenario, patient_id=self.pid,
                           synthetic=True, external_hospital_messages=False,
                           frontends=dict(paramedic='placeholder', hospital='placeholder'),
                           automation_scope='scripted_role_emulation', steps=[],
                           outcome=None, passed=False)

    def fail(self, message, step=None):
        if step is None and self.report['steps']:
            step = self.report['steps'][-1]['id']
        self.report['failure'] = dict(message=message, step_id=step)
        raise ScenarioError(message, self.report)

    def _http(self, method, path, body=None):
        request = urllib.request.Request(self.base + path, method=method,
            data=None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
        try:
            response = self.opener.open(request, timeout=min(self.timeout, 10))
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, OSError, TimeoutError):
            self.fail('Local API connection failed')
        with response:
            if self.report['steps']:
                self.report['steps'][-1]['status'] = response.status
            data = response.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                self.fail('Local API response exceeds the rehearsal limit')
            try:
                parsed = json.loads(data)
            except (ValueError, UnicodeDecodeError):
                self.fail('Local API did not return JSON')
            if not isinstance(parsed, dict):
                self.fail('Local API did not return a JSON object')
            return response.status, parsed

    def call(self, step, role, method, path, body=None, expected=200, checks=()):
        record = dict(id=step, role=role, method=method, path=path, status=None,
                      expected_status=expected, checks=list(checks))
        self.report['steps'].append(record)
        status, result = self._http(method, path, body)
        record['status'] = status
        if status != expected:
            record['error_code'] = result.get('error', 'UNEXPECTED_STATUS')
            self.fail(f'{step}: expected HTTP {expected}, received {status}', step)
        return result

    def check(self, condition, name):
        if self.report['steps']:
            self.report['steps'][-1]['checks'].append(dict(name=name, passed=bool(condition)))
        if not condition:
            self.fail(name, self.report['steps'][-1]['id'] if self.report['steps'] else None)

    def wait(self, step, path, predicate, role='paramedic'):
        """Poll without persisting a separate report entry for each unchanged read."""
        deadline = time.monotonic() + self.timeout
        polls = 0
        record = dict(id=step, role=role, method='GET', path=path, status=None,
                      expected_status=200, checks=[], polls=0)
        self.report['steps'].append(record)
        while True:
            polls += 1
            record['polls'] = polls
            status, result = self._http('GET', path)
            record['status'] = status
            if status != 200:
                record['error_code'] = result.get('error', 'UNEXPECTED_STATUS')
                self.fail(f'{step}: polling returned HTTP {status}', step)
            if predicate(result):
                record['checks'].append(dict(name='expected_state_observed', passed=True))
                return result
            if time.monotonic() >= deadline:
                record['checks'].append(dict(name='expected_state_observed', passed=False))
                self.fail(f'{step}: expected state was not observed within the polling timeout', step)
            time.sleep(min(.1, max(0, deadline - time.monotonic())))

    def hospital(self, hid):
        return f'/api/hospitals/{hid}/patients/{self.pid}'

    def current_inquiry(self, patient, hid):
        return next((q for q in patient['inquiries'] if q['hospital_id'] == hid
                     and q['patient_version'] == patient['version']
                     and q['state'] in ('PENDING', 'CHECKING', 'ACCEPTED')), None)

    def respond(self, step, hid, iid, action, **extra):
        inbox = self.call(step + '_read', 'hospital', 'GET', f'/api/hospitals/{hid}/inquiries')
        inquiry = next((q for q in inbox['open'] + inbox['recent']
                        if q['id'] == iid and q['patient_id'] == self.pid), None)
        self.check(inquiry is not None, 'hospital_discovers_own_inquiry')
        return self.call(step, 'hospital', 'POST', f'/api/inquiries/{iid}/respond',
                         dict(action=action, handled_by='가상 병원 담당자',
                              expected_version=inquiry['version'], **extra))

    def hospital_incoming(self, hid, step):
        listing = self.call(step, 'hospital', 'GET', f'/api/hospitals/{hid}/incoming')
        patient = next((p for p in listing['incoming'] if p['id'] == self.pid), None)
        self.check(patient is not None, 'hospital_discovers_confirmed_case_in_own_list')
        return patient

    def work(self, step, role, base, kind):
        actor = self.crew if role == 'paramedic' else self.desk
        result = self.call(step + '_start', role, 'POST', base + '/work-activities',
                           dict(actor_id=actor, client_activity_id=step + '-' + self.pid, kind=kind))
        return result['activity']['id'], actor

    def stop_work(self, step, role, base, activity):
        aid, actor = activity
        self.call(step + '_stop', role, 'POST', base + f'/work-activities/{aid}/stop',
                  dict(actor_id=actor))

    def call_record(self, requested_at):
        activity = self.work('crew_call', 'paramedic', self.p, 'call')
        body = dict(actor_id=self.crew, client_record_id='call-' + self.pid,
                    hospital_id=H1, call_started_at=requested_at, call_ended_at=requested_at,
                    reported_outcome='can_accept', counterparty_role='emergency_staff',
                    note='[가상 역할 시연] 전화 전달 내용 기록; 실제 수용 결정과 별도')
        record = self.call('call_record_create', 'paramedic', 'POST', self.p + '/call-records', body)['record']
        self.stop_work('crew_call', 'paramedic', self.p, activity)
        inbox = self.call('hospital_call_inbox', 'hospital', 'GET', f'/api/hospitals/{H1}/call-records')
        self.check(any(r['id'] == record['id'] for r in inbox['records']), 'call_discoverable_without_patient_id')
        confirmation = self.call('call_record_confirm', 'hospital', 'POST',
            self.hospital(H1) + f'/call-records/{record["id"]}/confirmation',
            dict(actor_id=self.desk, client_confirmation_id='confirm-' + self.pid, decision='confirm'))
        self.check(confirmation['record']['confirmation_status'] == 'confirmed', 'record_content_confirmed')
        patient = self.call('call_is_not_acceptance', 'paramedic', 'GET', self.p)
        self.check(patient['state'] == 'WAITING' and patient['destination_hospital_id'] is None
                   and not any(q['state'] == 'ACCEPTED' for q in patient['inquiries']),
                   'call_confirmation_does_not_accept_or_assign')

    def chat(self, hid, prefix='initial'):
        conversation = self.call(prefix + '_conversation', 'paramedic', 'GET', self.p + '/conversation')
        session = conversation['active_hospital_session']
        self.check(session is not None and session['hospital_id'] == hid, 'chosen_hospital_joins_room')
        self.check(any(item['kind'] == 'system' for item in conversation['items']), 'server_events_in_same_room')
        crew_body = dict(body='[가상 시연] 현재 상태를 공유합니다.', sender_name='가상 구급대원',
                         client_message_id=prefix + '-crew-' + self.pid, hospital_session_id=session['id'])
        crew = self.call(prefix + '_crew_chat', 'paramedic', 'POST', self.p + '/messages', crew_body)
        repeat = self.call(prefix + '_crew_chat_retry', 'paramedic', 'POST', self.p + '/messages', crew_body)
        self.check(repeat['duplicate'] and repeat['message']['id'] == crew['message']['id'], 'message_retry_is_idempotent')
        self.call(prefix + '_hospital_chat', 'hospital', 'POST', self.hospital(hid) + '/messages',
                  dict(body='[가상 시연] 전달 내용을 확인했습니다.', sender_name='가상 병원 담당자',
                       client_message_id=prefix + '-hospital-' + self.pid, hospital_session_id=session['id']))
        view = self.call(prefix + '_chat_reconnect', 'paramedic', 'GET', self.p + '/conversation')
        self.check({item['sender_role'] for item in view['items']} >= {'system', 'patient', 'hospital'},
                   'three_roles_restored_after_reconnect')
        return session['id']

    def reassign(self):
        self.call('enable_transport_monitor', 'paramedic', 'POST', self.p + '/transport-review',
                  dict(enabled=True, policy_name='fixed_1'))
        self.call('update_transport_position', 'paramedic', 'PATCH', self.p + '/transport-position',
                  dict(lat=37.505, lon=126.957))
        external = self.call('external_arrival', 'hospital', 'POST', f'/api/hospitals/{H1}/external-arrivals',
                  dict(resource_demands={'ed_bed': 1}, handled_by='가상 병원 담당자',
                       request_id='arrival-' + self.pid, note='[가상 시연] 새 환자 유입'))
        self.wait('condition_change_detected', self.p + '/transport-review',
                  lambda r: r['review']['state'] == 'RECHECK_REQUIRED')
        events = self.call('important_events', 'paramedic', 'GET',
                           self.p + '/important-events?' + urlencode(dict(actor_id=self.crew)))
        event = next((e for e in events['items'] if e['type'] == 'transport_recheck_required'), None)
        self.check(event is not None, 'affected_case_has_important_event')
        eid = event['event_id']
        self.call('notice_delivery', 'paramedic', 'POST', self.p + f'/important-events/{eid}/delivered',
                  dict(actor_id=self.crew))
        self.call('notice_ack', 'paramedic', 'POST', self.p + f'/important-events/{eid}/acknowledge',
                  dict(actor_id=self.crew))
        reconnect = self.call('notice_reconnect', 'paramedic', 'GET', self.p + '/important-events?' +
                              urlencode(dict(actor_id=self.crew, unacknowledged_only='true')))
        self.check(all(e['event_id'] != eid for e in reconnect['items']), 'acknowledged_event_stays_acknowledged')
        patient = self.call('ack_is_not_reroute', 'paramedic', 'GET', self.p)
        self.check(patient['destination_hospital_id'] == H1 and not patient['needs_adjustment'],
                   'notice_ack_does_not_change_medical_decision')
        review = self.hospital_incoming(H1, 'hospital_review_incoming')['transport_review']
        work = self.work('hospital_recheck', 'hospital', self.hospital(H1), 'recheck')
        self.call('hospital_adjustment', 'hospital', 'POST', self.hospital(H1) + '/decision',
                  dict(decision='adjust', note='[가상 시연] 기존 조건 유지가 어려워 대체 문의 요청',
                       handled_by='가상 병원 담당자', expected_review_version=review['version'],
                       expected_risk_signature=review['risk_signature']))
        self.stop_work('hospital_recheck', 'hospital', self.hospital(H1), work)
        patient = self.wait('automatic_alternative_inquiry', self.p,
                            lambda p: self.current_inquiry(p, H2) is not None)
        self.check(patient['destination_hospital_id'] == H1, 'automatic_search_keeps_original_destination')
        inquiry = self.current_inquiry(patient, H2)
        self.respond('alternative_acceptance', H2, inquiry['id'], 'accept')
        patient = self.call('accept_is_not_reroute', 'paramedic', 'GET', self.p)
        self.check(patient['destination_hospital_id'] == H1, 'new_acceptance_still_needs_explicit_destination_change')
        self.call('explicit_destination_change', 'paramedic', 'POST', self.p + '/change-destination',
                  dict(inquiry_id=inquiry['id'], lat=37.505, lon=126.957))
        self.hospital_incoming(H2, 'replacement_hospital_incoming')
        previous = self.call('previous_hospital_incoming', 'hospital', 'GET', f'/api/hospitals/{H1}/incoming')
        self.check(all(p['id'] != self.pid for p in previous['incoming']), 'previous_incoming_list_releases_case')
        old = self.call('old_hospital_room', 'hospital', 'GET', self.hospital(H1) + '/conversation')
        self.check(not old['can_send'], 'old_hospital_room_is_read_only')
        old_session = old['hospital_sessions'][-1]['id']
        self.call('old_hospital_new_message_blocked', 'hospital', 'POST', self.hospital(H1) + '/messages',
                  dict(body='[가상 시연] 참여 종료 후 전송 차단 확인', sender_name='가상 이전 병원',
                       hospital_session_id=old_session, client_message_id='old-blocked-' + self.pid), expected=403)
        self.chat(H2, prefix='replacement')
        # The arrival ledger remains explicit, just like an operator's cleanup.
        return H2, external

    def run(self):
        contract = self.call('flow_server_contract', 'server', 'GET', '/manifest.json')
        frontends = contract.get('frontends')
        self.check(contract.get('schema_version') == 1 and contract.get('service') == 'frontend-free-flow'
                   and contract.get('synthetic') is True and contract.get('external_hospital_messages') is False
                   and isinstance(frontends, dict)
                   and all(isinstance(frontends.get(role), dict)
                           and frontends[role].get('status') == 'placeholder'
                           for role in ('paramedic', 'hospital')),
                   'dedicated_frontend_free_server_verified_before_mutation')
        payload = dict(id=self.pid, reporter_role='transporter', consent=True, age=22,
                       lat=37.505, lon=126.957, symptoms='[가상 시연] 손목 통증',
                       consciousness='alert', breathing='normal',
                       required_capabilities=['정형외과'], resource_demands={'ed_bed': 1},
                       extra=dict(synthetic=True, scenario_id='FRONTEND_FREE_FLOW',
                                  hospital_messages_delivered_externally=False))
        created = self.call('patient_register', 'paramedic', 'POST', '/api/patients', payload)
        self.check(created['state'] == 'WAITING', 'new_case_waiting')
        self.call('start_automatic_inquiry', 'paramedic', 'POST', self.p + '/inquiry-workflow',
                  dict(enabled=True, policy_name='fixed_1'))
        patient = self.wait('automatic_initial_inquiry', self.p,
                            lambda p: self.current_inquiry(p, H1) is not None)
        inquiry = self.current_inquiry(patient, H1)
        self.call_record(created['requested_at'])
        work = self.work('hospital_review', 'hospital', self.hospital(H1), 'request_review')
        self.respond('hospital_checking', H1, inquiry['id'], 'checking')
        self.respond('hospital_additional_question', H1, inquiry['id'], 'question',
                     question='[가상 시연] 추가 확인 내용을 알려주세요.')
        self.call('crew_additional_answer', 'paramedic', 'POST', self.p + f'/inquiries/{inquiry["id"]}/answer',
                  dict(answer='[가상 시연] 확인했습니다.'))
        self.respond('hospital_acceptance', H1, inquiry['id'], 'accept')
        self.stop_work('hospital_review', 'hospital', self.hospital(H1), work)
        if self.report['scenario'] == 'amended':
            before = self.call('before_amendment', 'paramedic', 'GET', self.p)
            old = self.current_inquiry(before, H1)
            self.call('patient_information_amended', 'paramedic', 'POST', self.p + '/amendments',
                      dict(actor_id=self.crew, client_request_id='amend-' + self.pid,
                           expected_patient_version=before['version'], reason='[가상 시연] 추가 상태 확인',
                           changes={'symptoms': '[가상 시연] 손목 통증과 부종'}))
            patient = self.wait('automatic_reinquiry_after_amendment', self.p,
                lambda p: p['version'] > before['version'] and self.current_inquiry(p, H1) is not None
                          and self.current_inquiry(p, H1)['state'] != 'ACCEPTED')
            inquiry = self.current_inquiry(patient, H1)
            self.check(patient['destination_hospital_id'] is None, 'amendment_keeps_case_unconfirmed')
            self.call('old_response_version_rejected', 'hospital', 'POST', f'/api/inquiries/{inquiry["id"]}/respond',
                      dict(action='accept', handled_by='가상 병원 담당자', expected_version=old['version']), expected=409)
            self.respond('latest_information_acceptance', H1, inquiry['id'], 'accept')
        accepted = self.call('before_destination_confirmation', 'paramedic', 'GET', self.p)
        self.check(accepted['state'] == 'WAITING' and accepted['destination_hospital_id'] is None,
                   'acceptance_is_separate_from_destination_confirmation')
        self.call('crew_confirm_destination', 'paramedic', 'POST', self.p + '/confirm',
                  dict(inquiry_id=inquiry['id']))
        self.hospital_incoming(H1, 'initial_hospital_incoming')
        self.chat(H1)
        self.call('crew_departure', 'paramedic', 'POST', self.p + '/depart', dict(lat=37.505, lon=126.957))
        destination, external = H1, None
        if self.report['scenario'] == 'change':
            destination, external = self.reassign()
        self.call('hospital_arrival', 'hospital', 'POST', self.hospital(destination) + '/arrive',
                  dict(handled_by='가상 도착 담당자'))
        closed = self.call('arrival_closes_chat', 'paramedic', 'GET', self.p + '/conversation')
        self.check(closed['conversation']['state'] == 'CLOSED' and not closed['can_send'], 'arrival_closes_room')
        finished = self.call('hospital_discharge', 'hospital', 'POST', self.hospital(destination) + '/discharge',
                             dict(handled_by='가상 퇴실 담당자'))
        if external is not None:
            external_id = external['external_arrival_id']
            self.check(external_id is not None, 'external_arrival_identifier_available')
            self.call('external_patient_discharge', 'hospital', 'POST',
                      f'/api/hospitals/{H1}/external-arrivals/{external_id}/discharge',
                      dict(handled_by='가상 퇴실 담당자'))
        summary = self.call('completed_work_summary', 'paramedic', 'GET', self.p + '/work-activities/summary')
        self.check(summary['totals']['open_activity_count'] == 0, 'work_activities_have_explicit_stop')
        self.check(finished['state'] == 'COMPLETED', 'case_completed')
        self.report['outcome'] = dict(state=finished['state'], destination_hospital_id=finished['destination_hospital_id'],
            destination_changed=destination != H1, clinical_effectiveness_evaluated=False,
            measured_human_work_seconds_are_demonstration_only=True)
        self.report['passed'] = True
        return self.report


def run_scenario(base_url, scenario, *, run_id=None, timeout=10):
    """Rehearse one case against the isolated local flow server.

    ``timeout`` bounds each expected-state poll and HTTP calls, not an estimate
    of clinical response time. Human actions are scripted for integration QA.
    """
    run = _Rehearsal(base_url, scenario, run_id, timeout)
    try:
        return run.run()
    except ScenarioError:
        raise
    except (KeyError, TypeError, StopIteration, ValueError):
        run.fail('Local API response did not match the rehearsal contract',
                 run.report['steps'][-1]['id'] if run.report['steps'] else None)
