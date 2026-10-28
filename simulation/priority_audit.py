"""Read-only consistency audit for the priority-protection experiment.

Only the separate audit output is written. Development choices are independently
recomputed for verification, never applied back to the experiment. Uncertainty
describes variation between synthetic seeds, not clinical effectiveness.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import statistics

from .observed_audit import (bootstrap_seed_mean, common_patient_differences,
                             validate_records)


ROOT = Path(__file__).resolve().parent.parent
PRIORITIES = (4, 2, 1)
CONSUMED_VALIDATION_SEEDS = (frozenset(range(1001, 1021))
    | frozenset(range(2001, 2021)) | frozenset(range(3001, 3021)) | frozenset(range(4001, 4021)))


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _mean(values):
    return statistics.mean(values) if values else None


def _number(actual, expected, message):
    if actual is None or expected is None:
        _require(actual is expected, message)
    else:
        _require(math.isfinite(actual) and math.isfinite(expected)
                 and math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9), message)


def _p95(values):
    return sorted(values)[math.ceil(.95 * len(values)) - 1] if values else None


def _reported_interval(values, key, samples):
    """Independently reproduce the report's declared RNG/percentile convention."""
    _require(isinstance(samples, int) and not isinstance(samples, bool) and samples >= 100,
             'reported bootstrap sample count is missing/invalid')
    if len(values) < 2:
        return None
    rng = random.Random('71011|map_bootstrap|' + key)
    n = len(values)
    draws = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(samples))
    return [draws[int((samples - 1) * .025)], draws[int((samples - 1) * .975)]]


def _check_reported_interval(actual, values, key, samples):
    expected = _reported_interval(values, key, samples)
    if expected is None:
        _require(actual is None, 'reported bootstrap interval must be unavailable')
    else:
        _require(isinstance(actual, list) and len(actual) == 2, 'reported bootstrap interval shape changed')
        for left, right in zip(actual, expected):
            _number(left, right, 'reported bootstrap interval mismatch')


def priority_totals(records):
    """Recompute all tier denominators directly from individual outcomes."""
    result = {}
    for tier in PRIORITIES:
        members = [outcome for row in records for outcome in row['outcomes']
                   if outcome['priority'] == tier]
        times = [outcome['treatment_start_t'] - outcome['search_start_t']
                 for outcome in members if outcome['treatment_start_t'] is not None]
        result[str(tier)] = dict(patients=len(members), treatment_start_observed=len(times),
            untreated=sum(outcome['treatment_start_t'] is None for outcome in members),
            not_arrived=sum(outcome['final_arrive_t'] is None for outcome in members),
            unassigned=sum(outcome['first_commit_t'] is None for outcome in members),
            conditional_start_mean_minutes=_mean(times), start_p95_minutes=_p95(times),
            start_max_minutes=max(times, default=None))
    return result


def validate_priority_records(records, policy_ids, expected_cells):
    """Reuse generic count checks and independently verify per-tier summaries."""
    cells = validate_records(records, policy_ids, expected_cells)
    for row in records:
        _require(all(not isinstance(o['priority'], bool) and o['priority'] in PRIORITIES
                     for o in row['outcomes']), 'unsupported experimental priority')
        counts = priority_totals([row])
        stored = row.get('priority', {})
        for tier in PRIORITIES:
            expected = counts[str(tier)]
            if str(tier) in stored:
                group = stored[str(tier)]
                for key, total_key in (('total', 'patients'), ('start_observed', 'treatment_start_observed'),
                                       ('unassigned', 'unassigned'), ('not_treated', 'untreated'),
                                       ('not_arrived', 'not_arrived')):
                    if key in group:
                        _require(group[key] == expected[total_key], 'priority denominator mismatch: ' + key)
                if 'start_max_minutes' in group:
                    _number(group['start_max_minutes'], expected['start_max_minutes'],
                            'priority max-time mismatch')
        for key, expected in (
            ('unassigned', sum(counts[str(tier)]['unassigned'] for tier in PRIORITIES)),
            ('high_priority_unassigned', counts['4']['unassigned']),
        ):
            if key in row['metrics']:
                _require(row['metrics'][key] == expected, key + ' mismatch')
        _number(row['metrics']['inquiries_per_patient'],
                row['metrics']['inquiries'] / len(row['outcomes']) if row['outcomes'] else None,
                'inquiries-per-patient denominator mismatch')
    return cells


