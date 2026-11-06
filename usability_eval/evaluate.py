"""Validate and summarize observed manual/voice task attempts without imputation."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

SCHEMA_VERSION = 1
FIELDS = ('record_id', 'participant_id', 'participant_role', 'task_id', 'attempt',
          'method', 'source', 'status', 'order', 'elapsed_ms', 'error_count',
          'correction_count', 'touch_count', 'hospital_review_ms', 'inquiry_count',
          'interface_version', 'engine_version', 'failure_reason')
TEXT_REQUIRED = ('record_id', 'participant_id', 'task_id', 'interface_version', 'engine_version')
COUNTS = ('attempt', 'order', 'error_count', 'correction_count', 'touch_count', 'inquiry_count')
METRICS = ('elapsed_ms', 'error_count', 'correction_count', 'touch_count',
           'hospital_review_ms', 'inquiry_count')
PROTOCOL = {
    'schema_version': SCHEMA_VERSION,
    'purpose': 'Observed paired manual/voice tasks, not parser or simulated task time.',
    'status': 'not_measured',
    'actual_participants': 0,
    'actual_attempts': 0,
    'pair_key': ['source', 'participant_id', 'participant_role', 'task_id', 'attempt'],
    'pair_difference': 'voice - manual; negative means lower measured value',
    'source': ['human', 'synthetic'],
    'participant_role': ['paramedic', 'hospital'],
    'method': ['manual', 'voice'],
    'status_values': ['completed', 'failed', 'abandoned'],
    'required_fields': list(TEXT_REQUIRED) + ['participant_role', 'attempt', 'method',
        'source', 'status', 'order', 'elapsed_ms'],
    'optional_metrics': ['error_count', 'correction_count', 'touch_count',
        'hospital_review_ms', 'inquiry_count'],
    'missing': 'Empty optional measurements remain null, never zero.',
    'timing': 'One monotonic clock per attempt. elapsed_ms includes entry, speech, STT wait, correction, review and successful submission; failed/abandoned uses time until stop.',
    'hospital_review': 'Observed active review for one person and task, overlapping intervals counted once. Record under hospital role only. Blank if not observed.',
    'retries': 'Keep all attempts; increment attempt for BOTH methods. Do not replace failed attempts.',
    'order': 'Counterbalance order 1/2 across participants and tasks; paired methods must have distinct order.',
    'participant_scope': 'Use anonymous participant IDs; separately disclose student or practitioner recruitment. Role names describe assigned task, not professional credentials.',
    'evidence_scope': 'Automated API timing and synthetic examples are synthetic, not human measurements. Inquiry reduction alone does not establish hospital workload reduction.',
}


def _text(value, name, row):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError(f'row {row}: {name} must be a nonempty string (max 200 characters)')
    return value.strip()


def validate(records):
    if not isinstance(records, list) or len(records) > 100000:
        raise ValueError('records must be a list with at most 100000 attempts')
    cleaned, ids, cells = [], set(), set()
    for index, raw in enumerate(records, 1):
        if not isinstance(raw, dict) or set(raw) - set(FIELDS):
            raise ValueError(f'row {index}: unknown fields or non-object record')
        row = {key: raw.get(key) for key in FIELDS}
        for key in TEXT_REQUIRED:
            row[key] = _text(row[key], key, index)
        for key, allowed in [('participant_role', ('paramedic', 'hospital')),
                             ('method', ('manual', 'voice')), ('source', ('human', 'synthetic')),
                             ('status', ('completed', 'failed', 'abandoned'))]:
            if row[key] not in allowed:
                raise ValueError(f'row {index}: invalid {key}')
        for key in COUNTS:
            value = row[key]
            required = key in ('attempt', 'order')
            if value is None and not required:
                continue
            if type(value) is not int or value < (1 if required else 0):
                raise ValueError(f'row {index}: {key} must be a nonnegative integer (attempt/order >= 1)')
        if row['order'] not in (1, 2):
            raise ValueError(f'row {index}: order must be 1 or 2')
        for key in ('elapsed_ms', 'hospital_review_ms'):
            value = row[key]
            if value is None and key == 'hospital_review_ms':
                continue
            try:
                valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError(f'row {index}: {key} must be a finite nonnegative number')
        review = row['hospital_review_ms']
        if review is not None and (row['participant_role'] != 'hospital' or review > row['elapsed_ms']):
            raise ValueError(f'row {index}: review must be hospital work and cannot exceed elapsed time')
        reason = row['failure_reason']
        if reason is not None and (not isinstance(reason, str) or len(reason) > 1000):
            raise ValueError(f'row {index}: invalid failure_reason')
        if row['status'] != 'completed' and (not isinstance(reason, str) or not reason.strip()):
            raise ValueError(f'row {index}: failed/abandoned attempts require failure_reason')
        if row['status'] == 'completed' and reason and reason.strip():
            raise ValueError(f'row {index}: completed attempt cannot have failure_reason')
        identity = tuple(row[key] for key in PROTOCOL['pair_key']) + (row['method'],)
        if row['record_id'] in ids or identity in cells:
            raise ValueError(f'row {index}: duplicate record_id or participant/task/attempt/method')
        ids.add(row['record_id'])
        cells.add(identity)
        cleaned.append(row)
    return cleaned


def load_records(path):
    path = Path(path)
    if path.suffix.lower() == '.csv':
        with path.open(encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(FIELDS):
                raise ValueError('CSV headers must exactly match measurement_template.csv')
            rows = list(reader)
        for row in rows:
            for key in FIELDS:
                if row[key] == '':
                    row[key] = None
            for key in COUNTS:
                if row[key] is not None:
                    try:
                        row[key] = int(row[key])
                    except (ValueError, TypeError):
                        raise ValueError(f'{key}: expected integer') from None
            for key in ('elapsed_ms', 'hospital_review_ms'):
                if row[key] is not None:
                    try:
                        row[key] = float(row[key])
                    except (ValueError, TypeError):
                        raise ValueError(f'{key}: expected number') from None
        return validate(rows)
    if path.suffix.lower() != '.json':
        raise ValueError('input must be CSV or JSON')
    package = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(package, dict) or set(package) != {'schema_version', 'records'} or type(package['schema_version']) is not int or package['schema_version'] != SCHEMA_VERSION:
        raise ValueError('JSON must contain schema_version: 1 and records only')
    return validate(package['records'])


def stats(values, denominator):
    values = sorted(values)
    return dict(n=len(values), missing=denominator-len(values),
        mean=statistics.mean(values) if values else None,
        median=statistics.median(values) if values else None,
        p95=values[math.ceil(.95*len(values))-1] if values else None,
        maximum=max(values) if values else None,
        minimum=min(values) if values else None)


PROFILE_FIELDS = ('source', 'participant_role', 'manual_interface_version', 'voice_interface_version',
                  'manual_engine_version', 'voice_engine_version')


def _profile(methods):
    first = next(iter(methods.values()))
    result = dict(source=first['source'], participant_role=first['participant_role'])
    for method in ('manual', 'voice'):
        row = methods.get(method)
        result[method+'_interface_version'] = None if row is None else row['interface_version']
        result[method+'_engine_version'] = None if row is None else row['engine_version']
    result['interface_version'] = (result['manual_interface_version']
        if result['manual_interface_version'] == result['voice_interface_version'] else None)
    return result


def _profile_key(profile):
    return tuple(profile[field] for field in PROFILE_FIELDS)


def evaluate(records, plan=None):
    records = validate(records)
    assessment = None
    if plan is not None:
        from .order_plan import assess_records
        assessment = assess_records(records, plan)
    noncompliant = set() if assessment is None else set(assessment['noncompliant_record_ids'])
    result = dict(schema_version=SCHEMA_VERSION, measurement_status='measured' if any(r['source'] == 'human' for r in records) else 'not_measured',
        actual_attempts=sum(r['source'] == 'human' for r in records),
        actual_participants=len({r['participant_id'] for r in records if r['source'] == 'human'}),
        synthetic_attempts=sum(r['source'] == 'synthetic' for r in records),
        groups=[], pairs=[], incomplete_pairs=[], warnings=[], records=records,
        profile_fields=list(PROFILE_FIELDS), groups_are_version_stratified=True,
        order_summary=[], order_warnings=[], plan_assessment=assessment,
        difference='voice - manual',
        limitations=['Assigned roles are not professional credentials.',
            'Only completed pairs contribute to completion-time differences; all failures stay in attempt counts.',
            'Inquiry counts do not establish hospital workload reduction; observe hospital_review_ms.',
            'Repeated tasks from one participant are not independent participants.',
            'Source, role, interface versions and manual/voice engine profiles are not pooled.',
            'Interface-mismatched or plan-deviating pairs retain source records but have no paired differences.',
            'Order strata are descriptive; no p-values, causal or overall treatment-effect estimates are produced.',
            'Descriptive differences do not establish population effects or clinical effectiveness.'])
    indexed = defaultdict(dict)
    for row in records:
        indexed[tuple(row[key] for key in PROTOCOL['pair_key'])][row['method']] = row
    profiles, record_profiles = {}, {}
    for identity, methods in sorted(indexed.items()):
        info = dict(zip(PROTOCOL['pair_key'], identity))
        profile = _profile(methods)
        key = _profile_key(profile)
        profiles[key] = profile
        for row in methods.values():
            record_profiles[row['record_id']] = key
        if len(methods) != 2:
            result['incomplete_pairs'].append(dict(**info, **{k:v for k,v in profile.items() if k not in info},
                available=list(methods), missing=[m for m in ('manual','voice') if m not in methods]))
            continue
        manual, voice = methods['manual'], methods['voice']
        if manual['order'] == voice['order']:
            raise ValueError(f'{identity}: paired attempts must have distinct order')
        complete = manual['status'] == voice['status'] == 'completed'
        exclusions = []
        if not complete:
            exclusions.append('outcome_not_completed')
        if manual['interface_version'] != voice['interface_version']:
            exclusions.append('interface_version_mismatch')
        if manual['record_id'] in noncompliant or voice['record_id'] in noncompliant:
            exclusions.append('plan_deviation')
        comparable = not exclusions
        result['pairs'].append(dict(**info, **{k:v for k,v in profile.items() if k not in info},
            completed_pair=complete and comparable, outcome_completed_pair=complete,
            comparable_pair=comparable, exclusion_reasons=exclusions,
            manual_status=manual['status'], voice_status=voice['status'],
            manual_record_id=manual['record_id'], voice_record_id=voice['record_id'],
            voice_first=voice['order'] < manual['order'],
            deltas={key: voice[key]-manual[key] if comparable and voice[key] is not None and manual[key] is not None else None for key in METRICS}))
    # Keep empty role/source rows compatible with the original empty report,
    # while every observed profile has its own method and pair summaries.
    for source in ('human', 'synthetic'):
        for role in ('paramedic', 'hospital'):
            if not any(key[:2] == (source, role) for key in profiles):
                profile = {field: None for field in PROFILE_FIELDS}
                profile.update(source=source, participant_role=role, interface_version=None)
                profiles[_profile_key(profile)] = profile
    ordered_profiles = sorted(profiles.values(), key=lambda p: (
        ('human', 'synthetic').index(p['source']), ('paramedic', 'hospital').index(p['participant_role']),
        json.dumps(_profile_key(p), ensure_ascii=False)))
    for profile in ordered_profiles:
        key = _profile_key(profile)
        for method in ('manual', 'voice'):
            group = [r for r in records if record_profiles[r['record_id']] == key and r['method'] == method]
            completed = [r for r in group if r['status'] == 'completed']
            result['groups'].append(dict(**profile, method=method,
                attempts=len(group), completed=len(completed), failed=sum(r['status']=='failed' for r in group),
                abandoned=sum(r['status']=='abandoned' for r in group),
                completion_rate=len(completed)/len(group) if group else None,
                attempt_metrics={metric: stats([r[metric] for r in group if r[metric] is not None], len(group)) for metric in METRICS},
                completed_metrics={metric: stats([r[metric] for r in completed if r[metric] is not None], len(completed)) for metric in METRICS}))
    result['paired_summary'] = []
    for profile in ordered_profiles:
        group = [pair for pair in result['pairs'] if _profile_key(pair) == _profile_key(profile)]
        complete = [pair for pair in group if pair['completed_pair']]
        manual_first = sum(not pair['voice_first'] for pair in group)
        voice_first = len(group) - manual_first
        result['paired_summary'].append(dict(**profile, pairs=len(group), completed_pairs=len(complete),
            outcome_completed_pairs=sum(pair['outcome_completed_pair'] for pair in group),
            incomplete_outcome_pairs=sum(not pair['outcome_completed_pair'] for pair in group),
            excluded_completed_pairs=sum(pair['outcome_completed_pair'] and not pair['comparable_pair'] for pair in group),
            participants=len({pair['participant_id'] for pair in group}),
            voice_first=voice_first, manual_first=manual_first,
            deltas={metric: stats([pair['deltas'][metric] for pair in complete if pair['deltas'][metric] is not None], len(complete)) for metric in METRICS}))
        for first_method in ('manual', 'voice'):
            ordered = [pair for pair in group if pair['voice_first'] == (first_method == 'voice')]
            eligible = [pair for pair in ordered if pair['completed_pair']]
            result['order_summary'].append(dict(**profile, first_method=first_method, pairs=len(ordered),
                completed_pairs=len(eligible), participants=len({pair['participant_id'] for pair in ordered}),
                failed_pairs=sum('failed' in (pair['manual_status'],pair['voice_status']) for pair in ordered),
                abandoned_pairs=sum('abandoned' in (pair['manual_status'],pair['voice_status']) for pair in ordered),
                deltas={metric: stats([pair['deltas'][metric] for pair in eligible if pair['deltas'][metric] is not None], len(eligible)) for metric in METRICS}))
        if abs(manual_first-voice_first) > 1:
            result['order_warnings'].append(dict(code='ORDER_IMBALANCE', **profile,
                manual_first=manual_first, voice_first=voice_first, absolute_difference=abs(manual_first-voice_first)))
        if group and not (manual_first and voice_first):
            result['order_warnings'].append(dict(code='SINGLE_ORDER_ONLY', **profile,
                manual_first=manual_first, voice_first=voice_first))
    if result['incomplete_pairs']:
        result['warnings'].append('Unpaired attempts are preserved and excluded from paired differences.')
    if any('interface_version_mismatch' in pair['exclusion_reasons'] for pair in result['pairs']):
        result['warnings'].append('Interface-mismatched pairs are preserved but excluded from paired differences.')
    if result['order_warnings']:
        result['warnings'].append('Order imbalance or a single observed order is present; inspect order_warnings and order_summary by version profile.')
    if assessment is not None and not assessment['fully_compliant']:
        result['warnings'].append('Observed records do not fully match the plan; inspect missing slots, unexpected records and order/version deviations.')
    return result


def _write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def _new_output(path):
    path = Path(path).resolve()
    path.mkdir(parents=True, exist_ok=False)
    return path


def prepare(output):
    output = _new_output(output)
    stream = io.StringIO(newline='')
    csv.writer(stream).writerow(FIELDS)
    (output/'measurement_template.csv').write_text(stream.getvalue(), encoding='utf-8-sig')
    _write_json(output/'measurement_template.json', dict(schema_version=1, records=[]))
    _write_json(output/'protocol.json', PROTOCOL)
    _write_json(output/'report.json', evaluate([]))
    (output/'report.md').write_text(markdown(evaluate([])), encoding='utf-8')
    return output


def markdown(report):
    lines = ['# 수동·음성 사용성 평가', '',
        f"실제 측정: {'있음' if report['actual_attempts'] else '미측정'} · 실제 참여자 {report['actual_participants']}명 · 실제 과업 {report['actual_attempts']}건 · 합성 과업 {report['synthetic_attempts']}건", '',
        '실패·중단·짝누락은 원자료에 보존한다. 미측정값은 0으로 대체하지 않는다.', '',
        '화면 버전과 수동·음성 엔진 프로필을 나누어 보고하며 서로 다른 프로필을 합산하지 않는다.', '',
        '| 출처 | 역할 | 화면(수동 / 음성) | 엔진(수동 / 음성) | 방식 | 전체 | 완료 | 실패 | 중단 | 완료시간 평균(ms) |',
        '|---|---|---|---|---|---:|---:|---:|---:|---:|']
    def fmt(value):
        return '미측정' if value is None else f'{value:.2f}'
    def cell(value):
        return '미기록' if value is None else str(value).replace('|', '\\|').replace('\n', '<br>').replace('\r', '')
    def versions(group, suffix):
        return cell(group['manual_'+suffix]) + ' / ' + cell(group['voice_'+suffix])
    for g in report['groups']:
        lines.append(f"| {g['source']} | {g['participant_role']} | {versions(g, 'interface_version')} | {versions(g, 'engine_version')} | {g['method']} | {g['attempts']} | {g['completed']} | {g['failed']} | {g['abandoned']} | {fmt(g['completed_metrics']['elapsed_ms']['mean'])} |")
    lines += ['', '차이는 음성 − 수동이다. 같은 참여자·역할·과제·반복의 두 방식이 모두 완료되고 화면 버전과 계획 조건이 맞는 경우만 비교한다.', '',
              '| 출처 | 역할 | 화면(수동 / 음성) | 엔진(수동 / 음성) | 비교 가능한 완료 짝 | 제외된 완료 짝 | 참여자 | 시간차 평균(ms) | 오류차 | 수정차 | 터치차 | 병원 검토차(ms) |',
              '|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for g in report['paired_summary']:
        values = [fmt(g['deltas'][key]['mean']) for key in ('elapsed_ms','error_count','correction_count','touch_count','hospital_review_ms')]
        lines.append(f"| {g['source']} | {g['participant_role']} | {versions(g, 'interface_version')} | {versions(g, 'engine_version')} | {g['completed_pairs']} | {g['excluded_completed_pairs']} | {g['participants']} | " + ' | '.join(values) + ' |')
    lines += ['', '## 순서별 기술통계', '',
        '| 출처 | 역할 | 화면(수동 / 음성) | 엔진(수동 / 음성) | 먼저 수행 | 전체 짝 | 비교 가능한 완료 짝 | 시간차 평균(ms) |',
        '|---|---|---|---|---|---:|---:|---:|']
    for g in report['order_summary']:
        lines.append(f"| {g['source']} | {g['participant_role']} | {versions(g, 'interface_version')} | {versions(g, 'engine_version')} | {g['first_method']} | {g['pairs']} | {g['completed_pairs']} | {fmt(g['deltas']['elapsed_ms']['mean'])} |")
    lines += ['', f"순서 점검 {len(report['order_warnings'])}건. 한 순서만 관찰되었거나 프로필 내 두 순서의 수가 1보다 크게 차이나면 표시한다."]
    if report['plan_assessment'] is not None:
        plan = report['plan_assessment']
        lines += ['', '## 계획 대비 점검', '',
            f"계획 SHA `{plan['plan_sha256']}` · 예정 {plan['planned_method_attempts']}시도 · 관찰 {plan['observed_attempts']}시도", '',
            f"누락 {len(plan['missing_slots'])} · 계획 밖 기록 {len(plan['unexpected_records'])} · 순서/버전 이탈 {len(plan['deviations'])}.",
            '계획 자료는 측정값이 아니다. 누락을 성공·실패·0시간으로 만들어 채우지 않는다.']
    lines += ['', f"짝누락 {len(report['incomplete_pairs'])}건. 각 지표의 유효 표본·결측·p95·개별 최대 악화는 report.json에 보존한다.", '',
              '병원 부담 감소는 문의 수만으로 판정하지 않는다. 실제 검토시간이 비어 있으면 미측정이다.',
              '학생 역할 시험은 현직 구급대원·의료진 검증으로 표현하지 않는다. 합성 자료는 실제 사용성과 합산하지 않는다.',
              '여기 표시한 값은 기술통계이며 임상 효과·모집단 효과를 입증하지 않는다.', '']
    return '\n'.join(lines)


def run(input_path, output, plan_path=None):
    input_path = Path(input_path)
    files = [Path(__file__), Path(__file__).with_name('__init__.py'), Path(__file__).with_name('__main__.py'), Path(__file__).with_name('order_plan.py')]
    source_bytes = {path.name: path.read_bytes() for path in files}
    before = input_path.read_bytes()
    records = load_records(input_path)
    plan_bytes, plan = None, None
    if plan_path is not None:
        from .order_plan import load_plan
        plan_path = Path(plan_path)
        plan_bytes = plan_path.read_bytes()
        plan = load_plan(plan_path)
    report = evaluate(records, plan=plan)
    if input_path.read_bytes() != before:
        raise ValueError('input changed during evaluation')
    if any(path.read_bytes() != source_bytes[path.name] for path in files):
        raise ValueError('evaluation source changed during evaluation')
    if plan_bytes is not None and plan_path.read_bytes() != plan_bytes:
        raise ValueError('plan changed during evaluation')
    output = _new_output(output)
    (output/('input'+input_path.suffix.lower())).write_bytes(before)
    source_hashes = {}
    for path in files:
        content = source_bytes[path.name]
        target = output/'sources'/path.name
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(content)
        source_hashes[path.name] = hashlib.sha256(content).hexdigest()
    report['input_sha256'] = hashlib.sha256(before).hexdigest()
    report['source_hashes'] = source_hashes
    if plan_bytes is not None:
        (output/'plan.json').write_bytes(plan_bytes)
        report['plan_file_sha256'] = hashlib.sha256(plan_bytes).hexdigest()
    _write_json(output/'report.json', report)
    _write_json(output/'protocol.json', PROTOCOL)
    (output/'report.md').write_text(markdown(report), encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    preparation = sub.add_parser('prepare')
    preparation.add_argument('--output', required=True)
    evaluation = sub.add_parser('evaluate')
    evaluation.add_argument('--input', required=True)
    evaluation.add_argument('--output', required=True)
    evaluation.add_argument('--plan')
    planning = sub.add_parser('plan')
    planning.add_argument('--participants', nargs='+', required=True)
    planning.add_argument('--tasks', nargs='+', required=True)
    planning.add_argument('--role', choices=('paramedic', 'hospital'), required=True)
    planning.add_argument('--source', choices=('human', 'synthetic'), default='synthetic')
    planning.add_argument('--seed', type=int, required=True)
    planning.add_argument('--attempt', type=int, default=1)
    planning.add_argument('--interface-version', required=True)
    planning.add_argument('--manual-engine-version', default='none')
    planning.add_argument('--voice-engine-version', required=True)
    planning.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            print(prepare(args.output))
        elif args.command == 'plan':
            from .order_plan import create
            plan = create(args.output, participants=args.participants, tasks=args.tasks,
                role=args.role, source=args.source, seed=args.seed, attempt=args.attempt,
                interface_version=args.interface_version, manual_engine_version=args.manual_engine_version,
                voice_engine_version=args.voice_engine_version)
            print(json.dumps({key: plan[key] for key in ('revision', 'plan_sha256', 'schedule_sha256',
                'planned_participants', 'planned_pairs', 'actual_participants', 'actual_attempts')}, ensure_ascii=False))
        else:
            report = run(args.input, args.output, plan_path=args.plan)
            print(json.dumps({key: report[key] for key in ('measurement_status','actual_participants','actual_attempts','synthetic_attempts')}, ensure_ascii=False))
    except (ValueError, OSError) as error:
        parser.exit(2, f'{type(error).__name__}: {error}\n')
    return 0
