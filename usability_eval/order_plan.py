"""Reproducible balanced task allocation, separate from observed measurements."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import re

REVISION = 'usability-order-plan-20261102-v1'
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}\Z')


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def _ids(values, name):
    if not isinstance(values, (list, tuple)) or not values or len(values) > 1000:
        raise ValueError(name + ' must contain 1..1000 anonymous ASCII identifiers')
    if any(not isinstance(value, str) or _ID.fullmatch(value) is None for value in values):
        raise ValueError(name + ' must use anonymous ASCII identifiers (max 80 characters)')
    if len(set(values)) != len(values):
        raise ValueError(name + ' contains duplicate identifiers')
    return sorted(values)


def _version(value, name):
    if (not isinstance(value, str) or not value.strip() or len(value) > 200
            or any(ord(char) < 32 for char in value)):
        raise ValueError(name + ' must be nonempty text without control characters (max 200)')
    return value.strip()


def make_plan(participants, tasks, *, role, seed, attempt, interface_version,
              manual_engine_version, voice_engine_version, source='synthetic'):
    participants, tasks = _ids(participants, 'participants'), _ids(tasks, 'tasks')
    if len(participants) * len(tasks) > 50000:
        raise ValueError('plan exceeds 100000 method attempts')
    if role not in ('paramedic', 'hospital') or source not in ('human', 'synthetic'):
        raise ValueError('invalid role or source')
    if type(seed) is not int or not 0 <= seed <= (1 << 63)-1:
        raise ValueError('seed must be an integer from 0 through 2^63-1')
    if type(attempt) is not int or not 1 <= attempt <= 10000:
        raise ValueError('attempt must be an integer from 1 through 10000')
    interface_version = _version(interface_version, 'interface_version')
    manual_engine_version = _version(manual_engine_version, 'manual_engine_version')
    voice_engine_version = _version(voice_engine_version, 'voice_engine_version')
    ordered_p = sorted(participants, key=lambda value: (_digest([seed, 'participant', value]), value))
    ordered_t = sorted(tasks, key=lambda value: (_digest([seed, 'task', value]), value))
    offset = int(_digest([seed, 'first-method'])[-1], 16) % 2
    schedule = []
    for p_index, participant in enumerate(ordered_p):
        for t_index, task in enumerate(ordered_t):
            manual_first = (p_index + t_index + offset) % 2 == 0
            schedule.append(dict(participant_id=participant, participant_role=role,
                task_id=task, attempt=attempt, source=source,
                interface_version=interface_version, manual_engine_version=manual_engine_version,
                voice_engine_version=voice_engine_version, task_position=t_index + 1,
                sequence='AB' if manual_first else 'BA',
                manual_order=1 if manual_first else 2, voice_order=2 if manual_first else 1))
    def balance(rows):
        manual = sum(row['sequence'] == 'AB' for row in rows)
        return dict(manual_first=manual, voice_first=len(rows)-manual,
                    absolute_difference=abs(2*manual-len(rows)))
    plan = dict(schema_version=1, revision=REVISION, seed=seed, attempt=attempt,
        participants=participants, tasks=tasks, participant_role=role, source=source,
        interface_version=interface_version, manual_engine_version=manual_engine_version,
        voice_engine_version=voice_engine_version, planned_participants=len(participants),
        planned_pairs=len(schedule), planned_method_attempts=2*len(schedule),
        measurement_status='not_measured', actual_participants=0, actual_attempts=0,
        assignment_meaning=dict(A='manual', B='voice',
            balance='AB/BA counts differ by at most one overall, per participant and per task; this is allocation, not performance.'),
        schedule=schedule, schedule_sha256=_digest(schedule),
        balance=dict(overall=balance(schedule),
            per_participant={p: balance([row for row in schedule if row['participant_id'] == p]) for p in participants},
            per_task={t: balance([row for row in schedule if row['task_id'] == t]) for t in tasks}),
        limits=['A schedule is not observed human work or proof of credentials.',
            'Counterbalanced allocation does not remove all learning, period or carryover effects.',
            'Use the same success criteria and include correction and review until completion.'])
    plan['plan_sha256'] = _digest(plan)
    return plan


def validate_plan(plan):
    if not isinstance(plan, dict):
        raise ValueError('plan must be an object')
    try:
        expected = make_plan(plan['participants'], plan['tasks'], role=plan['participant_role'],
            seed=plan['seed'], attempt=plan['attempt'], interface_version=plan['interface_version'],
            manual_engine_version=plan['manual_engine_version'], voice_engine_version=plan['voice_engine_version'],
            source=plan['source'])
    except (KeyError, TypeError):
        raise ValueError('plan is missing required allocation metadata') from None
    if _canonical(plan) != _canonical(expected):
        raise ValueError('plan metadata, schedule or checksum differs from reproducible allocation')
    return expected


def load_plan(path):
    return validate_plan(json.loads(Path(path).read_text(encoding='utf-8-sig')))


def expected_slots(plan):
    plan = validate_plan(plan)
    slots = []
    for pair in plan['schedule']:
        for method in ('manual', 'voice'):
            slots.append(dict(source=pair['source'], participant_id=pair['participant_id'],
                participant_role=pair['participant_role'], task_id=pair['task_id'], attempt=pair['attempt'],
                method=method, order=pair[method+'_order'], interface_version=pair['interface_version'],
                engine_version=pair[method+'_engine_version']))
    return slots


def assess_records(records, plan):
    """Compare validated records with slots; never manufacture missing outcomes."""
    from .evaluate import PROTOCOL
    plan = validate_plan(plan)
    fields = tuple(PROTOCOL['pair_key']) + ('method',)
    identity = lambda row: tuple(row[key] for key in fields)
    slots = {identity(row): row for row in expected_slots(plan)}
    observed, unexpected, deviations, compliant, noncompliant = set(), [], [], [], []
    for row in records:
        key = identity(row)
        if key not in slots:
            unexpected.append(dict(record_id=row['record_id'], **{field: row[field] for field in fields}))
            noncompliant.append(row['record_id'])
            continue
        observed.add(key)
        differences = {field: dict(planned=slots[key][field], observed=row[field])
            for field in ('order', 'interface_version', 'engine_version') if row[field] != slots[key][field]}
        if differences:
            deviations.append(dict(record_id=row['record_id'], **{field: row[field] for field in fields}, differences=differences))
            noncompliant.append(row['record_id'])
        else:
            compliant.append(row['record_id'])
    missing = [slots[key] for key in sorted(set(slots)-observed)]
    return dict(revision=plan['revision'], plan_sha256=plan['plan_sha256'], schedule_sha256=plan['schedule_sha256'],
        planned_participants=plan['planned_participants'], planned_pairs=plan['planned_pairs'],
        planned_method_attempts=plan['planned_method_attempts'], observed_attempts=len(records),
        matched_slots=len(observed), missing_slots=missing, unexpected_records=unexpected,
        deviations=deviations, compliant_record_ids=sorted(compliant), noncompliant_record_ids=sorted(noncompliant),
        all_planned_slots_observed=not missing,
        fully_compliant=not (missing or unexpected or deviations),
        note='Plan mismatch is reported and excluded from planned paired differences; source records remain unchanged.')


def create(output, **parameters):
    from .evaluate import FIELDS, SCHEMA_VERSION, _new_output, _write_json, evaluate, markdown
    plan = make_plan(**parameters)
    output = _new_output(output)
    _write_json(output/'plan.json', plan)
    # The evaluable templates stay empty. Planned rows use the same field names
    # but leave status and every measurement blank until an observer fills them.
    _write_json(output/'measurement_template.json', dict(schema_version=SCHEMA_VERSION, records=[]))
    empty = io.StringIO(newline='')
    csv.writer(empty).writerow(FIELDS)
    (output/'measurement_template.csv').write_text(empty.getvalue(), encoding='utf-8-sig', newline='')
    pending = io.StringIO(newline='')
    writer = csv.DictWriter(pending, fieldnames=FIELDS)
    writer.writeheader()
    for slot in expected_slots(plan):
        row = {field: None for field in FIELDS}
        row.update(slot)
        row['record_id'] = 'planned-' + _digest(slot)[:32]
        writer.writerow(row)
    (output/'planned_measurements.csv').write_text(pending.getvalue(), encoding='utf-8-sig', newline='')
    table = io.StringIO(newline='')
    headers = list(plan['schedule'][0])
    writer = csv.DictWriter(table, fieldnames=headers)
    writer.writeheader()
    writer.writerows(plan['schedule'])
    (output/'schedule.csv').write_text(table.getvalue(), encoding='utf-8-sig', newline='')
    lines = ['# 수동·음성 과업 배치', '',
        'A=수동, B=음성. 아래는 예정 순서이며 실제 참여자·수행·측정값은 0건이다.', '',
        f"역할 `{plan['participant_role']}` · 출처 `{plan['source']}` · seed `{plan['seed']}` · 계획 SHA `{plan['plan_sha256']}`", '',
        '| 참여자 | 과제 위치 | 과제 | 반복 | 순서 | 수동 순서 | 음성 순서 |',
        '|---|---:|---|---:|---|---:|---:|']
    for row in plan['schedule']:
        lines.append(f"| {row['participant_id']} | {row['task_position']} | {row['task_id']} | {row['attempt']} | {row['sequence']} | {row['manual_order']} | {row['voice_order']} |")
    lines += ['', 'measurement_template.csv/json은 관찰값이 없는 평가 가능한 빈 양식이다.',
        'planned_measurements.csv는 순서를 미리 적은 작성용 양식이다. status·elapsed_ms를 실제로 관찰해 채우기 전에는 평가하지 않는다.',
        '계획의 human 표시는 예정 조건이며 실제 사람이 수행했다는 증거가 아니다. 자동 실행·예시 결과는 synthetic으로 구분한다.',
        '완료시간에는 모든 수정·검토·전송 완료까지 포함한다. 실패·중단과 빈 선택 지표도 보존한다.',
        'plan.json은 측정 records schema와 별개다. 평가할 때 --plan으로 동일 계획을 지정한다.', '']
    (output/'plan.md').write_text('\n'.join(lines), encoding='utf-8')
    report = evaluate([], plan=plan)
    _write_json(output/'report.json', report)
    (output/'report.md').write_text(markdown(report), encoding='utf-8')
    return plan
