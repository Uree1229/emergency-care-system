"""Paired reference/STT evaluation; no parser tuning, clinical writes or network.

The corpus is synthetic development material. Actual recognizer outputs must be
explicitly imported, and are never pooled with fabricated recognition errors.
"""
import argparse
import csv
import hashlib
import html
import io
import importlib
import json
import math
from pathlib import Path
import time
from datetime import datetime, timezone

from server import voice_terms, voice_input, voice_r1, voice_followup, voice_commands, voice_lexicon
from .metrics import score_prediction, summarize, text_error, timing_summary

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = Path(__file__).with_name('corpus_dev.json')
SOURCES = ('server/voice_terms.py', 'server/voice_input.py', 'server/voice_r1.py', 'server/voice_followup.py', 'config/paramedic_voice_terms.json',
           'server/voice_commands.py', 'config/voice_commands.json', 'server/voice_lexicon.py',
           'server/voice_treatment_review.py',
           'speech_eval/__init__.py', 'speech_eval/__main__.py',
           'speech_eval/runner.py', 'speech_eval/metrics.py')
SOURCES_ALLOWED = {'human_voice', 'synthetic_corruption', 'manual_transcript'}
ACTIONS = {'dictation', 'review', 'undo', 'confirm', 'submit', 'edit', 'add', 'observe', 'unknown',
           'missing', 'remove', 'history', 'help', 'repeat_question'}
SCORED_FIELDS = frozenset(('age', 'age_group', 'sex', 'symptoms', 'denied_symptoms',
    'uncertain_symptoms', 'consciousness', 'breathing', 'systolic_bp', 'diastolic_bp',
    'pulse', 'spo2', 'temperature', 'respiratory_rate', 'glucose', 'onset_at'))


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Nonfinite JSON values are not supported')
    if isinstance(value, dict):
        for child in value.values():
            _finite(child)
    elif isinstance(value, list):
        for child in value:
            _finite(child)


def load_json(path):
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    _finite(value)
    return value


def corpus(path=DEFAULT_CORPUS):
    value = load_json(path)
    if not isinstance(value, dict):
        raise ValueError('Corpus must be a JSON object')
    if type(value.get('schema_version')) is not int or value['schema_version'] != 1 or not isinstance(value.get('dataset_id'), str):
        raise ValueError('Unsupported corpus schema')
    if type(value.get('observed_at')) is not int or not 0 <= value['observed_at'] <= (1 << 63) - 1:
        raise ValueError('A fixed nonnegative integer observed_at is required')
    cases = value.get('cases')
    if not isinstance(cases, list) or not cases or len(cases) > 5000:
        raise ValueError('Corpus needs 1–5000 cases')
    seen = set()
    for row in cases:
        if not isinstance(row, dict):
            raise ValueError('Each corpus case must be an object')
        cid = row.get('id')
        if not isinstance(cid, str) or not cid or len(cid) > 80 or cid in seen:
            raise ValueError('Every case needs a unique bounded ID')
        seen.add(cid)
        for key in ('family', 'template_group', 'reference_text'):
            if not isinstance(row.get(key), str) or not row[key].strip() or len(row[key]) > 4000:
                raise ValueError(f'{cid}: {key} required')
        if row.get('mode') not in ('dictation', 'command'):
            raise ValueError(f'{cid}: explicit dictation/command mode required')
        expected = row.get('expected', {})
        if not isinstance(expected, dict) or expected.get('action') not in ACTIONS or not isinstance(expected.get('fields'), dict):
            raise ValueError(f'{cid}: expected action and fields required')
        if row['mode'] == 'dictation' and expected['action'] != 'dictation':
            raise ValueError(f'{cid}: dictation is not an executable command')
        if set(expected['fields']) - SCORED_FIELDS:
            raise ValueError(f'{cid}: labels outside the explicitly annotated 16-field scope')
        voice_terms.normalize_fields(expected['fields'])
        if expected.get('review_field') is not None and expected['review_field'] not in voice_terms.READBACK_FIELDS:
            raise ValueError(f'{cid}: unsupported review field')
        if not isinstance(row.get('tags'), list) or not all(isinstance(v, str) for v in row['tags']):
            raise ValueError(f'{cid}: tags must be strings')
    value['_path'] = str(Path(path).resolve())
    return value