def paired_priority(cells, baseline, selected, *, tier=None, field='treatment_start_t',
                    samples=10000, rng_seed=94131):
    """Pair the same patients within conditions, then aggregate whole seeds."""
    per_seed = defaultdict(list)
    identity_switches = []
    condition_rows = []
    for (scenario_id, seed), cell in sorted(cells.items()):
        before, after = cell[baseline], cell[selected]
        left = dict(before, outcomes=[o for o in before['outcomes']
                                     if tier is None or o['priority'] == tier])
        right = dict(after, outcomes=[o for o in after['outcomes']
                                     if tier is None or o['priority'] == tier])
        differences = common_patient_differences(left, right, field)
        per_seed[seed].extend(differences)
        condition_rows.append(dict(scenario_id=scenario_id, seed=seed,
            common_patients=len(differences), mean_difference_minutes=_mean(differences)))
        observed_left = {o['id'] for o in left['outcomes'] if o[field] is not None}
        observed_right = {o['id'] for o in right['outcomes'] if o[field] is not None}
        if observed_left != observed_right:
            identity_switches.append(dict(scenario_id=scenario_id, seed=seed,
                baseline_only=sorted(observed_left - observed_right),
                selected_only=sorted(observed_right - observed_left)))
    seed_rows = [dict(seed=seed, common_patients=len(values),
                     mean_difference_minutes=_mean(values))
                 for seed, values in sorted(per_seed.items())]
    values = [row['mean_difference_minutes'] for row in seed_rows
              if row['mean_difference_minutes'] is not None]
    pooled = [value for group in per_seed.values() for value in group]
    return dict(priority=tier, outcome_field=field, baseline_policy=baseline, selected_policy=selected,
        difference_direction='selected minus baseline; negative is smaller',
        aggregation='common patients within condition; pooled within seed; equal-weight mean of seed means',
        seeds=len(seed_rows), seeds_with_common_times=len(values), common_patients=len(pooled),
        mean_difference_minutes=_mean(values), pooled_common_patient_mean_minutes=_mean(pooled),
        interval_95_minutes=bootstrap_seed_mean(values, samples, rng_seed) if values else None,
        bootstrap_samples=samples, rng_seed=rng_seed,
        regression_seed_fraction=sum(v > 0 for v in values) / len(values) if values else None,
        per_seed=seed_rows, per_condition_seed=condition_rows, observed_identity_switches=identity_switches)


def tradeoffs(cells, baseline, selected):
    """Keep whole-population and each tier's gains and regressions visible."""
    results = []
    for (scenario_id, seed), cell in sorted(cells.items()):
        before = priority_totals([cell[baseline]])
        after = priority_totals([cell[selected]])
        differences = {str(tier): {key: after[str(tier)][key] - before[str(tier)][key]
            for key in ('untreated', 'not_arrived', 'unassigned')} for tier in PRIORITIES}
        total = {key: sum(differences[str(tier)][key] for tier in PRIORITIES)
                 for key in ('untreated', 'not_arrived', 'unassigned')}
        results.append(dict(scenario_id=scenario_id, seed=seed, priority_differences=differences,
            total_differences=total, high_priority_untreated_regression=differences['4']['untreated'] > 0,
            total_untreated_regression=total['untreated'] > 0,
            high_priority_gain_with_other_tier_loss=(differences['4']['untreated'] < 0
                and any(differences[str(tier)]['untreated'] > 0 for tier in (2, 1)))))
    return dict(cells=len(results),
        high_priority_untreated_regression_cells=sum(r['high_priority_untreated_regression'] for r in results),
        total_untreated_regression_cells=sum(r['total_untreated_regression'] for r in results),
        high_priority_gain_with_other_tier_loss_cells=sum(r['high_priority_gain_with_other_tier_loss']
                                                        for r in results),
        priority_untreated_differences={str(tier): sum(r['priority_differences'][str(tier)]['untreated']
                                                     for r in results) for tier in PRIORITIES},
        total_untreated_difference=sum(r['total_differences']['untreated'] for r in results),
        per_condition_seed=results)


def validate_fresh_seeds(payload, development):
    _require(payload['development_seeds'] == development['development_seeds']
             and payload['validation_seeds'] == development['validation_seeds'],
             'seed plan changed after development lock')
    for key in ('development_seeds', 'validation_seeds'):
        seeds = payload[key]
        _require(bool(seeds) and all(isinstance(seed, int) and not isinstance(seed, bool) and seed >= 0
                                    for seed in seeds) and len(set(seeds)) == len(seeds), 'invalid/duplicate seeds')
        _require(set(seeds).isdisjoint(CONSUMED_VALIDATION_SEEDS), 'consumed validation seeds reused')
    _require(set(payload['development_seeds']).isdisjoint(payload['validation_seeds']),
             'development and validation seeds overlap')
    _require(set(payload['previous_validation_seeds_excluded']) == CONSUMED_VALIDATION_SEEDS,
             'previous validation exclusion contract changed')


def guard_output_path(output, sources):
    _require(Path(output).resolve() not in {Path(source).resolve() for source in sources},
             'audit output must not overwrite a source file')


