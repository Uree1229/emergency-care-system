"""조회나 배정 솔버와 독립적으로 초기 문의 작업을 진행한다."""
import threading
import time

from .inquiry_workflow import run_once
from . import db, transport_review, transport_reassignment


class InquiryWorker:
    def __init__(self, path, estimator, poll_seconds=1.0, clock=lambda: int(time.time())):
        if poll_seconds <= 0:
            raise ValueError('poll_seconds must be positive')
        self.path, self.estimator = path, estimator
        self.poll_seconds, self.clock = poll_seconds, clock
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name='inquiry-worker', daemon=True)

    def run(self):
        while not self.stop_event.is_set():
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
                # 환자 내용이나 외부 API 예외를 출력하지 않는다.
                print('자동 문의 점검 실패: 저장 상태를 유지하고 다음 주기에 확인합니다.', flush=True)
            self.stop_event.wait(self.poll_seconds)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=5)
