"""gzip JSONL 저장·조회. 한 건씩 읽어 큰 코퍼스도 메모리에 모두 올리지 않는다."""
import gzip
import hashlib
import json
from pathlib import Path


def partition(seed):
    # 같은 seed의 모든 파생 상황은 같은 분할에 둔다.
    return 'train' if seed % 10 < 8 else ('validation' if seed % 10 == 8 else 'test')


def write_jsonl(path, records):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    # gzip 헤더의 파일명·시각을 고정해 같은 입력이면 바이트까지 재현된다.
    with path.open('wb') as raw:
        with gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as out:
            for record in records:
                out.write((json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode())
                count += 1
    return dict(path=path.name, records=count, bytes=path.stat().st_size,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def read_jsonl(path, split=None, profile=None):
    path = Path(path)
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            record = json.loads(line)
            if (split is None or record['split'] == split) and (profile is None or record['profile'] == profile):
                yield record


def find(path, case_id):
    for record in read_jsonl(path):
        if record['id'] == case_id:
            return record
    raise ValueError(f'unknown case ID: {case_id}')
