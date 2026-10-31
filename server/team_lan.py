"""Authenticated HTTPS same-origin gateway for a private-WiFi synthetic team test.

The existing full CommonRuntime stays on loopback and retains its DB lease.
No OS trust store, firewall, UI, database or upstream settings are changed.
"""
import argparse
import base64
import binascii
from contextlib import closing
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import ssl
import stat
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

FORMAT = 'team-private-wifi-https'
BASELINE = 'server-allocation-baseline-20261030-v1'
MAX_BODY = 16 * 1024 * 1024
UPSTREAM_RELAY_TIMEOUT = 35
FILES = ('rootCA.crt', 'rootCA.key', 'server.crt', 'server.key', 'access-token.txt')
PRIVATE = tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
       'te', 'trailer', 'transfer-encoding', 'upgrade'}


class LanError(ValueError):
    """Public diagnostics contain no request paths, credentials or patient values."""


def _ip(value):
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        raise LanError('RFC1918 IPv4 주소 또는 자체 시험용 127.0.0.1이 필요합니다.') from None
    if ip.version != 4 or not (str(ip) == '127.0.0.1' or any(ip in network for network in PRIVATE)):
        raise LanError('공인·링크로컬 주소는 사용하지 않습니다. 같은 WiFi의 사설 IPv4를 지정하세요.')
    return str(ip)


def _port(value):
    if type(value) is not int or not 1 <= value <= 65535:
        raise LanError('포트는 1~65535 정수여야 합니다.')
    return value


def _canonical_authority(value):
    """Compare HTTPS authorities without DNS or a socket; omitted port means 443."""
    if not isinstance(value, str) or re.fullmatch(r'[A-Za-z0-9.-]+(?::[0-9]{1,5})?', value) is None:
        return None
    try:
        parsed = urlsplit('https://'+value)
        port = parsed.port if parsed.port is not None else 443
        return (parsed.hostname.lower(), port) if 1 <= port <= 65535 else None
    except (ValueError, AttributeError):
        return None


def _allowed_authority(value, ip, port):
    authority = _canonical_authority(value)
    names = {ip, 'localhost'} if ip == '127.0.0.1' else {ip}
    return authority if authority and authority[0] in names and authority[1] == port else None


def _same_origin(value, authority):
    try:
        origin = urlsplit(value)
        return bool(authority and origin.scheme == 'https' and not (origin.path or origin.query or origin.fragment)
                    and _canonical_authority(origin.netloc) == authority)
    except (ValueError, TypeError):
        return False


def _path(value):
    path = Path(value).expanduser().absolute()
    if '..' in path.parts:
        raise LanError('상위 경로 별칭 없이 실제 파일 경로를 사용하세요.')
    for item in (*reversed(path.parents), path):
        if item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction()):
            raise LanError('링크·정션 경로는 사용하지 않습니다.')
        if item.exists():
            info = item.stat(follow_symlinks=False)
            if getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
                raise LanError('재분석 경로는 사용하지 않습니다.')
            if item == path and stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                raise LanError('하드링크 파일은 사용하지 않습니다.')
    return path.resolve()


def _bundle_path(value):
    folder = _path(value)
    root = Path(__file__).resolve().parents[1]
    if any(folder.is_relative_to(root/name) for name in
           ('server/static', 'patient-ui', 'hospital-ui', 'paramedic-ui')):
        raise LanError('비밀 bundle은 웹 공개 폴더 밖의 새 data 경로에 보관하세요.')
    return folder


def addresses():
    values = {'127.0.0.1'}
    try:
        for entry in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            try:
                values.add(_ip(entry[4][0]))
            except LanError:
                pass
    except OSError:
        pass
    return dict(addresses=sorted(values), default_https_port=18443,
                note='WiFi 어댑터의 주소를 직접 확인하세요. VPN·유선 주소도 목록에 있을 수 있습니다.')


def _openssl(value=None):
    selected = value or next((candidate for candidate in (
        'C:/Program Files/Git/usr/bin/openssl.exe', shutil.which('openssl'))
        if candidate and Path(candidate).is_file()), None)
    if not selected or not Path(selected).is_file():
        raise LanError('OpenSSL 실행 파일이 없습니다. --openssl로 실제 경로를 지정하세요.')
    return str(Path(selected).resolve())


