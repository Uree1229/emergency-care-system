"""공통 DB 스냅샷을 분 단위 배정 모델로 변환한다. 임상 판단을 수행하지 않는다."""
import math

from . import service, treatment_binding

RESOURCE_TYPES = ('ed_bed', 'special_team')
HORIZON_MINUTES = 120
DEFAULT_DURATION = 1800
DEFAULT_MAX_WAIT = 3600


def minute(epoch):
    return math.ceil(epoch / 60)


def build(snapshot, estimator):
    now = snapshot['now']
    hospitals = snapshot['hospitals']
    index = {h['id']: i for i, h in enumerate(hospitals)}
    resources = {(r['hospital_id'], r['resource_type']): r for r in snapshot['resources']}
    public_observations = {h['id']:treatment_binding.observation_map(
        snapshot.get('public_snapshots', []), now, h['id']) for h in hospitals}
    model = dict(now=minute(now), horizon=minute(now) + HORIZON_MINUTES,
                 version=snapshot['revision'], capacities=[], blocks=[], patients=[], destination_locks={})
    context = {}
    for h in hospitals:
        pair = []
        for rtype in RESOURCE_TYPES:
            r = resources.get((h['id'], rtype))
            known = r is not None and r['status'] != 'unknown' and now - r['updated_at'] <= service.STALE_AFTER
            # 만료·미확인 자원을 가용한 자원으로 추정하지 않는다.
            pair.append(r['capacity'] if known else 0)
            if known and r['occupied']:
                demands = [0, 0]
                demands[RESOURCE_TYPES.index(rtype)] = r['occupied']
                model['blocks'].append(dict(h=index[h['id']], start=model['now'],
                                            duration=HORIZON_MINUTES + 1440, special=rtype == 'special_team',
                                            demands=demands))
        model['capacities'].append(pair)
    for raw_patient in snapshot['patients']:
        p = treatment_binding.bind_patient(raw_patient)
        pid = p['id']
        locked = p['state'] in service.ACTIVE_TRANSPORT
        demand = p['resource_demands']
        supported = set(demand) <= set(RESOURCE_TYPES)
        eligible, travel, arrivals, routes, expiries, alternatives = [], [], [], {}, [], []
        warnings = []
        eligibility_reasons = []
        missing_capabilities = []
        treatment_evaluations = {}
        if not supported:
            warnings.append('UNSUPPORTED_RESOURCE')
        if p['priority_source'] != 'expert':
            warnings.append('PRIORITY_UNVERIFIED')
        for h in hospitals:
            hid, hi = h['id'], index[h['id']]
            evaluation = treatment_binding.evaluate_patient(p, public_observations[hid], now)
            treatment_evaluations[hid] = evaluation
            # 공개 판정의 unknown/오래됨은 유효한 환자별 수용 응답을 무효화하지 않는다.
            # 현재 운영 capacity는 별도로 검사하며 등록 시설 수를 대신 넣지 않는다.
            if evaluation['status'] != 'MATCH':
                warnings.append('TREATMENT_CONFIRMATION_REQUIRED:' + hid)
            if evaluation['stale']:
                warnings.append('PUBLIC_DATA_STALE:' + hid)
            estimate = None
            if locked and hid == p['destination_hospital_id'] and p['eta_at'] is not None:
                arrival = max(now, p['eta_at'])
                estimate = dict(duration_seconds=max(0, p['eta_at'] - now), provider='recorded_eta')
            elif p['lat'] is not None and p['lon'] is not None:
                estimate = estimator.estimate((p['lat'], p['lon']), (h['lat'], h['lon']))
                arrival = now + estimate['duration_seconds']
            else:
                arrival = now
            travel.append(minute(estimate['duration_seconds']) if estimate else 0)
            arrivals.append(minute(arrival))
            if estimate:
                routes[hid] = estimate
            blocked = any(r['hospital_id'] == hid and r['cleared_at'] is None and
                          (r['scope'] == 'all' or r['scope'] in p['required_capabilities']) and
                          service.restriction_status(r, now) in ('active', 'recheck', 'scheduled')
                          for r in snapshot['restrictions'])
            q = next((q for q in snapshot['inquiries'] if q['patient_id'] == pid and q['hospital_id'] == hid
                      and q['state'] == 'ACCEPTED' and q['patient_version'] == p['version']
                      and q['expires_at'] is not None and q['expires_at'] > now), None)
            if q is not None:
                codes = []
                if estimate is None:
                    codes.append('LOCATION_REQUIRED')
                if not supported:
                    codes.append('UNSUPPORTED_RESOURCE')
                if h.get('capabilities_status', 'known') != 'unknown' and not set(p['required_capabilities']) <= set(h['capabilities']):
                    codes.append('CAPABILITY_MISMATCH')
                if blocked:
                    codes.append('RESTRICTED')
                for key, need in demand.items():
                    r = resources.get((hid, key))
                    if r is None:
                        codes.append('OPERATING_RESOURCE_MISSING')
                    else:
                        if r['status'] == 'unknown':
                            codes.append('OPERATING_RESOURCE_UNKNOWN')
                        if now - r['updated_at'] > service.STALE_AFTER:
                            codes.append('OPERATING_RESOURCE_STALE')
                        if r['status'] != 'unknown' and r['capacity'] - r['occupied'] < need:
                            codes.append('RESOURCE_CAPACITY_INSUFFICIENT')
                codes = sorted(set(codes))
                eligibility_reasons.append(dict(hospital_id=hid, inquiry_id=q['id'], codes=codes))
                warnings.extend(code + ':' + hid for code in codes)
            if locked and hid == p['destination_hospital_id']:
                eligible.append(hi)
                if h.get('capabilities_status', 'known') != 'unknown':
                    missing_capabilities = sorted(set(p['required_capabilities']) - set(h['capabilities']))
                    if missing_capabilities:
                        warnings.append('DESTINATION_CAPABILITY_MISSING')
                if blocked:
                    warnings.append('DESTINATION_RESTRICTED')
                continue
            capabilities_unknown = h.get('capabilities_status', 'known') == 'unknown'
            if estimate is None or not supported or (not capabilities_unknown and
                    not set(p['required_capabilities']) <= set(h['capabilities'])):
                continue
            known = all((hid, key) in resources and resources[hid, key]['status'] != 'unknown' and
                        now - resources[hid, key]['updated_at'] <= service.STALE_AFTER for key in demand)
            if blocked or not known or q is None:
                continue
            if capabilities_unknown:
                warnings.append('CAPABILITIES_UNVERIFIED:' + hid)
            if locked:
                alternatives.append(dict(hospital_id=hid, inquiry_id=q['id'], expires_at=q['expires_at'],
                                         current_location_required=p['state'] == 'IN_TRANSIT'))
            else:
                eligible.append(hi)
                expiries.append(q['expires_at'])
        if not eligible and not locked:
            warnings.append('NO_ELIGIBLE_ACCEPTED_HOSPITAL' if eligibility_reasons else 'NO_VALID_ACCEPTED_HOSPITAL')
        duration = p['treatment_duration_seconds'] or DEFAULT_DURATION
        deadline = p['max_wait_seconds'] or DEFAULT_MAX_WAIT
        context[pid] = dict(patient=p, eligible=eligible, routes=routes, warnings=warnings,
                            eligibility_reasons=eligibility_reasons,
                            acceptance_expiries=expiries, locked=locked, duration=duration, deadline=deadline,
                            accepted_alternatives=alternatives,
                            missing_capabilities=missing_capabilities,
                            treatment_requirements=treatment_binding.requirements_for(p),
                            treatment_evaluations=treatment_evaluations)
        if 'DESTINATION_RESTRICTED' in warnings:
            context[pid]['blocking_review'] = True
        # 별도 자원 모델이 필요한 환자는 누락 없이 명시적 검토 결과를 제공한다.
        if not supported:
            if locked:
                context[pid]['blocking_review'] = True
            continue
        model['patients'].append(dict(id=pid, priority=service.effective_priority(p), special=bool(demand.get('special_team')),
                                     demands=[demand.get(k, 0) for k in RESOURCE_TYPES], release=p['requested_at'] // 60,
                                     deadline=deadline // 60, duration=minute(duration), travel=travel,
                                     arrivals=arrivals, eligible=eligible))
        if locked:
            if p['destination_hospital_id'] not in index:
                context[pid]['blocking_review'] = True
            else:
                model['destination_locks'][pid] = index[p['destination_hospital_id']]
    return model, context


