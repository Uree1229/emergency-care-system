"""Read-only corpus annotation checks; does not import or call the product parser/API."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'voice_lab/corpus/voice_stress_20261030.json'
raw = SOURCE.read_bytes()
data = json.loads(raw)
issues = []
checks = Counter()


def check(condition, label, detail=None):
    checks[label] += 1
    if not condition:
        issues.append({'check': label, 'detail': detail})


def require_match(pattern, text, case_id):
    match = re.search(pattern, text)
    check(match is not None, 'explicit_text_span', [case_id, pattern])
    return match


FIELDS = {'age', 'age_group', 'sex', 'symptoms', 'denied_symptoms', 'uncertain_symptoms',
          'consciousness', 'breathing', 'systolic_bp', 'diastolic_bp', 'pulse', 'spo2',
          'temperature', 'respiratory_rate', 'glucose', 'onset_at'}
R1_FIELDS = {'avpu', 'chief_complaint', 'age_category', 'pre_ktas', 'stemi_suspected',
             'injury_region', 'labor', 'pregnancy_weeks', 'seizure_ongoing', 'bleeding_risk',
             'high_risk_mechanism', 'pain_score', 'gcs'}
SYMPTOMS = {'흉통', '호흡곤란', '복통', '두통', '어지럼', '구토', '오심', '발열', '기침', '객혈',
            '출혈', '경련', '실신', '마비', '의식저하', '설사', '요통', '발진', '외상', '부종', '심계항진'}
all_rows = list(data['long_cases']) + list(data['boundary_cases'])
for flow in data['flows']:
    all_rows += [dict(step, id=f"{flow['id']}/{index + 1}") for index, step in enumerate(flow['steps'])]

check(data['synthetic'] is True, 'synthetic_label')
check(data['provenance']['extractor_results_used'] is False, 'no_parser_generated_gold_label')
check(data['provenance']['server_or_stt_called'] is False, 'no_api_or_stt_label')
ids = [row['id'] for row in all_rows]
check(len(ids) == len(set(ids)), 'unique_ids')
check(len(data['long_cases']) == 60, 'long_count')
check(len(data['boundary_cases']) == 240, 'boundary_count')
check(len(data['flows']) == 24 and sum(len(flow['steps']) for flow in data['flows']) == 216, 'flow_count')

for row in all_rows:
    cid, text, expected = row['id'], row['text'], row['expected_fields']
    check(0 < len(text) <= 4000, 'text_length_1_to_4000', cid)
    check(set(expected) <= FIELDS, 'supported_field_names', cid)
    check(set(row['forbidden_fields']) <= FIELDS | R1_FIELDS, 'supported_forbidden_names', cid)
    check(not set(expected).intersection(row['forbidden_fields']), 'expected_forbidden_disjoint', cid)
    sets = []
    for field in ('symptoms', 'denied_symptoms', 'uncertain_symptoms'):
        values = expected.get(field, [])
        check(len(values) == len(set(values)) and set(values) <= SYMPTOMS, 'canonical_symptom_list', [cid, field])
        sets.append(set(values))
    check(not (sets[0] & sets[1] or sets[0] & sets[2] or sets[1] & sets[2]), 'symptom_states_disjoint', cid)

long_value_checks = 0
for case in data['long_cases']:
    cid, text = case['id'], case['text']
    check(''.join(case['parts']) == text, 'reading_parts_preserve_text', cid)
    check(all(500 <= len(part) <= 900 for part in case['parts']), 'reading_part_length', cid)
    observed = {}
    patterns = {
        'age': (r'(\d+)세', int),
        'systolic_bp': (r'현재 환자의 혈압은 (\d+)에', int),
        'diastolic_bp': (r'현재 환자의 혈압은 \d+에 (\d+) 밀리미터', int),
        'pulse': (r'맥박은 분당 (\d+)회', int),
        'spo2': (r'산소포화도는 (\d+)퍼센트', int),
        'temperature': (r'체온은 섭씨 ([\d.]+)도', float),
        'respiratory_rate': (r'호흡수는 분당 (\d+)회', int),
        'glucose': (r'혈당은 (\d+) 밀리그램', int),
    }
    for field, (pattern, conversion) in patterns.items():
        match = require_match(pattern, text, cid)
        if match:
            observed[field] = conversion(match[1])
    sex = require_match(r'(남성|여성)', text, cid)
    symptoms = require_match(r'현재 확인된 증상은 (.*?)입니다\.', text, cid)
    state = require_match(r'현재 의식은 명료하고 호흡은 (비정상|정상)입니다\.', text, cid)
    minutes = require_match(r'정확히 (\d+)분 전부터 시작', text, cid)
    if sex:
        observed['sex'] = {'남성': 'male', '여성': 'female'}[sex[1]]
    if symptoms:
        observed['symptoms'] = symptoms[1].split('과 ')
    if state:
        observed['consciousness'] = 'alert'
        observed['breathing'] = {'정상': 'normal', '비정상': 'abnormal'}[state[1]]
    if minutes:
        observed['onset_at'] = data['now'] - int(minutes[1]) * 60
    check(observed == case['expected_fields'], 'long_explicit_values_and_relative_time',
          {'id': cid, 'from_text': observed, 'gold': case['expected_fields']})
    long_value_checks += len(observed)

boundary = {case['id']: case for case in data['boundary_cases']}
for cid, seconds in {'B040': 1800, 'B057': 4800, 'B058': 5400, 'B059': 7200,
                     'B060': 259200, 'B080': 1020, 'B180': 2400, 'B237': 2400}.items():
    check(boundary[cid]['expected_fields']['onset_at'] == data['now'] - seconds,
          'boundary_relative_time_arithmetic', cid)
check(boundary['B229']['text'] == '백이십육에 팔십입니다.' and
      boundary['B229']['expected_fields'] == {'systolic_bp': 126, 'diastolic_bp': 80},
      'ambiguous_copula_disambiguated')

flow_state_protocol = []
for flow in data['flows']:
    steps, cid = flow['steps'], flow['id']
    first = steps[0]
    age = require_match(r'(\d+)세 (남성|여성) 환자', first['text'], cid)
    onset = require_match(r'현재 증상은 (.*?)이고 발생은 (\d+)분 전', first['text'], cid)
    expected = {}
    if age:
        expected.update(age=int(age[1]), sex={'남성': 'male', '여성': 'female'}[age[2]])
    if onset:
        expected.update(symptoms=onset[1].split('과 '), onset_at=data['now'] - int(onset[2]) * 60)
    check(expected == first['expected_fields'], 'flow_initial_facts', cid)
    bp = require_match(r'^(\d+)에 (\d+)입니다', steps[1]['text'], cid)
    if bp:
        check(steps[1]['expected_fields'] == {'systolic_bp': int(bp[1]), 'diastolic_bp': int(bp[2])},
              'flow_bp_answer_scope', cid)
    pulse = require_match(r'분당 (\d+)회', steps[2]['text'], cid)
    update = require_match(r'맥박은 (\d+)로 바뀌었습니다', steps[3]['text'], cid)
    edit = require_match(r'맥박 (\d+)으로 수정해', steps[5]['text'], cid)
    for match, step in ((pulse, steps[2]), (update, steps[3]), (edit, steps[5])):
        if match:
            check(step['expected_fields'] == {'pulse': int(match[1])}, 'flow_pulse_facts', cid)
    if pulse and update and edit:
        planned_state = dict(first['expected_fields'])
        before_edit = None
        for index, step in enumerate(steps):
            intent = step.get('intent')
            if intent == 'edit':
                before_edit = dict(planned_state)
            if intent == 'undo':
                if before_edit is not None:
                    planned_state = dict(before_edit)
                check(step.get('undo_restores') == {'pulse': int(update[1])},
                      'flow_structured_undo_expected', cid)
            else:
                planned_state.update(step['expected_fields'])
            check(step.get('expected_state_subset') == planned_state,
                  'flow_cumulative_state_consistency', [cid, index + 1])
        flow_state_protocol.append({'id': cid, 'initial_pulse': int(pulse[1]),
                                    'observed_pulse': int(update[1]), 'edited_pulse': int(edit[1]),
                                    'after_undo_pulse': int(update[1]),
                                    'review_mutates_fields_or_version': False})

check(SOURCE.read_bytes() == raw, 'corpus_unchanged_during_audit')
report = {
    'kind': 'independent_pre_api_annotation_audit',
    'corpus_sha256': hashlib.sha256(raw).hexdigest(),
    'product_parser_api_stt_db_calls': 0,
    'manual_review': {'boundary_text_and_expected_rows_read': 240,
                      'flow_templates_read': ['F001', 'F002', 'F003', 'F012', 'F013', 'F024'],
                      'corrections_before_api': ['B229 ambiguous 80+copula / 82+particle wording',
                                                 'B098/B171-B180 semantic correction contract separated from legacy exclusion policy',
                                                 'B137 other-subject symptom leakage forbidden']},
    'automated_checks': dict(checks),
    'long_explicit_field_values_cross_checked': long_value_checks,
    'issues': issues,
    'flow_state_protocol': flow_state_protocol,
    'limits': ['Synthetic text and author-selected templates, not real audio/STT or clinical validation.',
               'expected_fields is a required subset; forbidden_fields is an explicit prohibited subset.',
               'Unlisted extra fields are not exhaustively annotated and require separate false-positive review.',
               'Manual boundary inspection and direct textual consistency checks do not establish clinical accuracy.',
               'Flow forbidden_fields concerns newly extracted values; undo intentionally restores prior session state.'],
}
(ROOT / 'build/voice_stress_corpus_independent_audit_20261030.json').write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'corpus_sha256': report['corpus_sha256'], 'row_count': len(all_rows),
                  'long_values_checked': long_value_checks, 'check_count': sum(checks.values()),
                  'issues': issues}, ensure_ascii=True, indent=2))
raise SystemExit(bool(issues))
