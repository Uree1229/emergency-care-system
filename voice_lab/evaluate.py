"""음성 초안 추출의 정확도 평가.

개발 문장(cases_dev.json)으로 추출기를 다듬고, 검증 문장(cases_holdout.json)은 다듬기를 마친 뒤 한 번만 평가한다.

python -m voice_lab.evaluate dev --output simulation_results/voice_lab_extract_dev_20261030
python -m voice_lab.evaluate holdout --output simulation_results/voice_lab_extract_validation_20261030

검증은 실행 전에 소스·문장 파일의 해시를 잠금 파일로 먼저 쓴다. 기존 결과가 있으면 덮어쓰지 않는다.
한계: 문장은 합성이고 음성 인식 오류를 넣지 않았다. 실제 구급대원 발화로 잰 정확도가 아니다.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

from . import extract

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ('voice_lab/__init__.py', 'voice_lab/terms.py', 'voice_lab/extract.py', 'voice_lab/evaluate.py')
CASES = {'dev': 'voice_lab/cases_dev.json', 'holdout': 'voice_lab/cases_holdout.json'}
META = ('intent', 'send_requested', 'rejected')
LIMITATIONS = (
    '문장은 합성이다. 실제 구급대원 발화와 말버릇, 사투리, 끊김이 들어 있지 않다.',
    '음성 인식 오류(소음, 동음이의, 띄어쓰기 오류)를 넣지 않았다. 실제 정확도는 이보다 낮을 수 있다.',
    '개발 문장과 검증 문장을 같은 작성자가 썼다. 검증 문장은 추출기를 다듬은 뒤에 썼지만 완전한 맹검은 아니다.',
    '정확도는 추출 초안 기준이다. 화면 확인에서 사람이 고친다는 전제이며, 임상 판단의 정확성을 뜻하지 않는다.',
)


def _group(field):
    if field in extract.REQUIRED:
        return '필수 5칸'
    if field.startswith('vitals.'):
        return '활력징후'
    return '나이·발생시각·2차 고려사항'


def _same(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(a - b) < 1e-9
    return a == b


def score_case(case, now):
    result = extract.propose_changes(case.get('draft'), case['utterance'], now)
    predicted = {c['field']: c['after'] for c in result['changes']}
    expect = case['expect']
    fields = {k: v for k, v in expect.items() if k not in META}
    row = dict(id=case['id'], utterance=case['utterance'], correct=[], wrong=[], missing=[],
               spurious=[f for f in predicted if f not in fields])
    for field, value in fields.items():
        if field not in predicted:
            row['missing'].append(field)
        elif _same(predicted[field], value):
            row['correct'].append(field)
        else:
            row['wrong'].append(dict(field=field, expected=value, predicted=predicted[field]))
    row['intent_ok'] = result['intent'] == expect.get('intent', 'update')
    row['send_ok'] = 'send_requested' not in expect or result['send_requested'] == expect['send_requested']
    rejected = sorted(r['field'] for r in result['rejected'])
    row['rejected_ok'] = 'rejected' not in expect or rejected == sorted(expect['rejected'])
    row['exact'] = not (row['wrong'] or row['missing'] or row['spurious']) and row['intent_ok'] and row['send_ok'] \
        and row['rejected_ok']
    row.update(intent=result['intent'], expected_intent=expect.get('intent', 'update'), predicted=predicted,
               ambiguities=result['ambiguities'], rejected=rejected)
    return row


def summarize(rows):
    def counts(select):
        correct = sum(1 for r in rows for f in r['correct'] if select(f))
        wrong = sum(1 for r in rows for w in r['wrong'] if select(w['field']))
        missing = sum(1 for r in rows for f in r['missing'] if select(f))
        spurious = sum(1 for r in rows for f in r['spurious'] if select(f))
        expected = correct + wrong + missing
        return dict(expected=expected, correct=correct, wrong=wrong, missing=missing, spurious=spurious,
                    recall=correct / expected if expected else None,
                    precision=correct / (correct + wrong + spurious) if correct + wrong + spurious else None)
    groups = {name: counts(lambda f, name=name: _group(f) == name)
              for name in ('필수 5칸', '활력징후', '나이·발생시각·2차 고려사항')}
    return dict(cases=len(rows), exact_cases=sum(r['exact'] for r in rows),
                exact_rate=sum(r['exact'] for r in rows) / len(rows),
                intent_accuracy=sum(r['intent_ok'] for r in rows) / len(rows),
                fields=counts(lambda f: True), groups=groups)


def _pct(value):
    return '—' if value is None else f'{value * 100:.1f}%'


def markdown(report):
    m = report['metrics']
    f = m['fields']
    lines = [f"# 음성 초안 추출 정확도 ({'개발 문장' if report['stage'] == 'dev' else '검증 문장'})", '',
             f"생성 {report['generated_at']} · 문장 {m['cases']}개 · 문장 파일 `{report['cases_file']}`", '',
             '| 지표 | 값 |', '|---|---:|',
             f"| 문장 완전 일치(모든 칸·명령 맞음, 남는 칸 없음) | {m['exact_cases']}/{m['cases']} ({_pct(m['exact_rate'])}) |",
             f"| 칸 재현율(말한 칸을 맞게 채움) | {f['correct']}/{f['expected']} ({_pct(f['recall'])}) |",
             f"| 칸 정밀도(채운 칸 중 맞은 칸) | {_pct(f['precision'])} (틀린 값 {f['wrong']}, 남는 칸 {f['spurious']}) |",
             f"| 명령 구분(수정·되돌리기·읽어 주기·전송) | {_pct(m['intent_accuracy'])} |", '',
             '| 묶음 | 기대 칸 | 맞음 | 틀린 값 | 못 찾음 | 남는 칸 | 재현율 | 정밀도 |', '|---|---:|---:|---:|---:|---:|---:|---:|']
    for name, g in m['groups'].items():
        lines.append(f"| {name} | {g['expected']} | {g['correct']} | {g['wrong']} | {g['missing']} | {g['spurious']} | "
                     f"{_pct(g['recall'])} | {_pct(g['precision'])} |")
    failures = [r for r in report['rows'] if not r['exact']]
    lines += ['', f'## 틀린 문장 ({len(failures)}개)', '']
    for r in failures:
        problems = [f"틀림 {w['field']}: 기대 {w['expected']} → {w['predicted']}" for w in r['wrong']]
        problems += [f'못 찾음 {x}' for x in r['missing']] + [f"남는 칸 {x}={r['predicted'][x]}" for x in r['spurious']]
        if not r['intent_ok']:
            problems.append(f"명령 기대 {r['expected_intent']} → {r['intent']}")
        if not r['send_ok']:
            problems.append('전송 요청 구분 틀림')
        if not r['rejected_ok']:
            problems.append(f"범위 밖 거부 기대와 다름(결과 {r['rejected']})")
        lines.append(f"- `{r['id']}` {r['utterance']} — " + '; '.join(problems))
    lines += ['', '## 한계', ''] + [f'- {x}' for x in LIMITATIONS]
    return '\n'.join(lines) + '\n'


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(stage, output):
    output = Path(output).resolve()
    json_path, md_path, lock_path = (Path(str(output) + s) for s in ('.json', '.md', '_lock.json'))
    for p in (json_path, md_path, lock_path):
        if p.exists():
            raise FileExistsError(f'{p} already exists')
    names = SOURCES + (CASES[stage],)
    hashes = {name: _sha256(ROOT / name) for name in names}
    if stage == 'holdout':
        lock_path.write_text(json.dumps(dict(schema_version=1, experiment='voice_lab_extract_20261030', stage=stage,
                                             source_sha256=hashes, locked_at=time.strftime('%Y-%m-%d %H:%M:%S')),
                                        ensure_ascii=False, indent=1), encoding='utf-8')
    data = json.loads((ROOT / CASES[stage]).read_text(encoding='utf-8'))
    rows = [score_case(case, data['now']) for case in data['cases']]
    if {name: _sha256(ROOT / name) for name in names} != hashes:
        raise RuntimeError('source changed during the evaluation')
    report = dict(stage=stage, generated_at=time.strftime('%Y-%m-%d %H:%M:%S'), cases_file=CASES[stage],
                  source_sha256=hashes, lock=lock_path.name if stage == 'holdout' else None,
                  metrics=summarize(rows), rows=rows)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    md_path.write_text(markdown(report), encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=sorted(CASES))
    parser.add_argument('--output', help='결과 파일 접두어. 없으면 화면에만 요약한다')
    args = parser.parse_args(argv)
    if args.output:
        report = run(args.stage, args.output)
    else:
        data = json.loads((ROOT / CASES[args.stage]).read_text(encoding='utf-8'))
        rows = [score_case(case, data['now']) for case in data['cases']]
        report = dict(stage=args.stage, generated_at=time.strftime('%Y-%m-%d %H:%M:%S'), cases_file=CASES[args.stage],
                      metrics=summarize(rows), rows=rows)
        print(markdown(report))
    m = report['metrics']
    print(f"exact {m['exact_cases']}/{m['cases']} · recall {_pct(m['fields']['recall'])} · "
          f"precision {_pct(m['fields']['precision'])} · intent {_pct(m['intent_accuracy'])}")


if __name__ == '__main__':
    main()