def _validate_summaries(suite, cells, baseline, policy_ids):
    """Validate reported scenario means and every shared-patient denominator."""
    rows = suite['records']
    summaries = suite['summaries']
    scenario_ids = {row['scenario_id'] for row in rows}
    _require(len(summaries) == len(scenario_ids)
             and {summary['scenario_id'] for summary in summaries} == scenario_ids,
             'summary scenario coverage mismatch')
    checks = 0
    for summary in summaries:
        _require(summary['baseline_id'] == baseline, 'summary baseline changed')
        _require(len(summary['policies']) == len(policy_ids)
                 and {p['id'] for p in summary['policies']} == set(policy_ids),
                 'summary policy coverage mismatch')
        for policy in summary['policies']:
            matches = [row for row in rows if row['scenario_id'] == summary['scenario_id']
                       and row['policy_id'] == policy['id']]
            for key in ('patients', 'commit_observed', 'arrival_observed', 'start_observed'):
                _require(policy['denominators'][key] == sum(row['metrics'][key] for row in matches),
                         'summary denominator mismatch: ' + key)
            for key in ('start_mean_minutes', 'arrival_mean_minutes', 'commit_mean_minutes',
                        'inquiries_per_patient', 'not_treated', 'not_arrived', 'unassigned'):
                if key not in policy['aggregate']:
                    continue
                values = [row['metrics'][key] for row in matches if row['metrics'][key] is not None]
                aggregate = policy['aggregate'][key]
                _number(aggregate['mean'], _mean(values), 'summary aggregate mean: ' + key)
                if 'seeds_observed' in aggregate:
                    _require(aggregate['seeds_observed'] == len(values), 'summary observed seeds mismatch')
                if 'seeds_total' in aggregate:
                    _require(aggregate['seeds_total'] == len(matches), 'summary total seeds mismatch')
            for field in ('first_commit_t', 'final_arrive_t', 'treatment_start_t'):
                diffs = [common_patient_differences(cells[(row['scenario_id'], row['seed'])][baseline], row, field)
                         for row in matches]
                shared = policy['shared_patient_times'][field]
                values = [_mean(group) for group in diffs if group]
                _require(shared['common_patient_total'] == sum(map(len, diffs)),
                         'summary common-patient denominator mismatch')
                _require(shared['paired_seed_count'] == len(values), 'summary paired-seed denominator mismatch')
                _number(shared['mean_difference'], _mean(values), 'summary common-patient mean mismatch')
                if 'bootstrap_samples' in suite:
                    _check_reported_interval(shared['interval_95'], values,
                        f'{summary["scenario_id"]}:{policy["id"]}:{field}', suite['bootstrap_samples'])
                if 'seed_details' in shared:
                    _require(len(shared['seed_details']) == len(matches)
                             and len({entry['seed'] for entry in shared['seed_details']}) == len(matches),
                             'summary seed-detail coverage mismatch')
                    details = {entry['seed']: entry for entry in shared['seed_details']}
                    for row, differences in zip(matches, diffs):
                        detail = details[row['seed']]
                        _require(detail['common_patients'] == len(differences), 'seed-detail common denominator')
                        _number(detail['mean_difference'], _mean(differences), 'seed-detail paired mean')
            checks += 1
    return checks


def _validate_source_inputs(records, specs, split):
    by_id = {spec['id']: spec for spec in specs}
    _require(len(by_id) == len(specs), 'source scenario IDs duplicated')
    for row in records:
        spec = by_id[row['scenario_id']]
        expected = sorted((p['id'], p.get('priority', 4), p['search_start_t']) for p in spec['patients'])
        actual = sorted((o['id'], o['priority'], o['search_start_t']) for o in row['outcomes'])
        _require(expected == actual, 'record patients differ from frozen scenario input')
        _require(row['split'] == split and row['case_kind'] == spec['case_kind'], 'record split/family changed')
        _number(row['nonresponse_probability'], spec.get('experiment_nonresponse', 0),
                'record nonresponse differs from frozen scenario input')


def _policy_totals(records):
    outcomes = [outcome for row in records for outcome in row['outcomes']]
    starts = [o['treatment_start_t'] - o['search_start_t'] for o in outcomes
              if o['treatment_start_t'] is not None]
    inquiries = sum(o['inquiries'] for o in outcomes)
    return dict(branches=len(records), patients=len(outcomes), treatment_start_observed=len(starts),
        untreated=sum(o['treatment_start_t'] is None for o in outcomes),
        not_arrived=sum(o['final_arrive_t'] is None for o in outcomes),
        unassigned=sum(o['first_commit_t'] is None for o in outcomes),
        conditional_start_mean_minutes=_mean(starts), start_p95_minutes=_p95(starts),
        inquiries=inquiries, inquiries_per_patient=inquiries / len(outcomes) if outcomes else None,
        priority=priority_totals(records))