def present(snapshot, model, context, plan, meta):
    result = {}
    hospitals = snapshot['hospitals']
    for pid, ctx in context.items():
        p = ctx['patient']
        assignment = plan.get(pid) if plan is not None else None
        status = 'RECOMMENDED' if assignment else 'UNASSIGNED'
        if ctx['locked']:
            status = 'DESTINATION_HELD' if assignment else 'NEEDS_REVIEW'
        if plan is None or 'UNSUPPORTED_RESOURCE' in ctx['warnings']:
            status = 'NEEDS_REVIEW'
        hid = hospitals[assignment['h']]['id'] if assignment else p['destination_hospital_id']
        start = assignment['start'] * 60 if assignment else None
        violations = start is not None and start > p['requested_at'] + ctx['deadline']
        warnings = list(ctx['warnings'])
        if ctx['locked'] and 'DESTINATION_CAPABILITY_MISSING' in warnings:
            status = 'NEEDS_REVIEW'
        if ctx['locked'] and p['needs_adjustment']:
            warnings.append('HOSPITAL_REQUESTED_ADJUSTMENT')
            status = 'NEEDS_REVIEW'
        if violations:
            warnings.append('WAIT_LIMIT_EXCEEDED')
            status = 'NEEDS_REVIEW'
        short, unknown = [], []
        if hid:
            occupied = {r['resource_type']: r for r in snapshot['resources'] if r['hospital_id'] == hid}
            pending = {}
            for other in snapshot['patients']:
                if other['id'] != pid and other['destination_hospital_id'] == hid and other['state'] in service.ACTIVE_TRANSPORT:
                    for k, v in other['resource_demands'].items():
                        pending[k] = pending.get(k, 0) + v
            for k, need in p['resource_demands'].items():
                r = occupied.get(k)
                if r is None or r['status'] == 'unknown':
                    unknown.append(k)
                elif r['capacity'] - r['occupied'] - pending.get(k, 0) < need:
                    short.append(k)
        inquiry = next((q for q in snapshot['inquiries'] if q['patient_id'] == pid and q['hospital_id'] == hid
                        and q['state'] == 'ACCEPTED' and q['patient_version'] == p['version']
                        and q['expires_at'] is not None and q['expires_at'] > snapshot['now']), None)
        result[pid] = dict(status=status, hospital_id=hid, inquiry_id=inquiry['id'] if inquiry else None,
                           eligibility_reasons=ctx.get('eligibility_reasons', []),
                           missing_capabilities=ctx['missing_capabilities'],
                           treatment_requirements=ctx['treatment_requirements'],
                           treatment_evaluation=ctx['treatment_evaluations'].get(hid),
                           treatment_evaluations=ctx['treatment_evaluations'],
                           destination_locked=ctx['locked'], planned_start_at=start,
                           accepted_alternatives=ctx['accepted_alternatives'] if p['needs_adjustment'] else [],
                           planned_end_at=start + minute(ctx['duration']) * 60 if start is not None else None,
                           wait_seconds=max(0, start - p['requested_at']) if start is not None else None,
                           confirmable_now=bool(assignment and not ctx['locked'] and inquiry and not short and not unknown),
                           warnings=warnings, travel=ctx['routes'].get(hid),
                           assumptions=dict(parameter_source='experiment', treatment_duration_seconds=ctx['duration'],
                                            max_wait_seconds=ctx['deadline'], occupied_release='unknown_blocked_for_horizon'),
                           solver=meta)
    return result