def _run(executable, *args):
    try:
        subprocess.run([executable, *map(str, args)], check=True, timeout=30,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (subprocess.SubprocessError, OSError):
        raise LanError('인증서 생성·검증에 실패했습니다. IP·CA·유효기간을 확인하고 새 bundle을 준비하세요.') from None


def prepare(ip, output, *, openssl=None):
    ip, executable, folder = _ip(ip), _openssl(openssl), _bundle_path(output)
    if folder.exists():
        raise LanError('기존 폴더·파일을 덮어쓰지 않습니다. 새 bundle 경로를 지정하세요.')
    folder.mkdir(parents=True, exist_ok=False, mode=0o700)
    with (folder/'.gitignore').open('x', encoding='ascii') as stream:
        stream.write('*\n')  # Protect secrets even if --output is outside data/.
    with tempfile.TemporaryDirectory(prefix='certificate-', dir=folder) as temporary:
        work = Path(temporary)
        _run(executable, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-sha256', '-days', '14',
             '-subj', '/CN=EmergencyCare Team WiFi Test Root', '-addext', 'basicConstraints=critical,CA:TRUE',
             '-addext', 'keyUsage=critical,keyCertSign,cRLSign', '-keyout', folder/'rootCA.key', '-out', folder/'rootCA.crt')
        _run(executable, 'req', '-new', '-newkey', 'rsa:2048', '-nodes', '-sha256',
             '-subj', '/CN=EmergencyCare Team WiFi Test', '-keyout', folder/'server.key', '-out', work/'server.csr')
        ips = list(dict.fromkeys((ip, '127.0.0.1')))
        (work/'extensions.cnf').write_text('basicConstraints=critical,CA:FALSE\n'
            'keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n'
            'subjectAltName='+','.join('IP:'+item for item in ips)+',DNS:localhost\n', encoding='ascii')
        _run(executable, 'x509', '-req', '-in', work/'server.csr', '-CA', folder/'rootCA.crt',
             '-CAkey', folder/'rootCA.key', '-set_serial', '0x'+secrets.token_hex(19), '-days', '14',
             '-sha256', '-extfile', work/'extensions.cnf', '-out', folder/'server.crt')
    with (folder/'access-token.txt').open('x', encoding='ascii', newline='\n') as stream:
        stream.write(secrets.token_urlsafe(24)+'\n')
    for name in ('rootCA.key', 'server.key', 'access-token.txt'):
        (folder/name).chmod(0o600)
    manifest = dict(format=FORMAT, version=1, created_at=int(time.time()), ip=ip,
                    upstream_host='127.0.0.1', runtime_mode='full', baseline_id=BASELINE,
                    ca_sha256=hashlib.sha256((folder/'rootCA.crt').read_bytes()).hexdigest())
    with (folder/'manifest.json').open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return dict(prepared=True, bundle=str(folder), ip=ip, username='team',
                trust_certificate=str(folder/'rootCA.crt'), token_file=str(folder/'access-token.txt'),
                note='CA 신뢰·방화벽·기기 설정은 자동 변경하지 않았습니다. 비밀번호 파일 값은 별도로 확인하세요.')


def _unique(items):
    result = {}
    for key, value in items:
        if key in result:
            raise LanError('bundle 명세에 중복 필드가 있습니다.')
        result[key] = value
    return result


def load_bundle(folder):
    folder = _bundle_path(folder)
    try:
        if not folder.is_dir():
            raise LanError('완성된 bundle 디렉터리가 필요합니다.')
        manifest_path = _path(folder/'manifest.json')
        if manifest_path.stat().st_size > 16384:
            raise LanError('bundle 명세 크기가 올바르지 않습니다.')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'), object_pairs_hook=_unique)
        keys = {'format', 'version', 'created_at', 'ip', 'upstream_host', 'runtime_mode', 'baseline_id', 'ca_sha256'}
        if (not isinstance(manifest, dict) or set(manifest) != keys or manifest['format'] != FORMAT
                or type(manifest['version']) is not int or manifest['version'] != 1
                or type(manifest['created_at']) is not int or manifest['created_at'] <= 0
                or manifest['upstream_host'] != '127.0.0.1' or manifest['runtime_mode'] != 'full'
                or manifest['baseline_id'] != BASELINE or _ip(manifest['ip']) != manifest['ip']):
            raise LanError('지원되는 사설 WiFi full 실행 bundle이 아닙니다.')
        for name in FILES:
            item = _path(folder/name)
            if not item.is_file() or item.stat().st_size > 65536:
                raise LanError('bundle 파일이 없거나 형식이 올바르지 않습니다.')
        if hashlib.sha256((folder/'rootCA.crt').read_bytes()).hexdigest() != manifest['ca_sha256']:
            raise LanError('CA 인증서가 bundle 명세와 다릅니다.')
        token = (folder/'access-token.txt').read_text(encoding='ascii').strip()
        if re.fullmatch(r'[A-Za-z0-9_-]{32}', token) is None:
            raise LanError('접속 비밀번호 파일 형식이 올바르지 않습니다.')
        return folder, manifest, token
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, KeyError):
        raise LanError('bundle을 읽을 수 없습니다. 비밀 파일을 출력하지 말고 준비 상태를 확인하세요.') from None


