"""Offline necessary-condition certificates for synthetic success targets.

This module reads hidden synthetic potentials solely to audit whether a target
is physically possible. It is not a policy, a prediction, or a simulator. A
missing obstruction is deliberately NOT a feasibility certificate.
"""
def _integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(label + ' must be an integer >= ' + str(minimum))
    return value


def exclusive_workload_certificate(scenario, required_patient_ids, *, deadline_seconds=300):
    """Prove overload on exclusive responsive-accept hospitals, if present.

    Relaxations favor feasibility: every hospital is immediately queryable,
    other patients and all unsuccessful queries disappear, and processing may
    be optimally reordered. Even preemption would not evade the work bound.
    A required job released at r must finish by r+deadline_seconds. For every
    release/deadline interval, work of jobs restricted to one hospital must
    fit workers * interval length. Strict overload proves infeasibility.
    """
    horizon = _integer(deadline_seconds, 'deadline_seconds', 1)
    requested = list(required_patient_ids)
    if not requested or any(type(p) is not str or not p for p in requested) or len(set(requested)) != len(requested):
        raise ValueError('required patient identifiers must be nonempty and unique')
    hospitals = {}
    for row in scenario['hospitals']:
        hid = row['id']
        if type(hid) is not str or not hid or hid in hospitals:
            raise ValueError('hospital identifiers must be unique nonempty strings')
        hospitals[hid] = _integer(row['workers'], 'workers', 1)
    if not hospitals:
        raise ValueError('hospital universe is missing')
    patients = {}
    for row in scenario['patients']:
        pid = row['id']
        if type(pid) is not str or not pid or pid in patients:
            raise ValueError('patient identifiers must be unique nonempty strings')
        patients[pid] = _integer(row['at'], 'release time')
    if any(pid not in patients for pid in requested):
        raise ValueError('required patient is outside the scenario')
    cells = {}
    for row in scenario['potentials']:
        pid, hid = row['patient_id'], row['hospital_id']
        if pid not in requested:
            continue
        if hid not in hospitals or (pid, hid) in cells:
            raise ValueError('potential universe is duplicated or unknown')
        _integer(row['service_seconds'], 'service_seconds', 1)
        if type(row['accept']) is not bool or type(row['no_response']) is not bool:
            raise ValueError('potential response flags must be booleans')
        cells[pid, hid] = row
    if set(cells) != {(pid, hid) for pid in requested for hid in hospitals}:
        raise ValueError('complete potential matrix required to prove exclusivity')

    options, exclusive, obstructions = {}, {}, []
    for pid in sorted(requested):
        options[pid] = [dict(hospital=hid, service_seconds=cells[pid, hid]['service_seconds'])
            for hid in sorted(hospitals) if cells[pid, hid]['accept']
            and not cells[pid, hid]['no_response']
            and cells[pid, hid]['service_seconds'] <= horizon]
        if not options[pid]:
            obstructions.append(dict(type='no_timely_responsive_accept', patient_ids=[pid],
                minimum_failures_in_this_obstruction=1))
        elif len(options[pid]) == 1:
            edge = options[pid][0]
            exclusive.setdefault(edge['hospital'], []).append(dict(patient_id=pid,
                released_at=patients[pid], deadline_at=patients[pid]+horizon,
                service_seconds=edge['service_seconds']))
    for hid in sorted(exclusive):
        jobs = exclusive[hid]
        recorded = set()
        for left in sorted({j['released_at'] for j in jobs}):
            for right in sorted({j['deadline_at'] for j in jobs}):
                if right <= left:
                    continue
                contained = [j for j in jobs if j['released_at'] >= left and j['deadline_at'] <= right]
                identifiers = tuple(j['patient_id'] for j in contained)
                work = sum(j['service_seconds'] for j in contained)
                capacity = hospitals[hid] * (right-left)
                if work <= capacity or identifiers in recorded:
                    continue
                recorded.add(identifiers)
                # Optimistically retain the shortest jobs. This is an upper
                # bound on count, not an actual schedule or policy rollout.
                prefix_work, maximum_count = 0, 0
                for duration in sorted(j['service_seconds'] for j in contained):
                    prefix_work += duration
                    if prefix_work > capacity:
                        break
                    maximum_count += 1
                obstructions.append(dict(type='exclusive_hospital_workload', hospital=hid,
                    patient_ids=list(identifiers), jobs=contained, workers=hospitals[hid],
                    interval_start=left, interval_end=right, required_worker_seconds=work,
                    available_worker_seconds=capacity, excess_worker_seconds=work-capacity,
                    optimistic_success_count_upper_bound=maximum_count,
                    minimum_failures_in_this_obstruction=len(contained)-maximum_count))
    return dict(schema_version=1, status='proven_infeasible' if obstructions else 'not_proven_infeasible',
        required_patient_ids=sorted(requested), deadline_seconds=horizon,
        timely_responsive_accept_options=options, obstructions=obstructions,
        conclusion_scope='necessary capacity obstruction only; absence does not establish feasibility',
        role='offline hidden-potential target audit; never a policy input',
        policy_runs=0, clock_rollouts=0)
