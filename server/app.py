"""병원 웹·환자 웹 공통 JSON API 서버 (S01).

실행:  python -m server.app --db data/demo.db --seed --port 8000
화면은 2~3초 주기로 이 서버의 상태만 조회한다. 의료·지도 API를 매 조회마다 부르지 않는다.
로그인이 없으므로 병원은 경로의 병원 ID로 구분한다(시연용 단순화).
"""
import argparse
import json
import re
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import db, service, synthetic, conversations, inquiry_workflow, inquiry_policies, transport_review, transport_reassignment
from .travel import TravelEstimator
from . import system_status
from . import recommendation_view
from . import accounting_reconciliation
from . import work_activity, important_events, call_records, call_inbox, patient_amendments
from . import voice_input, voice_terms, voice_lexicon, voice_treatment_review, allocation_input_review
from . import enroute_patient_review, data_provenance

STATIC = Path(__file__).parent / 'static'
UI_ROOTS = {
    '/paramedic/': Path(__file__).resolve().parents[1] / 'paramedic-ui',
    '/patient/': Path(__file__).resolve().parents[1] / 'patient-ui',
    '/hospital/': Path(__file__).resolve().parents[1] / 'hospital-ui',
}
ROUTES = []


def route(method, pattern):
    def wrap(fn):
        ROUTES.append((method, re.compile(f'^{pattern}$'), fn))
        return fn
    return wrap


# Voice drafts are separate from patient records until an explicit confirmed submit.
def _voice_query(ctx, allowed=()):
    service._require(not (ctx.query.keys() - set(allowed)), 'INVALID', '지원하지 않는 조회 항목입니다')


@route('GET', r'/api/voice/capabilities')
def voice_capabilities(ctx):
    _voice_query(ctx)
    return dict(voice_input.capabilities(), dictionary=voice_lexicon.overview(ctx.conn),
                allocation_input_review_revision=allocation_input_review.REVISION)


@route('GET', r'/api/voice/terms')
def voice_catalog(ctx):
    _voice_query(ctx)
    with voice_terms.use_dictionary(voice_lexicon.snapshot(ctx.conn)):
        return voice_terms.catalog()


@route('GET', r'/api/voice/dictionary')
def voice_dictionary(ctx):
    _voice_query(ctx)
    return voice_lexicon.overview(ctx.conn)


@route('GET', r'/api/voice/dictionary/search')
def voice_dictionary_search(ctx):
    _voice_query(ctx, ('q', 'limit', 'offset', 'source_id'))
    return voice_lexicon.search(ctx.conn, ctx.query.get('q', ''), limit=ctx.query.get('limit', '20'),
                               offset=ctx.query.get('offset', '0'), source_id=ctx.query.get('source_id'))


@route('GET', r'/api/voice/command-catalog')
def voice_command_catalog(ctx):
    _voice_query(ctx)
    with voice_terms.use_dictionary(voice_lexicon.snapshot(ctx.conn)):
        return voice_terms.command_catalog()


@route('POST', r'/api/voice/sessions')
def create_voice_session(ctx):
    _voice_query(ctx)
    return voice_input.create(ctx.conn, ctx.now, ctx.body)


@route('GET', r'/api/voice/sessions/(?P<sid>[\w-]+)')
def get_voice_session(ctx, sid):
    _voice_query(ctx, ('actor_id',))
    return voice_input.get(ctx.conn, ctx.now, sid, ctx.query.get('actor_id'))


@route('POST', r'/api/voice/sessions/(?P<sid>[\w-]+)/transcripts')
def voice_transcript(ctx, sid):
    _voice_query(ctx)
    return voice_input.transcript(ctx.conn, ctx.now, sid, ctx.body)


@route('POST', r'/api/voice/sessions/(?P<sid>[\w-]+)/audio')
def voice_audio(ctx, sid):
    _voice_query(ctx)
    return voice_input.audio(ctx.conn, ctx.now, sid, ctx.body)


@route('PATCH', r'/api/voice/sessions/(?P<sid>[\w-]+)/fields')
def edit_voice_fields(ctx, sid):
    _voice_query(ctx)
    return voice_input.edit(ctx.conn, ctx.now, sid, ctx.body)


@route('POST', r'/api/voice/sessions/(?P<sid>[\w-]+)/commands')
def voice_command(ctx, sid):
    _voice_query(ctx)
    return voice_input.command(ctx.conn, ctx.now, sid, ctx.body)


@route('GET', r'/api/voice/sessions/(?P<sid>[\w-]+)/readback')
def voice_readback(ctx, sid):
    _voice_query(ctx, ('actor_id', 'field'))
    return voice_input.readback(ctx.conn, ctx.now, sid, ctx.query.get('actor_id'), ctx.query.get('field'))