def check_upstream(port):
    port = _port(port)
    try:
        with closing(http.client.HTTPConnection('127.0.0.1', port, timeout=5)) as conn:
            conn.request('GET', '/api/team-runtime', headers={'Host': f'127.0.0.1:{port}'})
            response = conn.getresponse()
            body = response.read(1024*1024+1)
            value = json.loads(body) if response.status == 200 and len(body) <= 1024*1024 else None
        if (not isinstance(value, dict) or value.get('revision') != 'team-runtime-20261030-v1'
                or value.get('mode') != 'full' or value.get('synthetic') is not True
                or value.get('baseline_id') != BASELINE or value.get('travel_provider') != 'synthetic'
                or value.get('solver_seconds') != 3 or value.get('inquiry_policy') != 'fixed_3'
                or value.get('external_hospital_messages') is not False
                or value.get('database', {}).get('valid') is not True
                or value.get('profile', {}).get('ok') is not True
                or value['profile'].get('profile_id') != 'team-runtime-profile-20261030-v1'
                or value['profile'].get('mode') != 'full' or value['profile'].get('source_count') != 21
                or value['profile'].get('source_equivalence') not in ('exact_bytes', 'eol_only')
                or any(value.get('workers', {}).get(name, {}).get('enabled') is not True
                    or value['workers'][name].get('state') != 'RUNNING'
                    or value['workers'][name].get('thread_alive') is not True for name in ('planning', 'inquiry'))):
            raise LanError('upstream은 두 워커가 RUNNING인 공통 full 합성 실행기여야 합니다.')
        return dict(valid=True, mode='full', upstream_port=port, baseline_id=BASELINE)
    except (OSError, http.client.HTTPException, UnicodeError, json.JSONDecodeError, TypeError, AttributeError):
        raise LanError('loopback upstream 상태를 확인할 수 없습니다. 공통 full 서버와 워커를 먼저 켜세요.') from None


