"""Audit H1 online comparison artifacts using raw JSON only.

The frozen online auditor checks events, current offers, resource ledgers,
CP-SAT proofs, endpoints and paired metrics.  This extension independently
reconstructs H1's safe greedy baseline and checks its separate heuristic
records.  It never imports a simulator, solver or experiment runner.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

from . import online_allocation_audit as base


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = 'h1_online_20261015'
BUDGET_SCOPE = 'optional_search_after_snapshot_and_baseline'
OBJECTIVES = ['unassigned_priority_weight', 'priority_weighted_travel_minutes']
POLICY_IDS = ['R1', 'H1', 'C1']
FIXED_SETTINGS = dict(
    comparison_scope='same_potential_environment_not_identical_offers',
    inquiry_policy='fixed_3_first_acceptance_extra_wait_2_minutes',
    routing='synthetic fixed coordinates and travel; no external API',
    simulation_unit='integer minute',
    solver_wall_time='measured separately; not added to simulation clock',
    execution_order='rotate R1/H1/C1 by seed index plus family index; canonical output order',
    tuning='none; H1 uses previously selected 50ms/2048 moves unchanged',
)
H1OnlineAuditError = base.OnlineAllocationAuditError


def _require(condition, message):
    base._require(condition, message)


def _finite(value, name):
    _require(not isinstance(value, bool) and isinstance(value, (int, float))
             and math.isfinite(value) and value >= 0, 'invalid ' + name)
    return value


def _greedy(patients, free, options, travel):
    """Rebuild priority/request/id order and nearest feasible choice locally."""
    used, plan = Counter(), {}
    for patient in sorted(patients, key=lambda p: (-p['priority'], p['request_t'], p['id'])):
        pid, demand = patient['id'], patient['demand']
        feasible = [hid for hid in options[pid] if all(
            free.get((hid, resource), 0) - used[(hid, resource)] >= quantity
            for resource, quantity in demand.items() if quantity)]
        chosen = min(feasible, key=lambda hid: (travel[pid, hid], hid)) if feasible else None
        plan[pid] = chosen
        if chosen is not None:
            for resource, quantity in demand.items():
                if quantity:
                    used[chosen, resource] += quantity
    return plan


def _settings(report):
    _require(report.get('experiment_id') == EXPERIMENT_ID, 'wrong H1 experiment ID')
    policies = report.get('settings', {}).get('policies')
    _require(isinstance(policies, list) and len(policies) == 3
             and all(isinstance(p, dict) for p in policies), 'invalid H1 policy settings')
    specs = {p.get('id'): p for p in policies}
    _require([p.get('id') for p in policies] == POLICY_IDS,
             'H1 comparison requires canonical R1/H1/C1 order')
    settings = {key: value for key, value in report['settings'].items() if key != 'policies'}
    _require(base._same(settings, FIXED_SETTINGS), 'fixed H1 experiment settings differ')
    for code, spec in specs.items():
        allowed = {'id', 'label', 'rule', 'k', 'wait', 'notify'}
        allowed.update({'time_limit_seconds', 'max_moves'} if code == 'H1' else
                       {'time_limit_seconds', 'seed', 'require_backend'} if code == 'C1' else set())
        _require(set(spec) == allowed, 'unexpected or missing configured policy fields')
        _require(spec.get('rule') == code, 'configured H1 comparison rule mismatch')
        _require(spec.get('k') == 3 and spec.get('wait') == 2 and spec.get('notify') is True,
                 'inquiry or notification settings differ between rules')
    h1, c1 = specs['H1'], specs['C1']
    budget = _finite(h1.get('time_limit_seconds'), 'configured H1 time budget')
    base._integer(h1.get('max_moves'), 'invalid configured H1 move limit')
    _require(budget == .05 and h1['max_moves'] == 2048,
             'H1 settings differ from frozen development selection')
    _require(c1.get('time_limit_seconds') == budget and c1.get('seed') == 0
             and c1.get('require_backend') is True, 'C1 comparison budget or backend differs')
    return specs


def audit_h1_branch(scenario, branch, spec):
    """Check H1 evidence after the frozen raw trajectory audit has succeeded."""
    decisions = branch.get('allocation_decisions', [])
    records = branch.get('heuristic_records', [])
    _require(isinstance(records, list), 'invalid heuristic records')
    _require(branch['metrics'].get('whole_episode_optimal_proven') is False,
             'H1 whole-episode optimality claim')
    code = branch['policy']['rule']
    if code != 'H1':
        _require(not records, 'heuristic records on non-H1 branch')
        _require(all('heuristic_record_index' not in d for d in decisions),
                 'heuristic record reference on non-H1 branch')
        return dict(heuristic_batches=0, heuristic_baselines_checked=0,
                    heuristic_accepted_moves=0, heuristic_evaluated_moves=0)
    metadata = branch['policy'].get('heuristic', {})
    expected = dict(backend='bounded_joint_heuristic',
                    time_limit_seconds=spec['time_limit_seconds'], max_moves=spec['max_moves'],
                    time_budget_scope=BUDGET_SCOPE)
    _require(base._same(metadata, expected), 'reported heuristic settings differ from configured policy')
    _require(not branch.get('solver_records'), 'H1 records mixed with CP-SAT solver records')
    used = set()
    totals = dict(heuristic_batches=0, heuristic_baselines_checked=0,
                  heuristic_accepted_moves=0, heuristic_evaluated_moves=0)
    for index, decision in enumerate(decisions):
        _require(decision.get('rule') == 'H1', 'H1 decision rule mismatch')
        if decision['kind'] != 'batch':
            _require('heuristic_record_index' not in decision, 'single decision references heuristic batch')
            continue
        _require(decision.get('method') == 'heuristic', 'H1 batch method is not heuristic')
        _require(decision.get('optimal_proven') is False, 'H1 decision falsely claims optimality')
        record_index = decision.get('heuristic_record_index')
        base._integer(record_index, 'invalid heuristic record index')
        _require(record_index < len(records), 'missing heuristic record')
        _require(record_index not in used, 'heuristic record reused by multiple decisions')
        used.add(record_index)
        record = records[record_index]
        _require(isinstance(record, dict), 'invalid heuristic record')
        _require(record.get('decision_index') == index and record.get('t') == decision['t'],
                 'heuristic record points to another decision')
        _require(record.get('scope') == 'current_same_minute_accepted_offers',
                 'wrong heuristic record scope')
        _require(record.get('status') == 'HEURISTIC' and record.get('optimal_proven') is False,
                 'H1 record falsely claims optimality')
        plan = decision['assignments']
        _require(record.get('assignments') == plan, 'heuristic proposal differs from actual commits')
        if 'proposed_assignments' in record:
            _require(record['proposed_assignments'] == plan, 'heuristic proposal differs from actual commits')
        patients, free, options, travel = base._problem(scenario, decision)
        objective = base._objective(patients, plan, travel)
        _require(record.get('objective_names') == OBJECTIVES and record.get('objective') == objective,
                 'heuristic objective differs from raw plan')
        _require(record.get('batch_size') == len(patients)
                 and record.get('edge_count') == sum(map(len, options.values()))
                 and record.get('assigned_count') == sum(hid is not None for hid in plan.values()),
                 'heuristic problem size or assigned count mismatch')
        baseline = _greedy(patients, free, options, travel)
        baseline_objective = base._objective(patients, baseline, travel)
        _require(record.get('baseline_assignments') == baseline,
                 'heuristic baseline differs from independently rebuilt greedy plan')
        _require(record.get('baseline_objective') == baseline_objective
                 and record.get('baseline_assigned_count') == sum(hid is not None for hid in baseline.values()),
                 'heuristic baseline objective or count mismatch')
        _require(objective <= baseline_objective, 'heuristic worsens its safe greedy baseline')
        _require(record.get('time_limit_seconds') == spec['time_limit_seconds']
                 and record.get('max_moves') == spec['max_moves']
                 and record.get('time_budget_scope') == BUDGET_SCOPE,
                 'heuristic record settings differ from configured policy')
        evaluated = base._integer(record.get('evaluated_moves'), 'invalid evaluated move count')
        accepted = base._integer(record.get('accepted_moves'), 'invalid accepted move count')
        _require(accepted <= evaluated <= spec['max_moves'], 'heuristic move counts exceed limits')
        wall = _finite(record.get('wall_seconds'), 'heuristic wall time')
        preprocessing = _finite(record.get('preprocessing_seconds'), 'heuristic preprocessing time')
        search = _finite(record.get('search_seconds'), 'heuristic search time')
        _require(math.isclose(wall, preprocessing + search, rel_tol=1e-9, abs_tol=1e-9),
                 'heuristic timing components do not sum to wall time')
        _require(wall <= _finite(decision['elapsed_seconds'], 'decision wall time') + 1e-9,
                 'heuristic wall time exceeds enclosing decision time')
        # The optional budget is cooperative, not a hard bound on total wall time.
        improvements = record.get('improvements', [])
        move_types = record.get('accepted_move_types', [])
        _require(isinstance(improvements, list) and isinstance(move_types, list)
                 and len(improvements) == len(move_types) == accepted,
                 'heuristic improvement trace count mismatch')
        last_evaluation, previous = 0, baseline_objective
        for step, kind in zip(improvements, move_types):
            evaluation = base._integer(step.get('evaluation'), 'invalid improvement evaluation', minimum=1)
            current = step.get('objective')
            _require(last_evaluation < evaluation <= evaluated and step.get('kind') == kind,
                     'heuristic improvement trace order mismatch')
            _require(isinstance(current, list) and len(current) == 2
                     and all(type(v) is int and v >= 0 for v in current) and current < previous,
                     'heuristic improvement trace is not strictly improving')
            last_evaluation, previous = evaluation, current
        _require(previous == objective, 'heuristic final objective differs from improvement trace')
        totals['heuristic_batches'] += 1
        totals['heuristic_baselines_checked'] += 1
        totals['heuristic_accepted_moves'] += accepted
        totals['heuristic_evaluated_moves'] += evaluated
    _require(used == set(range(len(records))), 'orphan heuristic record')
    return totals


def _runtime_metrics(branch):
    metrics = branch['metrics']
    decisions = branch.get('allocation_decisions', [])
    heuristic = branch.get('heuristic_records', [])
    solvers = branch.get('solver_records', [])
    expected = dict(
        allocation_decisions=len(decisions),
        allocation_wall_seconds=sum(_finite(d['elapsed_seconds'], 'decision wall time') for d in decisions),
        heuristic_calls=len(heuristic),
        heuristic_wall_seconds=sum(_finite(r['wall_seconds'], 'heuristic wall time') for r in heuristic),
        solver_calls=len(solvers),
        solver_wall_seconds=sum(_finite(r['wall_seconds'], 'solver wall time') for r in solvers),
        solver_fallbacks=sum(bool(r.get('fallback')) for r in solvers),
        solver_optimal_batches=sum(bool(r.get('optimal_proven')) for r in solvers),
        heuristic_optimal_batches=0,
    )
    for key, value in expected.items():
        _require(key in metrics and base._same(metrics[key], value), 'runtime metric mismatch: ' + key)


def _p95(values):
    """Nearest-rank P95; empty denominators have no percentile."""
    if not values:
        return None
    return sorted(values)[math.ceil(len(values) * .95) - 1]


def _diagnostics(scenario, branch):
    # Endpoints are reconstructed from events, independently of stored outcomes.
    rows, _, _ = base._event_rows(scenario, branch)
    patients = {p['id']: p for p in scenario['patients']}
    arrivals = [row['final_arrive_t'] - patients[pid].get('occur_t', 0)
                for pid, row in rows.items() if row['final_arrive_t'] is not None]
    wall_times = [decision['elapsed_seconds'] * 1000
                  for decision in branch['allocation_decisions']]
    groups = {}
    for priority in (4, 2, 1):
        group = [row for pid, row in rows.items() if patients[pid].get('priority', 4) == priority]
        groups[str(priority)] = dict(patients=len(group),
            unassigned=sum(row['first_commit_t'] is None for row in group),
            not_arrived=sum(row['final_arrive_t'] is None for row in group),
            untreated=sum(row['treatment_start_t'] is None for row in group))
    return dict(arrival_p95_minutes=_p95(arrivals), arrival_denominator=len(arrivals),
        allocation_wall_p95_ms=_p95(wall_times), allocation_wall_max_ms=max(wall_times, default=None),
        allocation_decision_denominator=len(wall_times), priority_groups=groups)


def _paired_tails(scenario, baseline, comparison):
    left, _, _ = base._event_rows(scenario, baseline)
    right, _, _ = base._event_rows(scenario, comparison)
    differences = [right[pid]['final_arrive_t'] - row['final_arrive_t']
                   for pid, row in left.items() if row['final_arrive_t'] is not None
                   and right[pid]['final_arrive_t'] is not None]
    return dict(denominator=len(differences), delayed=sum(v > 0 for v in differences),
        earlier=sum(v < 0 for v in differences), equal=sum(v == 0 for v in differences),
        max_delay_minutes=max(0, max(differences)) if differences else None,
        p95_difference_minutes=_p95(differences))


def _case_metadata(report, case):
    scenario = case['scenario']
    _require(scenario.get('comparison_id') == EXPERIMENT_ID, 'wrong scenario comparison ID')
    _require([branch['id'] for branch in case['branches']] == POLICY_IDS,
             'branch output order differs from canonical policy order')
    offset = (report['seeds'].index(scenario['seed']) +
              report['families'].index(scenario['family'])) % len(POLICY_IDS)
    _require(case.get('execution_order') == POLICY_IDS[offset:] + POLICY_IDS[:offset],
             'execution order differs from prescribed rotation')
    _require(isinstance(case.get('diagnostics'), dict) and set(case['diagnostics']) == set(POLICY_IDS),
             'diagnostic branch IDs mismatch')
    _require(isinstance(case.get('paired_arrival_details'), dict)
             and set(case['paired_arrival_details']) == set(POLICY_IDS), 'paired tail branch IDs mismatch')
    baseline = case['branches'][0]
    for branch in case['branches']:
        _require(branch.get('comparison_id') == EXPERIMENT_ID, 'wrong branch comparison ID')
        code = branch['id']
        _require(base._same(case['diagnostics'][code], _diagnostics(scenario, branch)),
                 'raw diagnostic mismatch: ' + code)
        _require(base._same(case['paired_arrival_details'][code], _paired_tails(scenario, baseline, branch)),
                 'raw paired arrival tail mismatch: ' + code)


def audit_report(report, *, expected_source_sha256=None, lock=None, exact_limit=20000):
    """Validate the frozen online contract and the independent H1 extension."""
    try:
        result = base.audit_report(report, expected_source_sha256=expected_source_sha256,
                                   lock=lock, exact_limit=exact_limit)
        specs = _settings(report)
        totals = Counter()
        for case in report['results']:
            for branch in case['branches']:
                totals.update(audit_h1_branch(case['scenario'], branch, specs[branch['id']]))
                _runtime_metrics(branch)
            _case_metadata(report, case)
            totals['diagnostic_branches_checked'] += len(case['branches'])
            totals['paired_tail_branches_checked'] += len(case['branches'])
        return dict(result, **totals, experiment_id=EXPERIMENT_ID,
                    heuristic_optimality_claimed=False)
    except (KeyError, TypeError, AttributeError, IndexError) as error:
        raise H1OnlineAuditError('malformed H1 online audit artifact') from error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('report', type=Path)
    parser.add_argument('--lock', '--lock-file', dest='lock', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--exact-limit', type=int, default=20000)
    args = parser.parse_args(argv)
    if args.exact_limit < 0:
        parser.error('--exact-limit must be nonnegative')
    payload = json.loads(args.report.read_text(encoding='utf-8'))
    lock = json.loads(args.lock.read_text(encoding='utf-8')) if args.lock else None
    expected = {}
    for name in payload['source_sha256']:
        source = (ROOT / name).resolve()
        if not source.is_relative_to(ROOT):
            raise H1OnlineAuditError('source manifest path escapes project')
        expected[name] = hashlib.sha256(source.read_bytes()).hexdigest()
    result = dict(passed=True, **audit_report(payload, expected_source_sha256=expected,
                  lock=lock, exact_limit=args.exact_limit),
                  report_sha256=hashlib.sha256(args.report.read_bytes()).hexdigest(),
                  auditor_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if args.output:
        if args.output.resolve() in {args.report.resolve(), args.lock.resolve() if args.lock else None}:
            raise H1OnlineAuditError('audit output would overwrite report or lock')
        if args.output.exists():
            raise H1OnlineAuditError('audit output already exists; choose a new path')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding='utf-8')
    print(rendered, end='')


if __name__ == '__main__':
    main()
