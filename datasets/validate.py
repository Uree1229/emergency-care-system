"""전체 구조·출처·분할·해시 검증 및 서버/정답 경계 시나리오 재생."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from server import db
from solver_improved import check_input, exhaustive, score, solve, validate

from .episodes import episode_config, world_from_episode
from .io import partition, read_jsonl
from .replay import replay


def validate_record(record):
    if record['schema_version'] != 1 or record['source'] != 'synthetic':
        raise ValueError('unsupported schema or nonsynthetic provenance')
    if record['kind'] != 'golden' and record['split'] != partition(record['seed']):
        raise ValueError('seed leaked across splits')
    if record['kind'] in ('allocation', 'golden'):
        check_input(record['observed'])
        check_input(record['actual'])
        if record['time_unit'] != 'minute':
            raise ValueError('solver expects minute slots')
        if [p['id'] for p in record['observed']['patients']] != [p['id'] for p in record['actual']['patients']]:
            raise ValueError('observed and actual patients mismatch')
        if record['kind'] == 'allocation':
            if record['evaluation']['oracle_label'] is not None:
                raise ValueError('random cases must not claim an optimal label')
            rows = record['patient_metadata']
            metadata = {row['id']: row for row in rows}
            if len(metadata) != len(rows) or set(metadata) != {p['id'] for p in record['observed']['patients']}:
                raise ValueError('metadata patients mismatch')
            for p in record['observed']['patients']:
                row = metadata[p['id']]
                if sorted(row['accepted_hospitals']) != sorted(p['eligible']):
                    raise ValueError('metadata current acceptances mismatch')
                if not set(row['accepted_hospitals']) <= set(row['compatible_hospitals']):
                    raise ValueError('accepted hospital is not compatible')
    elif record['kind'] == 'episode':
        hs, ps, walkins = world_from_episode(record)
        ids = [p.id for p in ps]
        if len(ids) != len(set(ids)):
            raise ValueError('duplicate episode patients')
        events = record['events']
        if [e['at'] for e in events] != sorted(e['at'] for e in events):
            raise ValueError('events not sorted')
        request_ids = [e['patient_id'] for e in events if e['type'] == 'patient_request']
        if sorted(request_ids) != sorted(ids):
            raise ValueError('request events do not match patients')
        request_times = {p.id: p.t_req for p in ps}
        if any(e['at'] != request_times[e['patient_id']] for e in events if e['type'] == 'patient_request'):
            raise ValueError('request event time does not match requested_at')
        cfg = episode_config(record)
        hospitals = {h.id: h for h in hs}
        if len(hospitals) != len(hs):
            raise ValueError('duplicate hospitals')
        for h in hs:
            for resource, cap in h.cap.items():
                if not 0 <= h.used(resource) <= cap:
                    raise ValueError('initial occupancy outside capacity')
        for at, hid, p in walkins:
            if hid not in hospitals or not p.external or not 0 <= at < cfg['horizon']:
                raise ValueError('invalid external arrival')
        for p in ps:
            if p.priority not in (4, 2, 1) or not 0 <= p.t_req < cfg['horizon'] or p.duration < 1:
                raise ValueError('invalid episode patient')
        if len(record['latent_response_oracle']) != len(ps) * len(hs) * 2:
            raise ValueError('incomplete response oracle')
    elif record['kind'] == 'server':
        if record['time_unit'] != 'second':
            raise ValueError('server expects epoch seconds')
        if [s['at'] for s in record['steps']] != sorted(s['at'] for s in record['steps']):
            raise ValueError('server actions not time ordered')
    else:
        raise ValueError('unknown record kind')


def check_golden(record):
    data = record['observed']
    plan, meta = solve(data, seconds=1)
    expected = record['expected']
    if expected.get('needs_review'):
        assert plan is None and meta['status'] == 'NEEDS_REVIEW', record['id']
        return
    assert plan is not None and validate(data, plan), record['id']
    assert sum(a is not None for a in plan.values()) == expected['assigned'], record['id']
    for pid, h in expected.get('assignment', {}).items():
        assert plan[pid]['h'] == h, record['id']
    for pid, start in expected.get('starts', {}).items():
        assert plan[pid]['start'] == start, record['id']
    if 'high_assigned' in expected:
        assert sum(plan[p['id']] is not None for p in data['patients'] if p['priority'] == 4) == expected['high_assigned']
    reference = exhaustive(data, 'severity_first')
    assert score(data, plan, 'severity_first') == score(data, reference, 'severity_first'), record['id']


def validate_corpus(root):
    root = Path(root)
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    ids, counts, splits = set(), Counter(), Counter()
    golden, server = 0, 0
    for info in manifest['files']:
        path = root / info['path']
        if hashlib.sha256(path.read_bytes()).hexdigest() != info['sha256']:
            raise ValueError(f'hash mismatch: {path.name}')
        count = 0
        for record in read_jsonl(path):
            validate_record(record)
            if record['id'] in ids:
                raise ValueError('duplicate case ID')
            ids.add(record['id'])
            counts[f"{record['kind']}/{record['profile']}"] += 1
            splits[f"{record['kind']}/{record['split']}"] += 1
            if record['kind'] == 'golden':
                check_golden(record)
                golden += 1
            elif record['kind'] == 'server':
                conn = db.connect(':memory:')
                try:
                    replay(record, conn)
                    server += 1
                finally:
                    conn.close()
            count += 1
        if count != info['records']:
            raise ValueError('file count mismatch')
    if len(ids) != manifest['total_records'] or dict(counts) != manifest['counts'] or dict(splits) != manifest['splits']:
        raise ValueError('manifest coverage mismatch')
    return dict(records_validated=len(ids), profiles=len(counts), golden_cases_proven=golden,
                server_cases_replayed=server, hashes_verified=len(manifest['files']), seed_split_leakage=0,
                manifest_sha256=hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', default='datasets/generated')
    ap.add_argument('--output', help='검증 결과 JSON 저장 경로 (선택)')
    args = ap.parse_args()
    report = validate_corpus(args.input)
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