class Gateway(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = False

    def server_bind(self):
        if os.name == 'nt' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def get_request(self):
        connection, address = self.socket.accept()
        connection.settimeout(5)
        try:
            return self.tls_context.wrap_socket(connection, server_side=True), address
        except BaseException:
            connection.close()
            raise


def make_gateway(bundle, upstream_port, *, port=18443):
    folder, manifest, token = load_bundle(bundle)
    upstream_port, port = _port(upstream_port), _port(port)
    if upstream_port == port:
        raise LanError('gateway와 upstream은 서로 다른 포트를 사용해야 합니다.')
    executable = _openssl()
    _run(executable, 'verify', '-purpose', 'sslserver', '-CAfile', folder/'rootCA.crt',
         '-verify_ip', manifest['ip'], folder/'server.crt')
    _run(executable, 'x509', '-in', folder/'server.crt', '-noout', '-checkend', '0')
    check_upstream(upstream_port)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        context.load_cert_chain(folder/'server.crt', folder/'server.key')
    except (OSError, ssl.SSLError):
        raise LanError('TLS 인증서와 키를 읽을 수 없습니다. 새 bundle을 준비하세요.') from None
    expected = ('team:'+token).encode('ascii')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Paths and request values can identify a patient.

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def reply(self, status, payload, *, authenticate=False):
            data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.security_headers()
            self.send_header('Connection', 'close')
            if authenticate:
                self.send_header('WWW-Authenticate', 'Basic realm="Team WiFi test", charset="UTF-8"')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)
            self.close_connection = True

        def security_headers(self):
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Strict-Transport-Security', 'max-age=86400')

        def send_error(self, code, message=None, explain=None):
            self.reply(code, {'error': 'INVALID_GATEWAY_REQUEST'})

        def relay(self):
            try:
                hosts = self.headers.get_all('Host', [])
                auths = self.headers.get_all('Authorization', [])
                origins = self.headers.get_all('Origin', [])
                authority = _allowed_authority(hosts[0], manifest['ip'], port) if len(hosts) == 1 else None
                if authority is None:
                    return self.reply(400, {'error': 'INVALID_HOST'})
                if len(origins) > 1 or (origins and not _same_origin(origins[0], authority)):
                    return self.reply(403, {'error': 'CROSS_ORIGIN_DENIED'})
                if self.headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site'):
                    return self.reply(403, {'error': 'CROSS_ORIGIN_DENIED'})
                if len(auths) != 1 or not auths[0].lower().startswith('basic '):
                    return self.reply(401, {'error': 'AUTHENTICATION_REQUIRED'}, authenticate=True)
                try:
                    credentials = base64.b64decode(auths[0][6:], validate=True)
                except (binascii.Error, ValueError):
                    credentials = b''
                if not hmac.compare_digest(credentials, expected):
                    return self.reply(401, {'error': 'AUTHENTICATION_REQUIRED'}, authenticate=True)
                lengths = self.headers.get_all('Content-Length', [])
                raw = lengths[0] if len(lengths) == 1 else '0'
                if (len(lengths) > 1 or not raw.isascii() or not raw.isdecimal() or len(raw) > 12
                        or self.headers.get('Transfer-Encoding') or self.headers.get('Expect')):
                    return self.reply(400, {'error': 'INVALID_BODY_LENGTH'})
                length = int(raw)
                if length > MAX_BODY:
                    return self.reply(413, {'error': 'BODY_TOO_LARGE'})
                parsed = urlsplit(self.path)
                if (not self.path.startswith('/') or self.path.startswith('//') or parsed.scheme or parsed.netloc
                        or any(ord(c) < 32 or ord(c) == 127 for c in self.path)):
                    return self.reply(400, {'error': 'INVALID_PATH'})
                body = self.rfile.read(length) if length else None
                if body is not None and len(body) != length:
                    return self.reply(400, {'error': 'INCOMPLETE_BODY'})
                connection_headers = {item.strip().lower() for item in self.headers.get('Connection', '').split(',')}
                excluded = HOP | connection_headers | {'authorization', 'cookie', 'host', 'content-length', 'forwarded'}
                headers = {key: value for key, value in self.headers.items()
                    if key.lower() not in excluded and not key.lower().startswith('x-forwarded-')}
                headers['Host'] = f'127.0.0.1:{upstream_port}'
                if length:
                    headers['Content-Length'] = str(length)
                with closing(http.client.HTTPConnection('127.0.0.1', upstream_port, timeout=UPSTREAM_RELAY_TIMEOUT)) as conn:
                    conn.request(self.command, self.path, body=body, headers=headers)
                    response = conn.getresponse()
                    content = response.read(MAX_BODY+1)
                    if len(content) > MAX_BODY:
                        return self.reply(502, {'error': 'UPSTREAM_RESPONSE_TOO_LARGE'})
                    self.send_response(response.status)
                    blocked = HOP | {item.strip().lower() for item in response.getheader('Connection', '').split(',')}
                    blocked |= {'content-length', 'set-cookie', 'www-authenticate', 'cache-control',
                                'x-content-type-options', 'referrer-policy', 'strict-transport-security'}
                    for key, value in response.getheaders():
                        if key.lower() not in blocked and not key.lower().startswith('access-control-'):
                            self.send_header(key, value)
                    self.security_headers()
                    self.send_header('Content-Length', str(len(content)))
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    if self.command != 'HEAD':
                        self.wfile.write(content)
                    self.close_connection = True
            except (OSError, http.client.HTTPException, ValueError, UnicodeError):
                self.reply(502, {'error': 'UPSTREAM_UNAVAILABLE'})

        do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = relay

    try:
        server = Gateway((manifest['ip'], port), Handler, bind_and_activate=False)
        server.tls_context = context
        server.server_bind()
        server.server_activate()
        return server
    except (OSError, ssl.SSLError):
        if 'server' in locals():
            server.server_close()
        raise LanError('HTTPS 주소·포트를 열 수 없습니다. WiFi IP와 포트 점유를 확인하세요.') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('addresses')
    prep = commands.add_parser('prepare')
    prep.add_argument('--ip', required=True)
    prep.add_argument('--output', required=True)
    prep.add_argument('--openssl')
    serve = commands.add_parser('serve')
    serve.add_argument('--bundle', required=True)
    serve.add_argument('--upstream-port', required=True, type=int)
    serve.add_argument('--port', type=int, default=18443)
    args = parser.parse_args(argv)
    try:
        if args.action == 'addresses':
            result = addresses()
        elif args.action == 'prepare':
            result = prepare(args.ip, args.output, openssl=args.openssl)
        else:
            server = make_gateway(args.bundle, args.upstream_port, port=args.port)
            try:
                print(json.dumps(dict(ready=True, url=f'https://{server.server_address[0]}:{server.server_port}',
                    upstream_port=args.upstream_port, username='team',
                    note='비밀번호는 bundle의 access-token.txt에서 확인하세요. CA 신뢰는 기기에서 수동 설정합니다.'),
                    ensure_ascii=False), flush=True)
                server.serve_forever(poll_interval=.1)
            finally:
                server.server_close()
            return 0
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    except KeyboardInterrupt:
        return 0
    except (LanError, OSError):
        print(json.dumps({'error': 'LAN_PREPARATION_FAILED',
            'message': '설정을 확인하세요. 기존 파일·OS 신뢰·방화벽은 변경하지 않았으며 비밀 값은 출력하지 않았습니다.'},
            ensure_ascii=False), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
