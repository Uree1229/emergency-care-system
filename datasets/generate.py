"""배정 6천 건·이벤트 6백 건·서버 시나리오를 재현 가능한 압축 JSONL로 생성."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from .allocation import golden_cases, make_case
from .episodes import make_episode
from .io import write_jsonl
from .profiles import ALLOCATION_PROFILES, EPISODE_PROFILES
from .server_cases import PROFILES as SERVER_PROFILES, make_server_case


def generate(output, allocation_seeds=120, episode_seeds=30, server_seeds=10):
    if min(allocation_seeds, episode_seeds, server_seeds) < 1:
        raise ValueError('seed counts must be positive')
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    counts, splits = Counter(), Counter()
    def counted(records):
        for record in records:
            counts[f"{record['kind']}/{record['profile']}"] += 1
            splits[f"{record['kind']}/{record['split']}"] += 1
            yield record
    # seed 순서로 기록하여 앞부분만 읽어도 모든 프로필이 포함된다.
    allocation = (make_case(pid, desc, cfg, seed) for seed in range(allocation_seeds)
                  for pid, desc, cfg in ALLOCATION_PROFILES)
    episodes = (make_episode(pid, desc, cfg, seed) for seed in range(episode_seeds)
                for pid, desc, cfg in EPISODE_PROFILES)
    servers = (make_server_case(pid, seed) for seed in range(server_seeds) for pid in SERVER_PROFILES)
    files = [write_jsonl(root / 'allocation.jsonl.gz', counted(allocation)),
             write_jsonl(root / 'episodes.jsonl.gz', counted(episodes)),
             write_jsonl(root / 'server_cases.jsonl.gz', counted(servers)),
             write_jsonl(root / 'golden_cases.jsonl.gz', counted(golden_cases()))]
    manifest = dict(schema_version=1, source='synthetic', generator='python -m datasets.generate',
                    seed_counts=dict(allocation=allocation_seeds, episode=episode_seeds, server=server_seeds),
                    total_records=sum(f['records'] for f in files), files=files,
                    profiles=dict(allocation=[dict(id=p, description=d, changes=c) for p, d, c in ALLOCATION_PROFILES],
                                  episode=[dict(id=p, description=d, changes=c) for p, d, c in EPISODE_PROFILES],
                                  server=[dict(id=p, description=d) for p, d in SERVER_PROFILES.items()]),
                    counts=dict(sorted(counts.items())), splits=dict(sorted(splits.items())),
                    limitations=['분포·비율·치료시간·최대 대기는 실험 설정이며 실제 통계나 임상 기준이 아님',
                                 'allocation의 actual과 episode의 미래 사건은 사후 검증 전용',
                                 'train/validation/test는 실험 조정·검증 분할이며 학습된 의료 모델이 아님',
                                 '배정되지 못하는 사례·기존 목적지 충돌도 유효한 평가 입력',
                                 '합성 가정의 범위 밖 상황과 실제 의료 현장에 대한 효과를 증명하지 않음'])
    code_files = ['allocation.py', 'episodes.py', 'generate.py', 'io.py', 'profiles.py', 'server_cases.py']
    manifest['generator_sha256'] = hashlib.sha256(b''.join((Path(__file__).parent / f).read_bytes().replace(b'\r\n', b'\n')
                                                         for f in code_files)).hexdigest()
    (root / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    # 압축 파일을 풀지 않고 형태와 주요 경계조건을 확인할 수 있는 예시.
    examples = [make_case(pid, desc, cfg, 0) for pid, desc, cfg in ALLOCATION_PROFILES[:1]] + golden_cases()[:2]
    (root / 'examples.json').write_text(json.dumps(examples, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', default='datasets/generated')
    ap.add_argument('--allocation-seeds', type=int, default=120)
    ap.add_argument('--episode-seeds', type=int, default=30)
    ap.add_argument('--server-seeds', type=int, default=10)
    args = ap.parse_args()
    manifest = generate(args.output, args.allocation_seeds, args.episode_seeds, args.server_seeds)
    print(json.dumps(dict(total_records=manifest['total_records'], files=manifest['files'], splits=manifest['splits']), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