def observations(path, data):
    if path is None:
        return []
    path = Path(path)
    if path.stat().st_size > 25 * 1024 * 1024:
        raise ValueError('Observation file exceeds 25 MiB')
    if path.suffix == '.jsonl':
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
        if '_path' not in data:
            raise ValueError('Load a corpus from disk for version-bound JSONL imports')
        expected_hash = hashlib.sha256(Path(data['_path']).read_bytes()).hexdigest()
        if any(not isinstance(row, dict) or row.get('dataset_id') != data['dataset_id'] or row.get('corpus_sha256') != expected_hash for row in rows):
            raise ValueError('Every JSONL row needs matching dataset_id and corpus_sha256')
    else:
        package = load_json(path)
        if not isinstance(package, dict) or type(package.get('schema_version')) is not int or package['schema_version'] != 1 or package.get('dataset_id') != data['dataset_id']:
            raise ValueError('Observation dataset_id/schema does not match the corpus')
        if package.get('qa_fixture'):
            raise ValueError('QA-only downloads must not be evaluated as measured recordings')
        if '_path' not in data:
            raise ValueError('Load the corpus from disk to verify an observation corpus hash')
        canonical_hash = hashlib.sha256(Path(data['_path']).read_bytes()).hexdigest()
        if package.get('corpus_sha256') != canonical_hash:
            raise ValueError('Observation corpus hash does not match the current annotations')
        rows = package.get('recordings')
    if not isinstance(rows, list) or len(rows) > 10000:
        raise ValueError('recordings must be a list of at most 10000 attempts')
    ids, known = set(), {case['id'] for case in data['cases']}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Each recording must be a JSON object')
        _finite(row)
        for key in ('recording_id', 'case_id', 'source', 'engine', 'condition'):
            if not isinstance(row.get(key), str) or not row[key].strip() or len(row[key]) > 160:
                raise ValueError(f'Recording {key} is required')
        rid = row['recording_id']
        if rid in ids:
            raise ValueError(f'Duplicate recording_id: {rid}')
        ids.add(rid)
        if row['case_id'] not in known:
            raise ValueError(f'{rid}: unknown case_id')
        if row['source'] not in SOURCES_ALLOWED or row.get('status') not in ('ok', 'no_match', 'error'):
            raise ValueError(f'{rid}: explicit source and status required')
        hypothesis = row.get('hypothesis', '')
        if not isinstance(hypothesis, str) or len(hypothesis) > 4000:
            raise ValueError(f'{rid}: hypothesis must contain at most 4000 characters')
        if row['status'] == 'ok' and not hypothesis.strip():
            raise ValueError(f'{rid}: empty successful recognition must be marked no_match')
        if row['status'] != 'ok' and hypothesis.strip():
            raise ValueError(f'{rid}: unsuccessful recognition cannot carry an evaluated hypothesis')
        if row['source'] == 'human_voice':
            if row.get('qa_fixture'):
                raise ValueError(f'{rid}: mocked QA fixtures are not measured human speech')
            if not isinstance(row.get('speaker_id'), str) or not row['speaker_id'].strip():
                raise ValueError(f'{rid}: use an anonymous speaker_id for human speech')
            if row.get('reference_verified') is not True:
                raise ValueError(f'{rid}: confirm that the synthetic script was actually read as written')
        if not isinstance(row.get('timing', {}), dict):
            raise ValueError(f'{rid}: timing must be an object; missing measurements stay absent')
    return rows


def predict(case, text, observed_at):
    start = time.perf_counter_ns()
    try:
        if case['mode'] == 'command':
            command = voice_terms.parse_command(text)
            action = command['action']
            extraction = voice_input._extract(command['text'], observed_at) if action in ('edit', 'add', 'observe') else {'fields': {}, 'evidence': {}, 'warnings': []}
            result = {'action': action, 'fields': extraction['fields'], 'review_field': command.get('field') if action == 'review' else None,
                      'evidence': extraction['evidence'], 'warnings': extraction['warnings']}
        else:
            extraction = voice_input._extract(text, observed_at)
            result = {'action': 'dictation', **extraction}
    except Exception as exc:
        result = {'action': 'parser_error', 'fields': {}, 'warnings': [],
                  'error': {'type': type(exc).__name__, 'code': getattr(exc, 'code', 'PARSER_ERROR')}}
    result['parser_runtime_ms'] = (time.perf_counter_ns() - start) / 1_000_000
    result['unscored_fields'] = {k: v for k, v in result['fields'].items() if k not in SCORED_FIELDS}
    result['fields'] = {k: v for k, v in result['fields'].items() if k in SCORED_FIELDS}
    return result


