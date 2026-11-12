"""Run the real browser client against a NEW isolated synthetic loopback DB."""
import argparse
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import threading
import time

from server import db, service, call_records
from server.app import make_handler
from server.travel import TravelEstimator

ROOT=Path(__file__).resolve().parents[2]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--node',required=True)
    args=parser.parse_args();output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    path=output/'fixture.db';conn=db.connect(str(path));db.init(conn);now=int(time.time())
    for hid,lat in [('OPS-H1',35.16),('OPS-H2',35.18)]:
        service.upsert_hospital(conn,dict(id=hid,name='Synthetic '+hid,lat=lat,lon=126.85,capabilities=['응급의학'],source='synthetic'))
        service.set_resource(conn,now,hid,'ed_bed',4,0,source='synthetic')
    service.create_patient(conn,now-60,'OPS-P1',dict(reporter_role='transporter',lat=35.15,lon=126.85,
        age=47,symptoms='합성 검사',required_capabilities=['응급의학'],resource_demands={'ed_bed':1},consent=True))
    call_records.create(conn,now,'OPS-P1',dict(actor_id='synthetic-crew',client_record_id='seed-call',hospital_id='OPS-H1',
        call_started_at=now-30,call_ended_at=now-20,reported_outcome='can_accept',counterparty_role='emergency_staff',note='Synthetic shared call'))
    conn.close()
    http=ThreadingHTTPServer(('127.0.0.1',0),make_handler(str(path),TravelEstimator(use_kakao=False)))
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    try:
        result=subprocess.run([args.node,str(ROOT/'tests/browser/test_hospital_operations_http.mjs'),f'http://127.0.0.1:{http.server_port}',str(output/'report.json')],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
        (output/'stdout.log').write_text(result.stdout,encoding='utf-8');(output/'stderr.log').write_text(result.stderr,encoding='utf-8')
        print(result.stdout)
        if result.returncode:print(result.stderr)
        return result.returncode
    finally:http.shutdown();http.server_close();thread.join(timeout=3)


if __name__=='__main__':raise SystemExit(main())
