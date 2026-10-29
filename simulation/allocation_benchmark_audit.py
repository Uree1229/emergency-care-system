"""Independent raw-input audit of a frozen allocation benchmark artifact.

No benchmark world, metric helper or solver is imported. Resource ledgers,
offered edges, objectives and small-case optima are recomputed from saved JSON.
This validates artifact consistency, not clinical realism or solver internals.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from itertools import product
import json
import math
from pathlib import Path
import statistics


ROOT = Path(__file__).resolve().parents[1]
WEIGHTS = {'high': 4, 'medium': 2, 'low': 1}
POLICIES = ('R0', 'R1', 'R2', 'R3', 'C1')


class AllocationAuditError(ValueError):
    """Saved raw inputs and reported results do not agree."""


def _require(condition, message):
    if not condition:
        raise AllocationAuditError(message)


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _weight(patient):
    value = patient['priority']
    _require(value in WEIGHTS, f"unknown priority for {patient['id']}")
    return WEIGHTS[value]


def _raw_problem(snapshot):
    patients = snapshot['patients']
    hospitals = {row['id']: row for row in snapshot['hospitals']}
    _require(len(hospitals) == len(snapshot['hospitals']), 'duplicate hospitals')
    _require(len({p['id'] for p in patients}) == len(patients), 'duplicate patients')
    free = {(hid, resource): quantity - h.get('occupied', {}).get(resource, 0)
            - h.get('committed', {}).get(resource, 0) - h.get('queued', {}).get(resource, 0)
            for hid, h in hospitals.items() for resource, quantity in h['capacity'].items()}
    options = {}
    for p in patients:
        _weight(p)
        _require(p['t_req'] <= snapshot['at'], 'future patient in observed snapshot')
        _require(len(set(p['accepted_hospitals'])) == len(p['accepted_hospitals']), 'duplicate offers')
        valid = []
        for hid in p['accepted_hospitals']:
            _require(hid in hospitals, 'unknown offered hospital')
            _require(p['t_req'] <= p['accepted_at'][hid] <= snapshot['at'], 'future or pre-request acceptance')
            h = hospitals[hid]
            if not set(p.get('required_capabilities', [])) <= set(h.get('capabilities', [])):
                continue
            if all(free.get((hid, resource), 0) >= quantity
                   for resource, quantity in p['resource_demands'].items() if quantity > 0):
                valid.append(hid)
        options[p['id']] = valid
    return patients, free, options


def _raw_metrics(snapshot, assignments, free, options):
    patients = snapshot['patients']
    _require(set(assignments) == {p['id'] for p in patients}, 'assignment patient IDs do not match')
    used, times = Counter(), []
    unassigned, weighted = [], 0
    for p in patients:
        hid = assignments[p['id']]
        if hid is None:
            unassigned.append(p)
            continue
        _require(hid in options[p['id']], f"assignment outside feasible observed offers: {p['id']}")
        for resource, quantity in p['resource_demands'].items():
            if quantity > 0:
                used[hid, resource] += quantity
        minutes = p['travel_minutes'][hid]
        times.append(minutes)
        weighted += _weight(p) * minutes
    _require(all(quantity <= free.get(key, 0) for key, quantity in used.items()),
             'resource ledger exceeded (capacity - occupied - committed - queued)')
    return dict(patient_count=len(patients), assigned_count=len(times),
        unassigned_count=len(unassigned), unassigned_priority_weight=sum(_weight(p) for p in unassigned),
        priority_weighted_travel_minutes=weighted,
        unassigned_by_priority={str(tier): sum(_weight(p) == tier for p in unassigned) for tier in (4, 2, 1)},
        travel_denominator=len(times), travel_mean_minutes=statistics.mean(times) if times else None,
        travel_max_minutes=max(times) if times else None,
        travel_p95_minutes=sorted(times)[math.ceil(.95 * len(times)) - 1] if times else None,
        constraint_violations=0)


def _objective(metrics):
    return (metrics['unassigned_priority_weight'], metrics['priority_weighted_travel_minutes'])


def _exact_objective(patients, free, options):
    """Bounded, independent enumeration; includes the all-unassigned plan."""
    best = None
    for choice in product(*(options[p['id']] + [None] for p in patients)):
        used, unassigned, travel = Counter(), 0, 0
        for p, hid in zip(patients, choice):
            if hid is None:
                unassigned += _weight(p)
            else:
                travel += _weight(p) * p['travel_minutes'][hid]
                for resource, quantity in p['resource_demands'].items():
                    if quantity > 0:
                        used[hid, resource] += quantity
        if all(quantity <= free.get(key, 0) for key, quantity in used.items()):
            key = (unassigned, travel)
            if best is None or key < best:
                best = key
    return best


def _equal_number(left, right):
    return left == right if left is None or right is None else math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-9)


def _audit_solver(branch, raw, patients, options):
    records = branch.get('solver_records', [])
    _require(len(records) == 1, 'C1 must contain one decision record per nonempty snapshot')
    record = records[0]
    edge_count = sum(len(value) for value in options.values())
    _require(record['edge_count'] == edge_count, 'C1 edge count mismatch')
    _require(record['batch_size'] == len(patients), 'C1 batch size mismatch')
    _require(record['assignments'] == branch['assignments'], 'C1 recorded assignments mismatch')
    _require(record['objective'] == list(_objective(raw)), 'C1 recorded objective mismatch')
    _require(record['assigned_count'] == raw['assigned_count'], 'C1 assigned count mismatch')
    _require(branch['fallbacks'] == int(bool(record.get('fallback'))), 'C1 fallback counter mismatch')
    stages = record.get('stages', [])
    _require(len(stages) <= 2, 'C1 has unexpected solver stages')
    if stages:
        _require(stages[0]['objective_name'] == 'assigned_priority_weight', 'wrong C1 primary objective')
    if len(stages) == 2:
        _require(stages[1]['objective_name'] == 'priority_weighted_travel_minutes', 'wrong C1 secondary objective')
    for index, stage in enumerate(stages):
        if stage['status'] == 'OPTIMAL':
            _require(stage['objective'] is not None and _equal_number(stage['objective'], stage['best_bound']),
                     'OPTIMAL solver stage has no matching bound')
        elif stage['status'] == 'FEASIBLE':
            _require(stage['objective'] is not None, 'FEASIBLE solver stage has no objective')
            _require(stage['best_bound'] + 1e-8 >= stage['objective'] if index == 0 else
                     stage['best_bound'] - 1e-8 <= stage['objective'], 'solver bound points the wrong way')
    status = record['status']
    if status == 'OPTIMAL_EMPTY':
        _require(edge_count == 0 and not stages and raw['assigned_count'] == 0, 'false empty optimum')
        _require(all(record.get(field) is True for field in
                     ('optimal_proven', 'primary_optimal_proven', 'secondary_optimal_proven')), 'missing empty proof flags')
        _require(not record.get('fallback'), 'empty optimum marked fallback')
        return 'logical_empty'
    primary = bool(stages and stages[0]['status'] == 'OPTIMAL')
    secondary = bool(len(stages) == 2 and stages[1]['status'] == 'OPTIMAL')
    _require(bool(record.get('primary_optimal_proven')) == primary, 'false primary proof flag')
    _require(bool(record.get('secondary_optimal_proven')) == secondary, 'false secondary proof flag')
    _require(bool(record.get('optimal_proven')) == (primary and secondary), 'false overall proof flag')
    if record.get('optimal_proven'):
        _require(status == 'OPTIMAL' and not record.get('fallback'), 'false OPTIMAL final status')
        assigned_weight = sum(_weight(p) for p in patients) - raw['unassigned_priority_weight']
        _require(_equal_number(stages[0]['objective'], assigned_weight), 'primary optimum differs from returned plan')
        _require(_equal_number(stages[1]['objective'], raw['priority_weighted_travel_minutes']),
                 'secondary optimum differs from returned plan')
        return 'cp_sat_two_stage'
    _require(status != 'OPTIMAL', 'OPTIMAL status without proof')
    return 'unproven'


def audit_report(report, *, expected_source_sha256=None, lock=None):
    """Raise on inconsistency and return counts distinguishing logical/solver proof.

    Pass expected_source_sha256 (normally the locked manifest) to establish a
    provenance check; stored hashes alone do not prove which code was executed.
    """
    _require(report.get('kind') == 'frozen_allocation_benchmark', 'wrong report kind')
    _require(report.get('synthetic') is True, 'missing synthetic flag')
    stored_sources = report.get('source_sha256')
    _require(isinstance(stored_sources, dict) and stored_sources, 'missing source provenance')
    if 'priority_weights' in report['settings']:
        _require(report['settings']['priority_weights'] == WEIGHTS, 'priority weight definition mismatch')
    if expected_source_sha256 is not None:
        _require(stored_sources == expected_source_sha256, 'source hashes differ from expected manifest')
    if lock is not None:
        _require(report['settings'] == lock['settings'], 'report settings differ from lock')
        _require(stored_sources == lock['source_sha256'], 'report source hashes differ from lock')
        if 'families' in lock:
            _require(report.get('families') == lock['families'], 'report families differ from lock')
        if 'runtime_versions' in lock:
            _require(report.get('runtime_versions') == lock['runtime_versions'], 'report runtime versions differ from lock')
        _require(report['phase'] in ('development', 'validation'), 'unknown experiment phase')
        _require(report['seeds'] == lock[f"{report['phase']}_seeds"], 'phase seed list differs from lock')
        if report['phase'] == 'validation':
            _require(report.get('lock_sha256') == _digest(lock), 'validation lock digest mismatch')
    limit = report['settings']['r3_enumeration_limit']
    counts = dict(cases=0, branches=0, small_exact_cases=0, cp_sat_two_stage_proofs=0,
                  logical_empty_proofs=0, unproven_cp_cases=0, provenance_checked=expected_source_sha256 is not None or lock is not None)
    seen = set()
    for result in report['results']:
        snapshot = result['snapshot']
        key = (snapshot['family'], snapshot['seed'])
        _require(key not in seen, 'duplicate family/seed result')
        seen.add(key)
        _require(snapshot['seed'] in report['seeds'], 'snapshot seed outside report seed list')
        patients, free, options = _raw_problem(snapshot)
        combinations = math.prod(len(options[p['id']]) + 1 for p in patients)
        _require(result['combinations'] == combinations, 'combination count mismatch')
        branches = {b['policy']: b for b in result['branches']}
        _require(len(branches) == len(result['branches']) and set(branches) == set(POLICIES), 'policy set mismatch')
        raw = {}
        for code, branch in branches.items():
            _require(branch['input_sha256'] == _digest(snapshot), 'branch input digest mismatch')
            raw[code] = _raw_metrics(snapshot, branch['assignments'], free, options)
            for field, value in raw[code].items():
                _require(field in branch['metrics'] and branch['metrics'][field] == value,
                         f'{snapshot["id"]}/{code}: raw metric mismatch: {field}')
            counts['branches'] += 1
        proof = _audit_solver(branches['C1'], raw['C1'], patients, options)
        counts[{'logical_empty': 'logical_empty_proofs', 'cp_sat_two_stage': 'cp_sat_two_stage_proofs',
                'unproven': 'unproven_cp_cases'}[proof]] += 1
        if combinations <= limit:
            _require(branches['R3']['fallbacks'] == 0, 'R3 unexpectedly fell back below enumeration limit')
            optimum = _exact_objective(patients, free, options)
            _require(_objective(raw['R3']) == optimum, 'R3 differs from independent exact optimum')
            _require(_objective(raw['C1']) >= optimum, 'C1 claims an objective below independent optimum')
            if proof != 'unproven':
                _require(_objective(raw['C1']) == optimum, 'proven C1 differs from independent exact optimum')
            counts['small_exact_cases'] += 1
        else:
            _require(branches['R3']['fallbacks'] == 1, 'R3 failed to disclose enumeration-limit fallback')
        for code in POLICIES:
            baseline, comparison = branches['R1']['assignments'], branches[code]['assignments']
            shared = [p for p in patients if baseline[p['id']] is not None and comparison[p['id']] is not None]
            values = [p['travel_minutes'][comparison[p['id']]] - p['travel_minutes'][baseline[p['id']]] for p in shared]
            expected = dict(denominator=len(values), mean_difference_minutes=statistics.mean(values) if values else None,
                total_difference_minutes=sum(values), comparison_only=sum(baseline[p['id']] is None and comparison[p['id']] is not None for p in patients),
                baseline_only=sum(baseline[p['id']] is not None and comparison[p['id']] is None for p in patients))
            _require(result['common_travel_vs_r1'][code] == expected, 'common-patient travel denominator mismatch')
        counts['cases'] += 1
    _require(len(set(report['seeds'])) == len(report['seeds']), 'duplicate report seeds')
    if 'families' in report:
        _require(len(set(report['families'])) == len(report['families']), 'duplicate report families')
        _require(seen == {(family, seed) for family in report['families'] for seed in report['seeds']},
                 'missing or unexpected family/seed cases')
    if 'summary' in report:
        summary_by_family = {row['family']: row for row in report['summary']}
        _require(len(summary_by_family) == len(report['summary']), 'duplicate summary families')
        families = {result['snapshot']['family'] for result in report['results']}
        _require(set(summary_by_family) == families, 'summary family set differs from raw results')
        _require(seen == {(family, seed) for family in families for seed in report['seeds']},
                 'missing family/seed cases')
        for family, summary in summary_by_family.items():
            cases = [r for r in report['results'] if r['snapshot']['family'] == family]
            _require(set(summary['policies']) == set(POLICIES), 'summary policy set mismatch')
            comparisons = Counter()
            for case in cases:
                by_policy = {branch['policy']: branch for branch in case['branches']}
                cp_key, r3_key = _objective(by_policy['C1']['metrics']), _objective(by_policy['R3']['metrics'])
                comparisons['better' if cp_key < r3_key else 'equal' if cp_key == r3_key else 'worse'] += 1
            _require(summary['cp_vs_r3'] == {key: comparisons[key] for key in ('better', 'equal', 'worse')},
                     'summary C1/R3 comparison differs from raw objectives')
            for code in POLICIES:
                branches = [next(b for b in case['branches'] if b['policy'] == code) for case in cases]
                walls = [b['wall_seconds'] for b in branches]
                _require(all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0 for v in walls),
                         'invalid wall-time observation')
                expected = dict(cases=len(branches),
                    mean_unassigned_count=statistics.mean(b['metrics']['unassigned_count'] for b in branches),
                    mean_unassigned_priority_weight=statistics.mean(b['metrics']['unassigned_priority_weight'] for b in branches),
                    mean_priority_weighted_travel_minutes=statistics.mean(b['metrics']['priority_weighted_travel_minutes'] for b in branches),
                    fallbacks=sum(b['fallbacks'] for b in branches),
                    wall_median_seconds=statistics.median(walls), wall_max_seconds=max(walls))
                if 'wall_p95_seconds' in summary['policies'][code]:
                    expected['wall_p95_seconds'] = sorted(walls)[math.ceil(.95 * len(walls)) - 1]
                for field, value in expected.items():
                    _require(summary['policies'][code].get(field) == value, f'summary raw aggregate mismatch: {field}')
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('report', type=Path)
    parser.add_argument('--lock-file', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    report = json.loads(args.report.read_text(encoding='utf-8'))
    lock = json.loads(args.lock_file.read_text(encoding='utf-8')) if args.lock_file else None
    expected = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                for name in report['source_sha256']}
    result = dict(passed=True, **audit_report(report, expected_source_sha256=expected, lock=lock),
                  report_sha256=hashlib.sha256(args.report.read_bytes()).hexdigest(),
                  auditor_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    text = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding='utf-8')
    print(text, end='')


if __name__ == '__main__':
    main()