def _score(case, prediction):
    row = score_prediction(case['expected'], prediction)
    row.update(id=case['id'], family=case['family'], template_group=case['template_group'],
               reference_text=case['reference_text'], mode=case['mode'], expected=case['expected'],
               prediction=prediction, tags=case['tags'])
    return row


def fingerprints(corpus_path, recording_path=None):
    paths = {name: ROOT / name for name in SOURCES}
    paths['corpus'] = Path(corpus_path)
    if recording_path is not None:
        paths['recordings'] = Path(recording_path)
    return {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}


def evaluate(data, records):
    cases = {case['id']: case for case in data['cases']}
    reference_rows = [_score(case, predict(case, case['reference_text'], data['observed_at'])) for case in data['cases']]
    reference = {row['id']: row for row in reference_rows}
    measured = []
    for record in records:
        case = cases[record['case_id']]
        prediction = predict(case, record['hypothesis'], data['observed_at']) if record['status'] == 'ok' else {
            'action': 'recognition_error', 'fields': {}, 'warnings': [], 'parser_runtime_ms': None}
        row = _score(case, prediction)
        row['recording'] = record
        row['text_error'] = text_error(case['reference_text'], record['hypothesis']) if record['status'] == 'ok' else None
        clean_exact = reference[case['id']]['exact']
        row['paired_outcome'] = ('both_correct' if row['exact'] else 'stt_associated_regression') if clean_exact else (
            'hypothesis_only_correct' if row['exact'] else 'both_incorrect')
        measured.append(row)
    by_source = {}
    for source in sorted({row['recording']['source'] for row in measured}):
        selected = [row for row in measured if row['recording']['source'] == source]
        attempts = [row['recording'] for row in selected]
        measured_text = [row['text_error'] for row in selected if row['text_error'] is not None]
        total_chars = sum(r['ref_chars'] for r in measured_text)
        total_words = sum(r['ref_words'] for r in measured_text)
        timing_rows = [{**r.get('timing', {}), 'method': r.get('method', 'voice' if source == 'human_voice' else 'unknown'), 'case_id': r['case_id'],
                        'participant_id': r.get('speaker_id'), 'session_id': r['recording_id']} for r in attempts]
        by_source[source] = {'metrics': summarize(selected), 'attempts': len(selected),
            'successful_recognitions': sum(r['status'] == 'ok' for r in attempts),
            'failed_recognitions': sum(r['status'] != 'ok' for r in attempts),
            'unique_cases': len({r['case_id'] for r in attempts}),
            'unique_speakers': len({r.get('speaker_id') for r in attempts if r.get('speaker_id')}),
            'paired_outcomes': {name: sum(r['paired_outcome'] == name for r in selected) for name in
                               ('both_correct', 'stt_associated_regression', 'hypothesis_only_correct', 'both_incorrect')},
            'text': {'measured_n': len(measured_text), 'reference_characters': total_chars,
                     'cer': sum(r['edits'] for r in measured_text) / total_chars if total_chars else None,
                     'wer': sum(r['word_edits'] for r in measured_text) / total_words if total_words else None},
            'timing': timing_summary(timing_rows),
            'conditions': {condition: summarize([r for r in selected if r['recording']['condition'] == condition])
                           for condition in sorted({r['condition'] for r in attempts})},
            'engines': {engine: summarize([r for r in selected if r['recording']['engine'] == engine])
                        for engine in sorted({r['engine'] for r in attempts})}}
    return {'schema_version': 1, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'dataset_id': data['dataset_id'], 'purpose': data.get('purpose', 'unspecified'),
        'case_count': len(cases), 'template_group_count': len({c['template_group'] for c in cases.values()}),
        'adapter': 'server.voice_input._extract with empty initial draft context',
        'scored_fields': sorted(SCORED_FIELDS),
        'unscored_supported_fields': sorted(set(voice_terms.SUPPORTED_FIELDS) - SCORED_FIELDS),
        'human_speech_status': 'MEASURED_SELF_REPORTED_SCRIPTS' if by_source.get('human_voice', {}).get('attempts') else 'NOT_MEASURED',
        'reference_baseline': summarize(reference_rows), 'observations_by_source': by_source,
        'reference_rows': reference_rows, 'recording_rows': measured,
        'limitations': [
            'Self-authored synthetic development scripts, not independent validation or clinical accuracy.',
            'Modes are fixed per case, exactly as selected in the app; no automatic intent routing.',
            'Only raw pre-review drafts are scored. This does not measure corrected final patient records.',
            'The integrated R1 pipeline is executed, but only 16 explicitly annotated observation fields are scored. Extra R1 outputs are retained unscored; this is not a 29-field accuracy claim.',
            'State reconciliation, mandatory-field submission and destination assignment are outside this stateless draft benchmark.',
            'Synthetic corruptions and manually entered transcripts are separated from human speech.',
            'Reference-parser failures and STT-associated changes are shown separately, not silently corrected.',
            'CER/WER are format-sensitive, including spoken numbers versus digits; field correctness is primary.',
            'No actual user-input duration or workload benefit is inferred from parser runtime.',
            'The collector measures speech/STT timing only. Total input and correction burden require a paired task study.',
            'The evaluator does not send inquiries, assign hospitals, train models, or call an external STT service.']}


