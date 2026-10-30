"""Explicit treatment-condition review for an existing WAITING voice patient.

Preview is read-only. Voice observations never silently replace requirements;
the operator approves a version-bound proposal before its atomic application.
"""
import copy
import hashlib
import json

from . import service, treatment_binding, voice_r1, planning, recommendation_view

REVISION = 'voice-treatment-review-20261030-v1'
PROFILE_FIELDS = ('chief_complaint', 'age_category',
                  'stemi_suspected', 'injury_region', 'labor', 'pregnancy_weeks')


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def _selection(value, *, suggestion_available):
    if value is None:
        return {'mode':'suggested' if suggestion_available else 'keep'}
    service._require(isinstance(value,dict) and value.get('mode') in ('suggested','keep','custom'),
                     'INVALID_TREATMENT_SELECTION','selection.mode는 suggested/keep/custom입니다')
    mode=value['mode']
    allowed={'mode'} if mode!='custom' else {'mode','treatment_profile_id','required_capabilities','resource_demands'}
    service._require(not(value.keys()-allowed),'INVALID_TREATMENT_SELECTION','지원하지 않는 선택 항목입니다')
    if mode=='suggested':
        service._require(suggestion_available,'TREATMENT_SUGGESTION_UNAVAILABLE',
                         '현재 변경에 해당하는 치료조건 제안이 없습니다. 기존 조건을 유지하거나 직접 지정하세요',409)
    result=copy.deepcopy(value)
    if mode=='custom':
        service._require(len(value)>1,'INVALID_TREATMENT_SELECTION','직접 지정할 치료조건이 필요합니다')
        if value.get('treatment_profile_id') is not None:
            try:treatment_binding.profile(value['treatment_profile_id'])
            except ValueError as error:raise service.ServiceError('INVALID_TREATMENT_SELECTION',str(error),400) from None
        if 'required_capabilities' in value:
            caps=value['required_capabilities']
            service._require(isinstance(caps,list) and len(caps)<=50 and all(
                isinstance(item,str) and item.strip() and len(item)<=80 and not any(ord(ch)<32 for ch in item)
                for item in caps),'INVALID_TREATMENT_SELECTION','진료 분야는 유효한 이름 목록입니다')
            result['required_capabilities']=list(dict.fromkeys(caps))
        if 'resource_demands' in value:
            demands=value['resource_demands']
            service._require(isinstance(demands,dict) and demands and len(demands)<=30 and all(
                isinstance(key,str) and key.strip() and len(key)<=80 and not any(ord(ch)<32 for ch in key)
                and type(qty) is int and 0<qty<=service.MAX_EPOCH for key,qty in demands.items()),
                'INVALID_TREATMENT_SELECTION','필요 자원은 {자원 이름: 양의 정수}입니다')
    return result


def _conditions(patient):
    bound=treatment_binding.requirements_for(patient)
    return dict(treatment_profile_id=bound['profile_id'],
        required_capabilities=sorted(bound['required_capabilities']),resource_demands=bound['resource_demands'],
        explicit_required_capabilities=(sorted(patient['explicit_required_capabilities'])
            if patient.get('explicit_required_capabilities') is not None else None),
        explicit_resource_demands=copy.deepcopy(patient.get('explicit_resource_demands')),
        priority=patient.get('priority'),priority_source=patient.get('priority_source'))


