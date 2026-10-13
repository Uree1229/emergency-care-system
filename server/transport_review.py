"""확정된 이송의 수용 조건 재확인. 병원의 판단 없이 목적지는 바꾸지 않는다.

현재 자원 총량으로 모든 확정 이송의 수요를 유지할 수 있는지만 확인한다.
새 환자용 여유가 0이라는 이유로 기존 환자를 재배정하지 않으며, 부족한 자원을
쓰는 환자만 잠재적 영향 대상으로 표시한다. 특정 환자의 실제 치료 불가능이나
임상 우선순위를 판정하는 모듈은 아니다.
"""
import hashlib
import json

from . import db, service, inquiry_policies

STATES = {'MONITORING', 'RECHECK_REQUIRED', 'SEARCHING', 'AWAITING_CHANGE',
          'EXHAUSTED', 'PAUSED', 'COMPLETED', 'ERROR'}


def _review(conn, pid):
    return db.row(conn.execute('SELECT * FROM transport_reviews WHERE patient_id=?', (pid,)).fetchone())


def _policy(name):
    try:
        return inquiry_policies.spec(name)
    except ValueError as error:
        raise service.ServiceError('INVALID_POLICY', str(error), 400) from None


def _requirements(patient):
    return dict(required_capabilities=sorted(set(patient['required_capabilities'])),
                resource_demands={k: v for k, v in sorted(patient['resource_demands'].items())})


def _snapshot(conn, now, patient):
    """개인정보 없이 이 환자의 필수조건에 해당하는 정보만 기록한다."""
    hid = patient['destination_hospital_id']
    hospital = service._hospital(conn, hid)
    requirements = _requirements(patient)
    capabilities = requirements['required_capabilities']
    committed = service.expected_demand(conn, hid)
    current = {r['resource_type']: r for r in db.rows(conn.execute(
        'SELECT * FROM resources WHERE hospital_id=?', (hid,)))}
    resources = {}
    for rtype in requirements['resource_demands']:
        resource = current.get(rtype)
        if resource is None:
            resources[rtype] = None
            continue
        resources[rtype] = {key: resource[key] for key in
                            ('capacity', 'occupied', 'status', 'updated_at', 'version')}
        resources[rtype].update(committed=committed.get(rtype, 0),
                                stale=now - resource['updated_at'] > service.STALE_AFTER)
    relevant_restrictions = []
    for restriction in service.restrictions(conn, hid, now):
        if restriction['scope'] != 'all' and restriction['scope'] not in capabilities:
            continue
        relevant_restrictions.append({key: restriction[key] for key in
                                      ('id', 'scope', 'reason', 'starts_at', 'ends_at', 'status')})
    return dict(hospital_id=hid, captured_at=now, requirements=requirements,
                capability_status=hospital.get('capabilities_status', 'known'),
                matched_capabilities=sorted(set(capabilities) & set(hospital['capabilities'])),
                resources=resources, restrictions=relevant_restrictions)


def _unknown_resource_condition(resource):
    if resource is None:
        return None
    # 확정 수요 증감만으로 이미 미확인인 정보가 새로 바뀌었다고 판단하지 않는다.
    return {key: resource[key] for key in ('capacity', 'occupied', 'status', 'updated_at', 'version')}