def _pct(value):
    return '미측정/분모 없음' if value is None else f'{value * 100:.1f}%'


def markdown(report):
    summary = report['reference_baseline']['overall']
    counts = summary['field_counts']
    lines = ['# 구급대원 음성 입력 평가', '',
        f"합성 개발 문장 {report['case_count']}개 · 실제 음성 상태: **{report['human_speech_status']}**", '',
        '## 합성 대본 정답 문장 → 현재 서버 추출기', '', '| 지표 | 값 |', '|---|---:|',
        f"| 문장 전체 일치 | {summary['exact_count']}/{summary['row_count']} ({_pct(summary['exact_accuracy'])}) |",
        f"| 항목 정밀도 / 재현율 | {_pct(summary['field_precision'])} / {_pct(summary['field_recall'])} |",
        f"| 틀린 값 / 누락 / 불필요한 자동입력 | {counts['wrong']} / {counts['missing']} / {counts['unexpected']} |",
        f"| 명령 일치 | {_pct(summary['command']['accuracy'])} |", '',
        '문장별 정답은 추출기 출력으로 만들지 않았다. 이 표는 개발 자료의 현재 기준선이며 실제 음성 인식 성능이 아니다.',
        '운영 서버의 R1 통합 추출 경로를 실행하되, 정답이 있는 관찰 16개 항목만 채점한다. R1 추가 항목은 별도 보존하며 전체 지원 항목의 정확도로 부르지 않는다.', '',
        '## 유형별 결과', '', '| 유형 | 문장 수 | 전체 일치 | 항목 정밀도 | 항목 재현율 |', '|---|---:|---:|---:|---:|']
    for family, metrics in report['reference_baseline']['families'].items():
        lines.append(f"| {family} | {metrics['row_count']} | {_pct(metrics['exact_accuracy'])} | {_pct(metrics['field_precision'])} | {_pct(metrics['field_recall'])} |")
    lines += ['', '## STT 결과 평가', '']
    if not report['observations_by_source']:
        lines += ['실제 녹음·STT 결과가 아직 없다. 응답시간·음성 정확도·업무 절감은 미측정이다.', '']
    for source, block in report['observations_by_source'].items():
        m = block['metrics']['overall']
        lines += [f'### {source}', '',
            f"시도 {block['attempts']}건 · 인식 실패 {block['failed_recognitions']}건 · 문장 전체 일치 {_pct(m['exact_accuracy'])}",
            f"CER {_pct(block['text']['cer'])} · 항목 정밀도 {_pct(m['field_precision'])} · 재현율 {_pct(m['field_recall'])}", '',
            '실패한 인식도 시도 수와 구조화 오류의 분모에 포함한다. 시간은 실제 기록된 항목만 집계한다.', '']
    lines += ['## 틀리거나 놓친 문장', '', '| ID | 유형 | 원문 | 문제 |', '|---|---|---|---|']
    for row in report['reference_rows']:
        if row['exact']:
            continue
        problems = [f"값 오류 {r['field']}" for r in row['field_wrong']]
        problems += ['누락 ' + key for key in row['field_missing']]
        problems += [f"불필요 입력 {r['field']}" for r in row['field_unexpected']]
        if not row['action_correct']: problems.append('명령 오류')
        if not row['review_field_correct']: problems.append('조회 대상 오류')
        quote = row['reference_text'].replace('|', '\\|').replace('\n', ' ')
        lines.append(f"| {row['id']} | {row['family']} | {quote} | {', '.join(problems)} |")
    lines += ['', '## 범위와 한계', ''] + ['- ' + text for text in report['limitations']]
    lines += ['', '산출물: `report.json`, `report.html`, `reference_cases.csv`, `recordings.csv`, `recording_template.json`, `source-lock.json`.', '']
    return '\n'.join(lines)