@route('GET', r'/api/voice/sessions/(?P<sid>[\w-]+)/history')
def voice_history(ctx, sid):
    _voice_query(ctx, ('actor_id',))
    return voice_input.history(ctx.conn, ctx.now, sid, ctx.query.get('actor_id'))


@route('GET', r'/api/voice/sessions/(?P<sid>[\w-]+)/followup')
def voice_followup(ctx, sid):
    _voice_query(ctx, ('actor_id', 'profile'))
    return voice_input.followup(ctx.conn, ctx.now, sid, ctx.query.get('actor_id'),
                                ctx.query.get('profile', 'voice_extended_v1'))


@route('POST', r'/api/voice/sessions/(?P<sid>[\w-]+)/followup/answers')
def voice_followup_answer(ctx, sid):
    _voice_query(ctx)
    return voice_input.answer_followup(ctx.conn, ctx.now, sid, ctx.body)


@route('POST', r'/api/voice/sessions/(?P<sid>[\w-]+)/undo')
def undo_voice_fields(ctx, sid):
    _voice_query(ctx)
    return voice_input.undo(ctx.conn, ctx.now, sid, ctx.body)


@route('POST', r'/api/voice/sessions/(?P<sid>[\w-]+)/confirm')
def confirm_voice_fields(ctx, sid):
    _voice_query(ctx)
    return voice_input.confirm(ctx.conn, ctx.now, sid, ctx.body)


@route('POST', r'/api/voice/sessions/(?P<sid>[\w-]+)/submit')
def submit_voice_fields(ctx, sid):
    _voice_query(ctx)
    return voice_input.submit(ctx.conn, ctx.now, sid, ctx.body)


@route('GET', r'/api/voice/sessions/(?P<sid>[\w-]+)/treatment-review')
def voice_treatment_preview(ctx, sid):
    _voice_query(ctx, ('actor_id',))
    return voice_treatment_review.preview(ctx.conn,ctx.now,sid,ctx.query.get('actor_id'))


@route('POST', r'/api/voice/sessions/(?P<sid>[\w-]+)/treatment-review/preview')
def voice_treatment_selection(ctx, sid):
    _voice_query(ctx)
    return voice_treatment_review.preview_request(ctx.conn,ctx.now,sid,ctx.body)


@route('POST', r'/api/voice/sessions/(?P<sid>[\w-]+)/treatment-review/apply')
def voice_treatment_apply(ctx, sid):
    _voice_query(ctx)
    return voice_treatment_review.apply(ctx.conn,ctx.now,sid,ctx.body,runtime=ctx.runtime)


@route('GET', r'/api/voice/sessions/(?P<sid>[\w-]+)/transport-change-review')
def enroute_voice_preview(ctx, sid):
    _voice_query(ctx, ('actor_id',))
    return enroute_patient_review.preview(ctx.conn,ctx.now,sid,ctx.query.get('actor_id'))


@route('POST', r'/api/voice/sessions/(?P<sid>[\w-]+)/transport-change-review/preview')
def enroute_voice_selection(ctx, sid):
    _voice_query(ctx)
    return enroute_patient_review.preview_request(ctx.conn,ctx.now,sid,ctx.body)


@route('POST', r'/api/voice/sessions/(?P<sid>[\w-]+)/transport-change-review/request')
def enroute_voice_request(ctx, sid):
    _voice_query(ctx)
    return enroute_patient_review.request(ctx.conn,ctx.now,sid,ctx.body)


@route('GET', r'/api/patients/(?P<pid>[\w-]+)/transport-change-reviews')
def enroute_patient_reviews(ctx, pid):
    _voice_query(ctx)
    return enroute_patient_review.list_reviews(ctx.conn,ctx.now,patient_id=pid)


@route('GET', r'/api/hospitals/(?P<hid>[\w-]+)/transport-change-reviews')
def enroute_hospital_reviews(ctx, hid):
    _voice_query(ctx)
    return enroute_patient_review.list_reviews(ctx.conn,ctx.now,hospital_id=hid)


@route('POST', r'/api/hospitals/(?P<hid>[\w-]+)/transport-change-reviews/(?P<rid>[\w-]+)/decision')
def enroute_hospital_decision(ctx, hid, rid):
    _voice_query(ctx)
    return enroute_patient_review.decide(ctx.conn,ctx.now,rid,hid,ctx.body)


@route('GET', r'/api/data-provenance')
def data_provenance_inventory(ctx):
    _voice_query(ctx)
    return data_provenance.inventory(ctx.conn,ctx.now)


@route('GET', r'/api/hospitals/(?P<hid>[\w-]+)/data-provenance')
def hospital_data_provenance(ctx, hid):
    _voice_query(ctx)
    return data_provenance.inventory(ctx.conn,ctx.now,hospital_id=hid)


@route('GET', r'/api/resource-model-scope')
def resource_model_scope(ctx):
    _voice_query(ctx)
    return data_provenance.resource_scope()


