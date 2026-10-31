"""Audit frozen replay artifacts only; no production imports or service calls."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / '.codex-build/voice-toggle-stress-20261030/corpus-run-002'
load = lambda name: json.loads((RUN / name).read_text(encoding='utf-8'))
summary, mismatches = load('summary.json'), load('mismatches.json')
rows = [json.loads(line) for line in (RUN / 'raw-replay.jsonl').read_text(encoding='utf-8').splitlines()]
issues = []


def check(ok, name, detail=None):
    if not ok:
        issues.append({'check': name, 'detail': detail})


def same_value(actual, expected):
    if isinstance(expected, list):
        return (isinstance(actual, list) and len(actual) == len(expected)
                and Counter(map(repr, actual)) == Counter(map(repr, expected)))
    if isinstance(expected, bool):
        return isinstance(actual, bool) and actual == expected
    if isinstance(expected, (float, int)):
        return type(actual) in (float, int) and actual == expected
    return type(actual) is type(expected) and actual == expected


database = RUN / 'synthetic-replay.db'
conn = sqlite3.connect(database.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
frozen_corpus = load('frozen-corpus.json')
planned_rows = {}
for group in ('long_cases', 'boundary_cases', 'flows'):
    for case in frozen_corpus[group]:
        for index, step in enumerate(case['steps'] if group == 'flows' else [case], 1):
            planned_rows[f"{case['id']}-{index:02d}"] = step
before_edit = {}
last_session = {}
independent_passes = {}
assertion_total = assertion_passes = 0
stored_expected = defaultdict(list)
failure_counts = Counter()
failure_fields = defaultdict(Counter)
for row in rows:
    step, old, new = row['step'], row['before_session'], row['after_session']
    check(step == planned_rows.get(row['id']), 'runtime_step_matches_frozen_oracle', row['id'])
    previous, actual, intent = old['fields'], new['fields'], step.get('intent')
    sid = new['id']
    if sid in last_session:
        check(old == last_session[sid], 'flow_session_chain_continuity', row['id'])
    last_session[sid] = new
    changed = {field for field in set(previous) | set(actual)
               if (field in previous) != (field in actual)
               or not same_value(actual.get(field), previous.get(field))}
    observed_intent = row['response'].get('command', {}).get('action')
    if intent == 'observe' and observed_intent is None and row['status'] == 200 and new['version'] > old['version']:
        revision = conn.execute('SELECT kind FROM voice_revisions WHERE session_id=? AND version=?',
                                (sid, new['version'])).fetchone()
        if revision and revision[0] == 'observation':
            observed_intent = 'observe'
    if intent == 'edit':
        before_edit[sid] = previous
    actual_checks = []
    for assertion in row['assertions']:
        name, field = assertion['check'], assertion.get('field')
        if name == 'request_accepted':
            result = row['status'] == 200
        elif name in ('expected_field', 'expected_state', 'undo_restores'):
            result = field in actual and same_value(actual[field], assertion['expected'])
        elif name == 'forbidden_field':
            result = field not in actual
        elif name == 'forbidden_new_value':
            result = field not in changed
        elif name == 'intent':
            result = observed_intent == assertion['expected']
        elif name in ('review_fields_unchanged', 'state_fields_unchanged'):
            result = actual == previous
        elif name == 'review_version_unchanged':
            result = new['version'] == old['version']
        elif name == 'undo_restores_preceding_edit':
            result = sid in before_edit and actual == before_edit[sid]
        elif name == 'state_unchanged_except':
            allowed = set(step['state_unchanged_except'])
            result = {k: v for k, v in actual.items() if k not in allowed} == {k: v for k, v in previous.items() if k not in allowed}
        elif name == 'draft_only':
            result = new['state'] == 'DRAFT' and all(new[key] is None for key in ('patient_id', 'confirmed_version', 'submitted_at'))
        elif name == 'source_text_preserved':
            # Verify complete per-session text sequences against immutable SQLite below.
            result = True
        else:
            check(False, 'unknown_assertion_type', [row['id'], name])
            result = False
        check(result == assertion['pass'], 'assertion_independent_recalculation', [row['id'], name, field])
        assertion_total += 1
        assertion_passes += bool(result)
        actual_checks.append(bool(result))
        if not result:
            failure_counts[name] += 1
            failure_fields[row['group']][str((name, field))] += 1
    independent_passes[row['id']] = all(actual_checks)
    check(independent_passes[row['id']] == row['pass'], 'step_pass_independent_recalculation', row['id'])
    if row['status'] == 200:
        stored_expected[sid].append(step['text'])

for sid, expected in stored_expected.items():
    stored = [record[0] for record in conn.execute('SELECT transcript FROM voice_utterances WHERE session_id=? ORDER BY rowid', (sid,))]
    check(stored == expected, 'complete_session_source_transcript_sequence', sid)
database_counts = {table: conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                   for table in ('patients', 'inquiries', 'hospitals', 'voice_sessions', 'voice_utterances', 'voice_revisions')}
conn.close()
check(database_counts == summary['database_counts'], 'sqlite_counts_match_summary')
check(all(database_counts[table] == 0 for table in ('patients', 'inquiries', 'hospitals')), 'zero_operational_records')
check(len(rows) == summary['steps'], 'total_step_count')
check(set(independent_passes) == set(planned_rows), 'every_planned_step_present_once')
check(sum(independent_passes.values()) == summary['passed_steps'], 'total_step_pass_count')
check(assertion_total == summary['assertions'], 'total_assertion_count')
check(assertion_passes == summary['passed_assertions'], 'total_assertion_pass_count')
check(dict(failure_counts) == summary['failure_check_types'], 'failure_check_type_counts')
check(Counter(row['status'] for row in rows) == Counter({int(key): value for key, value in summary['statuses'].items()}), 'status_counts')
check({row['id'] for row in rows if not independent_passes[row['id']]} == {row['id'] for row in mismatches}, 'mismatch_row_ids')
for mismatch in mismatches:
    row = next(row for row in rows if row['id'] == mismatch['id'])
    check(mismatch['failed_assertions'] == [a for a in row['assertions'] if not a['pass']], 'mismatch_details', row['id'])

groups = {}
for group, claimed in summary['groups'].items():
    selected = [row for row in rows if row['group'] == group]
    case_ids = set(row['case_id'] for row in selected)
    passed_cases = sum(all(independent_passes[row['id']] for row in selected if row['case_id'] == cid) for cid in case_ids)
    groups[group] = {'steps': len(selected), 'passed_steps': sum(independent_passes[row['id']] for row in selected),
                     'cases': len(case_ids), 'passed_cases': passed_cases}
    for key, value in groups[group].items():
        check(value == claimed[key], 'group_counts', [group, key])
    for category, category_claim in claimed['by_category'].items():
        subset = [row for row in selected if row['category'] == category]
        check(len(subset) == category_claim['steps'] and sum(independent_passes[row['id']] for row in subset) == category_claim['passed_steps'],
              'category_counts', [group, category])

before, after = load('source-before.json'), load('source-after.json')
check(before == after, 'frozen_source_hashes_match')
corpus_digest = hashlib.sha256((RUN / 'frozen-corpus.json').read_bytes()).hexdigest()
check(corpus_digest == summary['corpus_sha256'] == '56f8850006557a99ce31ea1e5b0e5765200662ec3d7cc6f1c365e0e00f692af7', 'matches_pre_api_audited_corpus')
runner_key = next(key for key in before if key.replace('\\', '/') == 'voice_lab/evaluate_stress_corpus_20261030.py')
check(hashlib.sha256((RUN / 'evaluator-source.py').read_bytes()).hexdigest() == before[runner_key], 'frozen_runner_source_hash')
error_counts = Counter((row['status'], row['response'].get('error')) for row in rows if row['status'] != 200)
check(error_counts == Counter({(422, 'VOICE_COMMAND_UNSUPPORTED'): 24}), 'production_unsupported_errors_not_probe_block')

run001 = RUN.parent / 'corpus-run-001'
old_sources = json.loads((run001 / 'source-before.json').read_text(encoding='utf-8'))
source_changes_between_runs = [key for key in old_sources.keys() | before.keys() if old_sources.get(key) != before.get(key)]
check(source_changes_between_runs == [runner_key], 'only_evaluator_changed_between_runs', source_changes_between_runs)
report = {
    'kind': 'independent_read_only_runtime_artifact_audit', 'run': str(RUN),
    'product_service_parser_stt_http_calls': 0, 'sqlite_access': 'immutable read-only replay artifact',
    'corpus_sha256': corpus_digest, 'steps': len(rows), 'passed_steps': sum(independent_passes.values()),
    'groups': groups, 'assertions_recalculated': assertion_total, 'passed_assertions': assertion_passes,
    'failed_assertions': dict(failure_counts), 'failed_fields_by_group': dict(failure_fields),
    'database_counts': database_counts,
    'production_errors': [{'status': status, 'error': error, 'count': count} for (status, error), count in error_counts.items()],
    'source_changes_between_run001_and_run002': source_changes_between_runs, 'issues': issues,
    'interpretation': [
        '202/516 is full satisfaction of every scored expectation in each planned synthetic stress step; it is not STT accuracy.',
        'Long 0/60 is affected by a shared unparsed Celsius wording in all 60; the sample does not isolate text-length effects.',
        'Flow 24/216 includes dependent cumulative-state failures from the same 24 rejected compound BP answers, not 192 independent fresh errors.',
        'Run002 records 24 actual production VOICE_COMMAND_UNSUPPORTED responses; run001 probe-block responses are retained separately.',
        'Unannotated additional fields are not comprehensively scored by the required-subset oracle.',
    ],
}
(ROOT / 'build/voice_stress_runtime_independent_audit_20261030.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({key: report[key] for key in ('steps', 'passed_steps', 'groups', 'assertions_recalculated', 'passed_assertions', 'database_counts', 'production_errors', 'issues')}, ensure_ascii=True, indent=2))
raise SystemExit(bool(issues))
