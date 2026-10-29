from pathlib import Path
import os
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

# Load .env next to this script without printing credentials.
env = Path(__file__).resolve().parent / '.env'
if env.exists():
    for line in env.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
key = os.getenv('HIRA_API_KEY', '').strip()
if not key:
    print('HIRA_API_KEY가 없습니다. .env 파일과 이 스크립트를 같은 폴더에 두세요.')
    raise SystemExit(1)

endpoint = 'https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList'
params = {'ServiceKey': key, 'pageNo': 1, 'numOfRows': 5, 'yadmNm': '분당차병원'}
url = endpoint + '?' + urllib.parse.urlencode(params)
req = urllib.request.Request(url, headers={'User-Agent': 'EmergencyCapstone/1.0'})
try:
    with urllib.request.urlopen(req, timeout=20) as res:
        payload = res.read()
        print('HTTP 상태:', res.status)
    root = ET.fromstring(payload)
    print('API 결과 코드:', root.findtext('.//resultCode', default='없음'))
    print('API 결과 메시지:', root.findtext('.//resultMsg', default='없음'))
    items = root.findall('.//items/item')
    print('조회된 병원:', len(items), '건 (현재 페이지)')
    for item in items:
        print('병원명:', item.findtext('yadmNm', default='미확인'))
        print('주소:', item.findtext('addr', default='미확인'))
        print('암호화 요양기호(ykiho):', '존재' if item.findtext('ykiho') else '없음')
        print('---')
except urllib.error.HTTPError as exc:
    print('HTTP 오류:', exc.code)
    try:
        err = exc.read().decode('utf-8', errors='replace')
        print('오류 본문(일부):', err[:500])
    except Exception:
        pass
except urllib.error.URLError as exc:
    print('네트워크 오류:', str(exc.reason))
except ET.ParseError:
    print('응답이 XML 형식이 아닙니다. API 승인 및 요청주소를 확인하세요.')
