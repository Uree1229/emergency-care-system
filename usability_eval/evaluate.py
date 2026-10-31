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


def evaluate(records):
    records = validate(records)
    result = dict(schema_version=SCHEMA_VERSION, measurement_status='measured' if any(r['source'] == 'human' for r in records) else 'not_measured',
        actual_attempts=sum(r['source'] == 'human' for r in records),
        actual_participants=len({r['participant_id'] for r in records if r['source'] == 'human'}),
        synthetic_attempts=sum(r['source'] == 'synthetic' for r in records),
        groups=[], pairs=[], incomplete_pairs=[], warnings=[], records=records,
        difference='voice - manual',
        limitations=['Assigned roles are not professional credentials.',
            'Only completed pairs contribute to completion-time differences; all failures stay in attempt counts.',
            'Inquiry counts do not establish hospital workload reduction; observe hospital_review_ms.',
            'Repeated tasks from one participant are not independent participants.',
            'Descriptive differences do not establish population effects or clinical effectiveness.'])
    for source in ('human', 'synthetic'):
        for role in ('paramedic', 'hospital'):
            for method in ('manual', 'voice'):
                group = [r for r in records if (r['source'],r['participant_role'],r['method']) == (source,role,method)]
                completed = [r for r in group if r['status'] == 'completed']
                result['groups'].append(dict(source=source, participant_role=role, method=method,
                    attempts=len(group), completed=len(completed), failed=sum(r['status']=='failed' for r in group),
                    abandoned=sum(r['status']=='abandoned' for r in group),
                    completion_rate=len(completed)/len(group) if group else None,
                    attempt_metrics={key: stats([r[key] for r in group if r[key] is not None], len(group)) for key in METRICS},
                    completed_metrics={key: stats([r[key] for r in completed if r[key] is not None], len(completed)) for key in METRICS}))
    indexed = defaultdict(dict)
    for row in records:
        indexed[tuple(row[key] for key in PROTOCOL['pair_key'])][row['method']] = row
    for identity, methods in sorted(indexed.items()):
        info = dict(zip(PROTOCOL['pair_key'], identity))
        if len(methods) != 2:
            result['incomplete_pairs'].append(dict(**info, available=list(methods), missing=[m for m in ('manual','voice') if m not in methods]))
            continue
        manual, voice = methods['manual'], methods['voice']
        if manual['order'] == voice['order']:
            raise ValueError(f'{identity}: paired attempts must have distinct order')
        complete = manual['status'] == voice['status'] == 'completed'
        result['pairs'].append(dict(**info, completed_pair=complete,
            manual_status=manual['status'], voice_status=voice['status'],
            manual_record_id=manual['record_id'], voice_record_id=voice['record_id'],
            voice_first=voice['order'] < manual['order'],
            deltas={key: voice[key]-manual[key] if complete and voice[key] is not None and manual[key] is not None else None for key in METRICS}))
    result['paired_summary'] = []
    for source in ('human','synthetic'):
        for role in ('paramedic','hospital'):
            group = [p for p in result['pairs'] if p['source']==source and p['participant_role']==role]
            complete = [p for p in group if p['completed_pair']]
            result['paired_summary'].append(dict(source=source, participant_role=role, pairs=len(group),
                completed_pairs=len(complete), incomplete_outcome_pairs=len(group)-len(complete),
                participants=len({p['participant_id'] for p in group}),
                voice_first=sum(p['voice_first'] for p in group), manual_first=sum(not p['voice_first'] for p in group),
                deltas={key: stats([p['deltas'][key] for p in complete if p['deltas'][key] is not None], len(complete)) for key in METRICS}))
    if result['incomplete_pairs']:
        result['warnings'].append('Unpaired attempts are preserved and excluded from paired differences.')
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
        '| 출처 | 역할 | 방식 | 전체 | 완료 | 실패 | 중단 | 완료시간 평균(ms) |',
        '|---|---|---|---:|---:|---:|---:|---:|']
    def fmt(value):
        return '미측정' if value is None else f'{value:.2f}'
    for g in report['groups']:
        lines.append(f"| {g['source']} | {g['participant_role']} | {g['method']} | {g['attempts']} | {g['completed']} | {g['failed']} | {g['abandoned']} | {fmt(g['completed_metrics']['elapsed_ms']['mean'])} |")
    lines += ['', '차이는 음성 − 수동이다. 같은 참여자·역할·과제·반복의 두 방식이 모두 완료된 경우만 비교한다.', '',
              '| 출처 | 역할 | 완성된 짝 | 참여자 | 시간차 평균(ms) | 오류차 | 수정차 | 터치차 | 병원 검토차(ms) |',
              '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for g in report['paired_summary']:
        values = [fmt(g['deltas'][key]['mean']) for key in ('elapsed_ms','error_count','correction_count','touch_count','hospital_review_ms')]
        lines.append(f"| {g['source']} | {g['participant_role']} | {g['completed_pairs']} | {g['participants']} | " + ' | '.join(values) + ' |')
    lines += ['', f"짝누락 {len(report['incomplete_pairs'])}건. 각 지표의 유효 표본·결측·p95·개별 최대 악화는 report.json에 보존한다.", '',
              '병원 부담 감소는 문의 수만으로 판정하지 않는다. 실제 검토시간이 비어 있으면 미측정이다.',
              '학생 역할 시험은 현직 구급대원·의료진 검증으로 표현하지 않는다. 합성 자료는 실제 사용성과 합산하지 않는다.',
              '여기 표시한 값은 기술통계이며 임상 효과·모집단 효과를 입증하지 않는다.', '']
    return '\n'.join(lines)


def run(input_path, output):
    input_path = Path(input_path)
    files = [Path(__file__), Path(__file__).with_name('__init__.py'), Path(__file__).with_name('__main__.py')]
    source_bytes = {path.name: path.read_bytes() for path in files}
    before = input_path.read_bytes()
    records = load_records(input_path)
    report = evaluate(records)
    if input_path.read_bytes() != before:
        raise ValueError('input changed during evaluation')
    if any(path.read_bytes() != source_bytes[path.name] for path in files):
        raise ValueError('evaluation source changed during evaluation')
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
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            print(prepare(args.output))
        else:
            report = run(args.input, args.output)
            print(json.dumps({key: report[key] for key in ('measurement_status','actual_participants','actual_attempts','synthetic_attempts')}, ensure_ascii=False))
    except (ValueError, OSError) as error:
        parser.exit(2, f'{type(error).__name__}: {error}\n')
    return 0
