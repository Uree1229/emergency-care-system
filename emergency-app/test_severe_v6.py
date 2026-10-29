"""V6: 국립중앙의료원 중증질환 수용정보 API 연결 사전 점검.
기능명은 후보이며 공공데이터포털 상세 명세에서 검증이 필요합니다.
"""
from pathlib import Path
import os
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines() if (ROOT / '.env').exists() else []:
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line:
        continue
    k, v = line.split('=', 1)
    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

key = os.getenv('EMERGENCY_API_KEY', '').strip()
if not key:
    raise SystemExit('EMERGENCY_API_KEY가 .env에 없습니다.')

base = 'https://apis.data.go.kr/B552657/ErmctInfoInqireService/'
# 검증 전 후보 기능명. 400/404가 나오면 공식 명세의 요청주소 확인 필요.
operation = os.getenv('NMC_SEVERE_OPERATION', 'getSrsillDissAceptncPosblInfoInqire')
params = {'serviceKey': key, 'STAGE1': '경기', 'STAGE2': '성남시', 'pageNo': 1, 'numOfRows': 10}
url = base + operation + '?' + urllib.parse.urlencode(params)
print('V6 중증질환 수용정보 API 사전 테스트')
print('요청 기능(검증 전):', base + operation)
print('인증키는 출력하지 않습니다.')
try:
    req = urllib.request.Request(url, headers={'User-Agent': 'EmergencyCapstone/1.0'})
    with urllib.request.urlopen(req, timeout=15) as response:
        status = response.status
        data = response.read()
    print('HTTP 상태:', status)
except urllib.error.HTTPError as exc:
    print('HTTP 상태:', exc.code)
    print('오류 응답:', exc.read(600).decode('utf-8', errors='replace'))
    raise SystemExit(0)
except Exception as exc:
    print('네트워크 오류:', type(exc).__name__, str(exc))
    raise SystemExit(0)
try:
    root = ET.fromstring(data)
    print('API 결과:', root.findtext('.//resultCode', default='코드 없음'), root.findtext('.//resultMsg', default=''))
    items = root.findall('.//items/item')
    print('결과 건수(현재 페이지):', len(items))
    for item in items[:3]:
        print({child.tag: (child.text or '').strip() for child in item if child.tag.lower() not in ('hpid','phpid')})
except ET.ParseError:
    print('XML 외 응답:', data[:500].decode('utf-8', errors='replace'))