VOICE_HANDLERS = frozenset((voice_capabilities, voice_catalog, create_voice_session,
    get_voice_session, voice_transcript, voice_audio, edit_voice_fields, voice_command,
    voice_readback, voice_history, voice_followup, voice_followup_answer,
    undo_voice_fields, confirm_voice_fields, submit_voice_fields,
    voice_dictionary, voice_dictionary_search, voice_command_catalog,
    voice_treatment_preview, voice_treatment_selection, voice_treatment_apply,
    enroute_voice_preview, enroute_voice_selection, enroute_voice_request,
    enroute_patient_reviews, enroute_hospital_reviews, enroute_hospital_decision,
    data_provenance_inventory, hospital_data_provenance, resource_model_scope))


@route('GET', r'/api/system/status')
def get_system_status(ctx):
    return system_status.read_status(ctx.db_path, ctx.now, ctx.runtime)


# ---- 현장 업무 계측·중요 사건 전달 확인 (화면 연결용, 사용자 인증과는 별도)
def _measurement_integer(ctx, key, default=None, maximum=service.MAX_EPOCH):
    value = ctx.query.get(key)
    if value is None:
        return default
    service._require(isinstance(value, str) and value.isascii() and value.isdecimal()
                     and len(value) <= 19, 'INVALID', f'{key}는 0 이상의 정수입니다')
    number = int(value)
    service._require(number <= maximum, 'INVALID', f'{key} 값이 너무 큽니다')
    return number


@route('POST', r'/api/patients/(?P<pid>[\w-]+)/work-activities')
@route('POST', r'/api/hospitals/(?P<hid>[\w-]+)/patients/(?P<pid>[\w-]+)/work-activities')
def start_work_activity(ctx, pid, hid=None):
    return work_activity.start(ctx.conn, ctx.now, pid, 'hospital' if hid else 'paramedic',
                               ctx.body, hospital_id=hid)


@route('POST', r'/api/patients/(?P<pid>[\w-]+)/work-activities/(?P<aid>\d+)/stop')
@route('POST', r'/api/hospitals/(?P<hid>[\w-]+)/patients/(?P<pid>[\w-]+)/work-activities/(?P<aid>\d+)/stop')
def stop_work_activity(ctx, pid, aid, hid=None):
    return work_activity.stop(ctx.conn, ctx.now, pid, 'hospital' if hid else 'paramedic',
                              aid, ctx.body, hospital_id=hid)


@route('GET', r'/api/patients/(?P<pid>[\w-]+)/work-activities')
@route('GET', r'/api/hospitals/(?P<hid>[\w-]+)/patients/(?P<pid>[\w-]+)/work-activities')
def list_work_activities(ctx, pid, hid=None):
    return work_activity.list_records(ctx.conn, ctx.now, pid, 'hospital' if hid else 'paramedic',
                                      ctx.query.get('actor_id'), hospital_id=hid,
                                      limit=_measurement_integer(ctx, 'limit', 50, 100),
                                      before_id=_measurement_integer(ctx, 'before_id'))


@route('GET', r'/api/patients/(?P<pid>[\w-]+)/work-activities/summary')
@route('GET', r'/api/hospitals/(?P<hid>[\w-]+)/patients/(?P<pid>[\w-]+)/work-activities/summary')
def work_activity_summary(ctx, pid, hid=None):
    return work_activity.summary(ctx.conn, ctx.now, pid, 'hospital' if hid else 'paramedic',
                                 ctx.query.get('actor_id'), hospital_id=hid,
                                 since=_measurement_integer(ctx, 'since'),
                                 until=_measurement_integer(ctx, 'until'))


@route('GET', r'/api/patients/(?P<pid>[\w-]+)/important-events')
@route('GET', r'/api/hospitals/(?P<hid>[\w-]+)/patients/(?P<pid>[\w-]+)/important-events')
def list_important_events(ctx, pid, hid=None):
    return important_events.list_events(ctx.conn, ctx.now, pid,
                                        actor_id=ctx.query.get('actor_id'), hospital_id=hid,
                                        after_id=_measurement_integer(ctx, 'after_id', 0),
                                        limit=_measurement_integer(ctx, 'limit', 100, 100),
                                        unacknowledged_only=ctx.query.get('unacknowledged_only', False))


@route('POST', r'/api/patients/(?P<pid>[\w-]+)/important-events/(?P<eid>\d+)/delivered')
@route('POST', r'/api/hospitals/(?P<hid>[\w-]+)/patients/(?P<pid>[\w-]+)/important-events/(?P<eid>\d+)/delivered')
def deliver_important_event(ctx, pid, eid, hid=None):
    return important_events.mark_delivered(ctx.conn, ctx.now, pid, eid,
                                           actor_id=ctx.body.get('actor_id'), hospital_id=hid)


