"""서버 음성 경로의 수정 전·후를 같은 검증 문장으로 잰다(문장 파일을 인자로 받는 일반판).

python -m voice_lab.evaluate_server_ab --cases voice_lab/cases_validation_v4.json --label before --output simulation_results/voice_server_round3_before_20261030
python -m voice_lab.evaluate_server_ab --cases voice_lab/cases_validation_v4.json --label after --output simulation_results/voice_server_round3_after_20261030

- 채점·칸 변환은 evaluate_server와 같다(이전 결과 재현을 위해 그 파일과 evaluate_server_v3는 고치지 않는다).
- 잠금 파일에 서버 소스와 문장 파일의 해시를 남긴다. 기존 결과가 있으면 덮어쓰지 않는다.
- 문장은 수정 전에 작성·고정한다. 결과를 보고 수정 내용을 바꾸지 않는다.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

from . import evaluate, evaluate_server, evaluate_server_v3

ROOT = evaluate.ROOT
SOURCES = ('server/voice_terms.py', 'server/voice_r1.py', 'server/voice_input.py', 'config/paramedic_voice_terms.json',
           'voice_lab/extract.py', 'voice_lab/terms.py', 'voice_lab/evaluate_server.py', 'voice_lab/evaluate_server_ab.py')


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(cases, label, output):
    output = Path(output).resolve()
    json_path, md_path, lock_path = (Path(str(output) + s) for s in ('.json', '.md', '_lock.json'))
    for p in (json_path, md_path, lock_path):
        if p.exists():
            raise FileExistsError(f'{p} already exists')
    names = SOURCES + (cases,)
    hashes = {name: _sha256(ROOT / name) for name in names}
    lock_path.write_text(json.dumps(dict(schema_version=1, experiment='voice_server_ab_20261030', label=label, cases=cases,
                                         source_sha256=hashes, locked_at=time.strftime('%Y-%m-%d %H:%M:%S')),
                                    ensure_ascii=False, indent=1), encoding='utf-8')
    result = evaluate_server.run_stage(cases)
    if {name: _sha256(ROOT / name) for name in names} != hashes:
        raise RuntimeError('source changed during the evaluation')
    report = dict(label=label, cases_file=cases, generated_at=time.strftime('%Y-%m-%d %H:%M:%S'), lock=lock_path.name,
                  source_sha256=hashes, **result)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    md_path.write_text(evaluate_server_v3.markdown(report).replace('검증 문장 v3', f'검증 문장 `{cases}`')
                       .replace('문장 단위 판단 수정', '남은 문제 수정'), encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', required=True)
    parser.add_argument('--label', choices=('before', 'after'), required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    report = run(args.cases, args.label, args.output)
    for who in ('server', 'voice_lab_v1'):
        m = report[who]['metrics']
        print(args.label, who, f"exact {m['exact_cases']}/{m['cases']}", 'recall', evaluate._pct(m['fields']['recall']),
              'precision', evaluate._pct(m['fields']['precision']))


if __name__ == '__main__':
    main()
