"""Serve the reading studio through the real app handler and an isolated DB.

Run: python -B -m unittest test_paramedic_studio_static -v
"""
import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from server import db
from server.app import make_handler
from server.travel import TravelEstimator


ROOT = Path(__file__).resolve().parent


class ReadingStudioStaticHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        path = str(Path(cls.tmp.name) / 'studio-static.db')
        conn = db.connect(path)
        try:
            db.init(conn)
        finally:
            conn.close()
        cls.http = ThreadingHTTPServer(
            ('127.0.0.1', 0),
            make_handler(path, TravelEstimator(use_kakao=False)),
        )
        cls.thread = threading.Thread(
            target=cls.http.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True,
        )
        cls.thread.start()
        cls.addClassCleanup(cls.stop_server)

    @classmethod
    def stop_server(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join(timeout=2)

    def get(self, path):
        connection = http.client.HTTPConnection(
            '127.0.0.1', self.http.server_port, timeout=5,
        )
        try:
            # HTTPConnection sends the supplied path without normalizing '..'.
            connection.request('GET', path)
            response = connection.getresponse()
            return response.status, response.getheader('Content-Type', ''), response.read()
        finally:
            connection.close()

    def asset(self, relative, content_type):
        status, actual_type, body = self.get('/paramedic/' + relative)
        self.assertEqual(status, 200, relative)
        self.assertTrue(actual_type.startswith(content_type), (relative, actual_type))
        self.assertEqual(body, (ROOT / 'paramedic-ui' / relative).read_bytes())
        self.assertTrue(body, relative)
        return body

    def test_studio_entry_loads_the_presentation_module_and_stylesheet(self):
        html = self.asset('command-practice.html', 'text/html').decode('utf-8')
        self.assertIn('src="./reading-studio.mjs"', html)
        self.assertIn('href="./reading-studio.css"', html)

    def test_studio_styles_and_module_dependencies_have_browser_mime_types(self):
        self.asset('reading-studio.css', 'text/css')
        for name in ('reading-studio.mjs', 'command-commit.mjs', 'command-trace.mjs'):
            with self.subTest(name=name):
                self.asset(name, 'text/javascript')

    def test_noise_comparison_and_recovery_dependencies_are_served_without_a_build(self):
        html = self.asset('noise-compare.html', 'text/html').decode('utf-8')
        self.assertIn('noise-compare.js', html)
        self.asset('noise-compare.css', 'text/css')
        for name in (
            'noise-compare.js', 'noise-suppression.mjs', 'main-recovery.mjs',
            'vendor/jitsi-rnnoise-wasm-0.2.1/rnnoise-sync.mjs',
        ):
            with self.subTest(name=name):
                self.asset(name, 'text/javascript')

    def test_nested_svg_masks_are_served_as_images(self):
        for name in (
            'microphone-fill.svg', 'microphone.svg', 'gear-six.svg', 'archive.svg',
            'caret-right.svg', 'x.svg', 'check-circle.svg',
        ):
            with self.subTest(name=name):
                body = self.asset('studio-icons/' + name, 'image/svg+xml')
                self.assertTrue(body.lstrip().startswith(b'<svg'))

    def test_nested_bundled_font_has_woff2_mime_and_signature(self):
        body = self.asset('studio-icons/PretendardVariable.woff2', 'font/woff2')
        self.assertEqual(body[:4], b'wOF2')

    def test_nested_asset_support_does_not_allow_directory_escape(self):
        self.assertTrue((ROOT / 'server' / 'app.py').is_file())
        for path in (
            '/paramedic/../server/app.py',
            '/paramedic/%2e%2e/server/app.py',
            '/paramedic/%2e%2e%2fserver/app.py',
            '/paramedic/studio-icons/../../server/app.py',
            '/paramedic/studio-icons/%2e%2e/%2e%2e/server/app.py',
        ):
            with self.subTest(path=path):
                status, content_type, body = self.get(path)
                self.assertEqual(status, 404)
                self.assertTrue(content_type.startswith('application/json'))
                self.assertEqual(json.loads(body)['error'], 'NOT_FOUND')


if __name__ == '__main__':
    unittest.main()