def validate_map_replays(payload, replay_export, viewer, cells, baseline, selected,
                         policy_ids, replay_conditions, kind):
    """Verify the predeclared three examples and all fifteen replay branches."""
    exported = replay_export['scenarios']
    _require(len(exported) == 3 and len(policy_ids) == 5, 'expected three replay cases with five policies')
    conditions = dict(replay_conditions)
    _require({item['scenario']['case_kind'] for item in exported} == set(conditions),
             'predeclared replay families changed')
    _require(len(payload['replay_cases']) == 3, 'result replay selection count changed')
    planned = {(item['scenario_id'], item['seed']): item for item in payload['replay_cases']}
    _require(len(planned) == 3, 'duplicate replay selections')
    exports_by_id = {}
    for item in exported:
        scenario = item['scenario']
        key = item['scenario_id'], item['seed']
        _require(key in planned and all(item.get(name) == value for name, value in planned[key].items()),
                 'replay export differs from frozen selection')
        _require(item['seed'] == min(payload['validation_seeds'])
                 and scenario['seed'] == item['seed']
                 and scenario['experiment_nonresponse'] == conditions[scenario['case_kind']]
                 and item['outcome_independent_selection'] is True,
                 'replay selection must use predeclared first seed and nonresponse conditions')
        _require(scenario['experiment_selection'] == planned[key], 'scenario replay selection changed')
        _require(scenario['id'] not in exports_by_id, 'duplicate exported scenario ID')
        exports_by_id[scenario['id']] = item
    mapped = [item for item in viewer['scenarios']
              if item['scenario'].get('experiment_environment', {}).get('kind') == kind]
    _require(len(mapped) == 3 and {item['scenario']['id'] for item in mapped} == set(exports_by_id),
             'viewer replay scenario coverage differs from exports')
    checked = 0
    for replay in mapped:
        scenario = replay['scenario']
        original = exports_by_id[scenario['id']]['scenario']
        selection = scenario['experiment_selection']
        _require(selection == original['experiment_selection'], 'viewer replay selection changed')
        for field in ('patients', 'hospitals', 'response_environment', 'exogenous_events', 'inquiry_queue'):
            _require(scenario.get(field) == original.get(field), 'viewer replay input changed: ' + field)
        _require(selection['baseline_id'] == baseline and selection['policy_id'] == selected,
                 'map comparison differs from development winner')
        cell = cells[(selection['scenario_id'], selection['seed'])]
        differences = common_patient_differences(cell[baseline], cell[selected])
        _number(selection['difference'], _mean(differences), 'map selected difference mismatch')
        _require(selection['common_patients'] == len(differences), 'map selected common denominator mismatch')
        _require(len(replay['branches']) == 5
                 and {branch['id'] for branch in replay['branches']} == set(policy_ids),
                 'map five-policy branch coverage mismatch')
        for branch in replay['branches']:
            row = cell[branch['id']]
            _require(branch['policy'] == row['policy'], 'map policy configuration changed')
            expected = {outcome['id']: outcome for outcome in row['outcomes']}
            actual = {outcome['id']: outcome for outcome in branch['patient_outcomes']}
            _require(len(actual) == len(branch['patient_outcomes']) and set(expected) == set(actual),
                     'map patient identity/denominator mismatch')
            for pid in expected:
                for field in ('first_commit_t', 'final_arrive_t', 'treatment_start_t',
                              'inquiries', 'transfers', 'redirects', 'state'):
                    _require(expected[pid].get(field) == actual[pid].get(field),
                             f'map outcome mismatch: {scenario["id"]}/{branch["id"]}/{pid}/{field}')
            checked += 1
    return checked


OBJECTIVE_ORDER = ['priority4_not_treated', 'total_not_treated', 'priority4_not_arrived',
    'total_not_arrived', 'priority4_conditional_start_mean',
    'conditional_start_mean + 0.25 * inquiries_per_patient']


def independent_score(records):
    """Recompute the declared development objective from outcomes, not scores."""
    totals = _policy_totals(records)
    high = totals['priority']['4']
    start, inquiry = totals['conditional_start_mean_minutes'], totals['inquiries_per_patient']
    return dict(priority4_not_treated=high['untreated'], not_treated=totals['untreated'],
        priority4_not_arrived=high['not_arrived'], not_arrived=totals['not_arrived'],
        priority4_conditional_start_mean=high['conditional_start_mean_minutes'],
        conditional_start_mean=start, inquiries_per_patient=inquiry,
        combined_minutes=start + .25 * inquiry if start is not None else None,
        patients=totals['patients'], start_observed=totals['treatment_start_observed'],
        priority4_patients=high['patients'], priority4_start_observed=high['treatment_start_observed'],
        records=len(records))


