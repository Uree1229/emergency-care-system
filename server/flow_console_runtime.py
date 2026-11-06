"""Attach the local observer to an existing synthetic runtime, or launch it explicitly.

python -B -m server.flow_console_runtime --db data/flow-console-demo.db --mode voice-only
The base runtime owns DB validation, its lease, workers and non-destructive startup.
"""
import argparse
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

from . import flow_console

REVISION = 'flow-console-runtime-20261103-v1'


def _synthetic(owner, data_mode):
    if data_mode != 'synthetic' or getattr(owner, 'data_mode', 'synthetic') != 'synthetic':
        raise ValueError('관제 화면은 로컬 합성 실행에만 연결합니다.')
    if getattr(owner, 'host', '127.0.0.1') != '127.0.0.1':
        raise ValueError('관제 화면은 127.0.0.1에서만 실행합니다.')
    if not isinstance(getattr(owner, 'path', None), (str, Path)) or not str(owner.path):
        raise ValueError('실행기의 DB 경로를 확인하세요.')


def wrap_handler(parent, owner, data_mode='synthetic'):
    """Add one read-only GET route and delegate all other methods/routes intact."""
    _synthetic(owner, data_mode)
    if not isinstance(parent, type) or not callable(getattr(parent, '_dispatch', None)):
        raise TypeError('기존 요청 처리기가 필요합니다.')
    if getattr(parent, '_flow_console_revision', None) == REVISION:
        if Path(parent._flow_console_path).resolve() != Path(owner.path).resolve():
            raise ValueError('다른 DB의 관제 처리기를 중복 연결할 수 없습니다.')
        return parent

    class Handler(parent):
        _flow_console_revision = REVISION
        _flow_console_path = str(Path(owner.path).resolve())

        def _dispatch(self, method):
            url = urlsplit(self.path)
            if method == 'GET' and url.path == '/api/flow-console':
                if getattr(owner, 'data_mode', 'synthetic') != 'synthetic':
                    return self._send(404, {'error': 'FLOW_CONSOLE_UNAVAILABLE'})
                try:
                    if url.scheme or url.netloc or url.fragment:
                        return self._send(400, {'error': 'INVALID_QUERY'})
                    value = flow_console.snapshot(owner.path, int(time.time()),
                        data_mode='synthetic', **flow_console.parse_query(url.query))
                except flow_console.FlowConsoleError as error:
                    return self._send(error.status, error.response())
                return self._send(200, value)
            return super()._dispatch(method)

    return Handler


def install_console(runtime):
    """Install once after start(), without replacing or starting an HTTP server."""
    _synthetic(runtime, getattr(runtime, 'data_mode', 'synthetic'))
    http = getattr(runtime, 'http', None)
    if http is None or not isinstance(getattr(http, 'server_address', None), tuple) or http.server_address[0] != '127.0.0.1':
        raise ValueError('시작된 127.0.0.1 실행기에만 관제를 연결합니다.')
    http.RequestHandlerClass = wrap_handler(http.RequestHandlerClass, runtime)
    return runtime


def _port(value):
    try:
        result = int(value)
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError('포트는 0~65535 정수입니다.') from None
    if not 0 <= result <= 65535:
        raise argparse.ArgumentTypeError('포트는 0~65535 정수입니다.')
    return result


def argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, help='합성 공통 실행 전용 DB 또는 새 DB 경로')
    parser.add_argument('--port', type=_port, default=18035)
    parser.add_argument('--mode', choices=('full', 'voice-only'), default='voice-only')
    parser.add_argument('--run-dir', type=Path, help='선택: 이번 실행 전용 새 영수증 폴더')
    return parser


def _receipt(directory, name, value):
    if directory is not None:
        with (directory / name).open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')


def main(argv=None):
    args = argument_parser().parse_args(argv)
    # Import only for an explicit CLI launch. Pure wrappers have stdlib-only IO.
    from .team_runtime import CommonRuntime
    runtime = None
    directory = None
    started = False
    graceful = False
    reason = 'startup_failed'
    error_type = None
    exit_code = 1
    try:
        if args.run_dir is not None:
            candidate = args.run_dir.resolve()
            candidate.mkdir(parents=True, exist_ok=False)
            directory = candidate
        runtime = CommonRuntime(args.db, port=args.port, mode=args.mode, host='127.0.0.1')
        runtime.start()
        install_console(runtime)
        started = True
        console_url = runtime.base_url + '/flow-console.html'
        ready = dict(revision=REVISION, pid=os.getpid(), host='127.0.0.1', mode=args.mode,
                     database=str(Path(runtime.path).resolve()), base_url=runtime.base_url,
                     console_url=console_url, started_at=int(time.time()))
        _receipt(directory, 'ready.json', ready)
        print(console_url, flush=True)
        while runtime.thread.is_alive():
            if directory is not None and (directory / 'stop-requested.json').is_file():
                reason = 'stop_requested'
                break
            runtime.thread.join(timeout=.25)
        else:
            reason = 'server_thread_ended'
        exit_code = 0
    except KeyboardInterrupt:
        reason = 'keyboard_interrupt'
        exit_code = 0
    except Exception as error:
        error_type = type(error).__name__
        print(json.dumps(dict(error='FLOW_CONSOLE_START_FAILED', message=str(error)), ensure_ascii=False), flush=True)
    finally:
        if runtime is not None:
            try:
                runtime.close()
                graceful = started
            except Exception as error:
                error_type = type(error).__name__
                graceful = False
                exit_code = 1
        if directory is not None and directory.is_dir():
            try:
                _receipt(directory, 'stopped.json', dict(revision=REVISION, pid=os.getpid(),
                    started=started, graceful=graceful, reason=reason,
                    stopped_at=int(time.time()), error_type=error_type))
            except OSError:
                exit_code = 1
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