def _evaluate(snapshot, baseline):
    """부족은 재확인 사유다. 어느 환자를 돌려보낼지는 결정하지 않는다."""
    issues = []
    requirements = snapshot['requirements']
    if baseline.get('requirements') != requirements:
        issues.append(dict(kind='requirements_changed'))
    caps = requirements['required_capabilities']
    missing = sorted(set(caps) - set(snapshot['matched_capabilities']))
    if snapshot['capability_status'] == 'known':
        if missing:
            issues.append(dict(kind='capability_missing', capabilities=missing))
    elif caps and (snapshot['capability_status'] != baseline.get('capability_status') or
                   snapshot['matched_capabilities'] != baseline.get('matched_capabilities')):
        issues.append(dict(kind='capability_unknown', capabilities=caps,
                           status=snapshot['capability_status']))
    for rtype, resource in snapshot['resources'].items():
        previous = baseline.get('resources', {}).get(rtype)
        if resource is None or resource['status'] == 'unknown':
            if _unknown_resource_condition(resource) != _unknown_resource_condition(previous):
                issues.append(dict(kind='resource_unknown', resource_type=rtype,
                                   condition=_unknown_resource_condition(resource)))
            continue
        available = resource['capacity'] - resource['occupied']
        if available < resource['committed']:
            issues.append(dict(kind='resource_shortfall', resource_type=rtype,
                               capacity=resource['capacity'], occupied=resource['occupied'],
                               committed=resource['committed'],
                               shortfall=resource['committed'] - available))
        if resource['stale'] and (previous is None or not previous.get('stale') or
                                  previous['version'] != resource['version'] or
                                  previous['updated_at'] != resource['updated_at']):
            issues.append(dict(kind='resource_stale', resource_type=rtype,
                               version=resource['version'], updated_at=resource['updated_at']))
    for restriction in snapshot['restrictions']:
        if restriction['status'] in ('active', 'recheck'):
            issues.append(dict(kind='restriction', **restriction))
    if not issues:
        return None, dict(issues=[], snapshot=snapshot)
    payload = dict(requirements=requirements, issues=issues)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest(), dict(issues=issues, snapshot=snapshot)


def _stop_alternatives(conn, now, pid, reason):
    from . import transport_reassignment
    transport_reassignment.stop_in_transaction(conn, now, pid, reason=reason)


def capture_baseline_in_transaction(conn, now, pid):
    """확정 당시 조건은 항상 저장하되, 감시는 명시적으로 켠 건만 갱신한다."""
    patient = service._patient(conn, pid)
    service._require(patient['state'] in service.ACTIVE_TRANSPORT and patient['destination_hospital_id'],
                     'INVALID_STATE', '목적지가 확정된 이송 건만 조건을 저장합니다', 409)
    baseline = _snapshot(conn, now, patient)
    # 수용의 근거를 보존하는 메타데이터다. 환자 임상 입력/수용 문의 버전은 바꾸지 않는다.
    conn.execute('UPDATE patients SET transport_baseline=? WHERE id=?', (db.dumps(baseline), pid))
    review = _review(conn, pid)
    if not review or not review['enabled']:
        return review
    return _reset_review_baseline(conn, now, pid, patient, baseline)


def _reset_review_baseline(conn, now, pid, patient, baseline):
    _stop_alternatives(conn, now, pid, '목적지가 확정되어 대체병원 탐색을 종료했습니다.')
    conn.execute("UPDATE transport_reviews SET state='MONITORING',hospital_id=?,baseline=?,"
                 'risk_signature=NULL,acknowledged_signature=NULL,evidence=?,last_checked_at=?,updated_at=?,'
                 'version=version+1,last_error=NULL WHERE patient_id=?',
                 (patient['destination_hospital_id'], db.dumps(baseline), db.dumps(dict(issues=[], snapshot=baseline)),
                  now, now, pid))
    service.log(conn, now, 'system', 'transport_baseline_captured', pid, patient['destination_hospital_id'],
                requirements=baseline['requirements'])
    return _review(conn, pid)


def _saved_baseline(conn, now, patient):
    saved = patient.get('transport_baseline')
    if isinstance(saved, dict) and saved and saved.get('hospital_id') == patient['destination_hospital_id']:
        return saved
    # 기존 DB에는 실제 확정 시점의 관측을 복원할 수 없다. 첫 설정 시 관측으로 시작한다.
    return _snapshot(conn, now, patient)