def _build(conn,now,sid,actor,selection=None):
    from . import voice_input as voice
    row=voice._row(conn,sid,voice._actor(actor),now)
    service._require(row['patient_id'] is not None,'VOICE_PATIENT_REQUIRED',
                     '기존 환자에 연결된 음성 세션이 필요합니다')
    patient=service._patient(conn,row['patient_id'])
    fields=json.loads(row['fields']);metadata=json.loads(row['field_metadata'])
    first=conn.execute('SELECT before_fields FROM voice_revisions WHERE session_id=? ORDER BY id LIMIT 1',(sid,)).fetchone()
    original=json.loads(first['before_fields']) if first else fields
    source_changes=[dict(field=key,previous=original.get(key),current=fields.get(key),
                         removed=metadata.get(key,{}).get('removed') is True,
                         needs_clarification=metadata.get(key,{}).get('needs_clarification') is True)
                    for key in PROFILE_FIELDS if original.get(key)!=fields.get(key) or
                    ((key in original)!=(key in fields))]
    uncertain=[key for key in PROFILE_FIELDS if metadata.get(key,{}).get('needs_clarification')]
    proposed_profile=voice_r1.profile_for(fields) if source_changes and not uncertain else None
    current=_conditions(patient)
    suggestion_available=proposed_profile is not None and proposed_profile!=current['treatment_profile_id']
    choice=_selection(selection,suggestion_available=suggestion_available)
    override={'treatment_profile_id':current['treatment_profile_id']}
    if choice['mode']=='suggested':override['treatment_profile_id']=proposed_profile
    elif choice['mode']=='custom':
        override.update({key:copy.deepcopy(value) for key,value in choice.items() if key!='mode'})
    candidate=copy.deepcopy(patient)
    candidate.update(voice._patient_patch(row,now,patient))
    if override['treatment_profile_id'] is None:candidate['extra'].pop('treatment_profile_id',None)
    else:candidate['extra']['treatment_profile_id']=override['treatment_profile_id']
    for key in ('required_capabilities','resource_demands'):
        if key in override:
            candidate[key]=copy.deepcopy(override[key]);candidate['explicit_'+key]=copy.deepcopy(override[key])
    proposed=_conditions(candidate)
    changes=[dict(field=key,previous=current[key],current=proposed[key]) for key in current if current[key]!=proposed[key]]
    removed_caps=sorted(set(current['required_capabilities'])-set(proposed['required_capabilities']))
    reduced={key:dict(previous=qty,current=proposed['resource_demands'].get(key,0))
             for key,qty in current['resource_demands'].items() if proposed['resource_demands'].get(key,0)<qty}
    reductions=[]
    if removed_caps:reductions.append(dict(field='required_capabilities',removed=removed_caps))
    if reduced:reductions.append(dict(field='resource_demands',reduced=reduced))
    if (patient.get('priority_source')=='expert' and patient.get('priority') is not None and
            candidate.get('priority_source')=='expert' and candidate.get('priority') is not None and
            candidate['priority']<patient['priority']):
        reductions.append(dict(field='priority',previous=patient['priority'],current=candidate['priority']))
    blockers=[]
    if row['state']!='DRAFT':blockers.append('VOICE_SESSION_SUBMITTED')
    if patient['state']!='WAITING':blockers.append('INVALID_STATE')
    if patient['version']!=row['base_patient_version']:blockers.append('VOICE_PATIENT_VERSION_CONFLICT')
    if row['confirmed_version']!=row['version']:blockers.append('VOICE_CONFIRMATION_REQUIRED')
    warnings=[]
    if uncertain:warnings.append('TREATMENT_SOURCE_NEEDS_CLARIFICATION')
    if patient.get('explicit_required_capabilities') is None or patient.get('explicit_resource_demands') is None:
        warnings.append('LEGACY_EXPLICIT_CONDITIONS_REQUIRE_REVIEW')
    from .solver_adapter import RESOURCE_TYPES
    unsupported=sorted(set(proposed['resource_demands'])-set(RESOURCE_TYPES))
    if unsupported:warnings.append('UNSUPPORTED_SOLVER_RESOURCE')
    token_data=dict(revision=REVISION,actor_id=actor,session_id=sid,session_version=row['version'],
        patient_id=patient['id'],patient_version=patient['version'],patient_state=patient['state'],
        source_changes=source_changes,fields=fields,metadata=voice._review_status(metadata),
        current=current,proposed=proposed,selection=choice,
        catalog_sha256=hashlib.sha256(_json(treatment_binding.catalog()).encode()).hexdigest())
    token=hashlib.sha256(_json(token_data).encode()).hexdigest()
    review=dict(revision=REVISION,token=token,session_id=sid,session_version=row['version'],
        patient_id=patient['id'],patient_version=patient['version'],patient_state=patient['state'],
        selection=choice,current=current,proposed=proposed,changes=changes,source_changes=source_changes,
        review_required=bool(changes or source_changes),suggestion_available=suggestion_available,
        reductions=reductions,requires_reduction_confirmation=bool(reductions),
        blockers=blockers,can_apply=not blockers,warnings=warnings,
        source_needs_clarification=uncertain,unsupported_solver_resources=unsupported,
        automatic_diagnosis=False,automatic_destination_change=False,
        catalog_sha256=token_data['catalog_sha256'])
    return row,patient,review,override


def preview(conn,now,sid,actor,selection=None):
    from . import voice_input as voice
    voice._now(now)
    row,_,review,_=_build(conn,now,sid,actor,selection)
    return {'review':review,'session':voice.get(conn,now,row['id'],actor)['session']}


def detection(conn,now,row):
    """Small hint on every linked draft response; no preview or patient writes."""
    _,_,review,_=_build(conn,now,row['id'],row['actor_id'])
    return dict(review_required=review['review_required'],suggestion_available=review['suggestion_available'],
        suggested_profile_id=review['proposed']['treatment_profile_id'] if review['suggestion_available'] else None,
        source_changed_fields=[item['field'] for item in review['source_changes']],
        endpoint=f'/api/voice/sessions/{row["id"]}/treatment-review',
        can_apply=review['can_apply'],blockers=review['blockers'])


