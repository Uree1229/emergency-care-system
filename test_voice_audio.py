"""Azure short-audio adapter tests; every transport is mocked and networking denied."""

import base64
import http.client
import io
import json
import socket
import struct
import unittest
import urllib.error
import wave
from unittest import mock

from server import voice_audio
from server.service import ServiceError


ENV = {'AZURE_SPEECH_KEY': 'test-secret-never-in-errors',
       'AZURE_SPEECH_ENDPOINT': 'https://example-speech.cognitiveservices.azure.com'}


def wav_audio(frames=1600, channels=1, width=2, rate=16000):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as recording:
        recording.setnchannels(channels)
        recording.setsampwidth(width)
        recording.setframerate(rate)
        recording.writeframes(b'\0' * frames * channels * width)
    return buffer.getvalue()


def encoded(audio=None):
    return base64.b64encode(wav_audio() if audio is None else audio).decode('ascii')


class Response(io.BytesIO):
    def __init__(self, payload=None, *, body=None, status=200):
        self.status = status
        super().__init__(json.dumps(payload or {'RecognitionStatus': 'Success',
                                              'DisplayText': '환자 의식이 없습니다.'}).encode('utf-8')
                         if body is None else body)


class VoiceAudioTests(unittest.TestCase):
    def setUp(self):
        self.environment = mock.patch.dict('os.environ', ENV, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        network = mock.patch.object(socket, 'create_connection',
                                    side_effect=AssertionError('Real networking is disabled'))
        network.start()
        self.addCleanup(network.stop)

    def assert_error(self, status, code, callback):
        with self.assertRaises(ServiceError) as captured:
            callback()
        error = captured.exception
        self.assertEqual((error.status, error.code), (status, code))
        self.assertNotIn(ENV['AZURE_SPEECH_KEY'], str(error))
        self.assertNotIn(ENV['AZURE_SPEECH_ENDPOINT'], str(error))
        self.assertNotIn('private upstream body', str(error))
        return error

    def test_status_is_safe_and_explicitly_final_only(self):
        value = voice_audio.status()
        self.assertEqual(value, {
            'provider': 'azure_short_audio', 'configured': True, 'language': 'ko-KR',
            'audio_format': {'content_type': 'audio/wav', 'codec': 'PCM',
                             'sample_rate_hz': 16000, 'channels': 1, 'bits_per_sample': 16},
            'streaming': False, 'max_seconds': 60,
        })
        self.assertNotIn(ENV['AZURE_SPEECH_KEY'], json.dumps(value))
        self.assertNotIn(ENV['AZURE_SPEECH_ENDPOINT'], json.dumps(value))

    def test_success_sends_documented_headers_format_and_language(self):
        opener = mock.Mock(return_value=Response())
        result = voice_audio.transcribe(encoded(), opener=opener)
        self.assertEqual(result, {
            'text': '환자 의식이 없습니다.', 'provider': 'azure_short_audio',
            'language': 'ko-KR', 'is_final': True, 'duration_seconds': 0.1,
        })
        request = opener.call_args.args[0]
        self.assertEqual(request.method, 'POST')
        self.assertEqual(request.full_url, ENV['AZURE_SPEECH_ENDPOINT']
                         + '/stt/speech/recognition/conversation/cognitiveservices/v1'
                         + '?language=ko-KR&format=simple')
        self.assertEqual(request.data, wav_audio())
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(headers['ocp-apim-subscription-key'], ENV['AZURE_SPEECH_KEY'])
        self.assertEqual(headers['content-type'],
                         'audio/wav; codecs=audio/pcm; samplerate=16000')
        self.assertEqual(headers['accept'], 'application/json')
        self.assertEqual(opener.call_args.kwargs, {'timeout': 15})

    def test_maximum_sixty_seconds_and_text_boundary(self):
        result = voice_audio.transcribe(encoded(wav_audio(frames=16000 * 60)),
                                       opener=mock.Mock(return_value=Response({
                                           'RecognitionStatus': 'Success',
                                           'DisplayText': '가' * 4000})))
        self.assertEqual(result['duration_seconds'], 60)
        self.assertEqual(len(result['text']), 4000)

    def test_default_transport_blocks_every_redirect(self):
        transport = mock.Mock()
        transport.open.return_value = Response()
        with mock.patch.object(voice_audio.urllib.request, 'build_opener',
                               return_value=transport) as factory:
            voice_audio.transcribe(encoded())
        handler = factory.call_args.args[0]
        self.assertIsInstance(handler, voice_audio._NoRedirect)
        self.assertIsNone(handler.redirect_request(None, None, 307, '', {},
                                                  'https://attacker.invalid'))
        transport.open.assert_called_once()

    def test_missing_configuration_never_sends_audio(self):
        for missing in ('AZURE_SPEECH_KEY', 'AZURE_SPEECH_ENDPOINT'):
            with self.subTest(missing=missing), mock.patch.dict('os.environ', {missing: ''}):
                opener = mock.Mock()
                self.assertFalse(voice_audio.status()['configured'])
                self.assert_error(503, 'STT_NOT_CONFIGURED',
                                  lambda: voice_audio.transcribe(encoded(), opener=opener))
                opener.assert_not_called()

    def test_configuration_rejects_non_azure_or_decorated_origins(self):
        invalid = [
            'http://example.cognitiveservices.azure.com',
            'https://example.cognitiveservices.azure.com.attacker.invalid',
            'https://attacker.invalid', 'https://cognitiveservices.azure.com',
            'https://user:pass@example.cognitiveservices.azure.com',
            'https://example.cognitiveservices.azure.com:443',
            'https://example.cognitiveservices.azure.com/',
            'https://example.cognitiveservices.azure.com/stt',
            'https://example.cognitiveservices.azure.com?language=en-US',
            'https://example.cognitiveservices.azure.com#secret',
            'https://a.b.cognitiveservices.azure.com',
            'https://example.cognitiveservices.azure.com\r\nX-Foo: bar',
        ]
        for endpoint in invalid:
            with self.subTest(endpoint=endpoint), mock.patch.dict(
                    'os.environ', {'AZURE_SPEECH_ENDPOINT': endpoint}):
                self.assertFalse(voice_audio.status()['configured'])
                self.assert_error(503, 'STT_NOT_CONFIGURED',
                                  lambda: voice_audio.transcribe(encoded(), opener=mock.Mock()))

    def test_configuration_rejects_unsafe_key_headers(self):
        for key in ('', 'a\r\nInjected: yes', '한글', 'a' * 4097, 'a b'):
            with self.subTest(key=key), mock.patch.dict('os.environ', {'AZURE_SPEECH_KEY': key}):
                self.assertFalse(voice_audio.status()['configured'])

    def test_bad_base64_or_unsupported_media_never_reaches_transport(self):
        cases = [(None, 'audio/wav'), (b'bytes', 'audio/wav'), ('', 'audio/wav'),
                 ('@@@@', 'audio/wav'), ('한글', 'audio/wav'),
                 ('data:audio/wav;base64,' + encoded(), 'audio/wav'),
                 (encoded() + '\n', 'audio/wav'), ('AA==', 'audio/wav'),
                 (encoded(), 'audio/webm'), (encoded(), None)]
        for audio, content_type in cases:
            with self.subTest(audio_type=type(audio), content_type=content_type):
                opener = mock.Mock()
                self.assert_error(400, 'STT_INVALID_AUDIO', lambda: voice_audio.transcribe(
                    audio, content_type, opener=opener))
                opener.assert_not_called()

    def test_oversized_base64_is_rejected_before_decode(self):
        with mock.patch.object(voice_audio.base64, 'b64decode') as decode:
            self.assert_error(400, 'STT_AUDIO_TOO_LARGE', lambda: voice_audio.transcribe(
                'A' * (voice_audio.MAX_BASE64_CHARS + 1), opener=mock.Mock()))
        decode.assert_not_called()

    def test_oversized_decoded_file_is_rejected(self):
        self.assert_error(400, 'STT_AUDIO_TOO_LARGE', lambda: voice_audio.transcribe(
            encoded(b'\0' * (voice_audio.MAX_AUDIO_BYTES + 1)), opener=mock.Mock()))

    def test_invalid_wav_format_frames_and_chunk_sizes(self):
        invalid = [wav_audio(channels=2), wav_audio(width=1), wav_audio(width=3),
                   wav_audio(rate=48000), wav_audio(frames=0), wav_audio()[:-1],
                   wav_audio() + b'ignored tail', b'not a wave file' * 10]
        for offset, replacement in ((4, struct.pack('<I', 2)),
                                    (20, struct.pack('<H', 3)),
                                    (28, struct.pack('<I', 12345)),
                                    (32, struct.pack('<H', 4)),
                                    (40, struct.pack('<I', 99999)),
                                    (40, struct.pack('<I', 3199))):
            audio = bytearray(wav_audio())
            audio[offset:offset + len(replacement)] = replacement
            invalid.append(bytes(audio))
        for audio in invalid:
            with self.subTest(size=len(audio)):
                opener = mock.Mock()
                self.assert_error(400, 'STT_INVALID_AUDIO',
                                  lambda: voice_audio.transcribe(encoded(audio), opener=opener))
                opener.assert_not_called()

    def test_timeout_and_connection_error_messages_are_safe(self):
        for exception, status, code in (
                (TimeoutError('private upstream body'), 504, 'STT_TIMEOUT'),
                (urllib.error.URLError(TimeoutError(ENV['AZURE_SPEECH_KEY'])), 504, 'STT_TIMEOUT'),
                (urllib.error.URLError(ENV['AZURE_SPEECH_KEY']), 503, 'STT_UNAVAILABLE'),
                (OSError(ENV['AZURE_SPEECH_KEY']), 503, 'STT_UNAVAILABLE'),
                (http.client.IncompleteRead(b'private upstream body'), 502, 'STT_UPSTREAM_ERROR')):
            with self.subTest(exception=type(exception)):
                self.assert_error(status, code, lambda: voice_audio.transcribe(
                    encoded(), opener=mock.Mock(side_effect=exception)))

    def test_upstream_http_failures_and_redirects_are_safe(self):
        for upstream, status, code in ((400, 502, 'STT_UPSTREAM_ERROR'),
                                      (401, 503, 'STT_UNAVAILABLE'),
                                      (403, 503, 'STT_UNAVAILABLE'),
                                      (408, 504, 'STT_TIMEOUT'),
                                      (429, 503, 'STT_UNAVAILABLE'),
                                      (500, 502, 'STT_UPSTREAM_ERROR'),
                                      (503, 503, 'STT_UNAVAILABLE'),
                                      (504, 504, 'STT_TIMEOUT'),
                                      (301, 502, 'STT_UPSTREAM_ERROR'),
                                      (307, 502, 'STT_UPSTREAM_ERROR')):
            with self.subTest(upstream=upstream):
                exception = urllib.error.HTTPError(ENV['AZURE_SPEECH_ENDPOINT'], upstream,
                                                  ENV['AZURE_SPEECH_KEY'], {},
                                                  io.BytesIO(b'private upstream body'))
                self.assert_error(status, code, lambda: voice_audio.transcribe(
                    encoded(), opener=mock.Mock(side_effect=exception)))

    def test_non_200_response_without_http_error_is_rejected(self):
        self.assert_error(502, 'STT_UPSTREAM_ERROR', lambda: voice_audio.transcribe(
            encoded(), opener=mock.Mock(return_value=Response(status=302))))

    def test_explicit_speech_no_match_and_empty_transcript(self):
        for recognition in ('NoMatch', 'InitialSilenceTimeout', 'BabbleTimeout'):
            with self.subTest(recognition=recognition):
                self.assert_error(422, 'STT_NO_MATCH', lambda: voice_audio.transcribe(
                    encoded(), opener=mock.Mock(return_value=Response({
                        'RecognitionStatus': recognition}))))
        self.assert_error(422, 'STT_NO_MATCH', lambda: voice_audio.transcribe(
            encoded(), opener=mock.Mock(return_value=Response({
                'RecognitionStatus': 'Success', 'DisplayText': ' \n\t '}))))

    def test_malformed_or_oversized_upstream_responses(self):
        invalid = [b'not json', b'\xff', b'[]', b'null', b'{}',
                   b'{"RecognitionStatus":"Error"}',
                   b'{"RecognitionStatus":"Success"}',
                   b'{"RecognitionStatus":"Success","DisplayText":123}',
                   b'[' * 2000 + b']' * 2000,
                   b' ' * (voice_audio.MAX_RESPONSE_BYTES + 1),
                   json.dumps({'RecognitionStatus': 'Success',
                               'DisplayText': 'a' * 4001}).encode('utf-8')]
        for body in invalid:
            with self.subTest(response_size=len(body)):
                self.assert_error(502, 'STT_UPSTREAM_ERROR', lambda: voice_audio.transcribe(
                    encoded(), opener=mock.Mock(return_value=Response(body=body))))

    def test_response_read_is_bounded_to_64_kib_plus_sentinel(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = b'{"RecognitionStatus":"Success","DisplayText":"yes"}'
        voice_audio.transcribe(encoded(), opener=mock.Mock(return_value=response))
        response.read.assert_called_once_with(65537)


if __name__ == '__main__':
    unittest.main()
