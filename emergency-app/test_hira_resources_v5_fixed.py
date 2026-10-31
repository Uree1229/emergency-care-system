"""HIRA 시설/장비 API 응답 확인용. 기존 앱 파일을 수정하지 않습니다."""
import os
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path

BASE = 'https://apis.data.go.kr/B551182'
HOSPITAL = BASE + '/hospInfoServicev2/getHospBasisList'
# 공식 Swagger에서 확인한 기능명. 서비스 기본 경로는 앞서 성공한 진료과목 API와 동일하게 사용합니다.
DEFAULTS = {
    '시설정보': BASE + '/MadmDtlInfoService2.8/getEqpInfo2.8',
    '의료장비정보': BASE + '/MadmDtlInfoService2.8/getMedOftInfo2.8',
}

def load_env():
    p = Path(__file__).resolve().parent / '.env'
    if p.exists():
        for line in p.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

def call(url, key, params):
    query = urllib.parse.urlencode({'serviceKey': key, 'pageNo': 1, 'numOfRows': 100, '_type': 'xml', **params})
    request = urllib.request.Request(url + ('&' if '?' in url else '?') + query,
                                     headers={'User-Agent': 'Capstone-HIRA-Test/1.0'})
    try:
        with urllib.request.urlopen(request, timeout=12) as resp:
            status, data = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status, data = exc.code, exc.read()
    print('HTTP 상태:', status)
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        print('응답 일부:', data.decode('utf-8', 'replace')[:350])
        return []
    print('API 결과:', root.findtext('.//resultCode') or '코드 없음', root.findtext('.//resultMsg') or '')
    items = root.findall('.//items/item')
    print('결과 건수:', len(items))
    for item in items[:5]:
        fields = {x.tag: (x.text or '').strip() for x in item}
        fields.pop('ykiho', None)
        print(fields)
    return [{x.tag: (x.text or '').strip() for x in item} for item in items]

def main():
    load_env()
    key = os.getenv('HIRA_API_KEY', '').strip()
    if not key:
        print('HIRA_API_KEY가 .env에 없습니다.')
        return
    print('1. 분당차병원 식별자 조회')
    hospitals = call(HOSPITAL, key, {'yadmNm': '차의과학대학교분당차병원'})
    matched = [h for h in hospitals if '분당차병원' in h.get('yadmNm', '').replace(' ', '')]
    if len(matched) != 1 or not matched[0].get('ykiho'):
        print('병원 식별자를 확인하지 못했습니다. 중단합니다.')
        return
    ykiho = matched[0]['ykiho']
    for label, env in [('시설정보', 'HIRA_FACILITY_URL'), ('의료장비정보', 'HIRA_EQUIPMENT_URL')]:
        print('\n2. ' + label)
        url = os.getenv(env, '').strip() or DEFAULTS[label]
        print('요청 기능:', url)
        if not os.getenv(env):
            print('참고: 기능명은 공식 명세 확인, 실제 HTTP 응답은 테스트 중입니다.')
        call(url, key, {'ykiho': ykiho})
    print('\n인증키와 ykiho 값은 출력하지 않았습니다.')

if __name__ == '__main__':
    main()