@route('POST', r'/api/patients/(?P<pid>[\w-]+)/important-events/(?P<eid>\d+)/acknowledge')
@route('POST', r'/api/hospitals/(?P<hid>[\w-]+)/patients/(?P<pid>[\w-]+)/important-events/(?P<eid>\d+)/acknowledge')
def acknowledge_important_event(ctx, pid, eid, hid=None):
    return important_events.acknowledge(ctx.conn, ctx.now, pid, eid,
                                      actor_id=ctx.body.get('actor_id'), hospital_id=hid)


def _call_record_query(ctx, allowed=()):
    service._require(not (ctx.query.keys() - set(allowed)), 'INVALID', '지원하지 않는 조회 필터입니다')


@route('POST', r'/api/patients/(?P<pid>[\w-]+)/call-records')
@route('POST', r'/api/hospitals/(?P<hid>[\w-]+)/patients/(?P<pid>[\w-]+)/call-records')
def create_call_record(ctx, pid, hid=None):
    _call_record_query(ctx)
    return call_records.create(ctx.conn, ctx.now, pid, ctx.body, hospital_id=hid)


@route('GET', r'/api/patients/(?P<pid>[\w-]+)/call-records')
@route('GET', r'/api/hospitals/(?P<hid>[\w-]+)/patients/(?P<pid>[\w-]+)/call-records')
def list_call_records(ctx, pid, hid=None):
    _call_record_query(ctx, ('limit', 'before_id'))
    return call_records.list_records(ctx.conn, ctx.now, pid, hospital_id=hid,
                                     limit=_measurement_integer(ctx, 'limit', 50, 100),
                                     before_id=_measurement_integer(ctx, 'before_id'))


@route('GET', r'/api/patients/(?P<pid>[\w-]+)/call-records/(?P<rid>\d+)')
@route('GET', r'/api/hospitals/(?P<hid>[\w-]+)/patients/(?P<pid>[\w-]+)/call-records/(?P<rid>\d+)')
def get_call_record(ctx, pid, rid, hid=None):
    _call_record_query(ctx)
    return call_records.view(ctx.conn, ctx.now, pid, rid, hospital_id=hid)


@route('POST', r'/api/patients/(?P<pid>[\w-]+)/call-records/(?P<rid>\d+)/confirmation')
@route('POST', r'/api/hospitals/(?P<hid>[\w-]+)/patients/(?P<pid>[\w-]+)/call-records/(?P<rid>\d+)/confirmation')
def confirm_call_record(ctx, pid, rid, hid=None):
    _call_record_query(ctx)
    return call_records.confirm(ctx.conn, ctx.now, pid, rid, ctx.body, hospital_id=hid)


@route('GET', r'/api/hospitals/(?P<hid>[\w-]+)/call-records')
def hospital_call_inbox(ctx, hid):
    _call_record_query(ctx, ('limit', 'before_id', 'after_id', 'confirmation_status',
                             'current_only', 'patient_id', 'author_role'))
    return call_inbox.list_records(ctx.conn, ctx.now, hid,
                                  limit=_measurement_integer(ctx, 'limit', 50, 100),
                                  before_id=_measurement_integer(ctx, 'before_id'),
                                  after_id=_measurement_integer(ctx, 'after_id'),
                                  confirmation_status=ctx.query.get('confirmation_status', 'all'),
                                  current_only=ctx.query.get('current_only', True),
                                  patient_id=ctx.query.get('patient_id'),
                                  author_role=ctx.query.get('author_role', 'all'))


@route('POST', r'/api/patients/(?P<pid>[\w-]+)/amendments')
def amend_patient_information(ctx, pid):
    _call_record_query(ctx)
    return patient_amendments.amend(ctx.conn, ctx.now, pid, ctx.body)


@route('GET', r'/api/patients/(?P<pid>[\w-]+)/amendments')
def list_patient_amendments(ctx, pid):
    _call_record_query(ctx, ('limit', 'before_id'))
    return patient_amendments.list_records(ctx.conn, ctx.now, pid,
                                          limit=_measurement_integer(ctx, 'limit', 50, 100),
                                          before_id=_measurement_integer(ctx, 'before_id'))


MEASUREMENT_HANDLERS = frozenset((start_work_activity, stop_work_activity, list_work_activities,
                                  work_activity_summary, list_important_events,
                                  deliver_important_event, acknowledge_important_event,
                                  create_call_record, list_call_records, get_call_record, confirm_call_record,
                                  hospital_call_inbox, amend_patient_information, list_patient_amendments))


# ---- 병원 웹
@route('GET', r'/api/hospitals')
def _(ctx):
    return service.list_hospitals(ctx.conn, ctx.now)


@route('GET', r'/api/hospitals/(?P<hid>[\w-]+)')
def _(ctx, hid):
    return service.hospital_view(ctx.conn, hid, ctx.now)


