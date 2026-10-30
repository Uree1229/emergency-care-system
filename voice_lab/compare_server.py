"""서버 음성 추출기(server/voice_terms.py)와 voice_lab 추출기를 같은 문장으로 비교한다. 서버 파일은 수정하지 않는다.

python -m voice_lab.compare_server --output simulation_results/voice_compare_server_20261030

비교 방식(실행 전 고정):
- 두 추출기의 칸 체계가 달라 '공통 칸'만 같은 기준으로 채점한다.
  의식은 서버 단위(alert·impaired·unresponsive·unknown)로 맞춘다. voice_lab의 V·P는 impaired로 본다.
  연령대는 성인·소아만 공통이다(서버의 '60대' 등은 성인·소아로 바꾼다. 10대는 판단하지 않는다).
  주증상은 서버 증상 목록을 R1 10묶음으로 바꾸는 표(SYMPTOM_GROUP)를 덧씌워 비교한다. 서버 자체 기능이 아니다.
- R1에만 있는 칸(Pre-KTAS 등급, 2차 고려사항, 통증 점수, GCS, 임신·신생아 구분, 발생 시각 '모름')은 서버 미지원으로 따로 센다.
- 서버는 앱이 받아쓰기·명령 모드를 고른다. 여기서는 parse_command로 먼저 명령을 판단하고,
  수정·추가 명령이면 그 내용을, 아니면 문장 전체를 extract로 넣는다(서버에 유리한 자동 모드).
- 서버 추출기에는 개발·검증 문장 83개가 모두 처음 보는 문장이다. voice_lab은 검증 문장 40개만 처음 본다.
실행 전에 두 추출기 소스와 문장 파일의 해시를 잠금 파일로 쓴다. 기존 결과가 있으면 덮어쓰지 않는다.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

from server import voice_terms
from server.service import ServiceError

from . import evaluate, extract

ROOT = evaluate.ROOT
SOURCES = ('server/voice_terms.py', 'config/paramedic_voice_terms.json', 'voice_lab/terms.py', 'voice_lab/extract.py',
           'voice_lab/compare_server.py', 'voice_lab/cases_dev.json', 'voice_lab/cases_holdout.json')
SERVER_VITALS = {'systolic_bp': 'vitals.sbp', 'diastolic_bp': 'vitals.dbp', 'pulse': 'vitals.hr', 'spo2': 'vitals.spo2',
                 'temperature': 'vitals.temp', 'respiratory_rate': 'vitals.rr', 'glucose': 'vitals.glucose'}
CONSCIOUSNESS = {'A': 'alert', 'V': 'impaired', 'P': 'impaired', 'U': 'unresponsive', 'impaired': 'impaired',
                 'unknown': 'unknown'}
# 서버 표준 증상 → R1 주증상 묶음. 두통·어지럼·발열처럼 R1 묶음에 없는 증상은 '기타'로 둔다.
SYMPTOM_GROUP = {'흉통': 'cardiac', '심계항진': 'cardiac', '실신': 'cardiac', '호흡곤란': 'dyspnea', '객혈': 'bleeding',
                 '출혈': 'bleeding', '복통': 'abdominal', '구토': 'abdominal', '오심': 'abdominal', '설사': 'abdominal',
                 '경련': 'altered_seizure', '의식저하': 'altered_seizure', '마비': 'stroke', '외상': 'trauma',
                 '두통': 'other', '어지럼': 'other', '발열': 'other', '기침': 'other', '요통': 'other', '발진': 'other',
                 '부종': 'other'}
COMMON_VITALS = tuple(SERVER_VITALS.values())
INTENTS = {'review': 'readback', 'undo': 'undo', 'submit': 'send', 'confirm': 'confirm'}


def _comparable(field, value):
    """기대값을 공통 칸으로 옮긴다. 공통 칸이 아니면 None."""
    if field == 'avpu':
        return 'consciousness', CONSCIOUSNESS[value]
    if field in ('breathing', 'age', 'chief_complaint') or field in COMMON_VITALS:
        return field, value
    if field == 'age_group' and value in ('adult', 'child'):
        return field, value
    if field == 'onset_at' and isinstance(value, int):
        return field, value
    return None


def split_expect(expect):
    common, r1_only = {}, {}
    for field, value in expect.items():
        if field in evaluate.META:
            continue
        moved = _comparable(field, value)
        if moved:
            common[moved[0]] = moved[1]
        else:
            r1_only[field] = value
    return common, r1_only


def _server_age_group(fields):
    if 'age' in fields:
        return 'child' if fields['age'] < 15 else 'adult'
    decade = fields.get('age_group')
    if decade:
        start = int(decade[:-1])
        return 'child' if start == 0 else None if start == 10 else 'adult'
    return None


def server_predict(case, now):
    text = case['utterance']
    command = voice_terms.parse_command(text)
    intent = INTENTS.get(command['action'])
    if intent:
        return dict(intent=intent, fields={}, warnings=[])
    content = command['text'] if command['action'] in ('edit', 'add') else text
    try:
        out = voice_terms.extract(content, now)
    except ServiceError as error:
        return dict(intent='none', fields={}, warnings=[str(error)])
    raw, fields = out['fields'], {}
    for key in ('consciousness', 'breathing', 'age', 'onset_at'):
        if key in raw:
            fields[key] = raw[key]
    for key, name in SERVER_VITALS.items():
        if key in raw:
            fields[name] = raw[key]
    group = _server_age_group(raw)
    if group:
        fields['age_group'] = group
    grouped = [SYMPTOM_GROUP[s] for s in raw.get('symptoms', []) if s in SYMPTOM_GROUP]
    specific = [g for g in grouped if g != 'other']
    if grouped:
        fields['chief_complaint'] = (specific or grouped)[0]
    draft = case.get('draft') or {}
    changes = {k: v for k, v in fields.items() if extract._get(draft, k) != v}
    return dict(intent='update' if fields else 'none', fields=changes, warnings=out['warnings'])


def lab_predict(case, now):
    result = extract.propose_changes(case.get('draft'), case['utterance'], now)
    fields = {}
    for change in result['changes']:
        moved = _comparable(change['field'], change['after']) if change['after'] is not None else None
        if moved:
            fields[moved[0]] = moved[1]
    return dict(intent=result['intent'], fields=fields,
                r1_fields={c['field']: c['after'] for c in result['changes']})


def _score(expected, predicted):
    row = dict(correct=[], wrong=[], missing=[], spurious=[f for f in predicted if f not in expected])
    for field, value in expected.items():
        if field not in predicted:
            row['missing'].append(field)
        elif evaluate._same(predicted[field], value):
            row['correct'].append(field)
        else:
            row['wrong'].append(dict(field=field, expected=value, predicted=predicted[field]))
    return row


def _totals(rows, key):
    t = dict(correct=0, wrong=0, missing=0, spurious=0)
    for row in rows:
        for name in t:
            t[name] += len(row[key][name])
    expected = t['correct'] + t['wrong'] + t['missing']
    filled = t['correct'] + t['wrong'] + t['spurious']
    return t | dict(expected=expected, recall=t['correct'] / expected if expected else None,
                    precision=t['correct'] / filled if filled else None)


def compare(cases, now):
    rows = []
    for case in cases:
        common, r1_only = split_expect(case['expect'])
        server, lab = server_predict(case, now), lab_predict(case, now)
        lab_r1 = {k: v for k, v in lab['r1_fields'].items() if k in r1_only}
        expected_intent = case['expect'].get('intent', 'update')
        rows.append(dict(id=case['id'], utterance=case['utterance'], common=common, r1_only=r1_only,
                         server=_score(common, server['fields']), lab=_score(common, lab['fields']),
                         lab_r1=_score(r1_only, lab_r1), server_fields=server['fields'], lab_fields=lab['fields'],
                         server_warnings=server['warnings'], expected_intent=expected_intent,
                         server_intent=server['intent'], lab_intent=lab['intent']))
    command_rows = [r for r in rows if r['expected_intent'] != 'update']
    return dict(rows=rows, server=_totals(rows, 'server'), lab=_totals(rows, 'lab'), lab_r1=_totals(rows, 'lab_r1'),
                r1_only_expected=sum(len(r['r1_only']) for r in rows),
                commands=dict(cases=len(command_rows),
                              server=sum(r['server_intent'] == r['expected_intent'] for r in command_rows),
                              lab=sum(r['lab_intent'] == r['expected_intent'] for r in command_rows)),
                by_field=_by_field(rows))


def _by_field(rows):
    out = {}
    for who in ('server', 'lab'):
        for row in rows:
            for field in row['common']:
                entry = out.setdefault(field, {}).setdefault(who, dict(expected=0, correct=0))
                entry['expected'] += 1
                entry['correct'] += field in row[who]['correct']
    return out


def _pct(value):
    return '—' if value is None else f'{value * 100:.1f}%'


def markdown(report):
    lines = ['# 서버 음성 추출기와 voice_lab 비교', '', f"생성 {report['generated_at']} · 서버 파일 무수정 · 잠금 `{report['lock']}`", '']
    labels = dict(dev='개발 문장 43개(서버에는 처음, voice_lab은 다듬은 문장)', holdout='검증 문장 40개(둘 다 처음)')
    for stage in ('holdout', 'dev'):
        block = report['stages'][stage]
        s, l = block['server'], block['lab']
        c = block['commands']
        lines += [f'## {labels[stage]}', '', '| 공통 칸 | 서버 추출기 | voice_lab |', '|---|---:|---:|',
                  f"| 재현율(말한 칸을 맞게 채움) | {s['correct']}/{s['expected']} ({_pct(s['recall'])}) | "
                  f"{l['correct']}/{l['expected']} ({_pct(l['recall'])}) |",
                  f"| 정밀도(채운 칸 중 맞은 칸) | {_pct(s['precision'])} (틀림 {s['wrong']}, 남는 칸 {s['spurious']}) | "
                  f"{_pct(l['precision'])} (틀림 {l['wrong']}, 남는 칸 {l['spurious']}) |",
                  f"| 명령 구분({c['cases']}문장) | {c['server']}/{c['cases']} | {c['lab']}/{c['cases']} |",
                  f"| R1 전용 칸({block['r1_only_expected']}개) | 미지원 | {block['lab_r1']['correct']}/{block['lab_r1']['expected']} "
                  f"({_pct(block['lab_r1']['recall'])}) |", '',
                  '| 칸 | 기대 | 서버 맞음 | voice_lab 맞음 |', '|---|---:|---:|---:|']
        for field, entry in sorted(block['by_field'].items()):
            lines.append(f"| {field} | {entry['server']['expected']} | {entry['server']['correct']} | {entry['lab']['correct']} |")
        lines.append('')
        if stage == 'holdout':
            lines += ['### 서버 추출기가 놓치거나 틀린 문장', '']
            for r in block['rows']:
                srv = r['server']
                if srv['wrong'] or srv['missing'] or srv['spurious']:
                    problems = [f"틀림 {w['field']}: {w['expected']} → {w['predicted']}" for w in srv['wrong']]
                    problems += [f'못 찾음 {x}' for x in srv['missing']]
                    problems += [f"남는 칸 {x}={r['server_fields'][x]}" for x in srv['spurious']]
                    note = f" · 서버 경고: {'; '.join(r['server_warnings'])}" if r['server_warnings'] else ''
                    lines.append(f"- `{r['id']}` {r['utterance']} — " + '; '.join(problems) + note)
            lines.append('')
    lines += ['## 해석할 때 주의', '',
              '- 주증상 비교는 서버 증상 목록에 R1 묶음 변환표를 덧씌운 결과다. 서버는 원래 주증상을 고르지 않는다.',
              '- 서버는 정정·질문·과거력 문장을 일부러 비워 두는 보수적 설계다. 재현율이 낮아도 잘못 채우지 않는 쪽을 택한 결과다.',
              '- 서버 명령은 앱이 명령 모드를 고르는 전제다. 여기서는 문장으로 자동 판단했으므로 실제 사용과 다를 수 있다.',
              '- 문장은 합성이고 음성 인식 오류가 없다. 두 추출기의 상대 비교로만 쓴다.']
    return '\n'.join(lines) + '\n'


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(output):
    output = Path(output).resolve()
    json_path, md_path, lock_path = (Path(str(output) + s) for s in ('.json', '.md', '_lock.json'))
    for p in (json_path, md_path, lock_path):
        if p.exists():
            raise FileExistsError(f'{p} already exists')
    hashes = {name: _sha256(ROOT / name) for name in SOURCES}
    lock_path.write_text(json.dumps(dict(schema_version=1, experiment='voice_compare_server_20261030', source_sha256=hashes,
                                         symptom_group=SYMPTOM_GROUP, locked_at=time.strftime('%Y-%m-%d %H:%M:%S')),
                                    ensure_ascii=False, indent=1), encoding='utf-8')
    stages = {}
    for stage in ('dev', 'holdout'):
        data = json.loads((ROOT / evaluate.CASES[stage]).read_text(encoding='utf-8'))
        stages[stage] = compare(data['cases'], data['now'])
    if {name: _sha256(ROOT / name) for name in SOURCES} != hashes:
        raise RuntimeError('source changed during the comparison')
    report = dict(generated_at=time.strftime('%Y-%m-%d %H:%M:%S'), lock=lock_path.name, source_sha256=hashes, stages=stages)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    md_path.write_text(markdown(report), encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    report = run(args.output)
    for stage in ('holdout', 'dev'):
        b = report['stages'][stage]
        print(stage, 'server recall', _pct(b['server']['recall']), 'precision', _pct(b['server']['precision']),
              '| lab recall', _pct(b['lab']['recall']), 'precision', _pct(b['lab']['precision']),
              '| commands', b['commands'])


if __name__ == '__main__':
    main()
