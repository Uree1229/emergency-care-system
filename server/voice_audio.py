"""Bounded Korean short-audio transcription through Azure Speech.

This adapter returns final results only; it does not stream partial transcripts.
Credentials and the fixed resource origin are read only from server environment.
Protocol reference: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-speech-to-text-short
"""

import base64
import binascii
import http.client
import io
import json
import os
import re
import struct
import urllib.error
import urllib.request
import wave

from .service import ServiceError


PROVIDER = 'azure_short_audio'
LANGUAGE = 'ko-KR'
MAX_SECONDS = 60
SAMPLE_RATE = 16000
MAX_AUDIO_BYTES = SAMPLE_RATE * 2 * MAX_SECONDS + 44
MAX_BASE64_CHARS = ((MAX_AUDIO_BYTES + 2) // 3) * 4
MAX_RESPONSE_BYTES = 65536
TIMEOUT_SECONDS = 15
MAX_TEXT_CHARS = 4000
AUDIO_CONTENT_TYPE = 'audio/wav; codecs=audio/pcm; samplerate=16000'
_ORIGIN = re.compile(
    r'https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.cognitiveservices\.azure\.com',
    re.IGNORECASE | re.ASCII,
)
_PATH = '/stt/speech/recognition/conversation/cognitiveservices/v1?language=ko-KR&format=simple'


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward the subscription key to a Location supplied by upstream."""

    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def _configuration():
    key = os.environ.get('AZURE_SPEECH_KEY', '').strip()
    endpoint = os.environ.get('AZURE_SPEECH_ENDPOINT', '').strip()
    # A bare Azure resource origin is required: no ports, credentials or paths.
    if (not key or len(key) > 4096 or not all(33 <= ord(char) <= 126 for char in key)
            or not _ORIGIN.fullmatch(endpoint)):
        return None
    return key, endpoint.lower()


def status():
    """Return public capability information without exposing configuration values."""
    return {
        'provider': PROVIDER,
        'configured': _configuration() is not None,
        'language': LANGUAGE,
        'audio_format': {
            'content_type': 'audio/wav', 'codec': 'PCM', 'sample_rate_hz': SAMPLE_RATE,
            'channels': 1, 'bits_per_sample': 16,
        },
        'streaming': False,
        'max_seconds': MAX_SECONDS,
    }


def _invalid_audio():
    return ServiceError('STT_INVALID_AUDIO', '16kHz·모노·16비트 PCM WAV 음성이 필요합니다', 400)


def _validated_audio(audio_base64, content_type):
    if not isinstance(content_type, str) or content_type.strip().lower() not in (
            'audio/wav', AUDIO_CONTENT_TYPE):
        raise _invalid_audio()
    if not isinstance(audio_base64, str) or not audio_base64:
        raise _invalid_audio()
    if len(audio_base64) > MAX_BASE64_CHARS:
        raise ServiceError('STT_AUDIO_TOO_LARGE', '음성은 60초 이내로 녹음하세요', 400)
    try:
        audio = base64.b64decode(audio_base64, validate=True)
    except (binascii.Error, ValueError, UnicodeError):
        raise _invalid_audio() from None
    if len(audio) > MAX_AUDIO_BYTES:
        raise ServiceError('STT_AUDIO_TOO_LARGE', '음성은 60초 이내로 녹음하세요', 400)
    if len(audio) < 44:
        raise _invalid_audio()
    # Verify complete RIFF chunks as well as wave's format/frame parser. wave by
    # itself accepts truncated data and ignores a mismatched RIFF/byte-rate value.
    try:
        if (audio[:4] != b'RIFF' or audio[8:12] != b'WAVE'
                or struct.unpack_from('<I', audio, 4)[0] != len(audio) - 8):
            raise ValueError
        offset, format_seen, data_seen = 12, False, False
        data_bytes = 0
        while offset < len(audio):
            if offset + 8 > len(audio):
                raise ValueError
            name, size = struct.unpack_from('<4sI', audio, offset)
            start, end = offset + 8, offset + 8 + size
            if end > len(audio):
                raise ValueError
            if name == b'fmt ':
                if format_seen or data_seen or size < 16:
                    raise ValueError
                params = struct.unpack_from('<HHIIHH', audio, start)
                if params != (1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16):
                    raise ValueError
                format_seen = True
            elif name == b'data':
                if not format_seen or data_seen or not size or size % 2:
                    raise ValueError
                data_seen, data_bytes = True, size
            offset = end + (size % 2)
        if offset != len(audio) or not format_seen or not data_seen:
            raise ValueError
        with wave.open(io.BytesIO(audio), 'rb') as recording:
            frames = recording.getnframes()
            if (recording.getnchannels() != 1 or recording.getsampwidth() != 2
                    or recording.getframerate() != SAMPLE_RATE
                    or recording.getcomptype() != 'NONE'
                    or frames <= 0 or frames > SAMPLE_RATE * MAX_SECONDS
                    or frames * 2 != data_bytes
                    or len(recording.readframes(frames)) != data_bytes):
                raise ValueError
    except (EOFError, ValueError, struct.error, wave.Error):
        raise _invalid_audio() from None
    return audio, frames / SAMPLE_RATE


def _upstream_error():
    return ServiceError('STT_UPSTREAM_ERROR', '음성 인식 서비스 응답을 처리할 수 없습니다', 502)


def _http_error(code):
    if code in (408, 504):
        return ServiceError('STT_TIMEOUT', '음성 인식 요청 시간이 초과되었습니다', 504)
    if code in (401, 403, 429, 503):
        return ServiceError('STT_UNAVAILABLE', '음성 인식 서비스를 지금 사용할 수 없습니다', 503)
    return _upstream_error()


def transcribe(audio_base64, content_type='audio/wav', *, opener=None):
    """Validate a bounded WAV and return one final transcript without persistence.

    ``opener`` is an optional trusted test seam with urlopen's call signature.
    Production calls use an opener that rejects every HTTP redirect.
    """
    configuration = _configuration()
    if configuration is None:
        raise ServiceError('STT_NOT_CONFIGURED', '서버의 Azure Speech 설정이 필요합니다', 503)
    audio, duration = _validated_audio(audio_base64, content_type)
    key, endpoint = configuration
    request = urllib.request.Request(endpoint + _PATH, data=audio, method='POST', headers={
        'Ocp-Apim-Subscription-Key': key,
        'Content-Type': AUDIO_CONTENT_TYPE,
        'Accept': 'application/json',
    })
    try:
        open_request = opener or urllib.request.build_opener(_NoRedirect()).open
        with open_request(request, timeout=TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise _http_error(response.status)
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise _http_error(code) from None
    except TimeoutError:
        raise ServiceError('STT_TIMEOUT', '음성 인식 요청 시간이 초과되었습니다', 504) from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise ServiceError('STT_TIMEOUT', '음성 인식 요청 시간이 초과되었습니다', 504) from None
        raise ServiceError('STT_UNAVAILABLE', '음성 인식 서비스에 연결할 수 없습니다', 503) from None
    except http.client.HTTPException:
        raise _upstream_error() from None
    except (OSError, ValueError):
        raise ServiceError('STT_UNAVAILABLE', '음성 인식 서비스에 연결할 수 없습니다', 503) from None
    if not isinstance(body, bytes) or len(body) > MAX_RESPONSE_BYTES:
        raise _upstream_error()
    try:
        result = json.loads(body.decode('utf-8'))
    except (ValueError, UnicodeError, RecursionError):
        raise _upstream_error() from None
    if not isinstance(result, dict):
        raise _upstream_error()
    recognition = result.get('RecognitionStatus')
    if recognition in ('NoMatch', 'InitialSilenceTimeout', 'BabbleTimeout'):
        raise ServiceError('STT_NO_MATCH', '인식된 말이 없습니다. 다시 녹음하세요', 422)
    if recognition != 'Success' or not isinstance(result.get('DisplayText'), str):
        raise _upstream_error()
    if len(result['DisplayText']) > MAX_TEXT_CHARS:
        raise _upstream_error()
    text = result['DisplayText'].strip()
    if not text:
        raise ServiceError('STT_NO_MATCH', '인식된 말이 없습니다. 다시 녹음하세요', 422)
    return {
        'text': text, 'provider': PROVIDER, 'language': LANGUAGE,
        'is_final': True, 'duration_seconds': duration,
    }