def preview_request(conn,now,sid,body):
    from . import voice_input as voice
    voice._shape(body,voice.COMMON,{'selection','expected_patient_version'})
    voice._now(now)
    voice._identifier(body['client_request_id'],'client_request_id')
    row=voice._row(conn,sid,voice._actor(body['actor_id']),now)
    voice._mutable(row,body['expected_version'])
    result=preview(conn,now,sid,body['actor_id'],body.get('selection'))
    if 'expected_patient_version' in body:
        service._require(type(body['expected_patient_version']) is int and
            body['expected_patient_version']==result['review']['patient_version'],
            'VOICE_PATIENT_VERSION_CONFLICT','환자정보가 변경됐습니다. 최신 정보로 다시 확인하세요',409)
    return result


def apply(conn,now,sid,body,runtime=None):
    from . import voice_input as voice
    voice._shape(body,voice.COMMON|{'expected_patient_version','review_token','confirmed','selection'},
                 {'reason','allow_reduction'})
    service._require(body['confirmed'] is True,'TREATMENT_CONFIRMATION_REQUIRED',
                     '치료조건을 명시적으로 확인해야 합니다')
    service._require(type(body.get('allow_reduction',False)) is bool,'INVALID','allow_reduction은 불리언입니다')
    service._require(type(body['expected_patient_version']) is int and 0<body['expected_patient_version']<=service.MAX_EPOCH,
                     'INVALID','expected_patient_version은 양의 정수입니다')
    service._require(isinstance(body['review_token'],str) and len(body['review_token'])==64,
                     'INVALID','review_token이 필요합니다')
    reason=body.get('reason','구급대원이 치료조건 변경안을 확인함')
    service._require(isinstance(reason,str) and 0<len(reason.strip())<=500 and not any(ord(ch)<32 for ch in reason),
                     'INVALID','reason은 500자 이내의 설명입니다')
    def action(actor):
        row=voice._row(conn,sid,actor,now);voice._mutable(row,body['expected_version'])
        patient=service._patient(conn,row['patient_id']) if row['patient_id'] else None
        service._require(patient is not None,'VOICE_PATIENT_REQUIRED','기존 환자에 연결된 음성 세션이 필요합니다')
        service._require(patient['version']==body['expected_patient_version']==row['base_patient_version'],
                         'VOICE_PATIENT_VERSION_CONFLICT','환자정보가 변경됐습니다. 최신 정보로 다시 확인하세요',409,
                         current_patient_version=patient['version'])
        service._require(patient['state']=='WAITING','INVALID_STATE','목적지 확정 전 치료조건만 변경할 수 있습니다',409)
        service._require(row['confirmed_version']==row['version'],'VOICE_CONFIRMATION_REQUIRED',
                         '현재 음성 초안을 먼저 확인하세요',409)
        _,_,review,override=_build(conn,now,sid,actor,body['selection'])
        service._require(review['token']==body['review_token'],'TREATMENT_REVIEW_STALE',
                         '치료조건 제안이 변경됐습니다. 변경안을 다시 확인하세요',409)
        service._require(not review['reductions'] or body.get('allow_reduction') is True,
                         'TREATMENT_REDUCTION_CONFIRMATION_REQUIRED','필요조건 감소를 별도로 확인하세요',409,
                         reductions=review['reductions'])
        before=conn.execute('SELECT revision FROM planning_state WHERE id=1').fetchone()[0]
        key='treatment-'+hashlib.sha256((sid+'|'+body['client_request_id']).encode()).hexdigest()
        result=voice.submit(conn,now,sid,dict(actor_id=actor,client_request_id=key,
            expected_version=row['version']),reviewed_conditions=override)
        after=conn.execute('SELECT revision FROM planning_state WHERE id=1').fetchone()[0]
        service.log(conn,now,actor,'treatment_conditions_reviewed',patient['id'],
            voice_session_id=sid,voice_version=row['version'],review_token=review['token'],
            decision=review['selection']['mode'],changed_conditions=review['changes'],
            source_changes=review['source_changes'],reason=reason,
            from_patient_version=patient['version'],to_patient_version=result['patient']['version'])
        result['review']={**review,'applied':True,'can_apply':False,'blockers':['VOICE_SESSION_SUBMITTED'],
                          'applied_patient_version':result['patient']['version']}
        result['reevaluation']=dict(requested=after>before,status=planning.patient_result(conn,now,patient['id'])['status'],
            revision=after,availability=recommendation_view.availability(runtime),
            reason='PATIENT_INPUT_CHANGED' if after>before else 'NO_CHANGE',
            automatic_destination_change=False)
        return result
    return voice._operate(conn,now,'treatment_review_apply:'+sid,body,action)
