"""기존 공공 병원 HPID별 NMC 수용 관련 메시지 1회 수집 CLI."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from . import db
from .nmc_messages import MessageDataError, NmcMessageClient, apply_bundles, hospital_targets, validate_records


def sync_once(path, client=None, clock=time.time):
    if not Path(path).is_file():
        raise MessageDataError('먼저 국립중앙의료원 병원 데이터가 있는 DB를 준비하세요')
    client = client or NmcMessageClient()
    conn = db.connect(str(path))
    db.init(conn)
    conn.execute('''CREATE TABLE IF NOT EXISTS nmc_message_sync_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, started_at INTEGER NOT NULL,
        finished_at INTEGER, status TEXT NOT NULL, stats TEXT NOT NULL DEFAULT '{}', error TEXT)''')
    started = int(clock())
    run_id = conn.execute("INSERT INTO nmc_message_sync_runs(started_at,status) VALUES(?,'RUNNING')", (started,)).lastrowid
    report = dict(run_id=run_id, started_at=started, status='RUNNING', requested_hospitals=0,
                  hospitals_updated=0, snapshots=0, message_count=0, empty_results=0,
                  skipped_older=0, skipped_hospitals=[], hospital_results=[], errors=[],
                  note='메시지·표출 상태는 참고 정보. 원천 갱신시각 미상이며 환자별 수용 확답이 아님')
    try:
        targets, skipped = hospital_targets(conn)
        report['requested_hospitals'] = len(targets) + len(skipped)
        report['skipped_hospitals'] = skipped
        if not targets:
            raise MessageDataError('메시지를 수집할 공공 NMC 병원 목록이 없습니다')
        bundles = []
        for target in targets:
            try:
                records = client.fetch_all(target['hpid'])
                validate_records(target['hpid'], records)
                bundles.append({**target, 'records': records})
                report['hospital_results'].append({**target, 'message_count': len(records)})
            except Exception as exc:
                message = str(exc) if isinstance(exc, MessageDataError) else '기관별 메시지 수집에 실패했습니다'
                report['errors'].append({'hospital_id': target['hospital_id'], 'error': message})
                if isinstance(exc, MessageDataError) and exc.access_denied:
                    break
        report.update(apply_bundles(conn, int(clock()), bundles))
        report['status'] = ('SUCCESS' if report['hospitals_updated'] == report['requested_hospitals'] and not report['errors']
                            else 'PARTIAL' if bundles else 'ERROR')
    except Exception as exc:
        message = str(exc) if isinstance(exc, MessageDataError) else 'NMC 메시지 수집·적재 처리에 실패했습니다'
        report['errors'].append({'error': message})
        report['status'] = 'ERROR'
    finally:
        report['finished_at'] = int(clock())
        stats = {name: report[name] for name in ('requested_hospitals', 'hospitals_updated', 'snapshots',
                                                'message_count', 'empty_results', 'skipped_older')}
        conn.execute('UPDATE nmc_message_sync_runs SET finished_at=?,status=?,stats=?,error=? WHERE id=?',
                     (report['finished_at'], report['status'], db.dumps(stats),
                      '; '.join(row['error'] for row in report['errors']) or None, run_id))
        conn.close()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default='data/public.db')
    parser.add_argument('--out', default='data/nmc_message_sync')
    args = parser.parse_args(argv)
    try:
        report = sync_once(args.db)
    except MessageDataError as exc:
        parser.exit(2, str(exc) + '\n')
    directory = Path(args.out)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path = directory / (stamp + '.json')
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (directory / 'latest.json').write_text(json.dumps({'report': str(path)}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({name: report[name] for name in ('status', 'requested_hospitals', 'hospitals_updated',
                                                     'snapshots', 'message_count', 'empty_results', 'errors')}, ensure_ascii=False))
    print(json.dumps({'report': str(path)}, ensure_ascii=False))
    return 0 if report['status'] == 'SUCCESS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
