"""Independently audit C1 equal-objective stabilization from saved raw JSON.

Only pure frozen auditors are imported. The assignment rule, simulator,
scenario builder, OR-Tools and experiment runner are never executed here.
The two-objective proof concerns current reported offers only. Selecting a
greedy plan on an equal objective proves no global canonical tie optimum.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import statistics

from . import online_allocation_audit as base
from . import partial_information_audit as information
from . import responsive_confirmation_audit as responsive
from .h1_online_audit import _diagnostics, _finite, _paired_tails


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = 'stable_c1_20261015'
STABILITY_RULE = 'equal_objective_priority_request_id_nearest_baseline'
POLICY_IDS = responsive.POLICY_IDS + ['stable_observed_C1', 'stable_protected_C1', 'stable_responsive_C1']
DEVELOPMENT_SEEDS = [23101, 23102, 23103]
VALIDATION_SEEDS = [24101, 24102, 24103, 24104, 24105, 24106]
FAMILIES = responsive.FAMILIES + (
    'tie_older_request_priority', 'tie_same_request_identity',
    'tie_equal_distance_hospital_identity', 'greedy_suboptimal_scarce_option',
)
FIXED_SETTINGS = deepcopy(responsive.FIXED_SETTINGS)
FIXED_SETTINGS.update(
    execution_order='rotate seventeen branches by seed index plus family index; canonical output order',
    compute_time='allocation including tie postselection, protection and confirmation intervention measured separately, not added to simulation clock',
    tuning='fixed equal-objective baseline tie rule before new development and validation',
    stability=dict(rule=STABILITY_RULE,
        objective='exact original lexicographic pair; no epsilon or weighted perturbation',
        baseline='priority descending, request time ascending, patient ID ascending; feasible nearest hospital then hospital ID',
        replacement='baseline only if both objective components exactly equal the solver plan',
        solver='original shared 50ms model-and-solve budget; no extra solver stage',
        proof='original two-objective model only; no global tie-canonicality or whole-episode guarantee'))
COMPARISON_PAIRS = responsive.COMPARISON_PAIRS + tuple(
    (f'stable_{mode}_C1_vs_{mode}', f'{mode}_C1', f'stable_{mode}_C1')
    for mode in ('observed', 'protected', 'responsive')) + (
        ('stable_responsive_C1_vs_protected', 'protected_C1', 'stable_responsive_C1'),
        ('stable_responsive_C1_vs_observed', 'observed_C1', 'stable_responsive_C1'),
        ('stable_responsive_C1_vs_responsive_H1', 'responsive_H1', 'stable_responsive_C1'))
StableC1AuditError = base.OnlineAllocationAuditError


def _require(condition, message):
    base._require(condition, message)


def _greedy(patients, free, options, travel):
    """Local reconstruction from raw current inputs, independent of rule code."""
    plan, consumed = {}, Counter()
    ordered = sorted(patients, key=lambda row: (-row['priority'], row['request_t'], row['id']))
    for patient in ordered:
        pid, demand = patient['id'], patient['demand']
        feasible = [hid for hid in options[pid] if all(
            free.get((hid, resource), 0) - consumed[hid, resource] >= quantity
            for resource, quantity in demand.items() if quantity)]
        chosen = min(feasible, key=lambda hid: (travel[pid, hid], hid)) if feasible else None
        plan[pid] = chosen
        if chosen is not None:
            for resource, quantity in demand.items():
                consumed[chosen, resource] += quantity
    return plan


def stability_metrics(branch):
    records = [row['stability'] for row in branch['solver_records'] if 'stability' in row]
    return dict(stability_decisions=len(records),
        stability_tie_replacements=sum(row['applied'] for row in records),
        stability_unchanged_ties=sum(row['reason'] == 'equal_objective_unchanged' for row in records),
        stability_solver_better=sum(row['reason'] == 'solver_strictly_better' for row in records),
        stability_wall_seconds=sum(row['elapsed_seconds'] for row in records))


def audit_stability(scenario, branch):
    enabled = branch['stable_ties_enabled']
    _require(type(enabled) is bool, 'invalid stable-tie enabled flag')
    code = branch['policy']['rule']
    _require(not enabled or code == 'C1', 'stable-tie rule on a non-C1 branch')
    counts = Counter(stable_batches=0, stable_equal_objective_batches=0,
        stable_changed_batches=0, stable_solver_original_proofs=0)
    records = branch['solver_records']
    if not enabled:
        _require(all('stability' not in record for record in records),
                 'stability trace on frozen baseline branch')
        return dict(counts)
    used = set()
    for decision in branch['allocation_decisions']:
        if decision['kind'] != 'batch':
            continue
        index = base._integer(decision['solver_record_index'], 'invalid stable solver record index')
        _require(index < len(records) and index not in used, 'missing or reused stable solver record')
        used.add(index)
        record = records[index]
        trace = record.get('stability')
        _require(isinstance(trace, dict) and set(trace) == {
            'rule', 'solver_assignments', 'baseline_assignments', 'baseline_objective',
            'solver_objective', 'selected_assignments', 'applied', 'reason', 'elapsed_seconds',
            'optimality_claimed'},
            'missing or unknown stability trace fields')
        _require(trace['rule'] == STABILITY_RULE, 'wrong stable-tie rule identity')
        patients, free, options, travel = base._problem(scenario, decision)
        solver, baseline = trace['solver_assignments'], _greedy(patients, free, options, travel)
        _require(base._plan_feasible(patients, free, options, solver),
                 'original CP-SAT plan is outside current feasible model')
        _require(trace['baseline_assignments'] == baseline,
                 'stable baseline differs from independently rebuilt priority/request/id nearest plan')
        _require(base._plan_feasible(patients, free, options, baseline), 'rebuilt baseline is infeasible')
        solver_objective = base._objective(patients, solver, travel)
        baseline_objective = base._objective(patients, baseline, travel)
        _require(trace['solver_objective'] == solver_objective
                 and trace['baseline_objective'] == baseline_objective,
                 'stability objective differs from raw model plan')
        _require(solver_objective <= baseline_objective,
                 'original C1 objective is worse than its deterministic feasible baseline')
        equal = solver_objective == baseline_objective
        selected = baseline if equal else solver
        applied = equal and solver != baseline
        _require(type(trace['applied']) is bool and trace['applied'] == applied,
                 'stability applied flag differs from exact-objective plan selection')
        expected_reason = 'equal_objective_baseline' if applied else \
            'equal_objective_unchanged' if equal else 'solver_strictly_better'
        _require(trace['reason'] == expected_reason, 'stability reason differs from raw objective equality')
        _require(trace['selected_assignments'] == selected
                 and record['assignments'] == selected
                 and decision['proposed_assignments'] == selected,
                 'stability final proposal differs from prescribed equal-objective selection')
        _finite(trace['elapsed_seconds'], 'stability overhead seconds')
        _finite(record['wall_seconds'], 'solver total wall seconds')
        _finite(record['solver_pipeline_wall_seconds'], 'original solver pipeline seconds')
        _require(record['wall_seconds'] + 1e-9 >= trace['elapsed_seconds'] + record['solver_pipeline_wall_seconds'],
                 'stability overhead omitted from solver wall time')
        _finite(decision['elapsed_seconds'], 'whole allocation elapsed seconds')
        _require(decision['elapsed_seconds'] + 1e-9 >= record['wall_seconds'],
                 'solver and postselection wall time omitted from allocation metrics')
        _require(record['assigned_count'] == sum(hid is not None for hid in selected.values()),
                 'selected assigned count differs from stable plan')
        _require(trace['optimality_claimed'] is False,
                 'stability postselection falsely claims an additional optimum')
        original_record = deepcopy(record)
        original_record.update(assignments=deepcopy(solver), objective=solver_objective)
        original_decision = deepcopy(decision)
        original_decision.update(assignments=deepcopy(solver),
            objective=information._objective_dict(solver_objective),
            optimal_proven=decision['model_optimal_proven'])
        proof = base._solver_proof(original_record, original_decision, patients, options, solver_objective)
        _require(not any(record.get(field, False) for field in (
            'tie_optimal_proven', 'canonical_optimal_proven', 'whole_episode_optimal_proven',
            'actual_optimal_proven', 'global_optimal_proven')),
            'current-model two-objective proof overstated as global, canonical or actual optimum')
        counts.update(stable_batches=1, stable_equal_objective_batches=int(equal),
            stable_changed_batches=int(applied), stable_solver_original_proofs=int(proof != 'unproven'))
    _require(used == set(range(len(records))), 'orphan stable solver record')
    return dict(counts)


def audit_branch(scenario, branch, *, exact_limit=20000):
    checked = responsive.audit_branch(scenario, branch, exact_limit=exact_limit)
    checked['counts'].update(audit_stability(scenario, branch))
    checked['stability'] = stability_metrics(branch)
    return checked


def _settings(report):
    settings = report['settings']
    _require(base._same({key: value for key, value in settings.items() if key != 'policies'}, FIXED_SETTINGS),
             'fixed stability experiment settings differ')
    policies = settings['policies']
    _require(isinstance(policies, list) and [p.get('id') for p in policies] == POLICY_IDS,
             'comparison requires seventeen canonical baseline/intervention branches')
    for policy in policies:
        stable = policy['id'].startswith('stable_')
        prefix, code = policy['id'].removeprefix('stable_').split('_')
        protected = prefix in ('protected', 'early', 'watch', 'responsive')
        mode = 'observed' if protected else prefix
        fields = {'id', 'label', 'mode', 'rule', 'k', 'wait', 'notify', 'protection',
                  'prefetch', 'watch', 'grace_minutes', 'stable_ties'}
        fields.update({'time_limit_seconds', 'max_moves'} if code == 'H1' else
                      {'time_limit_seconds', 'seed', 'require_backend'} if code == 'C1' else set())
        _require(set(policy) == fields and policy['mode'] == mode and policy['rule'] == code
                 and type(policy['stable_ties']) is bool and policy['stable_ties'] == stable
                 and type(policy['protection']) is bool and policy['protection'] == protected
                 and type(policy['prefetch']) is bool and policy['prefetch'] == (prefix in ('early', 'responsive'))
                 and type(policy['watch']) is bool and policy['watch'] == (prefix in ('watch', 'responsive'))
                 and policy['grace_minutes'] == responsive.GRACE_BY_PREFIX[prefix],
                 'configured stability policy fields/mode/rule/flags mismatch')
        _require(not stable or code == 'C1', 'non-C1 stability experiment policy')
        _require(policy['k'] == 3 and policy['wait'] == 2 and policy['notify'] is True,
                 'inquiry or notification policy differs')
        if code in ('H1', 'C1'):
            _require(policy['time_limit_seconds'] == .05, 'model search budget differs')
        if code == 'H1':
            _require(policy['max_moves'] == 2048, 'heuristic move limit differs')
        if code == 'C1':
            _require(policy['seed'] == 0 and policy['require_backend'] is True,
                     'CP-SAT seed or required backend differs')
    return {policy['id']: policy for policy in policies}


def _audit_report(report, *, expected_source_sha256=None, lock=None, exact_limit=20000):
    _require(report.get('kind') == 'stable_c1_comparison'
             and report.get('experiment_id') == EXPERIMENT_ID and report.get('synthetic') is True,
             'wrong stable C1 report identity or synthetic scope')
    _require(report['phase'] in ('development', 'validation'), 'invalid report phase')
    _require(isinstance(report['source_sha256'], dict) and report['source_sha256'], 'missing source manifest')
    if expected_source_sha256 is not None:
        _require(report['source_sha256'] == expected_source_sha256, 'source hashes differ from expected manifest')
    _require(isinstance(report['runtime_versions'], dict) and report['runtime_versions'], 'missing runtime manifest')
    seeds, families = report['seeds'], report['families']
    _require(seeds and len(set(seeds)) == len(seeds)
             and all(type(seed) is int and seed >= 0 for seed in seeds), 'invalid or duplicated phase seeds')
    _require(families and len(set(families)) == len(families)
             and all(family in FAMILIES for family in families), 'unknown or duplicated stability families')
    if lock is not None:
        _require(lock.get('kind') == 'stable_c1_lock' and lock.get('experiment_id') == EXPERIMENT_ID,
                 'wrong development lock identity')
        for key in ('source_sha256', 'runtime_versions', 'settings', 'families'):
            _require(report[key] == lock[key], 'report differs from lock: ' + key)
        _require(lock['development_seeds'] == DEVELOPMENT_SEEDS
                 and lock['validation_seeds'] == VALIDATION_SEEDS
                 and lock['families'] == list(FAMILIES), 'lock differs from preregistered stability seeds/families')
        _require(seeds == lock[report['phase'] + '_seeds'], 'phase seed list differs from lock')
        if report['phase'] == 'validation':
            _require(report.get('lock_sha256') == base._digest(lock), 'validation lock digest differs')
    specs = _settings(report)
    seen, totals = set(), Counter()
    for case in report['results']:
        scenario = case['scenario']
        key = scenario['family'], scenario['seed']
        _require(key not in seen and key[0] in families and key[1] in seeds,
                 'unexpected or duplicated family/seed case')
        seen.add(key)
        _require(scenario.get('synthetic') is True and scenario.get('comparison_id') == EXPERIMENT_ID,
                 'scenario lacks synthetic stability comparison identity')
        branches = case['branches']
        _require([branch['id'] for branch in branches] == POLICY_IDS, 'branch output order differs')
        offset = (seeds.index(scenario['seed']) + families.index(scenario['family'])) % len(POLICY_IDS)
        _require(case['execution_order'] == POLICY_IDS[offset:] + POLICY_IDS[:offset],
                 'execution order differs from prescribed rotation')
        for name in ('summaries', 'information_metrics', 'guard_metrics',
                     'responsiveness_metrics', 'stability_metrics', 'diagnostics'):
            _require(set(case[name]) == set(POLICY_IDS), 'case branch IDs mismatch: ' + name)
        _require(set(case['paired_comparisons']) == {name for name, _, _ in COMPARISON_PAIRS},
                 'stability paired comparison IDs mismatch')
        by_id = {}
        for branch in branches:
            code = branch['id']
            by_id[code] = branch
            _require(branch.get('comparison_id') == EXPERIMENT_ID
                     and branch.get('source_experiment') == 'stable_c1_comparison',
                     'branch lacks stability comparison instrumentation')
            spec = specs[code]
            information._branch_policy(branch, spec)
            _require(type(branch['protection_enabled']) is bool
                     and branch['protection_enabled'] == spec['protection'],
                     'branch protection differs from configured policy')
            _require(type(branch['prefetch_enabled']) is bool and branch['prefetch_enabled'] == spec['prefetch']
                     and type(branch['watch_enabled']) is bool and branch['watch_enabled'] == spec['watch']
                     and branch['grace_minutes'] == spec['grace_minutes']
                     and type(branch['stable_ties_enabled']) is bool
                     and branch['stable_ties_enabled'] == spec['stable_ties'],
                     'branch confirmation/stability flags/grace differ from configured policy')
            checked = audit_branch(scenario, branch, exact_limit=exact_limit)
            for saved, actual in (('summaries', 'metrics'), ('information_metrics', 'information'),
                                  ('guard_metrics', 'guard'), ('responsiveness_metrics', 'responsiveness'),
                                  ('stability_metrics', 'stability')):
                _require(base._same(case[saved][code], checked[actual]), 'raw metric mismatch: ' + saved + '/' + code)
            _require(base._same(case['diagnostics'][code], _diagnostics(scenario, branch)),
                     'raw diagnostic mismatch: ' + code)
            totals.update(checked['counts'])
            totals['branches'] += 1
        for name, left_id, right_id in COMPARISON_PAIRS:
            pair = case['paired_comparisons'][name]
            left, right = by_id[left_id], by_id[right_id]
            _require(pair['baseline'] == left_id and pair['comparison'] == right_id,
                     'paired comparison labels differ: ' + name)
            _require(base._same(pair['metrics'], responsive._paired(scenario, left, right)),
                     'raw paired stability/common-patient mismatch: ' + name)
            _require(base._same(pair['arrival_details'], _paired_tails(scenario, left, right)),
                     'raw paired arrival tail mismatch: ' + name)
        totals['cases'] += 1
    _require(seen == {(family, seed) for family in families for seed in seeds},
             'missing family/seed comparison cases')
    if 'summary' in report:
        summary = {row['family']: row for row in report['summary']}
        _require(len(summary) == len(report['summary']) and set(summary) == set(families),
                 'aggregate family IDs mismatch')
        for family, group in summary.items():
            cases = [case for case in report['results'] if case['scenario']['family'] == family]
            _require(set(group['policies']) == set(POLICY_IDS), 'aggregate policy IDs mismatch')
            for code, saved in group['policies'].items():
                expected = dict(cases=len(cases), **{field: statistics.mean(case['summaries'][code][field]
                                for case in cases) for field in information.AGGREGATE_FIELDS})
                _require(base._same(saved, expected), 'aggregate differs from raw case means')
    return dict(totals, experiment_id=EXPERIMENT_ID,
        provenance_checked=expected_source_sha256 is not None or lock is not None,
        assignment_constraint_violations=0, actual_optimality_claimed=False,
        stability_contract='same two objective values only; priority/request/id nearest baseline; no third solve',
        proof_scope='verified_reported_model_only_not_canonical_tie_actual_gate_or_whole_episode')


def audit_report(report, *, expected_source_sha256=None, lock=None, exact_limit=20000):
    """Audit saved artifacts without executing the experiment or allocation rule."""
    _require(type(exact_limit) is int and exact_limit >= 0, 'invalid exact enumeration limit')
    try:
        return _audit_report(report, expected_source_sha256=expected_source_sha256,
                             lock=lock, exact_limit=exact_limit)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as error:
        raise StableC1AuditError('malformed stability artifact') from error


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
        _require(source.is_relative_to(ROOT), 'source manifest path escapes project')
        expected[name] = hashlib.sha256(source.read_bytes()).hexdigest()
    result = dict(passed=True, **audit_report(payload, expected_source_sha256=expected,
        lock=lock, exact_limit=args.exact_limit),
        report_sha256=hashlib.sha256(args.report.read_bytes()).hexdigest(),
        auditor_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if args.output:
        _require(not args.output.exists(), 'audit output already exists')
        _require(args.output.resolve() not in {args.report.resolve(), args.lock.resolve() if args.lock else None},
                 'audit output would overwrite report or lock')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as output:
            output.write(rendered)
    print(rendered, end='')


if __name__ == '__main__':
    main()