@route('PUT', r'/api/hospitals/(?P<hid>[\w-]+)/resources/(?P<rtype>[\w-]+)')
def _(ctx, hid, rtype):
    b = ctx.body
    return service.set_resource(ctx.conn, ctx.now, hid, rtype, b.get('capacity'), b.get('occupied'),
                                b.get('status', 'available'), actor=b.get('handled_by', 'hospital'),
                                expected_version=b.get('expected_version'))


@route('POST', r'/api/hospitals/(?P<hid>[\w-]+)/restrictions')
def _(ctx, hid):
    b = ctx.body
    return service.add_restriction(ctx.conn, ctx.now, hid, b.get('scope'), b.get('reason'), b.get('ends_at'),
                                   starts_at=b.get('starts_at'),
                                   actor=b.get('handled_by', 'hospital'))


@route('DELETE', r'/api/hospitals/(?P<hid>[\w-]+)/restrictions/(?P<rid>\d+)')
def _(ctx, hid, rid):
    return service.clear_restriction(ctx.conn, ctx.now, hid, int(rid))


@route('GET', r'/api/hospitals/(?P<hid>[\w-]+)/inquiries')
def _(ctx, hid):
    return service.hospital_inquiries(ctx.conn, ctx.now, hid)


@route('POST', r'/api/inquiries/(?P<iid>\d+)/respond')
def _(ctx, iid):
    b = ctx.body
    return service.respond(ctx.conn, ctx.now, int(iid), b.get('action'), b.get('handled_by'), b.get('reason'),
                           b.get('question'), b.get('expected_version'))


@route('GET', r'/api/hospitals/(?P<hid>[\w-]+)/incoming')
def _(ctx, hid):
    observed = recommendation_view.availability(ctx.runtime)
    return dict(incoming=service.incoming(ctx.conn, ctx.now, hid),
                affected=service.affected_patients(ctx.conn, ctx.now, hid),
                planning=recommendation_view.hospital_results(ctx.conn, ctx.now, hid, observed=observed),
                planning_availability=observed)


@route('POST', r'/api/hospitals/(?P<hid>[\w-]+)/external-arrivals')
def _(ctx, hid):
    b = ctx.body
    return service.register_external_arrival(ctx.conn, ctx.now, hid, b.get('resource_demands'),
                                             b.get('occurred_at'), b.get('note'), b.get('handled_by', 'hospital'),
                                             request_id=b.get('request_id'))


@route('GET', r'/api/hospitals/(?P<hid>[\w-]+)/external-arrivals')
def _(ctx, hid):
    q = ctx.query
    return service.list_external_arrivals(ctx.conn, ctx.now, hid, q.get('state', 'ACTIVE'),
                                          q.get('limit', 100), q.get('before_id'))


@route('GET', r'/api/hospitals/(?P<hid>[\w-]+)/external-arrivals/(?P<eid>\d+)')
def _(ctx, hid, eid):
    return service.external_arrival_view(ctx.conn, ctx.now, hid, eid)


@route('POST', r'/api/hospitals/(?P<hid>[\w-]+)/external-arrivals/(?P<eid>\d+)/discharge')
def _(ctx, hid, eid):
    return service.discharge_external_arrival(ctx.conn, ctx.now, hid, eid,
                                              ctx.body.get('handled_by', 'hospital'),
                                              ctx.body.get('expected_version'))


@route('POST', r'/api/hospitals/(?P<hid>[\w-]+)/external-arrivals/(?P<eid>\d+)/reconcile-accounting')
def _(ctx, hid, eid):
    return service.reconcile_external_arrival_accounting(ctx.conn, ctx.now, hid, eid, ctx.body)


@route('POST', r'/api/hospitals/(?P<hid>[\w-]+)/patients/(?P<pid>[\w-]+)/reconcile-accounting')
def _(ctx, hid, pid):
    return service.reconcile_patient_accounting(ctx.conn, ctx.now, pid, hid, ctx.body)


@route('GET', r'/api/hospitals/(?P<hid>[\w-]+)/accounting-reconciliations')
def _(ctx, hid):
    q = ctx.query
    return accounting_reconciliation.list_records(ctx.conn, ctx.now, hid, q.get('kind', 'all'),
                                                   q.get('limit', 100), q.get('before_id'))


@route('GET', r'/api/hospitals/(?P<hid>[\w-]+)/accounting-reconciliations/(?P<rid>\d+)')
def _(ctx, hid, rid):
    return accounting_reconciliation.view(ctx.conn, ctx.now, hid, rid)


@route('POST', r'/api/hospitals/(?P<hid>[\w-]+)/patients/(?P<pid>[\w-]+)/(?P<act>decision|arrive|no-show|discharge)')
def _(ctx, hid, pid, act):
    b, actor = ctx.body, ctx.body.get('handled_by', 'hospital')
    if act == 'decision':
        return service.hospital_decision(ctx.conn, ctx.now, pid, hid, b.get('decision'), b.get('note'), actor,
                                        b.get('expected_review_version'), b.get('expected_risk_signature'))
    if act == 'arrive':
        return service.confirm_arrival(ctx.conn, ctx.now, pid, hid, actor)
    if act == 'no-show':
        return service.mark_no_show(ctx.conn, ctx.now, pid, hid, b.get('reason', '미도착'), actor)
    return service.discharge(ctx.conn, ctx.now, pid, hid, actor)


