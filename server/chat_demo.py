"""대화 서버를 가상 데이터 전용 DB로 시연한다.

python -m server.chat_demo
http://127.0.0.1:18016/conversation-demo.html
"""
import argparse
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

from . import db, synthetic
from .app import make_handler
from .travel import TravelEstimator
from .inquiry_worker import InquiryWorker
from .system_status import RuntimeStatus


def main():
    parser = argparse.ArgumentParser(description='이송 대화 서버 시연 (가상 데이터 전용)')
    parser.add_argument('--db', default='data/chat-demo.db')
    parser.add_argument('--port', type=int, default=18016)
    args = parser.parse_args()
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(args.db)
    try:
        db.init(conn)
        if not conn.execute('SELECT 1 FROM hospitals LIMIT 1').fetchone():
            synthetic.seed_hospitals(conn, int(time.time()), occupancy=0)
        if conn.execute("SELECT 1 FROM hospitals WHERE source!='synthetic' LIMIT 1").fetchone():
            parser.error('시연에는 가상 병원 전용 DB를 사용하세요. 공공 자료 DB는 사용하지 않습니다.')
        if conn.execute("SELECT COUNT(*) FROM hospitals WHERE id IN ('H01','H04')").fetchone()[0] != 2:
            parser.error('시연 병원 H01·H04가 없습니다. 새 시연 DB 경로를 지정하세요.')
    finally:
        conn.close()
    # 수용 응답·확정은 사람이 시연한다. 초기 문의만 서버가 진행하며 외부 API는 쓰지 않는다.
    estimator = TravelEstimator(use_kakao=False)
    worker = InquiryWorker(args.db, estimator)
    runtime = RuntimeStatus()
    runtime.configure('planning', enabled=False)
    runtime.configure('inquiry', worker)
    server = ThreadingHTTPServer(('127.0.0.1', args.port),
                                make_handler(args.db, estimator, runtime=runtime))
    worker.start()
    print(f'http://127.0.0.1:{args.port}/conversation-demo.html', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        worker.stop()
        server.server_close()


if __name__ == '__main__':
    main()
