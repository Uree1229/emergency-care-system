"""조회나 배정 솔버와 독립적으로 초기 문의 작업을 진행한다."""
import threading
import time

from .inquiry_workflow import run_once
from . import db, transport_review, transport_reassignment
from .system_status import WorkerHealth


class InquiryWorker:
    def __init__(self, path, estimator, poll_seconds=1.0, clock=lambda: int(time.time()), health=None):
        if poll_seconds <= 0:
            raise ValueError('poll_seconds must be positive')
        self.path, self.estimator = path, estimator
        self.poll_seconds, self.clock = poll_seconds, clock
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name='inquiry-worker', daemon=True)
        self.health = health if health is not None else WorkerHealth(clock=clock, stale_after_seconds=180)

    def run(self):
        while not self.stop_event.is_set():
            self.health.cycle_started()
            try:
                run_once(self.path, self.estimator, clock=self.clock)
                conn = db.connect(self.path)
                try:
                    with db.transaction(conn):
                        transport_review.scan_in_transaction(conn, int(self.clock()))
                finally:
                    conn.close()
                transport_reassignment.run_once(self.path, self.estimator, clock=self.clock)
            except Exception:
                self.health.cycle_failed('INQUIRY_FAILED')
                # 환자 내용이나 외부 API 예외를 출력하지 않는다.
                print('자동 문의 점검 실패: 저장 상태를 유지하고 다음 주기에 확인합니다.', flush=True)
            else:
                self.health.cycle_succeeded()
            self.stop_event.wait(self.poll_seconds)

    def start(self):
        self.health.worker_started()
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=5)