# ---- 환자 웹
@route('GET', r'/api/patients/(?P<pid>[\w-]+)/transport-review')
def _(ctx, pid):
    return dict(review=transport_review.view(ctx.conn, ctx.now, pid),
                workflow=transport_reassignment.view(ctx.conn, ctx.now, pid))


@route('POST', r'/api/patients/(?P<pid>[\w-]+)/transport-review')
def _(ctx, pid):
    service._require(type(ctx.body.get('enabled')) is bool, 'INVALID', 'enabled는 true 또는 false입니다')
    return transport_review.enable(ctx.conn, ctx.now, pid, ctx.body['enabled'], ctx.body.get('policy_name'))


@route('PATCH', r'/api/patients/(?P<pid>[\w-]+)/transport-position')
def _(ctx, pid):
    b = ctx.body
    return service.update_transport_position(ctx.conn, ctx.now, pid, b.get('lat'), b.get('lon'), b.get('measured_at'))


@route('GET', r'/api/inquiry-policies')
def _(ctx):
    return inquiry_policies.registry()


@route('POST', r'/api/patients')
def _(ctx):
    b = dict(ctx.body)
    pid = b.pop('id', None) or 'P-' + uuid.uuid4().hex
    return service.create_patient(ctx.conn, ctx.now, pid, b)


@route('GET', r'/api/patients/(?P<pid>[\w-]+)')
def _(ctx, pid):
    return service.patient_view(ctx.conn, pid, ctx.now)


@route('PATCH', r'/api/patients/(?P<pid>[\w-]+)')
def _(ctx, pid):
    return service.update_patient(ctx.conn, ctx.now, pid, ctx.body)


@route('GET', r'/api/patients/(?P<pid>[\w-]+)/candidates')
def _(ctx, pid):
    return recommendation_view.candidates(ctx.conn, ctx.now, pid, ctx.estimator)


@route('GET', r'/api/patients/(?P<pid>[\w-]+)/input-review')
def get_allocation_input_review(ctx, pid):
    _voice_query(ctx)
    return recommendation_view.patient_input_review(ctx.conn, ctx.now, pid)


@route('GET', r'/api/patients/(?P<pid>[\w-]+)/inquiry-workflow')
def _(ctx, pid):
    return inquiry_workflow.view(ctx.conn, ctx.now, pid)


@route('POST', r'/api/patients/(?P<pid>[\w-]+)/inquiry-workflow')
def _(ctx, pid):
    if 'policy_name' in ctx.body:
        inquiry_workflow._spec(ctx.body['policy_name'])
    return inquiry_workflow.start(ctx.conn, ctx.now, pid, enabled=ctx.body.get('enabled'),
                                  policy_name=ctx.body.get('policy_name'))


@route('GET', r'/api/patients/(?P<pid>[\w-]+)/inquiry-decisions')
def _(ctx, pid):
    raw = ctx.query.get('limit', '20')
    service._require(isinstance(raw, str) and raw.isascii() and raw.isdecimal(),
                     'INVALID', 'limit은 1~100 정수입니다')
    return inquiry_workflow.decisions(ctx.conn, ctx.now, pid, limit=int(raw))


@route('GET', r'/api/patients/(?P<pid>[\w-]+)/recommendation')
def _(ctx, pid):
    return recommendation_view.patient_result(ctx.conn, ctx.now, pid, ctx.runtime)


@route('POST', r'/api/patients/(?P<pid>[\w-]+)/replan')
def _(ctx, pid):
    return recommendation_view.request(ctx.conn, ctx.now, pid, ctx.runtime)


@route('POST', r'/api/patients/(?P<pid>[\w-]+)/inquiries')
def _(ctx, pid):
    return service.send_inquiries(ctx.conn, ctx.now, pid, ctx.body.get('hospital_ids'))


@route('POST', r'/api/patients/(?P<pid>[\w-]+)/inquiries/(?P<iid>\d+)/(?P<act>retry|cancel|answer)')
def _(ctx, pid, iid, act):
    if act == 'retry':
        return service.retry_inquiry(ctx.conn, ctx.now, pid, int(iid))
    if act == 'cancel':
        return service.cancel_inquiry(ctx.conn, ctx.now, pid, int(iid), ctx.body.get('reason'))
    return service.answer_question(ctx.conn, ctx.now, pid, int(iid), ctx.body.get('answer'))