def _score_order(score):
    return (score['priority4_not_treated'], score['not_treated'], score['priority4_not_arrived'],
        score['not_arrived'], score['priority4_conditional_start_mean']
        if score['priority4_conditional_start_mean'] is not None else math.inf,
        score['combined_minutes'] if score['combined_minutes'] is not None else math.inf,
        score['candidate_order'])


def _canonical_policy(spec):
    # Share configuration parsing only, never the experiment's score/selector.
    from .sim import Policy
    values = {key: value for key, value in spec.items() if key not in ('id', 'label')}
    return Policy(name=spec['id'], **values).spec()


def audit_development_payload(payload):
    """Audit the locked development result before any holdout run is allowed."""
    from .priority_experiment import DEVELOPMENT_POLICIES, FIXED_POLICIES, LOCKED_V2, PRIORITY_POLICIES
    _require(payload['schema_version'] == 1 and payload['experiment'] == 'priority_protection_v1',
             'unexpected priority experiment schema')
    _require(payload['development_only'] is True, 'expected a development-only source')
    validate_fresh_seeds(payload, payload)
    _require(payload['suites']['validation']['records'] == []
             and payload['suites']['validation']['summaries'] == []
             and payload['replay_cases'] == [], 'development source already contains holdout results')
    specs = payload['scenario_specs']['development']
    val_specs = payload['scenario_specs']['validation']
    _require({s['id'] for s in specs}.isdisjoint(s['id'] for s in val_specs),
             'development and validation scenario IDs overlap')
    rows = payload['suites']['development']['records']
    policy_ids = {p['id'] for p in DEVELOPMENT_POLICIES}
    expected = {(s['id'], seed) for s in specs for seed in payload['development_seeds']}
    cells = validate_priority_records(rows, policy_ids, expected)
    _validate_source_inputs(rows, specs, 'development')
    configs = {p['id']: _canonical_policy(p) for p in DEVELOPMENT_POLICIES}
    _require(all(row['policy'] == configs[row['policy_id']] for row in rows),
             'development candidate configuration changed')
    _require(payload['branch_runs'] == len(rows)
             and payload['patient_runs'] == sum(len(row['outcomes']) for row in rows),
             'development branch/patient run count mismatch')
    tuning = payload['tuning']
    _require(tuning['selection_split'] == 'development' and tuning['validation_retuning'] is False
             and tuning['objective_order'] == OBJECTIVE_ORDER
             and tuning['inquiry_penalty_minutes'] == .25
             and tuning['v2_reference'] == LOCKED_V2, 'development lock contract changed')
    choices = {}
    for group, candidates, selected_key in (
        ('fixed', FIXED_POLICIES, 'selected_fixed_policy'),
        ('priority', PRIORITY_POLICIES, 'selected_priority_policy'),
    ):
        stored = tuning[group]
        _require(stored['selection_split'] == 'development' and stored['validation_retuning'] is False
                 and stored['objective_order'] == OBJECTIVE_ORDER
                 and stored['inquiry_penalty_minutes'] == .25, 'selection group contract changed')
        scores = [dict(policy_id=p['id'], candidate_order=index,
            **independent_score([row for row in rows if row['policy_id'] == p['id']]))
                  for index, p in enumerate(candidates)]
        _require(len(stored['scores']) == len(scores), 'development score coverage changed')
        for actual, expected_score in zip(stored['scores'], scores):
            _require(set(actual) == set(expected_score), 'development score fields changed')
            for key, value in expected_score.items():
                if key == 'policy_id':
                    _require(actual[key] == value, 'development candidate order changed')
                else:
                    _number(actual[key], value, 'development score mismatch: ' + key)
        winner = min(scores, key=_score_order)
        selected = next(p for p in candidates if p['id'] == winner['policy_id'])
        _require(stored['selected_policy'] == selected and tuning[selected_key] == selected,
                 'development winner does not match independent score calculation')
        choices[group] = dict(selected_policy_id=selected['id'], independently_recomputed_scores=scores)
    summary_checks = _validate_summaries(payload['suites']['development'], cells, LOCKED_V2['id'], policy_ids)
    return dict(status='pass', development_only=True,
        checks=dict(development_input_coverage=True, outcome_and_priority_denominators=True,
            candidate_configurations=True, objective_scores_and_winners=True, fresh_seed_plan=True),
        coverage=dict(conditions=len(specs), seeds=len(payload['development_seeds']),
            policies=len(policy_ids), branches=len(rows), patients=payload['patient_runs'],
            checked_summary_policies=summary_checks), choices=choices)


