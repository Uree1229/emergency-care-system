"""공공 병원 기본정보·가용병상 적재. --interval로 프로세스 실행 중 주기 갱신한다."""
import argparse
import json
import threading
import time
from pathlib import Path

from .public_data import NmcClient, PublicDataError, sync_once


def main(argv=None):
    ap = argparse.ArgumentParser(description='국립중앙의료원 병원·공공 가용병상 DB 동기화')
    ap.add_argument('--db', default='data/public.db')
    ap.add_argument('--region', required=True, help='공공 API 시도 이름')
    ap.add_argument('--district', required=True, help='가용병상 API의 필수 시군구 이름')
    ap.add_argument('--interval', type=int, default=0, help='0: 한 번 실행, 60 이상: 갱신 간격(초)')
    ap.add_argument('--basic-interval', type=int, default=86400, help='병원 기본정보 갱신 간격(초)')
    ap.add_argument('--rows', type=int, default=100)
    ap.add_argument('--details', action='store_true', help='병원별 등록 진료과·시설 상세도 별도 수집')
    ap.add_argument('--severe', action='store_true', help='중증질환 수용 원문 코드를 별도 보존(자동 필터에 미사용)')
    args = ap.parse_args(argv)
    if args.interval != 0 and args.interval < 60:
        ap.error('--interval은 0 또는 60초 이상이어야 합니다')
    if args.basic_interval < max(args.interval, 60):
        ap.error('--basic-interval은 갱신 간격 이상이어야 합니다')
    try:
        client = NmcClient(page_size=args.rows, district=args.district)
    except PublicDataError as exc:
        ap.exit(2, str(exc) + '\n')
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    next_basic = 0
    stop = threading.Event()
    try:
        while not stop.is_set():
            basic = time.monotonic() >= next_basic
            try:
                options = {}
                if args.details and basic:
                    options['include_details'] = True
                if args.severe:
                    options['include_severe'] = True
                result = sync_once(args.db, args.region, client=client, include_basic=basic, **options)
                print(json.dumps(result, ensure_ascii=False), flush=True)
                if basic:
                    next_basic = time.monotonic() + args.basic_interval
            except PublicDataError as exc:
                print(json.dumps({'status': 'ERROR', 'error': str(exc)}, ensure_ascii=False), flush=True)
                if not args.interval:
                    return 1
            if not args.interval:
                return 0
            stop.wait(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
