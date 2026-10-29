"""Read-only, independently recomputed audit of observed-inquiry experiment files.

python -m simulation.observed_audit

Only the audit output is written. Source results, policies and scenarios are
never modified. Bootstrap uncertainty describes synthetic seed variation.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import statistics


ROOT = Path(__file__).resolve().parent.parent
REPLAY_FAMILIES = {'burst_w1', 'streaming_w2', 'shifting_background_w1'}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _equal_number(actual, expected, description):
    if actual is None or expected is None:
        _require(actual is expected, description)
    else:
        _require(math.isfinite(actual) and math.isfinite(expected)
                 and math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9),
                 description)


def _mean(values):
    return statistics.mean(values) if values else None


def _outcome_map(row):
    return {outcome['id']: outcome for outcome in row['outcomes']}


def validate_records(records, policy_ids, expected_cells):
    """Validate paired input coverage and recompute every outcome denominator."""
    cells = defaultdict(dict)
    for row in records:
        key = row['scenario_id'], row['seed']
        pid = row['policy_id']
        _require(pid in policy_ids and pid not in cells[key], 'unknown/duplicate policy cell')
        cells[key][pid] = row
        outcomes, metrics = row['outcomes'], row['metrics']
        _require(len(_outcome_map(row)) == len(outcomes), 'duplicate patient ID')
        _require(metrics['patients'] == len(outcomes), 'patient denominator mismatch')
        _require(metrics['inquiries'] == sum(o['inquiries'] for o in outcomes), 'inquiry sum mismatch')
        for field, count_key, mean_key in (
            ('treatment_start_t', 'start_observed', 'start_mean_minutes'),
            ('final_arrive_t', 'arrival_observed', 'arrival_mean_minutes'),
            ('first_commit_t', 'commit_observed', 'commit_mean_minutes'),
        ):
            times = [o[field] - o['search_start_t'] for o in outcomes if o[field] is not None]
            _require(metrics[count_key] == len(times), count_key + ' denominator mismatch')
            _equal_number(metrics[mean_key], _mean(times), mean_key + ' mismatch')
        _require(metrics['not_treated'] == len(outcomes) - metrics['start_observed'],
                 'untreated count mismatch')
        _require(metrics['not_arrived'] == len(outcomes) - metrics['arrival_observed'],
                 'not-arrived count mismatch')
    _require(set(cells) == set(expected_cells), 'missing/extra scenario-seed cells')
    for key, cell in cells.items():
        _require(set(cell) == set(policy_ids), f'policy coverage mismatch: {key}')
        signatures = {
            tuple(sorted((o['id'], o['priority'], o['search_start_t']) for o in row['outcomes']))
            for row in cell.values()
        }
        _require(len(signatures) == 1, f'patient input differs between policies: {key}')
    return dict(cells)


def common_patient_differences(before, after, field='treatment_start_t'):
    """Exclude one-sided observations; equal observed counts need not mean same IDs."""
    left, right = _outcome_map(before), _outcome_map(after)
    _require(set(left) == set(right), 'paired patient identities differ')
    ids = sorted(pid for pid in left if left[pid][field] is not None
                 and right[pid][field] is not None)
    return [(right[pid][field] - right[pid]['search_start_t'])
            - (left[pid][field] - left[pid]['search_start_t']) for pid in ids]


def bootstrap_seed_mean(values, samples=10000, rng_seed=73131):
    """Nearest-rank percentile interval after resampling whole seed aggregates."""
    _require(isinstance(samples, int) and not isinstance(samples, bool) and samples >= 100,
             'bootstrap samples must be an integer >= 100')
    _require(bool(values) and all(math.isfinite(value) for value in values),
             'bootstrap needs finite seed means')
    rng = random.Random(rng_seed)
    n = len(values)
    means = sorted(statistics.mean(values[rng.randrange(n)] for _ in range(n))
                   for _ in range(samples))
    return [means[math.ceil(.025 * samples) - 1], means[math.ceil(.975 * samples) - 1]]


def paired_overall(cells, baseline, selected, samples=10000, rng_seed=73131):
    """Pool common patients within each seed, then give each seed equal weight."""
    per_seed = defaultdict(lambda: {'differences': [], 'patients': 0, 'inquiry_difference': 0})
    switches = []
    for (scenario_id, seed), cell in sorted(cells.items()):
        before, after = cell[baseline], cell[selected]
        item = per_seed[seed]
        item['differences'].extend(common_patient_differences(before, after))
        item['patients'] += before['metrics']['patients']
        item['inquiry_difference'] += after['metrics']['inquiries'] - before['metrics']['inquiries']
        left, right = _outcome_map(before), _outcome_map(after)
        treated_left = {pid for pid, outcome in left.items() if outcome['treatment_start_t'] is not None}
        treated_right = {pid for pid, outcome in right.items() if outcome['treatment_start_t'] is not None}
        if treated_left != treated_right:
            switches.append(dict(scenario_id=scenario_id, seed=seed,
                baseline_only=sorted(treated_left - treated_right),
                selected_only=sorted(treated_right - treated_left)))
    seed_rows = [dict(seed=seed, common_patients=len(item['differences']),
        mean_difference_minutes=_mean(item['differences']),
        all_patients=item['patients'],
        inquiry_difference_per_patient=item['inquiry_difference'] / item['patients'])
        for seed, item in sorted(per_seed.items())]
    time_values = [r['mean_difference_minutes'] for r in seed_rows
                   if r['mean_difference_minutes'] is not None]
    inquiry_values = [r['inquiry_difference_per_patient'] for r in seed_rows]
    all_differences = [value for item in per_seed.values() for value in item['differences']]
    return dict(baseline_policy=baseline, selected_policy=selected,
        difference_direction='selected minus baseline; negative is smaller',
        aggregation='common-patient differences pooled within seed across all conditions; equal-weight mean of seed means',
        interval_method='paired whole-seed bootstrap; percentile interval with nearest-rank endpoints',
        bootstrap_samples=samples, rng_seed=rng_seed, confidence_level=.95,
        inference_scope='synthetic seed variation only; not real-world or clinical effectiveness',
        seeds=len(seed_rows), seeds_with_common_times=len(time_values),
        common_patients=len(all_differences),
        mean_difference_minutes=_mean(time_values),
        interval_95_minutes=bootstrap_seed_mean(time_values, samples, rng_seed),
        pooled_common_patient_mean_minutes=_mean(all_differences),
        regression_seed_fraction=sum(value > 0 for value in time_values) / len(time_values),
        inquiry_mean_difference_per_patient=_mean(inquiry_values),
        inquiry_interval_95=bootstrap_seed_mean(inquiry_values, samples, rng_seed),
        inquiry_denominator='all patients; censored outcomes are retained',
        per_seed=seed_rows, treated_identity_switches=switches)


def audit_results(payload, development, replay_export, viewer, *, samples=10000, rng_seed=73131):
    _require(payload['tuning'] == development['tuning'], 'development policy lock changed')
    _require(payload['development_only'] is False, 'validation has not run')
    _require(set(payload['validation_seeds']).isdisjoint(payload['development_seeds']),
             'development and validation seeds overlap')
    _require(set(payload['validation_seeds']).isdisjoint(payload['previous_validation_seeds_excluded']),
             'previous validation seeds were reused')
    tuning = payload['tuning']
    baseline, selected = tuning['selected_fixed_policy']['id'], tuning['selected_observed_policy']['id']
    policy_ids = {baseline, selected, tuning['v1_reference']['id']}
    policy_ids.update(score['policy_id'] for score in tuning['fixed']['scores'])
    specs = payload['scenario_specs']['validation']
    expected_cells = {(spec['id'], seed) for spec in specs for seed in payload['validation_seeds']}
    rows = payload['suites']['validation']['records']
    cells = validate_records(rows, policy_ids, expected_cells)
    _require(payload['branch_runs'] == len(rows) + len(payload['suites']['development']['records']),
             'total branch count mismatch')
    all_rows = rows + payload['suites']['development']['records']
    _require(payload['patient_runs'] == sum(row['metrics']['patients'] for row in all_rows),
             'total patient-runs mismatch')

    totals, priority = {}, {}
    for pid in sorted(policy_ids):
        matches = [row for row in rows if row['policy_id'] == pid]
        n = sum(row['metrics']['patients'] for row in matches)
        observed = sum(row['metrics']['start_observed'] for row in matches)
        total_start = sum((row['metrics']['start_mean_minutes'] or 0) * row['metrics']['start_observed']
                          for row in matches)
        totals[pid] = dict(branches=len(matches), patients=n, treatment_start_observed=observed,
            untreated=sum(row['metrics']['not_treated'] for row in matches),
            not_arrived=sum(row['metrics']['not_arrived'] for row in matches),
            conditional_start_mean_minutes=total_start / observed if observed else None,
            inquiries_per_patient=sum(row['metrics']['inquiries'] for row in matches) / n)
        counts = Counter()
        for row in matches:
            for outcome in row['outcomes']:
                counts[(outcome['priority'], 'patients')] += 1
                if outcome['treatment_start_t'] is None:
                    counts[(outcome['priority'], 'untreated')] += 1
                if outcome['first_commit_t'] is None:
                    counts[(outcome['priority'], 'unassigned')] += 1
        priority[pid] = {str(group): {key: counts[(group, key)]
            for key in ('patients', 'untreated', 'unassigned')} for group in (1, 2, 4)}

    summary_checks = 0
    for summary in payload['suites']['validation']['summaries']:
        for policy in summary['policies']:
            matches = [row for row in rows if row['scenario_id'] == summary['scenario_id']
                       and row['policy_id'] == policy['id']]
            _require(bool(matches), 'summary has no matching records')
            for key in ('patients', 'commit_observed', 'arrival_observed', 'start_observed'):
                _require(policy['denominators'][key] == sum(row['metrics'][key] for row in matches),
                         'summary denominator mismatch: ' + key)
            for key in ('start_mean_minutes', 'arrival_mean_minutes', 'commit_mean_minutes',
                        'inquiries_per_patient', 'not_treated', 'not_arrived'):
                values = [row['metrics'][key] for row in matches if row['metrics'][key] is not None]
                _equal_number(policy['aggregate'][key]['mean'], _mean(values), 'summary mean: ' + key)
            diffs = [common_patient_differences(cells[(row['scenario_id'], row['seed'])][baseline], row)
                     for row in matches]
            shared = policy['shared_patient_times']['treatment_start_t']
            _require(shared['common_patient_total'] == sum(map(len, diffs)), 'summary common-patient denominator')
            _equal_number(shared['mean_difference'], _mean([_mean(d) for d in diffs if d]),
                          'summary common-patient mean')
            summary_checks += 1

    exports = replay_export['scenarios']
    _require(len(exports) == 3 and {item['scenario']['case_kind'] for item in exports} == REPLAY_FAMILIES,
             'predeclared replay families differ')
    for item in exports:
        _require(item['seed'] == min(payload['validation_seeds'])
                 and item['scenario']['experiment_nonresponse'] == 0
                 and item['outcome_independent_selection'] is True,
                 'replay selection was not predeclared first-seed/nonresponse-zero')
    mapped = [item for item in viewer['scenarios'] if item['scenario'].get('experiment_environment', {})
              .get('kind') == 'observed_inquiry_validation']
    _require(len(mapped) == len(exports), 'new map replay count mismatch')
    _require({item['scenario']['id'] for item in mapped} == {item['scenario']['id'] for item in exports},
             'map replay IDs differ from exports')
    replay_checks = 0
    for replay in mapped:
        scenario = replay['scenario']
        selection = scenario['experiment_selection']
        cell = cells[(selection['scenario_id'], selection['seed'])]
        _require({branch['id'] for branch in replay['branches']} == policy_ids, 'map policy coverage mismatch')
        diffs = common_patient_differences(cell[baseline], cell[selected])
        _equal_number(selection['difference'], _mean(diffs), 'replay selected difference mismatch')
        _require(selection['common_patients'] == len(diffs), 'replay common denominator mismatch')
        for branch in replay['branches']:
            expected = _outcome_map(cell[branch['id']])
            actual = {outcome['id']: outcome for outcome in branch['patient_outcomes']}
            _require(set(expected) == set(actual), 'map patient identities mismatch')
            for pid in expected:
                for field in ('first_commit_t', 'final_arrive_t', 'treatment_start_t',
                              'inquiries', 'transfers', 'redirects'):
                    _require(expected[pid].get(field) == actual[pid].get(field),
                             f'map outcome mismatch: {scenario["id"]}/{branch["id"]}/{pid}/{field}')
            replay_checks += 1

    paired = paired_overall(cells, baseline, selected, samples, rng_seed)
    return dict(schema_version=1, experiment='observed_inquiry_v2', status='pass',
        generated_at=datetime.now(timezone.utc).isoformat(),
        checks=dict(development_lock_unchanged=True, same_policy_input_coverage=True,
            outcome_counts_and_time_means=True, summary_denominators_and_means=True,
            predeclared_replay_selection=True, replay_outcomes_match_experiment=True),
        coverage=dict(validation_conditions=len(specs), validation_seeds=len(payload['validation_seeds']),
            policies=len(policy_ids), scenario_seed_cells=len(cells), validation_branches=len(rows),
            total_branches=payload['branch_runs'], total_patient_runs=payload['patient_runs'],
            checked_summary_policies=summary_checks, checked_replay_branches=replay_checks),
        policy_totals=totals, paired_overall=paired, priority_outcomes=priority,
        conditional_mean_difference_minutes=totals[selected]['conditional_start_mean_minutes']
                                           - totals[baseline]['conditional_start_mean_minutes'],
        priority4_untreated_difference=priority[selected]['4']['untreated'] - priority[baseline]['4']['untreated'],
        limitations=[
            'This audit checks calculation and comparison consistency, not the validity of synthetic clinical assumptions.',
            'Confidence intervals reflect synthetic seed variation only, not current-system or clinical effectiveness.',
            'Conditionally observed outcome means can compare different patient identities; paired means use their intersection.',
            'Equal total untreated does not imply equal priority protection; priority-specific counts are reported separately.',
        ])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, default=ROOT / 'simulation_results/observed_inquiry.json')
    parser.add_argument('--development', type=Path, default=ROOT / 'simulation_results/observed_inquiry_development.json')
    parser.add_argument('--replays', type=Path, default=ROOT / 'simulation_results/observed_inquiry_replays.json')
    parser.add_argument('--viewer', type=Path, default=ROOT / 'simulation/viewer/map_replay_data.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'simulation_results/observed_inquiry_audit.json')
    parser.add_argument('--bootstrap-samples', type=int, default=10000)
    parser.add_argument('--rng-seed', type=int, default=73131)
    args = parser.parse_args(argv)
    paths = (args.results, args.development, args.replays, args.viewer)
    if args.output.resolve() in {path.resolve() for path in paths}:
        parser.error('audit output must not overwrite a source file')
    values = [json.loads(path.read_text(encoding='utf-8')) for path in paths]
    result = audit_results(*values, samples=args.bootstrap_samples, rng_seed=args.rng_seed)
    result['source_files'] = [str(path.resolve()) for path in paths]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(f'Audit PASS: {result["coverage"]["validation_branches"]} validation branches, '
          f'{result["coverage"]["checked_replay_branches"]} replay branches')
    print(f'Paired seed mean difference: {result["paired_overall"]["mean_difference_minutes"]:.6f} min; '
          f'95% interval {result["paired_overall"]["interval_95_minutes"]}')
    print(f'Output: {args.output}')


if __name__ == '__main__':
    main()