def _validate_overall_summaries(suite, policy_ids):
    summaries = suite['overall_summary']
    _require(len(summaries) == len(policy_ids)
             and {item['policy_id'] for item in summaries} == set(policy_ids),
             'overall priority summary policy coverage mismatch')
    for item in summaries:
        rows = [row for row in suite['records'] if row['policy_id'] == item['policy_id']]
        outcomes = [outcome for row in rows for outcome in row['outcomes']]
        _require(item['records'] == len(rows), 'overall summary branch denominator mismatch')
        _require(set(item['by_priority']) == {str(o['priority']) for o in outcomes},
                 'overall summary priority coverage mismatch')
        for tier in (None, *PRIORITIES):
            members = [o for o in outcomes if tier is None or o['priority'] == tier]
            if tier is not None and not members:
                continue
            group = item['overall'] if tier is None else item['by_priority'][str(tier)]
            _require(group['patients'] == len(members), 'overall priority patient denominator mismatch')
            for field, prefix in (('first_commit_t', 'commit'), ('final_arrive_t', 'arrival'),
                                  ('treatment_start_t', 'start')):
                values = [o[field] - o['search_start_t'] for o in members if o[field] is not None]
                _require(group[prefix + '_observed'] == len(values), 'overall priority observed denominator mismatch')
                for suffix, expected in (('mean_minutes', _mean(values)), ('p95_minutes', _p95(values)),
                                          ('max_minutes', max(values, default=None))):
                    _number(group[prefix + '_' + suffix], expected, 'overall priority time statistic mismatch')
            for key, field in (('not_treated', 'treatment_start_t'), ('not_arrived', 'final_arrive_t'),
                                ('unassigned', 'first_commit_t')):
                _require(group[key] == sum(o[field] is None for o in members), 'overall priority censoring mismatch')
        overall = item['overall']
        for key in ('inquiries', 'redirects', 'transfers'):
            _require(overall[key] == sum(o.get(key, 0) for o in outcomes), 'overall event sum mismatch: ' + key)
        _number(overall['inquiries_per_patient'], overall['inquiries'] / len(outcomes) if outcomes else None,
                'overall inquiry denominator mismatch')
        for key, diagnostic in item['diagnostics'].items():
            values = [row['metrics'][key] for row in rows
                      if isinstance(row['metrics'].get(key), (int, float))]
            aggregation = 'max' if 'max_' in key else 'branch_mean' if key == 'queue_wait_mean' else 'sum'
            expected = (max(values) if aggregation == 'max' else
                        _mean(values) if aggregation == 'branch_mean' else sum(values)) if values else None
            _require(diagnostic['aggregation'] == aggregation
                     and diagnostic['branches_observed'] == len(values)
                     and diagnostic['branches_total'] == len(rows), 'diagnostic aggregation denominator changed')
            _number(diagnostic['value'], expected, 'diagnostic aggregate mismatch: ' + key)


