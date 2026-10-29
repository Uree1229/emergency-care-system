"""심평원 진료과목 API 2.8 진단용. 기존 앱 파일은 수정하지 않습니다."""
import os
import json
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path

for line in (Path(__file__).parent / '.env').read_text(encoding='utf-8-sig').splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"\''))
key = os.getenv('HIRA_API_KEY', '').strip()
if not key:
    raise SystemExit('HIRA_API_KEY가 .env에 없습니다.')

# 이전에 성공한 병원 기본목록 API
hosp_url = 'https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList'
# 2.8 서비스 URL은 현재 문서 캡처에서 직접 보이지 않아 추정값이며 검증이 필요합니다.
detail_url = os.getenv('HIRA_DETAIL_URL', 'https://apis.data.go.kr/B551182/MadmDtlInfoService2.8/getDgsbjtInfo2.8')

def fetch(url, params):
    # urllib.urlencode가 키를 인코딩하므로 디코딩된 키 사용을 전제합니다.
    q = urllib.parse.urlencode({'serviceKey': key, 'pageNo': 1, 'numOfRows': 100, '_type': 'xml', **params})
    try:
        with urllib.request.urlopen(url + '?' + q, timeout=20) as response:
            data = response.read()
            status = response.status
    except urllib.error.HTTPError as e:
        data = e.read()
        status = e.code
    print('HTTP 상태:', status)
    if status != 200:
        print('오류 응답(최대 500자):', data.decode('utf-8', errors='replace')[:500])
        return None
    try:
        root = ET.fromstring(data)
        print('API 결과:', root.findtext('.//resultCode'), root.findtext('.//resultMsg'))
        return [{c.tag: c.text for c in item} for item in root.findall('.//items/item')]
    except ET.ParseError:
        print('응답(최대 500자):', data.decode('utf-8', errors='replace')[:500])
        return None

print('1. 분당차병원 검색')
hospitals = fetch(hosp_url, {'yadmNm': '분당차병원'})
if hospitals is None:
    raise SystemExit('병원 검색 실패')
matches = [h for h in hospitals if '분당차병원' in (h.get('yadmNm') or '') and h.get('ykiho')]
if len(matches) != 1:
    raise SystemExit('병원 일치 또는 ykiho 확인 실패')
print('병원:', matches[0].get('yadmNm'), '(ykiho는 출력하지 않음)')
print('\n2. 진료과목 상세조회 (2.8)')
items = fetch(detail_url, {'ykiho': matches[0]['ykiho']})
if items is not None:
    print('진료과목 결과:', len(items), '건')
    for item in items[:10]:
        print({k:v for k,v in item.items() if k.lower() not in ('ykiho','servicekey')})
print('\n주의: 상세 API의 서비스 URL은 아직 검증되지 않았습니다. 400 오류 응답을 공유하면 원인을 더 정확히 확인할 수 있습니다.')