def _scan_one(conn, now, patient, review):
    if not review['enabled'] or patient['state'] not in service.ACTIVE_TRANSPORT:
        return False
    if patient['destination_hospital_id'] != review['hospital_id']:
        capture_baseline_in_transaction(conn, now, patient['id'])
        review = _review(conn, patient['id'])
    snapshot = _snapshot(conn, now, patient)
    signature, evidence = _evaluate(snapshot, review['baseline'])
    state = review['state']
    active_search = state in ('SEARCHING', 'AWAITING_CHANGE', 'EXHAUSTED')
    if not active_search:
        state = 'RECHECK_REQUIRED' if signature and signature != review['acknowledged_signature'] else 'MONITORING'
    changed = signature != review['risk_signature'] or state != review['state']
    # 같은 조건을 반복 확인해도 병원 판단용 버전은 바뀌지 않는다.
    conn.execute('UPDATE transport_reviews SET state=?,risk_signature=?,evidence=?,last_checked_at=?,'
                 'updated_at=CASE WHEN ? THEN ? ELSE updated_at END,version=version+?,'
                 'acknowledged_signature=CASE WHEN ? IS NULL THEN NULL ELSE acknowledged_signature END '
                 'WHERE patient_id=?',
                 (state, signature, db.dumps(evidence), now, int(changed), now, int(changed),
                  signature, patient['id']))
    if changed and state == 'RECHECK_REQUIRED':
        service.log(conn, now, 'system', 'transport_recheck_required', patient['id'], review['hospital_id'],
                    risk_signature=signature, issues=evidence['issues'])
    elif changed and not signature and not active_search:
        service.log(conn, now, 'system', 'transport_conditions_restored', patient['id'], review['hospital_id'])
    return changed


def scan_in_transaction(conn, now, hid=None):
    """자원 변경 및 주기 워커에서 호출한다. 조정 필요/목적지는 건드리지 않는다."""
    sql = "SELECT p.* FROM patients p JOIN transport_reviews t ON p.id=t.patient_id " \
          "WHERE t.enabled=1 AND p.state IN ('CONFIRMED','IN_TRANSIT')"
    args = ()
    if hid is not None:
        sql += ' AND p.destination_hospital_id=?'
        args = (hid,)
    changed = []
    for patient in db.rows(conn.execute(sql + ' ORDER BY p.id', args)):
        if _scan_one(conn, now, patient, _review(conn, patient['id'])):
            changed.append(_review(conn, patient['id']))
    return changed


def enable(conn, now, pid, enabled=True, policy_name=None):
    """확정된 이송 건에서 사용자가 명시적으로 조건 감시를 켠다."""
    service._require(type(enabled) is bool, 'INVALID', 'enabled는 true 또는 false입니다')
    selected = _policy(policy_name) if policy_name is not None else None
    with db.transaction(conn):
        patient = service._patient(conn, pid)
        review = _review(conn, pid)
        service._require(patient['state'] in service.ACTIVE_TRANSPORT, 'INVALID_STATE',
                         '확정 또는 이동 중인 이송 건만 조건 감시를 설정합니다', 409)
        if selected is not None and review and selected['name'] != review['policy_name']:
            service._require(not review['enabled'], 'POLICY_ACTIVE',
                             '감시 중인 정책은 바꿀 수 없습니다. 먼저 일시정지하세요.', 409)
        policy = selected or _policy(review['policy_name'] if review else 'fixed_3')
        if review and bool(review['enabled']) == enabled and policy['name'] == review['policy_name']:
            if enabled:
                _scan_one(conn, now, patient, review)
            return view(conn, now, pid)
        if review is None:
            baseline = _saved_baseline(conn, now, patient)
            conn.execute('INSERT INTO transport_reviews(patient_id,enabled,state,hospital_id,policy_name,'
                         'baseline,evidence,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                         (pid, int(enabled), 'MONITORING' if enabled else 'PAUSED',
                          patient['destination_hospital_id'], policy['name'], db.dumps(baseline), '{}', now))
            if enabled:
                _reset_review_baseline(conn, now, pid, patient, baseline)
        elif enabled:
            conn.execute("UPDATE transport_reviews SET enabled=1,state='MONITORING',policy_name=?,"
                         'updated_at=?,version=version+1,last_error=NULL WHERE patient_id=?',
                         (policy['name'], now, pid))
            if not review['baseline'] or review['hospital_id'] != patient['destination_hospital_id']:
                _reset_review_baseline(conn, now, pid, patient, _saved_baseline(conn, now, patient))
        else:
            _stop_alternatives(conn, now, pid, '사용자가 이송 조건 감시를 일시정지했습니다.')
            conn.execute("UPDATE transport_reviews SET enabled=0,state='PAUSED',policy_name=?,updated_at=?,"
                         'version=version+1 WHERE patient_id=?', (policy['name'], now, pid))
        service.log(conn, now, 'system', 'transport_monitor_started' if enabled else 'transport_monitor_paused',
                    pid, patient['destination_hospital_id'], policy_name=policy['name'])
        if enabled:
            current = _review(conn, pid)
            if patient['needs_adjustment']:
                conn.execute("UPDATE transport_reviews SET state='SEARCHING' WHERE patient_id=?", (pid,))
                from . import transport_reassignment
                transport_reassignment.start_in_transaction(conn, now, pid, policy_name=policy['name'])
            else:
                _scan_one(conn, now, patient, current)
    return view(conn, now, pid)


