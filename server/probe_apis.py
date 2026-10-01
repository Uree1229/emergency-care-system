"""공공 의료 API 실응답 수집과 필드 목록 작성 (데이터 조사 문서 2절의 완료 조건 1, 2, 4, 5).

원본 필드명을 추측해 확정하지 않기 위해, 실제 응답의 XML/JSON 항목을 그대로 세어
data/api_probe/에 남긴다. 그 결과를 보고 api_field_mapping.md를 채운다.

사용:
    set DATA_GO_KR_KEY=발급받은_일반_인증키(Decoding)
    python -m server.probe_apis --stage1 광주광역시
    python -m server.probe_apis --only nmc_rltm_beds --rows 5

엔드포인트 경로와 파라미터는 활용가이드로 확인 전이므로 verified=False다. 확인 후 고친다.
인증키는 저장 파일·출력에서 지운다.
"""
import argparse
import json
import os
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

ENDPOINTS = {
    # 국립중앙의료원 전국 응급의료기관 정보 조회 서비스 (data.go.kr 15000563)
    'nmc_hospital_list': dict(url='https://apis.data.go.kr/B552657/ErmctInfoInqireService/getEgytListInfoInqire',
                              params=['Q0', 'Q1'], verified=False, purpose='응급의료기관 목록·주소·좌표'),
    'nmc_rltm_beds': dict(url='https://apis.data.go.kr/B552657/ErmctInfoInqireService/getEmrrmRltmUsefulSckbdInfoInqire',
                          params=['STAGE1', 'STAGE2'], verified=False, purpose='응급실 실시간 가용병상'),
    'nmc_severe_accept': dict(url='https://apis.data.go.kr/B552657/ErmctInfoInqireService/getSrsillDissAceptncPosblInfoInqire',
                              params=['STAGE1', 'STAGE2'], verified=False, purpose='중증질환자 수용 가능 정보'),
    # 건강보험심사평가원 병원정보서비스 (data.go.kr 15001698)
    'hira_hospital_basis': dict(url='https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList',
                                params=['sidoCd'], verified=False, purpose='요양기관 기본정보·암호화 기관기호'),
}
# 시도 이름을 파라미터마다 넣는다. 심평원 sidoCd는 코드값이라 --sido-cd로 따로 받는다.
STAGE_PARAMS = {'Q0', 'STAGE1'}
SUBSTAGE_PARAMS = {'Q1', 'STAGE2'}


def scrub(text, key):
    for k in {key, urllib.parse.quote(key, safe=''), urllib.parse.quote_plus(key)}:
        text = text.replace(k, '***')
    return text


def fetch(url, params, key, timeout=15):
    query = urllib.parse.urlencode({'serviceKey': key, **params})
    started = time.time()
    with urllib.request.urlopen(f'{url}?{query}', timeout=timeout) as resp:
        body = resp.read().decode('utf-8', errors='replace')
        return resp.status, resp.headers.get('Content-Type', ''), body, time.time() - started


def inventory_xml(body):
    """item 단위로 각 태그의 등장·빈 값 횟수와 예시값을 센다. 빈 값과 누락을 구분한다."""
    root = ET.fromstring(body)
    header = {e.tag: (e.text or '').strip() for e in root.iter() if e.tag in ('resultCode', 'resultMsg', 'totalCount')}
    items = list(root.iter('item'))
    stats = defaultdict(lambda: dict(present=0, empty=0, samples=[]))
    for it in items:
        for child in it:
            s = stats[child.tag]
            s['present'] += 1
            value = (child.text or '').strip()
            if not value:
                s['empty'] += 1
            elif len(s['samples']) < 3 and value not in s['samples']:
                s['samples'].append(value)
    for s in stats.values():
        s['missing'] = len(items) - s['present']
    return header, len(items), dict(sorted(stats.items()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stage1', default='광주광역시')
    ap.add_argument('--stage2', default=None)
    ap.add_argument('--sido-cd', default=None, help='심평원 시도코드 (활용가이드 코드표 확인 후 입력)')
    ap.add_argument('--rows', type=int, default=10)
    ap.add_argument('--only', choices=sorted(ENDPOINTS))
    ap.add_argument('--out', default='data/api_probe')
    args = ap.parse_args()
    key = os.environ.get('DATA_GO_KR_KEY')
    if not key:
        raise SystemExit('DATA_GO_KR_KEY 환경변수에 공공데이터포털 인증키를 넣어 주세요.')
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    report = dict(fetched_at=stamp, note='원본 갱신시각 필드가 없으면 source_observed_at=null로 둔다', endpoints={})
    for name, ep in ENDPOINTS.items():
        if args.only and name != args.only:
            continue
        params = {'pageNo': 1, 'numOfRows': args.rows}
        for p in ep['params']:
            if p in STAGE_PARAMS:
                params[p] = args.stage1
            elif p in SUBSTAGE_PARAMS and args.stage2:
                params[p] = args.stage2
            elif p == 'sidoCd' and args.sido_cd:
                params[p] = args.sido_cd
        entry = dict(purpose=ep['purpose'], verified=ep['verified'], url=ep['url'], params=params)
        try:
            status, ctype, body, elapsed = fetch(ep['url'], params, key)
            body = scrub(body, key)
            (out / f'{name}_{stamp}.raw').write_text(body, encoding='utf-8')
            entry.update(http_status=status, content_type=ctype, seconds=round(elapsed, 2))
            try:
                header, count, fields = inventory_xml(body)
                entry.update(header=header, items=count, fields=fields)
            except ET.ParseError:
                entry.update(parse_error='XML이 아님. raw 파일 확인', head=body[:300])
        except Exception as e:   # 오류 응답도 검증 자료로 남긴다
            entry.update(error=scrub(f'{type(e).__name__}: {e}', key))
        report['endpoints'][name] = entry
        print(f"{name}: {entry.get('http_status', entry.get('error'))} items={entry.get('items')}")
    path = out / f'inventory_{stamp}.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'필드 목록: {path}')


if __name__ == '__main__':
    main()
