"""Connect the teammate intake app to common-server transport episodes.

Saved local profiles and active transport episodes are different objects. This
module sends a profile snapshot only after consent; it never calls medical APIs.
"""
from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
import json
import math
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
import uuid

SOURCE = 'emergency-app@945c413'
TIMEOUT_SECONDS = 5
VOICE_TTL_MS = 2 * 60 * 60 * 1000
BODY_PARTS = frozenset(('머리', '가슴', '복부', '오른팔', '왼팔', '오른다리', '왼다리'))


class CommonServerError(Exception):
    def __init__(self, message, status=400, code='INVALID'):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


def common_server_base():
    value = os.environ.get('COMMON_API_URL', 'http://127.0.0.1:18020').strip()
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.netloc.endswith(':')
                or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
            raise ValueError()
        # Accessing the property validates malformed/out-of-range ports too.
        parsed.port
    except ValueError:
        raise CommonServerError('COMMON_API_URL에는 http/https 서버 기본 주소를 지정하세요.', 503, 'CONFIG') from None
    return value.rstrip('/')


def _request(path, body=None):
    encoded = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
    request = Request(common_server_base() + path, data=encoded,
                      headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read()
    except HTTPError as error:
        try:
            payload = json.loads(error.read())
        except (ValueError, UnicodeError):
            payload = {}
        finally:
            error.close()
        if not isinstance(payload, dict):
            payload = {}
        raise CommonServerError(payload.get('message') or '공통 서버가 요청을 처리하지 못했습니다.',
                                error.code, payload.get('error', 'UPSTREAM_ERROR')) from None
    except (URLError, TimeoutError, OSError):
        raise CommonServerError('공통 서버에 연결하지 못했습니다. 서버 실행을 확인한 뒤 같은 요청으로 다시 시도하세요.',
                                503, 'CONNECTION_FAILED') from None
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        raise CommonServerError('공통 서버의 응답 형식을 확인할 수 없습니다.', 502, 'INVALID_RESPONSE') from None


def hospital_catalog():
    hospitals = _request('/api/hospitals')
    if not isinstance(hospitals, list):
        raise CommonServerError('공통 서버의 병원 목록 형식을 확인할 수 없습니다.', 502, 'INVALID_RESPONSE')
    return hospitals


def new_submission_id():
    return str(uuid.uuid4())


def _optional_text(value):
    return (str(value).strip() or None) if value is not None else None


def confirmed_voice_intake(raw, confirmed, flow_token, patient_id, *, now_ms=None):
    """Validate an explicitly confirmed display; never derive clinical fields."""
    if not raw and confirmed != 'yes':
        return None
    try:
        if not isinstance(raw, str) or len(raw) > 16000 or confirmed != 'yes':
            raise ValueError()
        item = json.loads(raw)
        keys = {'version', 'flow_token', 'created_at', 'updated_at', 'patient_id',
                'transcript', 'conditions', 'areas', 'symptoms', 'onset', 'confirmed'}
        now = int(time.time() * 1000) if now_ms is None else now_ms
        if not isinstance(item, dict) or set(item) != keys:
            raise ValueError()
        if (type(item['version']) is not int or item['version'] != 2 or item['confirmed'] is not True
                or str(uuid.UUID(flow_token)) != flow_token or item['flow_token'] != flow_token
                or item['patient_id'] != str(patient_id or 0)):
            raise ValueError()
        created, updated = item['created_at'], item['updated_at']
        if (type(created) is not int or type(updated) is not int
                or not 0 <= created <= updated <= now + 60000 or now - created >= VOICE_TTL_MS):
            raise ValueError()
        if not isinstance(item['transcript'], str) or not item['transcript'].strip() or len(item['transcript']) > 8000:
            raise ValueError()
        if any(not isinstance(item[key], str) or len(item[key]) > 1000
               for key in ('conditions', 'areas', 'symptoms', 'onset')):
            raise ValueError()
        return item
    except (ValueError, TypeError, AttributeError):
        raise CommonServerError('음성 정보의 환자·흐름·유효기간과 명시 확인을 다시 확인하세요.',
                                400, 'VOICE_REVIEW_REQUIRED') from None


def build_episode_payload(profile, reporter_role, consciousness, breathing, symptoms,
                          required_capabilities, special_team, lat, lon, consent,
                          inquiry_policy='fixed_3', *, body_parts=None, voice_intake=None):
    if consent is not True:
        raise CommonServerError('환자 정보 저장과 병원 전달에 동의해야 문의를 시작할 수 있습니다.', 400, 'CONSENT_REQUIRED')
    if reporter_role not in ('self', 'guardian', 'transporter'):
        raise CommonServerError('입력자 구분을 선택하세요.')
    for value, limit in ((lat, 90), (lon, 180)):
        if type(value) not in (int, float) or not math.isfinite(value) or not -limit <= value <= limit:
            raise CommonServerError('관측한 위도와 경도를 올바르게 입력하세요.', 400, 'LOCATION_REQUIRED')
    if (not isinstance(required_capabilities, list)
            or not all(isinstance(c, str) and c.strip() for c in required_capabilities)):
        raise CommonServerError('필요 진료 분야는 확인한 항목을 선택하세요.')
    if type(special_team) is not bool:
        raise CommonServerError('전문팀 요구 여부를 확인하세요.')
    if not isinstance(inquiry_policy, str) or not inquiry_policy.strip():
        raise CommonServerError('문의 정책을 확인하세요.')
    if not isinstance(symptoms, str) or len(symptoms) > 2000:
        raise CommonServerError('증상은 2,000자 이내로 입력하세요.')
    if (body_parts is not None and (not isinstance(body_parts, list)
            or any(part not in BODY_PARTS for part in body_parts))):
        raise CommonServerError('선택한 신체 부위를 확인하세요.')
    personal = dict(name=None, birth=None, contact=None, address=None, guardian=None, guardian_phone=None, age=None)
    profile_id = None
    if profile is not None:
        if len(profile) != 7:
            raise CommonServerError('등록 환자 정보 형식을 확인하세요.')
        profile_id, name, birth, phone, address, guardian, guardian_phone = profile
        personal.update(name=_optional_text(name), contact=_optional_text(phone), address=_optional_text(address),
                        guardian=_optional_text(guardian), guardian_phone=_optional_text(guardian_phone))
        try:
            born = date.fromisoformat(birth)
            today = datetime.now(timezone(timedelta(hours=9))).date()
            if born.isoformat() == birth and born <= today:
                personal['birth'] = birth
                personal['age'] = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        except (ValueError, TypeError):
            pass  # Invalid legacy profile birthdays remain unknown rather than guessed.
    extra = {'source_app': SOURCE, 'source_profile_id': profile_id}
    if body_parts is not None:
        extra['body_parts'] = sorted(set(body_parts))
    if voice_intake is not None:
        extra['voice_intake'] = voice_intake
    return dict(**personal, reporter_role=reporter_role, consent=True,
                consciousness={'yes': 'alert', 'no': 'unresponsive'}.get(consciousness, 'unknown'),
                breathing={'yes': 'normal', 'no': 'abnormal'}.get(breathing, 'unknown'),
                symptoms=symptoms.strip() or '확인되지 않음',
                priority=None, priority_source='unknown', required_capabilities=sorted(set(required_capabilities)),
                resource_demands={'ed_bed': 1, **({'special_team': 1} if special_team else {})},
                lat=lat, lon=lon, auto_inquiry=True, inquiry_policy=inquiry_policy,
                extra=extra)


def _retry_signature(raw, key, submission_id, flow_token):
    message = json.dumps(['intake-retry-v1', common_server_base(), submission_id, flow_token, raw],
                         separators=(',', ':')).encode('ascii')
    return hmac.digest(key, message, 'sha256').hex()


def sign_episode_payload(payload, key, submission_id, flow_token):
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    signature = _retry_signature(raw, key, submission_id, flow_token)
    return f'v1.{signature}.{raw}'


def restore_episode_payload(token, expected, key, submission_id, flow_token):
    """Return a verified original, or require normal validation for older forms."""
    if not token:
        return None
    try:
        if not isinstance(token, str):
            raise ValueError()
        if not token.startswith('v1.'):
            # Older forms may retry only with the current server-built payload and catalogue.
            if len(token) > 40000 or json.loads(token) != expected:
                raise ValueError()
            return None
        _, signature, raw = token.split('.', 2)
        if len(signature) != 64:
            raise ValueError()
        expected_signature = _retry_signature(raw, key, submission_id, flow_token)
        if not hmac.compare_digest(signature.encode('utf-8'), expected_signature.encode('ascii')):
            raise ValueError()
        original = json.loads(raw)
        personal = {'name', 'birth', 'contact', 'address', 'guardian', 'guardian_phone', 'age'}
        if (not isinstance(original, dict) or set(original) != set(expected)
                or any(original[key] != expected[key] for key in expected.keys() - personal)):
            raise ValueError()
        return original
    except (ValueError, TypeError, KeyError, RecursionError):
        raise CommonServerError('처음 전송한 내용과 입력이 달라 재전송하지 않았습니다. 원래 입력을 확인하거나 새 이송을 시작하세요.',
                                409, 'SUBMISSION_CONFLICT') from None


def create_episode(payload, submission_id):
    try:
        token = uuid.UUID(submission_id)
    except (ValueError, TypeError, AttributeError):
        raise CommonServerError('전송 요청 식별자가 올바르지 않습니다. 입력 화면을 다시 열어주세요.') from None
    if not isinstance(payload, dict) or payload.get('consent') is not True:
        raise CommonServerError('환자 정보 저장과 병원 전달에 동의해야 문의를 시작할 수 있습니다.', 400, 'CONSENT_REQUIRED')
    fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                          separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
    pid = 'MOBILE-' + token.hex
    outgoing = dict(payload, id=pid, extra={**payload.get('extra', {}), 'submission_fingerprint': fingerprint})
    try:
        result = _request('/api/patients', outgoing)
        if (not isinstance(result, dict) or result.get('id') != pid or not isinstance(result.get('extra'), dict)
                or result.get('extra', {}).get('source_app') != SOURCE
                or result.get('extra', {}).get('submission_fingerprint') != fingerprint):
            raise CommonServerError('공통 서버의 등록 결과를 확인하지 못했습니다.', 502, 'INVALID_RESPONSE')
        return result
    except CommonServerError as failure:
        if failure.code not in ('DUPLICATE', 'CONNECTION_FAILED', 'INVALID_RESPONSE', 'SERVER_ERROR'):
            raise
        try:
            existing = _request('/api/patients/' + pid)
        except CommonServerError:
            raise failure from None
        if (isinstance(existing, dict) and existing.get('id') == pid
                and isinstance(existing.get('extra'), dict) and existing['extra'].get('source_app') == SOURCE
                and existing.get('extra', {}).get('submission_fingerprint') == fingerprint):
            return existing
        raise CommonServerError('같은 전송 식별자로 다른 환자 정보를 보낼 수 없습니다. 기존 전송을 확인하세요.',
                                409, 'SUBMISSION_CONFLICT') from None


def transport_url(pid):
    return common_server_base() + '/patient/?' + urlencode({'pid': pid})