def finish_in_transaction(conn, now, pid, reason):
    review = _review(conn, pid)
    if not review:
        return None
    _stop_alternatives(conn, now, pid, reason)
    if review['enabled'] or review['state'] != 'COMPLETED':
        conn.execute("UPDATE transport_reviews SET enabled=0,state='COMPLETED',updated_at=?,"
                     'version=version+1 WHERE patient_id=?', (now, pid))
        service.log(conn, now, 'system', 'transport_monitor_completed', pid, review['hospital_id'], reason=reason)
    return _review(conn, pid)


def decision_in_transaction(conn, now, pid, hid, decision, note=None,
                            expected_version=None, expected_signature=None):
    """기존 병원 판단과 같은 트랜잭션에서 실행한다. 충돌이면 그 판단도 롤백된다."""
    review = _review(conn, pid)
    if not review or not review['enabled']:
        return review
    patient = service._patient(conn, pid)
    service._require(patient['state'] in service.ACTIVE_TRANSPORT and patient['destination_hospital_id'] == hid,
                     'INVALID_STATE', '현재 목적지 병원만 이송 조건을 판단합니다', 409)
    service._require(decision in ('keep', 'adjust'), 'INVALID', 'decision은 keep 또는 adjust')
    _scan_one(conn, now, patient, review)
    review = _review(conn, pid)
    service._require(type(expected_version) is int and expected_version == review['version'] and
                     expected_signature == review['risk_signature'],
                     'TRANSPORT_REVIEW_CONFLICT',
                     '병원 조건이나 이송 진행 상황이 바뀌었습니다. 새로고침 후 다시 판단하세요.', 409,
                     review_version=review['version'], risk_signature=review['risk_signature'])
    if decision == 'keep':
        _stop_alternatives(conn, now, pid, '병원이 기존 환자의 수용을 유지한다고 확인했습니다.')
        conn.execute("UPDATE transport_reviews SET state='MONITORING',acknowledged_signature=risk_signature,"
                     'updated_at=?,version=version+1,last_error=NULL WHERE patient_id=?', (now, pid))
        service.log(conn, now, 'system', 'transport_recheck_acknowledged', pid, hid,
                    risk_signature=review['risk_signature'], decision=decision)
    else:
        conn.execute("UPDATE transport_reviews SET state='SEARCHING',acknowledged_signature=risk_signature,"
                     'updated_at=?,version=version+1,last_error=NULL WHERE patient_id=?', (now, pid))
        from . import transport_reassignment
        transport_reassignment.start_in_transaction(conn, now, pid, policy_name=review['policy_name'])
        service.log(conn, now, 'system', 'transport_alternative_requested', pid, hid,
                    risk_signature=review['risk_signature'], decision=decision)
    return _review(conn, pid)


def view(conn, now, pid):
    """조회는 병원에 문의하거나 감시 상태를 바꾸지 않는다."""
    patient = service._patient(conn, pid)
    review = _review(conn, pid)
    if not review:
        return dict(patient_id=pid, enabled=False, state='NOT_STARTED',
                    hospital_id=patient['destination_hospital_id'],
                    policy=_policy('fixed_3'), baseline={}, evidence=dict(issues=[]),
                    risk_signature=None, acknowledged_signature=None)
    out = dict(review)
    out['enabled'] = bool(out['enabled'])
    out['policy'] = _policy(out['policy_name'])
    out['recheck_required'] = out['state'] == 'RECHECK_REQUIRED'
    return out
