"""R1 맞춤 뒤 서버 음성 경로(voice_terms + voice_r1, 세션 반영 단계와 같음)의 정확도를 voice_lab v1과 함께 잰다.

python -m voice_lab.evaluate_server --output simulation_results/voice_server_r1_validation_20261030

방식(실행 전 고정):
- 서버는 voice_input._extract(세션 반영과 같은 추출)를 쓰고, 서버 칸 이름을 voice_lab 칸 이름으로 바꿔 채점한다.
  AVPU가 없고 의식 상태가 impaired이면 voice_lab의 'impaired'와 같게 본다. age_category는 age_group으로 본다.
- 명령은 parse_command로 먼저 판단하고(수정·추가면 그 내용을 추출), 앱이 명령 모드를 고르지 않는 자동 판단으로 잰다.
- 검증 문장 v2(cases_validation_v2.json)는 처음 쓰는 문장이다. voice_lab v1은 고정본이며 참고로 같이 잰다.
- 이전 검증 문장(cases_holdout.json)과 개발 문장은 이번 서버 수정에 참고했으므로 '개발 확인'으로만 보고한다.
실행 전에 소스와 문장 파일의 해시를 잠금 파일로 쓴다. 기존 결과가 있으면 덮어쓰지 않는다.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

from server import voice_input, voice_terms

from . import evaluate, extract

ROOT = evaluate.ROOT
SOURCES = ('server/voice_terms.py', 'server/voice_r1.py', 'server/voice_input.py', 'config/paramedic_voice_terms.json',
           'voice_lab/terms.py', 'voice_lab/extract.py', 'voice_lab/evaluate.py', 'voice_lab/evaluate_server.py',
           'voice_lab/cases_dev.json', 'voice_lab/cases_holdout.json', 'voice_lab/cases_validation_v2.json')
STAGES = (('validation_v2', 'voice_lab/cases_validation_v2.json', '검증 문장 v2(처음 쓰는 문장)'),
          ('dev_check_holdout_v1', 'voice_lab/cases_holdout.json', '개발 확인: 이전 검증 문장(이번 수정에 참고함)'),
          ('dev_check_dev', 'voice_lab/cases_dev.json', '개발 확인: 개발 문장'))
TO_LAB = {'systolic_bp': 'vitals.sbp', 'diastolic_bp': 'vitals.dbp', 'pulse': 'vitals.hr', 'respiratory_rate': 'vitals.rr',
          'spo2': 'vitals.spo2', 'temperature': 'vitals.temp', 'glucose': 'vitals.glucose', 'pain_score': 'vitals.nrs',
          'gcs': 'vitals.gcs', 'age_category': 'age_group'}
SAME = ('age', 'onset_at', 'breathing', 'chief_complaint', 'pre_ktas', 'stemi_suspected', 'injury_region', 'labor',
        'pregnancy_weeks', 'seizure_ongoing', 'bleeding_risk', 'high_risk_mechanism')
INTENTS = {'review': 'readback', 'undo': 'undo', 'submit': 'send', 'missing': 'missing', 'confirm': 'confirm'}


def to_lab(fields):
    out = {TO_LAB[k]: v for k, v in fields.items() if k in TO_LAB}
    out.update({k: fields[k] for k in SAME if k in fields})
    if 'avpu' in fields:
        out['avpu'] = fields['avpu']
    elif fields.get('consciousness') == 'impaired':
        out['avpu'] = 'impaired'
    return out


def to_server(draft):
    """voice_lab 초안 칸을 서버 칸 이름으로(세션 문맥용)."""
    back = {v: k for k, v in TO_LAB.items()}
    fields = {back.get(k, k): v for k, v in (draft or {}).items() if k != 'vitals'}
    fields.update({back['vitals.' + k]: v for k, v in (draft or {}).get('vitals', {}).items() if 'vitals.' + k in back})
    return fields


def server_result(case, now):
    command = voice_terms.parse_command(case['utterance'])
    intent = INTENTS.get(command['action'])
    if intent:
        return dict(intent=intent, changes={}, send_requested=intent == 'send')
    content = command['text'] if command['action'] in ('edit', 'add') else case['utterance']
    out = voice_input._extract(content, now, {'fields': json.dumps(to_server(case.get('draft')))})
    fields = to_lab(out['fields'])
    draft = case.get('draft') or {}
    changes = {k: v for k, v in fields.items() if extract._get(draft, k) != v}
    return dict(intent='update' if fields else 'none', changes=changes, send_requested=False)


def lab_result(case, now):
    result = extract.propose_changes(case.get('draft'), case['utterance'], now)
    return dict(intent=result['intent'], changes={c['field']: c['after'] for c in result['changes']},
                send_requested=result['send_requested'])


def score(case, result):
    expect = case['expect']
    fields = {k: v for k, v in expect.items() if k not in evaluate.META}
    predicted = result['changes']
    row = dict(id=case['id'], utterance=case['utterance'], correct=[], wrong=[], missing=[],
               spurious=[f for f in predicted if f not in fields], predicted=predicted, intent=result['intent'],
               expected_intent=expect.get('intent', 'update'))
    for field, value in fields.items():
        if field not in predicted:
            row['missing'].append(field)
        elif evaluate._same(predicted[field], value):
            row['correct'].append(field)
        else:
            row['wrong'].append(dict(field=field, expected=value, predicted=predicted[field]))
    row['intent_ok'] = row['intent'] == row['expected_intent']
    row['send_ok'] = 'send_requested' not in expect or result['send_requested'] == expect['send_requested']
    row['rejected_ok'] = True
    row['exact'] = not (row['wrong'] or row['missing'] or row['spurious']) and row['intent_ok'] and row['send_ok']
    return row


def run_stage(path):
    data = json.loads((ROOT / path).read_text(encoding='utf-8'))
    out = {}
    for who, fn in (('server', server_result), ('voice_lab_v1', lab_result)):
        rows = [score(case, fn(case, data['now'])) for case in data['cases']]
        out[who] = dict(metrics=evaluate.summarize(rows), rows=rows)
    return out


def _pct(value):
    return evaluate._pct(value)


def markdown(report):
    lines = ['# R1 맞춤 뒤 서버 음성 경로 정확도', '', f"생성 {report['generated_at']} · 잠금 `{report['lock']}`", '']
    for key, _, label in STAGES:
        block = report['stages'][key]
        s, l = block['server']['metrics'], block['voice_lab_v1']['metrics']
        lines += [f'## {label}', '', '| 지표 | 서버(R1 맞춤) | voice_lab v1 |', '|---|---:|---:|',
                  f"| 문장 완전 일치 | {s['exact_cases']}/{s['cases']} | {l['exact_cases']}/{l['cases']} |",
                  f"| 칸 재현율 | {s['fields']['correct']}/{s['fields']['expected']} ({_pct(s['fields']['recall'])}) | "
                  f"{l['fields']['correct']}/{l['fields']['expected']} ({_pct(l['fields']['recall'])}) |",
                  f"| 칸 정밀도 | {_pct(s['fields']['precision'])} (틀림 {s['fields']['wrong']}, 남는 칸 {s['fields']['spurious']}) | "
                  f"{_pct(l['fields']['precision'])} (틀림 {l['fields']['wrong']}, 남는 칸 {l['fields']['spurious']}) |",
                  f"| 명령 구분 | {_pct(s['intent_accuracy'])} | {_pct(l['intent_accuracy'])} |"]
        for name in s['groups']:
            gs, gl = s['groups'][name], l['groups'][name]
            lines.append(f"| {name} 재현율·정밀도 | {_pct(gs['recall'])} · {_pct(gs['precision'])} | "
                         f"{_pct(gl['recall'])} · {_pct(gl['precision'])} |")
        lines.append('')
        if key == 'validation_v2':
            lines += ['### 서버가 틀린 문장', '']
            for r in block['server']['rows']:
                if r['exact']:
                    continue
                problems = [f"틀림 {w['field']}: {w['expected']} → {w['predicted']}" for w in r['wrong']]
                problems += [f'못 찾음 {x}' for x in r['missing']] + [f"남는 칸 {x}={r['predicted'][x]}" for x in r['spurious']]
                if not r['intent_ok']:
                    problems.append(f"명령 기대 {r['expected_intent']} → {r['intent']}")
                lines.append(f"- `{r['id']}` {r['utterance']} — " + '; '.join(problems))
            lines.append('')
    lines += ['## 해석할 때 주의', '',
              '- 문장은 합성이고 음성 인식 오류가 없다. 검증 문장 v2는 서버 수정 뒤 처음 썼지만 같은 작성자가 썼다.',
              '- 서버 명령은 원래 앱이 명령 모드를 고르는 설계다. 여기서는 문장만으로 자동 판단해 명령 인식이 낮게 나올 수 있다.',
              '- 서버는 불확실·상충·대략값을 일부러 비워 두는 보수적 설계다. voice_lab v1은 고르는 쪽이라 재현율과 정밀도의 성격이 다르다.']
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
    lock_path.write_text(json.dumps(dict(schema_version=1, experiment='voice_server_r1_20261030', source_sha256=hashes,
                                         locked_at=time.strftime('%Y-%m-%d %H:%M:%S')), ensure_ascii=False, indent=1),
                         encoding='utf-8')
    stages = {key: run_stage(path) for key, path, _ in STAGES}
    if {name: _sha256(ROOT / name) for name in SOURCES} != hashes:
        raise RuntimeError('source changed during the evaluation')
    report = dict(generated_at=time.strftime('%Y-%m-%d %H:%M:%S'), lock=lock_path.name, source_sha256=hashes, stages=stages)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    md_path.write_text(markdown(report), encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    report = run(args.output)
    for key, _, _ in STAGES:
        for who in ('server', 'voice_lab_v1'):
            m = report['stages'][key][who]['metrics']
            print(key, who, f"exact {m['exact_cases']}/{m['cases']}", 'recall', _pct(m['fields']['recall']),
                  'precision', _pct(m['fields']['precision']), 'intent', _pct(m['intent_accuracy']))


if __name__ == '__main__':
    main()
