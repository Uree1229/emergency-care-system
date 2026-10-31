"""Replay independently frozen synthetic texts through the production voice service.

This measures text handling, not speech recognition or clinical accuracy.  A fresh
SQLite file is required; no HTTP, microphone, cloud STT, patients or inquiries are
used.  Expectations are never generated from the parser or rewritten by this tool.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIELDS = {
    'age', 'sex', 'symptoms', 'denied_symptoms', 'uncertain_symptoms',
    'consciousness', 'breathing', 'systolic_bp', 'diastolic_bp', 'pulse',
    'spo2', 'temperature', 'respiratory_rate', 'glucose', 'onset_at',
}
FORBIDDABLE_FIELDS = FIELDS | {
    'age_group', 'avpu', 'chief_complaint', 'age_category', 'pre_ktas',
    'stemi_suspected', 'injury_region', 'labor', 'pregnancy_weeks',
    'seizure_ongoing', 'bleeding_risk', 'high_risk_mechanism', 'pain_score', 'gcs',
}
BLOCKED_COMMANDS = {'confirm', 'submit'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(corpus):
    paths = list((ROOT / 'server').glob('*.py'))
    paths += list((ROOT / 'config').glob('*voice*.json'))
    paths += [corpus, Path(__file__).resolve()]
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(set(paths))}


def equal(actual, expected):
    # Canonical list order is immaterial, but extra/duplicate members are not.
    if isinstance(expected, list):
        return isinstance(actual, list) and sorted(actual) == sorted(expected)
    if type(expected) in (int, float):
        return type(actual) in (int, float) and actual == expected
    return type(actual) is type(expected) and actual == expected


def validate(corpus):
    assert corpus['synthetic'] is True and type(corpus['now']) is int
    assert corpus['provenance']['extractor_results_used'] is False
    assert corpus['provenance']['server_or_stt_called'] is False
    ids = []
    texts = []
    for group in ('long_cases', 'boundary_cases', 'flows'):
        for case in corpus[group]:
            ids.append(case['id'])
            steps = case['steps'] if group == 'flows' else [case]
            for step in steps:
                assert 0 < len(step['text'].strip()) <= 4000, case['id']
                assert set(step['expected_fields']) <= FIELDS, case['id']
                assert set(step['forbidden_fields']) <= FORBIDDABLE_FIELDS, case['id']
                assert not (set(step['expected_fields']) & set(step['forbidden_fields'])), case['id']
                texts.append(step['text'])
            if group == 'long_cases':
                assert ''.join(case['parts']) == case['text'], case['id']
    assert len(ids) == len(set(ids))
    return {'case_count': len(ids), 'utterance_count': len(texts),
            'total_chars': sum(map(len, texts)), 'max_chars': max(map(len, texts)),
            'unique_texts': len(set(texts)),
            'long_cases': len(corpus['long_cases']),
            'boundary_cases': len(corpus['boundary_cases']),
            'flows': len(corpus['flows'])}


def run(corpus_path, output):
    corpus = json.loads(corpus_path.read_text(encoding='utf-8'))
    stats = validate(corpus)
    # Freeze before importing or calling the implementation under evaluation.
    before_hashes = snapshot(corpus_path)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'source-before.json').write_text(json.dumps(before_hashes, indent=2), encoding='utf-8')
    (output / 'frozen-corpus.json').write_bytes(corpus_path.read_bytes())
    (output / 'evaluator-source.py').write_bytes(Path(__file__).read_bytes())
    from server import db, service, voice_input as voice, voice_terms, voice_audio

    def no_stt(*args, **kwargs):
        raise AssertionError('External speech transcription is forbidden in text replay')
    voice_audio.transcribe = no_stt
    conn = db.connect(str(output / 'synthetic-replay.db'))
    db.init(conn)
    now = corpus['now']
    actor = 'synthetic-stress-20261030'
    counter = 0
    rows = []
    cases = []
    started = time.perf_counter()

    def key():
        nonlocal counter
        counter += 1
        return f'stress-{counter}'

    def check(bucket, name, passed, **detail):
        bucket.append({'check': name, 'pass': bool(passed), **detail})

    def evaluate(group, case):
        session = voice.create(conn, now, {'actor_id': actor, 'client_request_id': key()})['session']
        case_rows = []
        prior_to_edit = None
        steps = case['steps'] if group == 'flows' else [case]
        for index, step in enumerate(steps, 1):
            previous = session
            mode = step.get('mode', 'answer' if step.get('route') == 'followup' else 'dictation')
            row = {'id': f"{case['id']}-{index:02d}", 'case_id': case['id'],
                   'group': group, 'category': case['category'], 'mode': mode,
                   'step': step, 'before_session': previous,
                   'policy_scope': 'new_semantic_correction_stress' if case['id'] == 'B098' or
                   (case['id'].startswith('B') and 171 <= int(case['id'][1:]) <= 180)
                   else 'independent_semantic_stress'}
            assertions = []
            common = {'actor_id': actor, 'client_request_id': key(),
                      'expected_version': session['version']}
            response = None
            status = 200
            begin = time.perf_counter()
            try:
                if mode == 'answer':
                    response = voice.answer_followup(conn, now, session['id'], {
                        **common, 'utterance_id': key(), 'question_id': step['question_id'],
                        'profile': 'voice_extended_v1', 'text': step['text']})
                elif mode == 'command':
                    parsed = voice_terms.parse_command(step['text'])
                    # A misclassified confirm must not be executed by the probe.
                    if parsed.get('action') in BLOCKED_COMMANDS:
                        raise service.ServiceError('PROBE_UNSAFE_COMMAND_BLOCKED',
                                                   'Probe blocked a non-draft command', 422,
                                                   actual_action=parsed.get('action'))
                    response = voice.transcript(conn, now, session['id'], {
                        **common, 'utterance_id': key(), 'text': step['text'],
                        'mode': 'command', 'is_final': True})
                else:
                    assert mode in ('dictation', 'auto'), mode
                    response = voice.transcript(conn, now, session['id'], {
                        **common, 'utterance_id': key(), 'text': step['text'],
                        'mode': mode, 'is_final': True})
                session = response.get('session') or voice.get(conn, now, session['id'], actor)['session']
            except service.ServiceError as exc:
                status, response = exc.status, exc.to_dict()
                session = voice.get(conn, now, session['id'], actor)['session']
            row.update(response=response, status=status,
                       elapsed_ms=round((time.perf_counter()-begin)*1000, 3))
            check(assertions, 'request_accepted', status == 200, status=status)
            actual = session['fields']
            changed = {f for f in FORBIDDABLE_FIELDS if (f in previous['fields']) != (f in actual)
                       or previous['fields'].get(f) != actual.get(f)}
            for field, expected in step['expected_fields'].items():
                check(assertions, 'expected_field', field in actual and equal(actual[field], expected),
                      field=field, expected=expected, present=field in actual, actual=actual.get(field))
            # In a flow, an unchanged earlier value is allowed to remain present.
            # Undo is a control action; its intended restoration is checked below.
            if step.get('intent') != 'undo':
                for field in step['forbidden_fields']:
                    passed = field not in changed if group == 'flows' else field not in actual
                    check(assertions, 'forbidden_new_value' if group == 'flows' else 'forbidden_field',
                          passed, field=field, actual=actual.get(field), present=field in actual)
            intent = step.get('intent')
            if intent:
                observed = response.get('command', {}).get('action')
                if (intent == 'observe' and observed is None and status == 200
                        and session['version'] > previous['version']):
                    latest = conn.execute('SELECT kind FROM voice_revisions WHERE session_id=? ORDER BY id DESC LIMIT 1',
                                          (session['id'],)).fetchone()
                    observed = 'observe' if latest and latest['kind'] == 'observation' else observed
                check(assertions, 'intent', observed == intent, expected=intent, actual=observed)
            if intent == 'review':
                check(assertions, 'review_fields_unchanged', actual == previous['fields'])
                check(assertions, 'review_version_unchanged', session['version'] == previous['version'])
            if intent == 'edit':
                prior_to_edit = previous['fields']
            if intent == 'undo':
                check(assertions, 'undo_restores_preceding_edit', prior_to_edit is not None and actual == prior_to_edit,
                      expected=prior_to_edit, actual=actual)
            for field, expected in step.get('expected_state_subset', {}).items():
                check(assertions, 'expected_state', field in actual and equal(actual[field], expected),
                      field=field, expected=expected, actual=actual.get(field))
            if step.get('state_unchanged'):
                check(assertions, 'state_fields_unchanged', actual == previous['fields'])
            if 'state_unchanged_except' in step:
                exceptions = set(step['state_unchanged_except'])
                check(assertions, 'state_unchanged_except',
                      {k:v for k,v in actual.items() if k not in exceptions} ==
                      {k:v for k,v in previous['fields'].items() if k not in exceptions})
            for field, expected in step.get('undo_restores', {}).items():
                check(assertions, 'undo_restores', field in actual and equal(actual[field], expected),
                      field=field, expected=expected, actual=actual.get(field))
            check(assertions, 'draft_only', session['state'] == 'DRAFT' and session['patient_id'] is None
                  and session['confirmed_version'] is None and session['submitted_at'] is None)
            # Every successful transcript call preserves exactly the input source.
            stored = conn.execute('SELECT transcript FROM voice_utterances WHERE session_id=? ORDER BY rowid DESC LIMIT 1',
                                  (session['id'],)).fetchone()
            check(assertions, 'source_text_preserved', status != 200 or (stored is not None and stored['transcript'] == step['text']))
            row['after_session'] = session
            row['assertions'] = assertions
            row['pass'] = all(a['pass'] for a in assertions)
            row['semantic_checks'] = sum(a['check'] not in {'request_accepted', 'draft_only', 'source_text_preserved'} for a in assertions)
            rows.append(row)
            case_rows.append(row)
            raw.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n')
            raw.flush()
        cases.append({'id': case['id'], 'group': group, 'category': case['category'],
                      'pass': all(r['pass'] for r in case_rows), 'steps': len(case_rows),
                      'failed_steps': [r['id'] for r in case_rows if not r['pass']]})

    with (output / 'raw-replay.jsonl').open('w', encoding='utf-8') as raw:
        try:
            for group in ('long_cases', 'boundary_cases', 'flows'):
                for case in corpus[group]:
                    evaluate(group, case)
        finally:
            conn.commit()
    counts = {t: conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
              for t in ('patients', 'inquiries', 'hospitals', 'voice_sessions', 'voice_utterances', 'voice_revisions')}
    conn.close()
    after_hashes = snapshot(corpus_path)
    (output / 'source-after.json').write_text(json.dumps(after_hashes, indent=2), encoding='utf-8')
    changes = [p for p in before_hashes.keys() | after_hashes.keys() if before_hashes.get(p) != after_hashes.get(p)]
    failures = [{'id': r['id'], 'case_id': r['case_id'], 'group': r['group'],
                 'category': r['category'], 'mode': r['mode'], 'text': r['step']['text'],
                 'policy_scope': r['policy_scope'], 'status': r['status'],
                 'failed_assertions': [a for a in r['assertions'] if not a['pass']]}
                for r in rows if not r['pass']]
    groups = {}
    for group in ('long_cases', 'boundary_cases', 'flows'):
        selected = [r for r in rows if r['group'] == group]
        selected_cases = [c for c in cases if c['group'] == group]
        by_category = {}
        for category in sorted(set(r['category'] for r in selected)):
            items = [r for r in selected if r['category'] == category]
            by_category[category] = {'steps': len(items), 'passed_steps': sum(r['pass'] for r in items)}
        groups[group] = {'cases': len(selected_cases), 'passed_cases': sum(c['pass'] for c in selected_cases),
                         'steps': len(selected), 'passed_steps': sum(r['pass'] for r in selected),
                         'by_category': by_category}
    assertions = [a for r in rows for a in r['assertions']]
    summary = {'schema_version': 'voice-stress-replay-20261030-v1',
               'scope': 'Synthetic text service replay; not microphone/STT/browser/clinical accuracy',
               'oracle_scope': {'expected_fields': 'required subset; exact value for each specified field',
                                'forbidden_fields': 'explicit subset; absent in new cases, unchanged in flows',
                                'untagged_additional_fields': 'not scored; do not infer full extraction accuracy',
                                'exit_zero': 'completed with frozen sources and zero operational writes; semantic mismatches may remain'},
               'now': now, 'corpus_sha256': before_hashes[str(corpus_path.relative_to(ROOT))],
               'corpus': stats, 'groups': groups,
               'steps': len(rows), 'passed_steps': sum(r['pass'] for r in rows),
               'semantic_steps': sum(r['semantic_checks'] > 0 for r in rows),
               'structural_only_steps': sum(r['semantic_checks'] == 0 for r in rows),
               'assertions': len(assertions), 'passed_assertions': sum(a['pass'] for a in assertions),
               'failure_check_types': dict(Counter(a['check'] for a in assertions if not a['pass'])),
               'statuses': dict(Counter(r['status'] for r in rows)),
               'elapsed_seconds': round(time.perf_counter()-started, 3),
               'database_counts': counts, 'source_changes_during_run': changes,
               'corpus_unchanged': digest(corpus_path) == before_hashes[str(corpus_path.relative_to(ROOT))],
               'operational_writes_zero': all(counts[t] == 0 for t in ('patients', 'inquiries', 'hospitals')),
               'external_stt_calls': 0, 'http_calls': 0, 'cases': cases}
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (output / 'mismatches.json').write_text(json.dumps(failures, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k not in {'cases','groups'}}, ensure_ascii=False, indent=2))
    return 0 if not changes and summary['corpus_unchanged'] and summary['operational_writes_zero'] else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, default=ROOT/'voice_lab/corpus/voice_stress_20261030.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.corpus.resolve(), args.output.resolve()))
