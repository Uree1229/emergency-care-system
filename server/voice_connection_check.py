"""Run the app's actual HTTP client against a private synthetic voice server.

No user UI, microphone, speech provider, running demo DB, or hospital is used.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time

from . import db, service
from .app import make_handler
from .travel import TravelEstimator

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = ('server/voice_connection_check.py', 'server/voice_client_connection_probe.mjs',
    'server/voice_patient_flow_probe.mjs',
    'server/voice_commands.py', 'server/voice_lexicon.py', 'server/voice_dictionary_cli.py', 'config/voice_commands.json',
    'server/voice_treatment_review.py',
    'server/voice_input.py', 'server/voice_followup.py', 'server/voice_terms.py', 'server/voice_r1.py',
    'server/voice_audio.py', 'server/app.py', 'server/db.py', 'server/service.py',
    'config/paramedic_voice_terms.json', 'paramedic-ui/voice-client.mjs')


def fingerprints():
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def run(output, node, *, patient_flow=False):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    before = fingerprints()
    report = dict(fixture=True, actual_speech_count=0, actual_hospital_requests=0,
        mode='synthetic_patient_flow' if patient_flow else 'draft_only',
        frontend_import='read_only:paramedic-ui/voice-client.mjs', source_hashes_before=before)
    try:
        with tempfile.TemporaryDirectory(prefix='isolated-', dir=output) as directory:
            assert Path(directory).resolve().is_relative_to(output)
            path = str(Path(directory)/'voice.db')
            conn=db.connect(path);db.init(conn)
            if patient_flow:
                for hid, lat in (('VOICE-FLOW-H1',35.17),('VOICE-FLOW-H2',35.20)):
                    service.upsert_hospital(conn,dict(id=hid,name='Synthetic '+hid,lat=lat,lon=126.85,
                        capabilities=['응급의학'],source='synthetic'))
                    service.set_resource(conn,int(time.time()),hid,'ed_bed',2,0,source='synthetic')
            conn.close()
            http=ThreadingHTTPServer(('127.0.0.1',0),make_handler(path,TravelEstimator(use_kakao=False)))
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            try:
                probe='voice_patient_flow_probe.mjs' if patient_flow else 'voice_client_connection_probe.mjs'
                process=subprocess.run([str(node),str(ROOT/'server'/probe),
                    f'http://127.0.0.1:{http.server_port}',str(output/'client-report.json')],
                    cwd=ROOT, capture_output=True,text=True,encoding='utf-8',timeout=45,
                    **({'creationflags':subprocess.CREATE_NO_WINDOW} if hasattr(subprocess,'CREATE_NO_WINDOW') else {}))
                report.update(client_exit_code=process.returncode,stdout=process.stdout,stderr=process.stderr)
                conn=db.connect(path)
                report['entity_counts']={name:conn.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
                    for name in ('patients','inquiries','voice_sessions','voice_utterances')}
                conn.close()
                report['workflow']=json.loads((output/'client-report.json').read_text(encoding='utf-8')) if (output/'client-report.json').exists() else None
            finally:
                http.shutdown();http.server_close();thread.join(timeout=3)
    except Exception as error:
        report['error']=repr(error)
    after=fingerprints();report['source_hashes_after']=after;report['sources_stable']=before==after
    expected={'patients':1,'inquiries':1} if patient_flow else {'patients':0,'inquiries':0}
    report['expected_entity_counts']=expected
    workflow=report.get('workflow') or {}
    report['pass']=(report.get('client_exit_code')==0
        and all(report.get('entity_counts',{}).get(key)==value for key,value in expected.items())
        and workflow.get('failed')==0 and workflow.get('passed',0)>0
        and report['sources_stable'] and not report.get('error'))
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'pass':report['pass'],'workflow_steps':report.get('workflow',{}).get('passed') if report.get('workflow') else 0,
        'sources_stable':report['sources_stable'],'actual_speech_count':0,'output':str(output),'error':report.get('error')},ensure_ascii=False))
    return report['pass']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node',default=shutil.which('node'),help='Node executable; frontend files are only imported, never edited')
    parser.add_argument('--patient-flow',action='store_true',help='Use a private synthetic patient/hospital fixture to verify submission through arrival')
    parser.add_argument('--output',default=str(ROOT/'outputs'/'voice_connection_20261030'/datetime.now(timezone(timedelta(hours=9))).strftime('%Y%m%d_%H%M%S')))
    args=parser.parse_args()
    if not args.node or not Path(args.node).is_file():parser.error('Node 실행 파일을 --node로 지정하세요.')
    if Path(args.output).exists():parser.error('기존 결과를 덮어쓰지 않도록 새 output 경로를 사용하세요.')
    raise SystemExit(0 if run(args.output,args.node,patient_flow=args.patient_flow) else 1)


if __name__=='__main__':
    main()
