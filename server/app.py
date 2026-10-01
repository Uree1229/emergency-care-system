"""병원 웹·환자 웹 공통 JSON API 서버 (S01).

실행:  python -m server.app --db data/demo.db --seed --port 8000
화면은 2~3초 주기로 이 서버의 상태만 조회한다. 의료·지도 API를 매 조회마다 부르지 않는다.
로그인이 없으므로 병원은 경로의 병원 ID로 구분한다(시연용 단순화).
"""
import argparse
import json
import re
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import db, service, synthetic
from .travel import TravelEstimator

STATIC = Path(__file__).parent / 'static'
ROUTES = []


def route(method, pattern):
    def wrap(fn):
        ROUTES.append((method, re.compile(f'^{pattern}$'), fn))
        return fn
    return wrap


# ---- 병원 웹
@route('GET', r'/api/hospitals')
def _(ctx):
    return service.list_hospitals(ctx.conn, ctx.now)


@route('GET', r'/api/hospitals/(?P<hid>[\w-]+)')
def _(ctx, hid):
    return service.hospital_view(ctx.conn, hid, ctx.now)


@route('PUT', r'/api/hospitals/(?P<hid>[\w-]+)/resources/(?P<rtype>[\w-]+)')
def _(ctx, hid, rtype):
    b = ctx.body
    return service.set_resource(ctx.conn, ctx.now, hid, rtype, b.get('capacity'), b.get('occupied'),
                                b.get('status', 'available'), actor=b.get('handled_by', 'hospital'),
                                expected_version=b.get('expected_version'))


@route('POST', r'/api/hospitals/(?P<hid>[\w-]+)/restrictions')
def _(ctx, hid):
    b = ctx.body
    return service.add_restriction(ctx.conn, ctx.now, hid, b.get('scope'), b.get('reason'), b.get('ends_at'),
                                   actor=b.get('handled_by', 'hospital'))


@route('DELETE', r'/api/hospitals/(?P<hid>[\w-]+)/restrictions/(?P<rid>\d+)')
def _(ctx, hid, rid):
    return service.clear_restriction(ctx.conn, ctx.now, hid, int(rid))


@route('GET', r'/api/hospitals/(?P<hid>[\w-]+)/inquiries')
def _(ctx, hid):
    return service.hospital_inquiries(ctx.conn, ctx.now, hid)


@route('POST', r'/api/inquiries/(?P<iid>\d+)/respond')
def _(ctx, iid):
    b = ctx.body
    return service.respond(ctx.conn, ctx.now, int(iid), b.get('action'), b.get('handled_by'), b.get('reason'),
                           b.get('question'), b.get('expected_version'))


@route('GET', r'/api/hospitals/(?P<hid>[\w-]+)/incoming')
def _(ctx, hid):
    return dict(incoming=service.incoming(ctx.conn, ctx.now, hid),
                affected=service.affected_patients(ctx.conn, ctx.now, hid))


@route('POST', r'/api/hospitals/(?P<hid>[\w-]+)/external-arrivals')
def _(ctx, hid):
    b = ctx.body
    return service.register_external_arrival(ctx.conn, ctx.now, hid, b.get('resource_demands'),
                                             b.get('occurred_at'), b.get('note'), b.get('handled_by', 'hospital'))


@route('POST', r'/api/hospitals/(?P<hid>[\w-]+)/patients/(?P<pid>[\w-]+)/(?P<act>decision|arrive|no-show|discharge)')
def _(ctx, hid, pid, act):
    b, actor = ctx.body, ctx.body.get('handled_by', 'hospital')
    if act == 'decision':
        return service.hospital_decision(ctx.conn, ctx.now, pid, hid, b.get('decision'), b.get('note'), actor)
    if act == 'arrive':
        return service.confirm_arrival(ctx.conn, ctx.now, pid, hid, actor)
    if act == 'no-show':
        return service.mark_no_show(ctx.conn, ctx.now, pid, hid, b.get('reason', '미도착'), actor)
    return service.discharge(ctx.conn, ctx.now, pid, hid, actor)


# ---- 환자 웹
@route('POST', r'/api/patients')
def _(ctx):
    b = dict(ctx.body)
    pid = b.pop('id', None) or f'P-{int(ctx.now * 1000) % 10**9:09d}'
    return service.create_patient(ctx.conn, ctx.now, pid, b)


@route('GET', r'/api/patients/(?P<pid>[\w-]+)')
def _(ctx, pid):
    return service.patient_view(ctx.conn, pid, ctx.now)


@route('PATCH', r'/api/patients/(?P<pid>[\w-]+)')
def _(ctx, pid):
    return service.update_patient(ctx.conn, ctx.now, pid, ctx.body)


@route('GET', r'/api/patients/(?P<pid>[\w-]+)/candidates')
def _(ctx, pid):
    return service.candidates(ctx.conn, ctx.now, pid, ctx.estimator)


@route('POST', r'/api/patients/(?P<pid>[\w-]+)/inquiries')
def _(ctx, pid):
    return service.send_inquiries(ctx.conn, ctx.now, pid, ctx.body.get('hospital_ids'))


@route('POST', r'/api/patients/(?P<pid>[\w-]+)/inquiries/(?P<iid>\d+)/(?P<act>retry|cancel|answer)')
def _(ctx, pid, iid, act):
    if act == 'retry':
        return service.retry_inquiry(ctx.conn, ctx.now, pid, int(iid))
    if act == 'cancel':
        return service.cancel_inquiry(ctx.conn, ctx.now, pid, int(iid), ctx.body.get('reason'))
    return service.answer_question(ctx.conn, ctx.now, pid, int(iid), ctx.body.get('answer'))