@route('POST', r'/api/patients/(?P<pid>[\w-]+)/(?P<act>confirm|depart|change-destination|cancel)')
def _(ctx, pid, act):
    b = ctx.body
    if act == 'confirm':
        return service.confirm_destination(ctx.conn, ctx.now, pid, b.get('inquiry_id'))
    if act == 'depart':
        return service.depart(ctx.conn, ctx.now, pid, ctx.estimator, b.get('lat'), b.get('lon'))
    if act == 'change-destination':
        return service.change_destination(ctx.conn, ctx.now, pid, b.get('inquiry_id'), ctx.estimator,
                                          b.get('lat'), b.get('lon'))
    return service.cancel_patient(ctx.conn, ctx.now, pid, b.get('reason'))


@route('GET', r'/api/events')
def _(ctx):
    q = ctx.query
    return service.events_since(ctx.conn, int(q.get('since', 0)), q.get('patient_id'), q.get('hospital_id'))


# ---- 이송 대화 (업무 사건 + 자유메시지)
def _conversation(ctx, pid, hid=None):
    q = ctx.query
    return conversations.get_conversation(ctx.conn, ctx.now, pid, hospital_id=hid,
                                          event_cursor=q.get('event_cursor', 0),
                                          message_cursor=q.get('message_cursor', 0), limit=q.get('limit', 100))


@route('GET', r'/api/patients/(?P<pid>[\w-]+)/conversation')
def _(ctx, pid):
    return _conversation(ctx, pid)


@route('POST', r'/api/patients/(?P<pid>[\w-]+)/messages')
def _(ctx, pid):
    return conversations.send_message(ctx.conn, ctx.now, pid, ctx.body)


@route('GET', r'/api/hospitals/(?P<hid>[\w-]+)/patients/(?P<pid>[\w-]+)/conversation')
def _(ctx, hid, pid):
    return _conversation(ctx, pid, hid)


@route('POST', r'/api/hospitals/(?P<hid>[\w-]+)/patients/(?P<pid>[\w-]+)/messages')
def _(ctx, hid, pid):
    return conversations.send_message(ctx.conn, ctx.now, pid, ctx.body, hospital_id=hid)


class Ctx:
    def __init__(self, conn, now, body, query, estimator, db_path=None, runtime=None):
        self.conn, self.now, self.body, self.query, self.estimator = conn, now, body, query, estimator
        self.db_path, self.runtime = db_path, runtime


