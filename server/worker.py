"""단일 배정 워커. 작업 요청은 DB에 남으며 재시작 후에도 처리한다."""
import multiprocessing
import threading
import time
import uuid

from . import db, planning, service, solver_adapter
from .system_status import WorkerHealth


def _solve_child(data, seconds, pipe):
    try:
        from solver_improved import solve
        pipe.send(solve(data, seconds=seconds))
    except Exception:
        pipe.send((None, dict(status='SOLVER_ERROR')))
    finally:
        pipe.close()


def bounded_solve(data, seconds=3):
    from solver_improved import solve, validate
    fallback, meta = solve(data, seconds=0)
    if seconds == 0:
        return fallback, meta
    ctx = multiprocessing.get_context('spawn')
    reader, writer = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_solve_child, args=(data, seconds, writer), daemon=True)
    started = time.monotonic()
    try:
        proc.start()
        writer.close()
        if reader.poll(max(0, seconds - (time.monotonic() - started))):
            plan, info = reader.recv()
            if plan is not None and validate(data, plan):
                return plan, info
        meta.update(status='TIME_LIMIT_FALLBACK' if fallback is not None else 'NEEDS_REVIEW', fallback=True)
        return fallback, meta
    except (EOFError, OSError, RuntimeError):
        meta.update(status='SOLVER_ERROR_FALLBACK' if fallback is not None else 'NEEDS_REVIEW', fallback=True)
        return fallback, meta
    finally:
        if proc.pid is not None:
            if proc.is_alive():
                proc.terminate()
            proc.join(timeout=2)
        reader.close()
        writer.close()


def run_once(path, estimator, clock=lambda: int(time.time()), seconds=3, runner=bounded_solve):
    conn = db.connect(path)
    owner = uuid.uuid4().hex
    try:
        now = clock()
        with db.transaction(conn):
            service.expire_acceptances(conn, now)
            state = conn.execute('SELECT * FROM planning_state WHERE id=1').fetchone()
            expired = conn.execute('SELECT 1 FROM planning_results WHERE expires_at<=? LIMIT 1', (now,)).fetchone()
            if state['lease_until'] > now or (state['revision'] == state['completed_revision'] and not expired):
                return False
            conn.execute('UPDATE planning_state SET lease_owner=?, lease_until=? WHERE id=1', (owner, now + 300))
            snapshot = planning.capture(conn, now)
        # 계산과 외부 API 요청은 DB 쓰기 잠금 밖에서 실행한다.
        model, context = solver_adapter.build(snapshot, estimator)
        if not model['capacities'] or any(c.get('blocking_review') for c in context.values()):
            plan, meta = None, dict(status='NEEDS_REVIEW', reason='UNSUPPORTED_EXISTING_DEMAND')
        elif len(model['patients']) > 64 or len(model['capacities']) > 32:
            plan, meta = None, dict(status='NEEDS_REVIEW', reason='DEMO_SIZE_LIMIT')
        else:
            plan, meta = runner(model, seconds)
        results = solver_adapter.present(snapshot, model, context, plan, meta)
        return planning.publish(conn, snapshot, results, clock(), owner)
    except Exception:
        # 내부 오류·환자 내용은 API나 로그에 노출하지 않는다. 다음 루프에서 재시도한다.
        with db.transaction(conn):
            conn.execute("UPDATE planning_state SET lease_until=?, lease_owner=NULL, last_error='PLANNING_FAILED' "
                         'WHERE id=1 AND lease_owner=?', (clock() + 5, owner))
        raise
    finally:
        conn.close()


class Worker:
    def __init__(self, path, estimator, seconds=3, health=None):
        self.path, self.estimator, self.seconds = path, estimator, seconds
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name='planning-worker', daemon=True)
        self.health = health if health is not None else WorkerHealth(stale_after_seconds=360)

    def run(self):
        while not self.stop_event.is_set():
            self.health.cycle_started()
            try:
                run_once(self.path, self.estimator, seconds=self.seconds)
            except Exception:
                self.health.cycle_failed('PLANNING_FAILED')
                print('배정 계산 실패: DB 상태를 유지하고 재시도합니다.')
            else:
                self.health.cycle_succeeded()
            self.stop_event.wait(1)

    def start(self):
        self.health.worker_started()
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=5)
