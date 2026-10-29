"""Generate the shared map replay and serve only its public viewer assets.

Run from the repository root: python -m simulation.viewer_server
This local presentation viewer does not expose the project database or credentials.
"""
import argparse
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import webbrowser


ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / 'simulation' / 'viewer'


class ViewerHandler(SimpleHTTPRequestHandler):
    # Windows registry MIME associations can map .js to text/plain. With
    # nosniff that prevents all playback code from executing in the browser.
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        '.html': 'text/html; charset=utf-8',
        '.js': 'text/javascript; charset=utf-8',
        '.mjs': 'text/javascript; charset=utf-8',
        '.css': 'text/css; charset=utf-8',
        '.json': 'application/json; charset=utf-8',
        '.geojson': 'application/geo+json; charset=utf-8',
        '.svg': 'image/svg+xml',
    }

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        super().end_headers()

    def list_directory(self, path):
        self.send_error(404, 'Directory listing is disabled')
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description='응급이송 알고리즘 지도 재생 화면')
    parser.add_argument('--port', type=int, default=18005)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--reuse-data', action='store_true', help='기존 재생 JSON을 다시 생성하지 않음')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    output = VIEWER / 'map_replay_data.json'
    if not args.reuse_data:
        generated = subprocess.run(
            [sys.executable, '-m', 'simulation.map_replay', '--output', str(output)],
            cwd=str(ROOT), check=False,
        )
        if generated.returncode:
            return generated.returncode
    if not output.is_file() or not (VIEWER / 'index.html').is_file():
        parser.error('재생 화면 또는 데이터가 없습니다. --reuse-data 없이 다시 실행하세요.')
    handler = functools.partial(ViewerHandler, directory=str(VIEWER))
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), handler)
    except OSError as error:
        print(f'포트 {args.port}에서 실행할 수 없습니다: {error}. --port로 다른 포트를 지정하세요.', file=sys.stderr)
        return 1
    url = f'http://127.0.0.1:{args.port}/'
    print(f'지도 시뮬레이션: {url}', flush=True)
    print('종료: Ctrl+C. 위치·도로 경로 외 자원·환자·응답은 합성입니다. 자동차 예상시간이며 실제 구급차 시간은 아닙니다.', flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