def make_handler(db_path, estimator, clock=lambda: int(time.time()), cors_origin='*', runtime=None):
    """cors_origin: 화면을 다른 주소·포트에서 개발할 때 허용할 Origin. None이면 CORS 헤더를 보내지 않는다."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):   # 경로에 환자 ID가 있으므로 접근 로그를 남기지 않는다 (O04)
            pass

        def _send(self, status, payload, ctype='application/json; charset=utf-8'):
            data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self._cors()
            self.end_headers()
            self.wfile.write(data)

        def _static(self, path):
            from urllib.parse import unquote, urlsplit
            path = unquote(path)
            for prefix in UI_ROOTS:
                if path == prefix.rstrip('/'):
                    query = urlsplit(self.path).query
                    self.send_response(301)
                    self.send_header('Location', prefix + ('?' + query if query else ''))
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
            root, rel = STATIC, path.lstrip('/')
            for prefix, ui_root in UI_ROOTS.items():
                if path.startswith(prefix):
                    root, rel = ui_root, path[len(prefix):]
                    break
            target = (root / (rel or 'index.html')).resolve()
            if root.resolve() not in target.parents or not target.is_file():
                return self._send(404, {'error': 'NOT_FOUND'})
            ctype = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
                     '.mjs': 'text/javascript; charset=utf-8',
                     '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml',
                     '.woff2': 'font/woff2'}.get(target.suffix, 'application/octet-stream')
            self._send(200, target.read_bytes(), ctype)

        def _dispatch(self, method):
            from urllib.parse import parse_qsl, urlsplit
            url = urlsplit(self.path)
            if method == 'GET' and not url.path.startswith('/api/'):
                return self._static(url.path)
            for m, pattern, fn in ROUTES:
                match = pattern.match(url.path)
                if m == method and match:
                    break
            else:
                return self._send(404, {'error': 'NOT_FOUND', 'message': '없는 경로'})
            try:
                if fn in VOICE_HANDLERS:
                    raw_length = self.headers.get('Content-Length', '0')
                    service._require(raw_length.isascii() and raw_length.isdecimal() and len(raw_length) <= 10,
                                     'INVALID', '올바른 Content-Length가 필요합니다')
                    service._require(not self.headers.get('Transfer-Encoding'), 'INVALID',
                                     '음성 API는 Content-Length가 있는 JSON 요청을 사용하세요')
                    service._require(int(raw_length) <= voice_input.MAX_HTTP_BYTES, 'PAYLOAD_TOO_LARGE',
                                     '음성 API 요청 크기를 초과했습니다', 413)
                length = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(length) or b'{}') if length else {}
                if not isinstance(body, dict):
                    raise service.ServiceError('INVALID', 'JSON 객체가 필요합니다', 400)
                # 빈 작업자·기간 필터를 생략으로 바꿔 집계 범위를 넓히지 않는다.
                # 기존 API의 쿼리 계약은 유지하고 신규 계측 경로에서만 엄격히 검사한다.
                strict_query = fn in MEASUREMENT_HANDLERS or fn in VOICE_HANDLERS or fn is get_allocation_input_review
                pairs = parse_qsl(url.query, keep_blank_values=strict_query)
                if strict_query:
                    service._require(len({key for key, value in pairs}) == len(pairs),
                                     'INVALID', '중복된 쿼리 항목은 사용할 수 없습니다')
                # 상태 조회는 자체 읽기 전용 연결을 쓴다. db.connect는 파일 생성·WAL 설정을 수행한다.
                conn = None if fn is get_system_status else db.connect(db_path)
                try:
                    result = fn(Ctx(conn, clock(), body, dict(pairs), estimator,
                                    db_path=db_path, runtime=runtime), **match.groupdict())
                finally:
                    if conn is not None:
                        conn.close()
                status = 503 if fn is get_system_status and result['database']['state'] == 'UNAVAILABLE' else 200
                self._send(status, result)
            except service.ServiceError as e:
                self._send(e.status, e.to_dict())
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._send(400, {'error': 'INVALID_JSON', 'message': 'UTF-8 JSON 형식 오류'})
            except Exception:
                traceback.print_exc()
                self._send(500, {'error': 'SERVER_ERROR', 'message': '서버 오류'})

        def do_GET(self):
            self._dispatch('GET')

        def do_POST(self):
            self._dispatch('POST')

        def do_PUT(self):
            self._dispatch('PUT')

        def do_PATCH(self):
            self._dispatch('PATCH')

        def do_DELETE(self):
            self._dispatch('DELETE')

        def do_OPTIONS(self):   # 브라우저 사전 요청(preflight)
            self.send_response(204)
            self._cors()
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, PATCH, DELETE, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.send_header('Access-Control-Max-Age', '600')
            self.send_header('Content-Length', '0')
            self.end_headers()

        def _cors(self):
            if cors_origin:
                self.send_header('Access-Control-Allow-Origin', cors_origin)

    return Handler


def main():
    ap = argparse.ArgumentParser(description='응급환자 병원 연결 시연 서버')
    ap.add_argument('--db', default='data/demo.db')
    ap.add_argument('--port', type=int, default=8000)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--seed', action='store_true', help='가상 병원 10곳을 넣는다 (기존 DB 덮어씀)')
    ap.add_argument('--scenario', choices=['inquiry', 'accepted', 'in_transit', 'sudden'],
                    help='--seed 후 시연 시나리오를 이 단계까지 만든다 (server/scenario.py)')
    ap.add_argument('--cors-origin', default='*', help="허용할 화면 Origin. 'none'이면 끔")
    ap.add_argument('--no-planner', action='store_true', help='배정 워커를 끈다 (UI 단독 개발용)')
    ap.add_argument('--no-inquiry-worker', action='store_true', help='초기 자동 문의 워커를 끈다')
    ap.add_argument('--solver-seconds', type=float, default=3, help='솔버 프로세스 제한시간; 0은 순차 배정만')
    args = ap.parse_args()
    if not 0 <= args.solver_seconds <= 30:
        ap.error('--solver-seconds는 0~30초 사이여야 합니다')
    if args.scenario and not args.seed:
        ap.error('--scenario는 --seed와 함께 쓰세요')
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    if args.seed:
        for suffix in ('', '-wal', '-shm'):
            Path(args.db + suffix).unlink(missing_ok=True)
    conn = db.connect(args.db)
    db.init(conn)
    if args.seed:
        synthetic.seed_hospitals(conn, int(time.time()))
    if args.scenario:
        from . import scenario
        scenario.build(conn, int(time.time()), args.scenario)
    conn.close()
    estimator = TravelEstimator()
    from .worker import Worker
    from .inquiry_worker import InquiryWorker
    worker = None if args.no_planner else Worker(args.db, estimator, args.solver_seconds)
    inquiry_worker = None if args.no_inquiry_worker else InquiryWorker(args.db, estimator)
    runtime = system_status.RuntimeStatus()
    runtime.configure('planning', worker, enabled=worker is not None)
    runtime.configure('inquiry', inquiry_worker, enabled=inquiry_worker is not None)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(
        args.db, estimator, cors_origin=None if args.cors_origin == 'none' else args.cors_origin, runtime=runtime))
    if worker:
        worker.start()
    if inquiry_worker:
        inquiry_worker.start()
    print(f'http://{args.host}:{args.port}  (DB: {args.db})')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if worker:
            worker.stop()
        if inquiry_worker:
            inquiry_worker.stop()
        server.server_close()


if __name__ == '__main__':
    main()