def _validate_comparisons(suite, cells, baseline, policy_ids, samples, rng_seed):
    stored = suite['policy_comparisons']
    _require(len(stored) == len(policy_ids) and {item['policy_id'] for item in stored} == set(policy_ids),
             'policy comparison coverage mismatch')
    independently_paired = {}
    for comparison in stored:
        selected = comparison['policy_id']
        _require(comparison['baseline_id'] == baseline, 'policy comparison baseline changed')
        _require(set(comparison['shared_patient_times']) == {'all', '4', '2', '1'},
                 'comparison priority coverage changed')
        independently_paired[selected] = {}
        for tier in (None, *PRIORITIES):
            group = 'all' if tier is None else str(tier)
            paired = paired_priority(cells, baseline, selected, tier=tier, samples=samples, rng_seed=rng_seed)
            independently_paired[selected][group] = paired
            reported = comparison['shared_patient_times'][group]
            _number(reported['mean_difference'], paired['mean_difference_minutes'], 'comparison common time mean mismatch')
            _require(reported['common_patient_total'] == paired['common_patients']
                     and reported['paired_seed_count'] == paired['seeds_with_common_times'],
                     'comparison common patient/seed denominator mismatch')
            seed_details = {entry['seed']: entry for entry in reported['seed_details']}
            _require(len(seed_details) == len(reported['seed_details'])
                     and set(seed_details) == {entry['seed'] for entry in paired['per_seed']},
                     'comparison seed detail coverage mismatch')
            for item in paired['per_seed']:
                actual = seed_details[item['seed']]
                _number(actual['mean_difference'], item['mean_difference_minutes'], 'comparison seed time mismatch')
                _require(actual['common_patients'] == item['common_patients'], 'comparison seed common denominator mismatch')
                before = [o for (_, seed), cell in cells.items() if seed == item['seed']
                          for o in cell[baseline]['outcomes'] if tier is None or o['priority'] == tier]
                after = [o for (_, seed), cell in cells.items() if seed == item['seed']
                         for o in cell[selected]['outcomes'] if tier is None or o['priority'] == tier]
                _require(actual['patients'] == len(after)
                         and actual['baseline_start_observed'] == sum(o['treatment_start_t'] is not None for o in before)
                         and actual['comparison_start_observed'] == sum(o['treatment_start_t'] is not None for o in after),
                         'comparison seed observed/all denominator mismatch')
            for key in ('patients', 'baseline_start_observed', 'comparison_start_observed'):
                _require(reported[key] == sum(item[key] for item in seed_details.values()),
                         'comparison overall denominator mismatch')
            values = [item['mean_difference_minutes'] for item in paired['per_seed']
                      if item['mean_difference_minutes'] is not None]
            _require(reported['regression_seed_count'] == sum(value > 0 for value in values),
                     'comparison regression seed count mismatch')
            _number(reported['regression_fraction'], sum(v > 0 for v in values) / len(values) if values else None,
                    'comparison regression fraction mismatch')
            _check_reported_interval(reported['interval_95'], values,
                f'priority:{selected}:{tier}', suite['bootstrap_samples'])
        details = comparison['condition_seed_details']
        indexed = {(entry['scenario_id'], entry['seed']): entry for entry in details}
        _require(len(indexed) == len(details) and set(indexed) == set(cells), 'comparison condition/seed coverage mismatch')
        inquiry_deltas = defaultdict(lambda: [0, 0])
        for key, cell in cells.items():
            actual = indexed[key]
            before, after = cell[baseline], cell[selected]
            high_before, high_after = priority_totals([before])['4'], priority_totals([after])['4']
            differences = common_patient_differences(before, after)
            expected = dict(priority4_not_treated_difference=high_after['untreated'] - high_before['untreated'],
                not_treated_difference=after['metrics']['not_treated'] - before['metrics']['not_treated'],
                not_arrived_difference=after['metrics']['not_arrived'] - before['metrics']['not_arrived'],
                inquiries_per_patient_difference=after['metrics']['inquiries_per_patient'] - before['metrics']['inquiries_per_patient'],
                common_start_mean_difference=_mean(differences), common_patients=len(differences))
            for name, value in expected.items():
                _number(actual[name], value, 'comparison condition difference mismatch: ' + name)
            inquiry_deltas[key[1]][0] += after['metrics']['inquiries'] - before['metrics']['inquiries']
            inquiry_deltas[key[1]][1] += len(after['outcomes'])
        values = [counts[0] / counts[1] for counts in inquiry_deltas.values()]
        _number(comparison['inquiry_difference']['mean_difference'], _mean(values), 'comparison inquiry mean mismatch')
        _check_reported_interval(comparison['inquiry_difference']['interval_95'], values,
                                 f'priority:{selected}:inquiry', suite['bootstrap_samples'])
        seed_counts = {item['seed']: item for item in comparison['seed_count_details']}
        _require(len(seed_counts) == len(comparison['seed_count_details'])
                 and set(seed_counts) == set(inquiry_deltas), 'comparison seed-count coverage mismatch')
        for seed, item in seed_counts.items():
            members = [detail for detail in details if detail['seed'] == seed]
            for name in ('priority4_not_treated_difference', 'not_treated_difference'):
                _require(item[name] == sum(detail[name] for detail in members), 'comparison seed count mismatch')
            counts = inquiry_deltas[seed]
            _number(item['inquiries_per_patient_difference'], counts[0] / counts[1],
                    'comparison seed inquiry denominator mismatch')
        for field, predicate in (('priority4_worsened_condition_seeds', lambda d: d['priority4_not_treated_difference'] > 0),
                                  ('total_worsened_condition_seeds', lambda d: d['not_treated_difference'] > 0),
                                  ('time_worsened_condition_seeds', lambda d: d['common_start_mean_difference'] is not None
                                                                          and d['common_start_mean_difference'] > 0)):
            _require(comparison[field] == sum(predicate(detail) for detail in details), 'comparison worsened count mismatch')
    return independently_paired


