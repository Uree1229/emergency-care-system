"""기존 NMC 병원에 심평원 신고 현황을 보수적으로 연결하는 1회 수집 CLI."""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

from . import db
from .hira_data import DETAIL_ENDPOINTS, HiraClient, HiraDataError, apply_bundles
from .hira_matching import load_reviewed_links, match_hospitals


SPATIAL_SEARCH_RADIUS_METERS = 1000


def _spatial_search_params(hospital):
    """이름 검색에서 누락된 후보를 조회할 좌표만 검증한다.

    1 km는 후보 수집 범위이며, 기관 연결에는 여전히 매처의 300 m 제한과
    정규화 이름·주소의 완전 일치가 필요하다.
    """
    lat, lon = hospital.get('lat'), hospital.get('lon')
    if isinstance(lat, bool) or isinstance(lon, bool):
        return None
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if (not math.isfinite(lat) or not math.isfinite(lon) or
            not -90 <= lat <= 90 or not -180 <= lon <= 180 or (lat, lon) == (0, 0)):
        return None
    return {'xPos':str(lon), 'yPos':str(lat), 'radius':str(SPATIAL_SEARCH_RADIUS_METERS)}


def sync_once(path, client=None, clock=time.time, *, reviewed_links=None):
    if not Path(path).is_file():
        raise HiraDataError('먼저 국립중앙의료원 병원 데이터가 있는 DB를 준비하세요')
    client = client or HiraClient()
    reviewed_links = load_reviewed_links() if reviewed_links is None else reviewed_links
    conn = db.connect(str(path))
    db.init(conn)
    conn.execute('''CREATE TABLE IF NOT EXISTS hira_sync_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, started_at INTEGER NOT NULL,
        finished_at INTEGER, status TEXT NOT NULL, stats TEXT NOT NULL DEFAULT '{}', error TEXT)''')
    started = int(clock())
    run_id = conn.execute("INSERT INTO hira_sync_runs(started_at,status) VALUES(?,'RUNNING')", (started,)).lastrowid
    report = {'run_id':run_id, 'started_at':started, 'status':'RUNNING',
              'requested_hospitals':0, 'candidate_records':[], 'matches':[],
              'unmatched':[], 'ambiguous':[], 'errors':[],
              'spatial_searches':[],
              'hospitals_linked':0, 'snapshots':0,
              'note':'심평원 신고 현황. 현재 당직·가용수·환자별 수용 확답으로 사용하지 않음'}
    try:
        hospitals = db.rows(conn.execute("SELECT * FROM hospitals WHERE source='public' ORDER BY id"))
        if not hospitals:
            raise HiraDataError('연계할 공공 병원 목록이 없습니다')
        report['requested_hospitals'] = len(hospitals)
        candidates, seen_records = [], set()

        def add_candidates(rows):
            for row in rows:
                # distance는 좌표 검색에서만 생기는 질의별 계산값이다. 기관의
                # 실질 필드가 같다면 첫 원본을 보존하며 한 후보로 취급한다.
                identity = json.dumps({k:v for k,v in row.items() if k != 'distance'},
                                      ensure_ascii=False, sort_keys=True)
                if identity not in seen_records:
                    candidates.append(row)
                    seen_records.add(identity)

        # 전체 시도의 모든 의원을 내려받지 않고 기존 응급의료기관 이름으로 검색한다.
        for name in dict.fromkeys(h['name'] for h in hospitals):
            rows = client.fetch_all('hira_hospital_basis', yadmNm=name)
            add_candidates(rows)
        initial_matching = match_hospitals(hospitals, candidates, reviewed_links=reviewed_links)
        hospitals_by_id = {h['id']:h for h in hospitals}
        # 이름 검색은 공백·법인명 표기에 따라 후보 자체를 놓칠 수 있다. 이름을
        # 느슨하게 비교하지 않고 주변 기관을 추가 수집한 뒤 같은 매처로 검증한다.
        for unmatched in initial_matching['unmatched']:
            if 'NO_EXACT_NAME_MATCH' not in unmatched['reasons']:
                continue
            params = _spatial_search_params(hospitals_by_id[unmatched['hospital_id']])
            if params is None:
                continue
            search = {'hospital_id':unmatched['hospital_id'], **params}
            report['spatial_searches'].append(search)
            rows = client.fetch_all('hira_hospital_basis', **params)
            search['candidate_count'] = len(rows)
            add_candidates(rows)
        report['candidate_records'] = candidates
        matching = match_hospitals(hospitals, candidates, reviewed_links=reviewed_links)
        for name in ('matches','unmatched','ambiguous'):
            report[name] = matching[name]
        by_id = {r['ykiho']:r for r in candidates if r.get('ykiho')}
        bundles = []
        access_failed = False
        for match in matching['matches']:
            try:
                records = {'hira_hospital_basis':[by_id[match['ykiho']]]}
                for endpoint in DETAIL_ENDPOINTS:
                    records[endpoint] = client.fetch_all(endpoint,ykiho=match['ykiho'])
                bundles.append({**match,'records':records})
            except HiraDataError as exc:
                report['errors'].append({'hospital_id':match['hospital_id'],'endpoint':endpoint,'error':str(exc)})
                if exc.access_denied:
                    access_failed = True
                    break
        report.update(apply_bundles(conn,int(clock()),bundles,reviewed_links=reviewed_links))
        report['status'] = ('SUCCESS' if len(bundles) == len(hospitals) and not report['errors']
                            else 'ERROR' if access_failed and not bundles else 'PARTIAL')
    except Exception as exc:
        # 내부 예외·요청 URL·인증키를 로그나 파일에 남기지 않는다.
        message = str(exc) if isinstance(exc,HiraDataError) else '심평원 수집·매칭·적재 처리에 실패했습니다'
        report['errors'].append({'error':message})
        report['status'] = 'ERROR'
    finally:
        report['finished_at'] = int(clock())
        stats = {k:report[k] for k in ('requested_hospitals','hospitals_linked','snapshots')}
        stats.update(matched=len(report['matches']), unmatched=len(report['unmatched']), ambiguous=len(report['ambiguous']))
        error = '; '.join(r['error'] for r in report['errors']) or None
        conn.execute('UPDATE hira_sync_runs SET finished_at=?,status=?,stats=?,error=? WHERE id=?',
                     (report['finished_at'],report['status'],db.dumps(stats),error,run_id))
        conn.close()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description='심평원 기관 매칭·기본정보 및 전체 11종 상세 신고정보 수집')
    parser.add_argument('--db',default='data/public.db')
    parser.add_argument('--out',default='data/hira_sync')
    args = parser.parse_args(argv)
    try:
        report = sync_once(args.db)
    except HiraDataError as exc:
        parser.exit(2,str(exc)+'\n')
    directory = Path(args.out)
    directory.mkdir(parents=True,exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path = directory / (stamp+'.json')
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (directory/'latest.json').write_text(json.dumps({'report':str(path)},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('status','requested_hospitals','hospitals_linked','snapshots','errors')},ensure_ascii=False))
    print(json.dumps({'report':str(path)},ensure_ascii=False))
    return 0 if report['status']=='SUCCESS' else 1


if __name__=='__main__':
    raise SystemExit(main())
