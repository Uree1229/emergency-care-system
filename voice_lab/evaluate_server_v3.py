"""문장 단위 판단 수정(1번)의 전후를 검증 문장 v3로 잰다. 수정 전과 수정 후에 각각 한 번씩 실행한다.

python -m voice_lab.evaluate_server_v3 --label before --output simulation_results/voice_server_scope_before_20261030
python -m voice_lab.evaluate_server_v3 --label after --output simulation_results/voice_server_scope_after_20261030

- 채점·칸 변환은 evaluate_server와 같다(그 파일은 이전 결과 재현을 위해 고치지 않는다).
- 잠금 파일에 서버 소스 해시를 남겨, 어떤 코드로 잰 결과인지 구분한다. 기존 결과가 있으면 덮어쓰지 않는다.
- v3 문장은 수정 전에 작성했다. 결과를 보고 수정 내용을 바꾸지 않는다.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

from . import evaluate, evaluate_server

ROOT = evaluate.ROOT
CASES = 'voice_lab/cases_validation_v3.json'
SOURCES = ('server/voice_terms.py', 'server/voice_r1.py', 'server/voice_input.py', 'config/paramedic_voice_terms.json',
           'voice_lab/extract.py', 'voice_lab/terms.py', 'voice_lab/evaluate_server.py', 'voice_lab/evaluate_server_v3.py', CASES)


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def markdown(report):
    s, l = report['server']['metrics'], report['voice_lab_v1']['metrics']
    lines = [f"# 서버 음성 경로 정확도: 문장 단위 판단 수정 {'전' if report['label'] == 'before' else '후'}", '',
             f"생성 {report['generated_at']} · 검증 문장 v3 {s['cases']}개 · 잠금 `{report['lock']}`", '',
             '| 지표 | 서버 | voice_lab v1(참고) |', '|---|---:|---:|',
             f"| 문장 완전 일치 | {s['exact_cases']}/{s['cases']} | {l['exact_cases']}/{l['cases']} |",
             f"| 칸 재현율 | {s['fields']['correct']}/{s['fields']['expected']} ({evaluate._pct(s['fields']['recall'])}) | "
             f"{l['fields']['correct']}/{l['fields']['expected']} ({evaluate._pct(l['fields']['recall'])}) |",
             f"| 칸 정밀도 | {evaluate._pct(s['fields']['precision'])} (틀림 {s['fields']['wrong']}, 남는 칸 {s['fields']['spurious']}) | "
             f"{evaluate._pct(l['fields']['precision'])} (틀림 {l['fields']['wrong']}, 남는 칸 {l['fields']['spurious']}) |"]
    for name in s['groups']:
        gs, gl = s['groups'][name], l['groups'][name]
        lines.append(f"| {name} 재현율·정밀도 | {evaluate._pct(gs['recall'])} · {evaluate._pct(gs['precision'])} | "
                     f"{evaluate._pct(gl['recall'])} · {evaluate._pct(gl['precision'])} |")
    lines += ['', '## 서버가 틀린 문장', '']
    for r in report['server']['rows']:
        if r['exact']:
            continue
        problems = [f"틀림 {w['field']}: {w['expected']} → {w['predicted']}" for w in r['wrong']]
        problems += [f'못 찾음 {x}' for x in r['missing']] + [f"남는 칸 {x}={r['predicted'][x]}" for x in r['spurious']]
        lines.append(f"- `{r['id']}` {r['utterance']} — " + '; '.join(problems))
    return '\n'.join(lines) + '\n'


def run(label, output):
    output = Path(output).resolve()
    json_path, md_path, lock_path = (Path(str(output) + s) for s in ('.json', '.md', '_lock.json'))
    for p in (json_path, md_path, lock_path):
        if p.exists():
            raise FileExistsError(f'{p} already exists')
    hashes = {name: _sha256(ROOT / name) for name in SOURCES}
    lock_path.write_text(json.dumps(dict(schema_version=1, experiment='voice_server_scope_20261030', label=label,
                                         source_sha256=hashes, locked_at=time.strftime('%Y-%m-%d %H:%M:%S')),
                                    ensure_ascii=False, indent=1), encoding='utf-8')
    result = evaluate_server.run_stage(CASES)
    if {name: _sha256(ROOT / name) for name in SOURCES} != hashes:
        raise RuntimeError('source changed during the evaluation')
    report = dict(label=label, generated_at=time.strftime('%Y-%m-%d %H:%M:%S'), lock=lock_path.name,
                  source_sha256=hashes, **result)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    md_path.write_text(markdown(report), encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', choices=('before', 'after'), required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    report = run(args.label, args.output)
    for who in ('server', 'voice_lab_v1'):
        m = report[who]['metrics']
        print(args.label, who, f"exact {m['exact_cases']}/{m['cases']}", 'recall', evaluate._pct(m['fields']['recall']),
              'precision', evaluate._pct(m['fields']['precision']))


if __name__ == '__main__':
    main()
