"""Serve executable assets consistently even with Windows MIME registry overrides."""
from unittest import TestCase
from unittest.mock import patch

from simulation.viewer_server import ViewerHandler


class ViewerMimeTests(TestCase):
    def test_script_mime_is_not_taken_from_windows_registry(self):
        handler = object.__new__(ViewerHandler)
        with patch('mimetypes.guess_type', return_value=('text/plain', None)):
            for name in ('app.js', 'APP.JS', 'module.mjs'):
                self.assertEqual(handler.guess_type(name), 'text/javascript; charset=utf-8')

    def test_styles_and_replay_data_have_explicit_mime_types(self):
        handler = object.__new__(ViewerHandler)
        for name, expected in [('styles.css', 'text/css'), ('map_replay_data.json', 'application/json'),
                               ('boundaries.geojson', 'application/geo+json'), ('index.html', 'text/html')]:
            self.assertEqual(handler.guess_type(name).split(';')[0], expected)