def html_report(report):
    m = report['reference_baseline']['overall']
    esc = lambda value: html.escape(str(value), quote=True)
    cards = [('합성 개발 문장', report['case_count']), ('원문 구조화 일치', _pct(m['exact_accuracy'])),
             ('실제 음성 평가', '미측정' if report['human_speech_status'] == 'NOT_MEASURED' else '자료 있음')]
    rows = []
    for row in report['reference_rows'] + report['recording_rows']:
        source = row.get('recording', {}).get('source', 'synthetic_reference')
        cells = [row['id'], row['family'], source, row.get('recording', {}).get('hypothesis', row['reference_text']),
                 json.dumps(row['expected'], ensure_ascii=False), json.dumps({k: row['prediction'].get(k) for k in ('action', 'fields', 'review_field')}, ensure_ascii=False),
                 '일치' if row['exact'] else '확인 필요']
        rows.append(f'<tr data-exact="{int(row["exact"])}"><td>' + '</td><td>'.join(esc(v) for v in cells) + '</td></tr>')
    return ('<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>음성 입력 평가 결과</title><style>body{font:15px/1.6 system-ui,"Malgun Gothic";margin:36px;color:#213548;background:#f4f7f8}'
        'h1{font-size:28px}.cards{display:flex;gap:16px;flex-wrap:wrap}.card{background:white;border:1px solid #d5e0e5;padding:20px;min-width:180px;border-radius:8px}'
        '.card strong{display:block;font-size:26px;color:#087f79}table{border-collapse:collapse;width:100%;background:white;margin-top:20px;font-size:13px}td,th{border:1px solid #d5e0e5;padding:10px;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{background:#14283d;color:white}td:nth-child(4){min-width:160px}td:nth-child(5),td:nth-child(6){max-width:280px}.table{overflow:auto}label{display:block;margin-top:24px}</style>'
        '<h1>구급대원 음성 입력 평가</h1><p>합성 개발 자료의 기준선입니다. 실제 발화 정확도나 입력시간 감소가 검증됐다는 뜻이 아닙니다.</p>'
        '<div class="cards">' + ''.join(f'<div class="card">{esc(name)}<strong>{esc(value)}</strong></div>' for name, value in cards) + '</div>'
        '<label><input id="errors" type="checkbox"> 오류가 있는 문장만 표시</label><div class="table"><table><thead><tr>'
        + ''.join('<th>' + name + '</th>' for name in ('ID', '유형', '자료 출처', '평가 문장', '정답', '추출 결과', '상태'))
        + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div><script>document.querySelector("#errors").onchange=function(){for(const r of document.querySelectorAll("tbody tr"))r.hidden=this.checked&&r.dataset.exact==="1";};</script></html>')


def _csv(rows):
    buffer = io.StringIO(newline='')
    writer = csv.writer(buffer)
    writer.writerow(('recording_id', 'case_id', 'family', 'source', 'mode', 'reference', 'hypothesis', 'expected', 'prediction', 'exact', 'wrong', 'missing', 'unexpected'))
    for row in rows:
        recording = row.get('recording', {})
        # CSV formulas are escaped, including externally imported transcript text.
        values = [recording.get('recording_id', ''), row['id'], row['family'], recording.get('source', 'synthetic_reference'),
            row['mode'], row['reference_text'], recording.get('hypothesis', ''),
            json.dumps(row['expected'], ensure_ascii=False), json.dumps(row['prediction'], ensure_ascii=False),
            row['exact'], len(row['field_wrong']), len(row['field_missing']), len(row['field_unexpected'])]
        writer.writerow(["'" + v if isinstance(v, str) and (v.lstrip(' \t\r\n').startswith(('=', '+', '-', '@')) or v.startswith(('\t', '\r'))) else v for v in values])
    return buffer.getvalue()


def run(corpus_path, recording_path, output):
    output = Path(output)
    if output.exists():
        raise ValueError('Output already exists; choose a new directory to preserve prior results')
    before = fingerprints(corpus_path, recording_path)
    importlib.reload(voice_r1)
    importlib.reload(voice_followup)
    importlib.reload(voice_commands)
    importlib.reload(voice_lexicon)
    importlib.reload(voice_terms)  # Bind execution to the just-fingerprinted production source/catalog.
    importlib.reload(voice_input)
    data = corpus(corpus_path)
    data['_path'] = str(Path(corpus_path))
    records = observations(recording_path, data)
    output.mkdir(parents=True, exist_ok=False)
    lock = {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'before': before,
            'corpus_path': str(Path(corpus_path).resolve()),
            'recording_path': str(Path(recording_path).resolve()) if recording_path else None,
            'stage': 'development_baseline_not_independent_validation'}
    (output / 'source-lock.json').write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding='utf-8')
    report = evaluate(data, records)
    after = fingerprints(corpus_path, recording_path)
    if after != before:
        raise ValueError('Source/input changed during evaluation; lock preserved, no result published')
    report['source_hashes'] = before
    snapshots = output / 'sources'
    for name in SOURCES:
        target = snapshots / name
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = (ROOT / name).read_bytes()
        if hashlib.sha256(payload).hexdigest() != before[name]:
            raise ValueError('Source changed while archiving; no result published')
        target.write_bytes(payload)
    corpus_bytes = Path(corpus_path).read_bytes()
    if hashlib.sha256(corpus_bytes).hexdigest() != before['corpus']:
        raise ValueError('Corpus changed while archiving; no result published')
    (snapshots / 'corpus.json').write_bytes(corpus_bytes)
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    (output / 'report.md').write_text(markdown(report), encoding='utf-8')
    (output / 'report.html').write_text(html_report(report), encoding='utf-8')
    (output / 'reference_cases.csv').write_text(_csv(report['reference_rows']), encoding='utf-8-sig')
    (output / 'recordings.csv').write_text(_csv(report['recording_rows']), encoding='utf-8-sig')
    template = {'schema_version': 1, 'dataset_id': data['dataset_id'], 'corpus_sha256': before['corpus'], 'recordings': []}
    (output / 'recording_template.json').write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def prompts(corpus_path, output):
    output = Path(output)
    if output.exists():
        raise ValueError('Prompt file already exists; choose a new path')
    data = corpus(corpus_path)
    public = {'schema_version': 1, 'dataset_id': data['dataset_id'],
        'corpus_sha256': hashlib.sha256(Path(corpus_path).read_bytes()).hexdigest(),
        'purpose': data['purpose'],
        'cases': [{k: row[k] for k in ('id', 'family', 'mode', 'reference_text')} for row in data['cases']]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Synthetic development scripts and real STT-output evaluation')
    parser.add_argument('action', choices=('evaluate', 'prompts'))
    parser.add_argument('--corpus', default=str(DEFAULT_CORPUS))
    parser.add_argument('--recordings')
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == 'prompts':
            prompts(args.corpus, args.output)
            print(json.dumps({'prompts': str(Path(args.output).resolve())}))
        else:
            report = run(args.corpus, args.recordings, args.output)
            metrics = report['reference_baseline']['overall']
            print(json.dumps({'output': str(Path(args.output).resolve()), 'cases': report['case_count'],
                'exact_count': metrics['exact_count'], 'exact_accuracy': metrics['exact_accuracy'],
                'human_speech_status': report['human_speech_status']}, ensure_ascii=False))
    except (ValueError, OSError, KeyError) as error:
        parser.error(str(error))