@route('POST', r'/api/patients/(?P<pid>[\w-]+)/(?P<act>confirm|depart|change-destination|cancel)')
def _(ctx, pid, act):
    b = ctx.body
    if act == 'confirm':
        return service.confirm_destination(ctx.conn, ctx.now, pid, b.get('inquiry_id'))
    if act == 'depart':
        return service.depart(ctx.conn, ctx.now, pid, ctx.estimator, b.get('lat'), b.get('lon'))
    if act == 'change-destination':
        return service.change_destination(ctx.conn, ctx.now, pid, b.get('inquiry_id'), ctx.estimator,
                                          b.get('lat'), b.get('lon'))
    return service.cancel_patient(ctx.conn, ctx.now, pid, b.get('reason'))


@route('GET', r'/api/events')
def _(ctx):
    q = ctx.query
    return service.events_since(ctx.conn, int(q.get('since', 0)), q.get('patient_id'), q.get('hospital_id'))


class Ctx:
    def __init__(self, conn, now, body, query, estimator):
        self.conn, self.now, self.body, self.query, self.estimator = conn, now, body, query, estimator


def make_handler(db_path, estimator, clock=lambda: int(time.time()), cors_origin='*'):
    """cors_origin: 화면을 다른 주소·포트에서 개발할 때 허용할 Origin. None이면 CORS 헤더를 보내지 않는다."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):   # 경로에 환자 ID가 있으므로 접근 로그를 남기지 않는다 (O04)
            pass

        def _send(self, status, payload, ctype='application/json; charset=utf-8'):
            data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self._cors()
            self.end_headers()
            self.wfile.write(data)

        def _static(self, path):
            rel = path.lstrip('/') or 'index.html'
            target = (STATIC / rel).resolve()
            if STATIC.resolve() not in target.parents or not target.is_file():
                return self._send(404, {'error': 'NOT_FOUND'})
            ctype = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
                     '.css': 'text/css; charset=utf-8'}.get(target.suffix, 'application/octet-stream')
            self._send(200, target.read_bytes(), ctype)

        def _dispatch(self, method):
            from urllib.parse import parse_qsl, urlsplit
            url = urlsplit(self.path)
            if method == 'GET' and not url.path.startswith('/api/'):
                return self._static(url.path)
            for m, pattern, fn in ROUTES:
                match = pattern.match(url.path)
                if m == method and match:
                    break
            else:
                return self._send(404, {'error': 'NOT_FOUND', 'message': '없는 경로'})
            try:
                length = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(length) or b'{}') if length else {}
                if not isinstance(body, dict):
                    raise service.ServiceError('INVALID', 'JSON 객체가 필요합니다', 400)
                conn = db.connect(db_path)
                try:
                    result = fn(Ctx(conn, clock(), body, dict(parse_qsl(url.query)), estimator), **match.groupdict())
                finally:
                    conn.close()
                self._send(200, result)
            except service.ServiceError as e:
                self._send(e.status, e.to_dict())
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._send(400, {'error': 'INVALID_JSON', 'message': 'UTF-8 JSON 형식 오류'})
            except Exception:
                traceback.print_exc()
                self._send(500, {'error': 'SERVER_ERROR', 'message': '서버 오류'})

        def do_GET(self):
            self._dispatch('GET')

        def do_POST(self):
            self._dispatch('POST')

        def do_PUT(self):
            self._dispatch('PUT')

        def do_PATCH(self):
            self._dispatch('PATCH')

        def do_DELETE(self):
            self._dispatch('DELETE')

        def do_OPTIONS(self):   # 브라우저 사전 요청(preflight)
            self.send_response(204)
            self._cors()
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, PATCH, DELETE, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.send_header('Access-Control-Max-Age', '600')
            self.send_header('Content-Length', '0')
            self.end_headers()

        def _cors(self):
            if cors_origin:
                self.send_header('Access-Control-Allow-Origin', cors_origin)

    return Handler


def main():
    ap = argparse.ArgumentParser(description='응급환자 병원 연결 시연 서버')
    ap.add_argument('--db', default='data/demo.db')
    ap.add_argument('--port', type=int, default=8000)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--seed', action='store_true', help='가상 병원 10곳을 넣는다 (기존 DB 덮어씀)')
    ap.add_argument('--scenario', choices=['inquiry', 'accepted', 'in_transit', 'sudden'],
                    help='--seed 후 시연 시나리오를 이 단계까지 만든다 (server/scenario.py)')
    ap.add_argument('--cors-origin', default='*', help="허용할 화면 Origin. 'none'이면 끔")
    args = ap.parse_args()
    if args.scenario and not args.seed:
        ap.error('--scenario는 --seed와 함께 쓰세요')
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    if args.seed:
        for suffix in ('', '-wal', '-shm'):
            Path(args.db + suffix).unlink(missing_ok=True)
    conn = db.connect(args.db)
    db.init(conn)
    if args.seed:
        synthetic.seed_hospitals(conn, int(time.time()))
    if args.scenario:
        from . import scenario
        scenario.build(conn, int(time.time()), args.scenario)
    conn.close()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(
        args.db, TravelEstimator(), cors_origin=None if args.cors_origin == 'none' else args.cors_origin))
    print(f'http://{args.host}:{args.port}  (DB: {args.db})')
    server.serve_forever()


if __name__ == '__main__':
    main()