def audit_results(payload, development, replay_export, viewer, *, samples=10000, rng_seed=94131):
    from .priority_experiment import FIXED_POLICIES, LOCKED_V2, REPLAY_CONDITIONS
    development_audit = audit_development_payload(development)
    _require(payload['experiment'] == 'priority_protection_v1' and payload['schema_version'] == 1
             and payload['development_only'] is False, 'validation result is unavailable or has wrong schema')
    validate_fresh_seeds(payload, development)
    for key in ('tuning', 'scenario_specs', 'service_lognormal_sigma'):
        _require(payload[key] == development[key], 'development lock changed: ' + key)
    _require(payload['suites']['development'] == development['suites']['development'],
             'development results changed after policy lock')
    if 'development_reselected' in payload:
        _require(payload['development_reselected'] is False and payload['development_reexecuted'] is False,
                 'development policy was reselected or reexecuted')
    locked = payload['tuning']
    baseline, selected = LOCKED_V2['id'], locked['selected_priority_policy']['id']
    policies = [LOCKED_V2, locked['selected_priority_policy'], *FIXED_POLICIES]
    policy_ids = {policy['id'] for policy in policies}
    _require(len(policy_ids) == 5, 'validation must contain five distinct policies')
    rows = payload['suites']['validation']['records']
    specs = payload['scenario_specs']['validation']
    expected = {(spec['id'], seed) for spec in specs for seed in payload['validation_seeds']}
    cells = validate_priority_records(rows, policy_ids, expected)
    _validate_source_inputs(rows, specs, 'validation')
    configs = {p['id']: _canonical_policy(p) for p in policies}
    _require(all(row['policy'] == configs[row['policy_id']] for row in rows), 'validation policy configuration changed')
    all_rows = rows + payload['suites']['development']['records']
    _require(payload['branch_runs'] == len(all_rows)
             and payload['patient_runs'] == sum(len(row['outcomes']) for row in all_rows),
             'experiment branch/patient run totals mismatch')
    summary_checks = _validate_summaries(payload['suites']['validation'], cells, baseline, policy_ids)
    _validate_overall_summaries(payload['suites']['validation'], policy_ids)
    paired = _validate_comparisons(payload['suites']['validation'], cells, baseline, policy_ids, samples, rng_seed)
    replay_checks = validate_map_replays(payload, replay_export, viewer, cells, baseline, selected,
                                         policy_ids, REPLAY_CONDITIONS, 'priority_protection_validation')
    totals = {pid: _policy_totals([row for row in rows if row['policy_id'] == pid]) for pid in sorted(policy_ids)}
    return dict(schema_version=1, experiment='priority_protection_v1', status='pass',
        generated_at=datetime.now(timezone.utc).isoformat(), development_audit=development_audit,
        checks=dict(development_lock_unchanged=True, objective_scores_and_winners=True,
            fresh_validation_seeds=True, full_paired_input_coverage=True,
            outcome_and_priority_denominators=True, priority_conditional_means_and_p95=True,
            summary_and_common_patient_statistics=True, reported_whole_seed_intervals=True,
            predeclared_replay_selection=True,
            fifteen_map_branches_match_experiment=True),
        coverage=dict(validation_conditions=len(specs), validation_seeds=len(payload['validation_seeds']),
            validation_policies=len(policy_ids), validation_cells=len(cells), validation_branches=len(rows),
            total_branches=payload['branch_runs'], total_patient_runs=payload['patient_runs'],
            checked_summary_policies=summary_checks, checked_replay_branches=replay_checks),
        baseline_policy=baseline, selected_priority_policy=selected, policy_totals=totals,
        independently_paired_times=paired, selected_tradeoffs=tradeoffs(cells, baseline, selected),
        bootstrap=dict(samples=samples, rng_seed=rng_seed, confidence_level=.95,
            method='paired whole-seed percentile bootstrap with nearest-rank endpoints'),
        limitations=['This audit verifies arithmetic and consistency, not synthetic-model validity or clinical effectiveness.',
            'Same-minute priority protection cannot guarantee resources for patients not yet known to the policy.',
            'Conditional time means exclude unobserved outcomes; censored counts and identity switches are retained.',
            'Whole-seed intervals describe synthetic seed variation only; audit bootstrap uses an independent RNG.'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, default=ROOT / 'simulation_results/priority_protection_corrected.json')
    parser.add_argument('--development', type=Path, default=ROOT / 'simulation_results/priority_protection_corrected_development.json')
    parser.add_argument('--replays', type=Path, default=ROOT / 'simulation_results/priority_protection_corrected_replays.json')
    parser.add_argument('--viewer', type=Path, default=ROOT / 'simulation/viewer/map_replay_data.json')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--development-only', action='store_true')
    parser.add_argument('--bootstrap-samples', type=int, default=10000)
    parser.add_argument('--rng-seed', type=int, default=94131)
    args = parser.parse_args(argv)
    paths = (args.results, args.development, args.replays, args.viewer)
    output = args.output or ROOT / ('simulation_results/priority_protection_corrected_development_audit.json'
                                  if args.development_only else 'simulation_results/priority_protection_corrected_audit.json')
    try:
        guard_output_path(output, paths)
        if args.development_only:
            result = audit_development_payload(json.loads(args.development.read_text(encoding='utf-8')))
            sources = [args.development]
        else:
            values = [json.loads(path.read_text(encoding='utf-8')) for path in paths]
            result = audit_results(*values, samples=args.bootstrap_samples, rng_seed=args.rng_seed)
            sources = paths
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f'Audit FAIL: {error}\n')
    result['source_files'] = [str(path.resolve()) for path in sources]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print('Audit PASS: ' + json.dumps(result['coverage'], ensure_ascii=False))
    print(f'Output: {output}')


if __name__ == '__main__':
    main()
