"""Independent audit of saved online allocation replay artifacts.

Only raw JSON is consumed: no simulation, solver or experiment helpers are
imported.  Current decision ledgers and local optimality claims are checked
separately from whole-trajectory elapsed times.  Unobserved endpoints are
censored at the observation end, never silently dropped from capped means.
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


class OnlineAllocationAuditError(ValueError):
    """A saved replay and its reported interpretation disagree."""


def _require(condition, message):
    if not condition:
        raise OnlineAllocationAuditError(message)


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _integer(value, message, *, minimum=0):
    _require(not isinstance(value, bool) and isinstance(value, int) and value >= minimum, message)
    return value


def _mean(values):
    return statistics.mean(values) if values else None


def _same(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and set(actual) == set(expected) and all(
            _same(actual[k], value) for k, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            _same(left, right) for left, right in zip(actual, expected))
    if isinstance(expected, (float, int)) and not isinstance(expected, bool):
        return isinstance(actual, (float, int)) and not isinstance(actual, bool) and math.isclose(
            actual, expected, rel_tol=1e-12, abs_tol=1e-9)
    return actual == expected


def _end(scenario, branch):
    expected = scenario.get('max_minutes', 180)
    end = _integer(branch['metrics']['observation_end_t'], 'invalid observation end')
    # ReplaySimulation uses horizon=1 and tail=max_minutes, so its last tick is
    # max_minutes, rather than max_minutes - 1.
    _require(end == expected, 'observation end differs from scenario horizon')
    return end


def _event_rows(scenario, branch):
    """Recover endpoints from events, then verify the separately saved outcomes."""
    end = _end(scenario, branch)
    patients = {p['id']: p for p in scenario['patients']}
    _require(len(patients) == len(scenario['patients']), 'duplicate scenario patients')
    outcomes = {p['id']: p for p in branch['patient_outcomes']}
    _require(len(outcomes) == len(branch['patient_outcomes']) and set(outcomes) == set(patients),
             'outcome patient IDs differ from scenario')
    rows = {pid: dict(id=pid, first_commit_t=None, final_arrive_t=None,
        treatment_start_t=None, treatment_complete_t=None, inquiries=0, redirects=0,
        transfers=0, commit_events=0) for pid in patients}
    last_t = -1
    notifications = []
    events = branch['events']
    for index, event in enumerate(events):
        at = _integer(event['t'], 'invalid event time')
        _require(last_t <= at <= end, 'events unordered or outside observation window')
        last_t = at
        pid, kind = event.get('patient'), event['type']
        if pid not in patients:
            _require(pid is None or event.get('external') or str(pid).startswith('EXTERNAL-'),
                     'event contains unknown patient')
            continue
        spec, row = patients[pid], rows[pid]
        occur, search = spec.get('occur_t', 0), spec['search_start_t']
        _require(at >= occur, 'event occurs before patient exists')
        if kind in ('ask', 'commit', 'accept', 'decline', 'no_response', 'notify',
                    'redirect', 'transfer_depart', 'arrive', 'treatment_start'):
            _require(at >= search, 'clinical workflow event precedes search start')
        if kind == 'ask':
            row['inquiries'] += 1
        elif kind == 'commit':
            row['commit_events'] += 1
            if row['first_commit_t'] is None:
                row['first_commit_t'] = at
        elif kind == 'redirect':
            _require(row['first_commit_t'] is not None, 'redirect without earlier selection')
            row['redirects'] += 1
        elif kind == 'transfer_depart':
            _require(row['first_commit_t'] is not None, 'transfer without earlier selection')
            row['transfers'] += 1
        elif kind == 'arrive':
            _require(row['first_commit_t'] is not None, 'arrival without selection')
            row['final_arrive_t'] = at
        elif kind == 'arrival_unable':
            _require(row['final_arrive_t'] == at, 'failed arrival without matching arrival')
            row['final_arrive_t'] = None
        elif kind == 'treatment_start':
            _require(row['final_arrive_t'] is not None, 'treatment without successful final arrival')
            _require(row['treatment_start_t'] is None, 'duplicate patient treatment start')
            row['treatment_start_t'] = at
        elif kind == 'treatment_complete':
            _require(row['treatment_start_t'] is not None, 'completion without treatment')
            row['treatment_complete_t'] = at
        elif kind == 'notify':
            notifications.append((index, event))
    for pid, row in rows.items():
        outcome, spec = outcomes[pid], patients[pid]
        _require(spec['search_start_t'] <= end, 'future, unobserved patient in denominator')
        _require(outcome['occur_t'] == spec.get('occur_t', 0) and
                 outcome['search_start_t'] == spec['search_start_t'] and
                 outcome['priority'] == spec.get('priority', 4), 'outcome patient inputs mismatch')
        for field in ('first_commit_t', 'final_arrive_t', 'treatment_start_t',
                      'treatment_complete_t', 'inquiries', 'redirects', 'transfers'):
            _require(outcome[field] == row[field], f'{pid}: outcome/event mismatch: {field}')
        _require(row['commit_events'] == int(row['first_commit_t'] is not None) +
                 row['redirects'] + row['transfers'], 'commits do not match first selection and destination changes')
        expected_state = ('COMPLETE' if row['treatment_complete_t'] is not None else
            'INCOMPLETE' if row['treatment_start_t'] is not None else
            'UNASSIGNED' if row['first_commit_t'] is None else 'NOT_TREATED')
        _require(outcome['state'] == expected_state, 'outcome state inconsistent with endpoints')
    lags = []
    for index, event in notifications:
        next_redirect = next((later for later in events[index + 1:]
            if later['type'] == 'redirect' and later.get('patient') == event['patient']), None)
        if next_redirect is not None:
            lags.append(next_redirect['t'] - event['t'])
    return rows, lags, len(notifications)


def raw_branch_metrics(scenario, branch):
    """Recompute full-flow metrics from raw events, with explicit denominators."""
    rows, notification_lags, notifications = _event_rows(scenario, branch)
    patients = {p['id']: p for p in scenario['patients']}
    end = _end(scenario, branch)
    actual, capped = {name: [] for name in ('selection', 'arrival', 'treatment')}, {
        name: [] for name in ('selection', 'arrival', 'treatment')}
    for pid, row in rows.items():
        p = patients[pid]
        for name, field, origin in (
            ('selection', 'first_commit_t', p['search_start_t']),
            ('arrival', 'final_arrive_t', p.get('occur_t', 0)),
            ('treatment', 'treatment_start_t', p.get('occur_t', 0))):
            endpoint = row[field]
            elapsed = max(0, end - origin) if endpoint is None else endpoint - origin
            if endpoint is not None:
                actual[name].append(elapsed)
            capped[name].append(elapsed)
    high = [row for pid, row in rows.items() if patients[pid].get('priority', 4) == 4]
    n = len(rows)
    inquiry_count = sum(row['inquiries'] for row in rows.values())
    result = dict(patients=n,
        unassigned=sum(row['first_commit_t'] is None for row in rows.values()),
        not_arrived=sum(row['final_arrive_t'] is None for row in rows.values()),
        untreated=sum(row['treatment_start_t'] is None for row in rows.values()),
        high_priority_unassigned=sum(row['first_commit_t'] is None for row in high),
        high_priority_untreated=sum(row['treatment_start_t'] is None for row in high),
        inquiries=inquiry_count, inquiries_per_patient=inquiry_count / n if n else 0,
        redirects=sum(row['redirects'] for row in rows.values()),
        transfers=sum(row['transfers'] for row in rows.values()), notifications=notifications,
        notification_redirect_pairs=len(notification_lags),
        notification_without_redirect=notifications - len(notification_lags),
        notify_to_redirect_mean_minutes=_mean(notification_lags))
    for name in actual:
        result[name + '_denominator'] = len(actual[name])
        result[name + '_mean_minutes'] = _mean(actual[name])
        result[name + '_capped_mean_minutes'] = _mean(capped[name])
    return result


def paired_common_metrics(scenario, baseline, comparison):
    """Compare identical patient IDs; never compare unrelated survivor means."""
    left, _, _ = _event_rows(scenario, baseline)
    right, _, _ = _event_rows(scenario, comparison)
    result = {}
    for label, endpoint in (('selection', 'first_commit_t'), ('arrival', 'final_arrive_t'),
                            ('treatment', 'treatment_start_t')):
        common = [pid for pid in left if left[pid][endpoint] is not None and right[pid][endpoint] is not None]
        values = [right[pid][endpoint] - left[pid][endpoint] for pid in common]
        result[label] = dict(denominator=len(common), patient_ids=sorted(common),
            mean_difference_minutes=_mean(values), total_difference_minutes=sum(values),
            baseline_only=sum(left[pid][endpoint] is not None and right[pid][endpoint] is None for pid in left),
            comparison_only=sum(left[pid][endpoint] is None and right[pid][endpoint] is not None for pid in left))
    return result


def _problem(scenario, decision):
    at = _integer(decision['t'], 'invalid decision time')
    inputs = decision['inputs']
    _require(set(inputs) <= {'patients', 'hospitals', 'free_resources', 'travel_minutes'},
             'non-current or unknown input field in decision snapshot')
    patients = inputs['patients']
    _require(len({p['id'] for p in patients}) == len(patients), 'duplicate decision patients')
    scenario_patients = {p['id']: p for p in scenario['patients']}
    hospitals = {h['id']: h for h in inputs['hospitals']}
    _require(len(hospitals) == len(inputs['hospitals']), 'duplicate decision hospitals')
    free = {}
    for hid, h in hospitals.items():
        _require(set(h) <= {'id', 'capacity', 'used', 'committed', 'queued', 'capabilities'},
                 'future or unknown hospital snapshot field')
        for name in ('capacity', 'used', 'committed', 'queued'):
            for quantity in h.get(name, {}).values():
                _integer(quantity, 'invalid current resource ledger')
        resources = set().union(*(set(h.get(name, {})) for name in ('capacity', 'used', 'committed', 'queued')))
        for resource in resources:
            free[hid, resource] = (h.get('capacity', {}).get(resource, 0) -
                h.get('used', {}).get(resource, 0) - h.get('committed', {}).get(resource, 0) -
                h.get('queued', {}).get(resource, 0))
    saved_free = {(r['hospital'], r['resource']): r['free'] for r in inputs['free_resources']}
    _require(len(saved_free) == len(inputs['free_resources']), 'duplicate free-resource keys')
    _require(all(key in free and free[key] == quantity for key, quantity in saved_free.items()),
             'free resources differ from capacity - used - committed - queued')
    travel = {(r['patient'], r['hospital']): _integer(r['minutes'], 'invalid observed travel estimate')
              for r in inputs['travel_minutes']}
    _require(len(travel) == len(inputs['travel_minutes']), 'duplicate travel edges')
    options = {}
    ttl = scenario.get('accept_ttl_minutes', 10)
    for p in patients:
        _require(set(p) <= {'id', 'priority', 'demand', 'required_capabilities', 'position',
                            'request_t', 'options', 'accepted_at', 'valid_offer_expiries'},
                 'future or unknown patient snapshot field')
        _require(p['id'] in scenario_patients, 'decision contains unknown patient')
        spec = scenario_patients[p['id']]
        _require(p['request_t'] == spec['search_start_t'] <= at, 'future decision patient')
        _require(p['priority'] == spec.get('priority', 4) and p['priority'] in (1, 2, 4), 'decision priority mismatch')
        _require(p['demand'] == spec.get('resource_demands', {'ed_bed': 1}), 'decision resource demand mismatch')
        _require(len(set(p['options'])) == len(p['options']), 'duplicate decision offers')
        for quantity in p['demand'].values():
            _integer(quantity, 'invalid patient demand')
        feasible = []
        for hid in p['options']:
            _require(hid in hospitals, 'unknown offered hospital')
            _require((p['id'], hid) in travel, 'missing offered travel estimate')
            accepted = p['accepted_at'].get(hid)
            _require(accepted is not None and p['request_t'] <= accepted <= at < accepted + ttl,
                     'future, missing or expired acceptance')
            if 'valid_offer_expiries' in p:
                _require(p['valid_offer_expiries'].get(hid) == accepted + ttl,
                         'offer expiry differs from observed acceptance and configured TTL')
            h = hospitals[hid]
            compatible = set(p.get('required_capabilities', [])) <= set(h.get('capabilities', []))
            if compatible and all(free.get((hid, resource), 0) >= quantity
                                  for resource, quantity in p['demand'].items() if quantity > 0):
                feasible.append(hid)
        options[p['id']] = feasible
    return patients, free, options, travel


def _objective(patients, plan, travel):
    return [sum(p['priority'] for p in patients if plan[p['id']] is None),
        sum(p['priority'] * travel[p['id'], plan[p['id']]] for p in patients if plan[p['id']] is not None)]


def _plan_feasible(patients, free, options, plan):
    if set(plan) != {p['id'] for p in patients}:
        return False
    consumed = Counter()
    for p in patients:
        hid = plan[p['id']]
        if hid is None:
            continue
        if hid not in options[p['id']]:
            return False
        for resource, quantity in p['demand'].items():
            if quantity:
                consumed[hid, resource] += quantity
    return all(quantity <= free.get(key, 0) for key, quantity in consumed.items())


def _exact(patients, free, options, travel):
    best = None
    for choice in product(*(options[p['id']] + [None] for p in patients)):
        plan = {p['id']: hid for p, hid in zip(patients, choice)}
        if _plan_feasible(patients, free, options, plan):
            objective = _objective(patients, plan, travel)
            if best is None or objective < best:
                best = objective
    return best


def _solver_proof(record, decision, patients, options, objective):
    _require(record['t'] == decision['t'], 'solver record time differs from decision')
    _require(record['scope'] == 'current_same_minute_accepted_offers', 'solver claims wrong optimization scope')
    _require(record['assignments'] == decision['assignments'], 'solver/decision assignment mismatch')
    _require(record['objective'] == objective, 'solver objective differs from raw plan')
    _require(record['batch_size'] == len(patients) and record['edge_count'] == sum(map(len, options.values())),
             'solver problem size differs from observed problem')
    stages = record.get('stages', [])
    _require(len(stages) <= 2, 'too many CP-SAT stages')
    for i, stage in enumerate(stages):
        _require(stage['objective_name'] == ('assigned_priority_weight' if i == 0 else
            'priority_weighted_travel_minutes'), 'wrong CP-SAT objective name')
        if stage['status'] == 'OPTIMAL':
            _require(stage['objective'] is not None and _same(stage['objective'], stage['best_bound']),
                     'OPTIMAL stage lacks matching bound')
        elif stage['status'] == 'FEASIBLE':
            _require(stage['objective'] is not None, 'FEASIBLE stage lacks incumbent')
            _require(stage['best_bound'] >= stage['objective'] - 1e-8 if i == 0 else
                     stage['best_bound'] <= stage['objective'] + 1e-8, 'invalid solver bound direction')
    if record['status'] == 'OPTIMAL_EMPTY':
        _require(not stages and not any(options.values()) and not record.get('fallback') and
            all(record.get(key) is True for key in ('optimal_proven', 'primary_optimal_proven',
                                                   'secondary_optimal_proven')), 'false empty optimum')
        return 'logical_empty'
    primary = bool(stages and stages[0]['status'] == 'OPTIMAL')
    secondary = bool(len(stages) == 2 and stages[1]['status'] == 'OPTIMAL')
    _require(bool(record.get('primary_optimal_proven')) == primary and
             bool(record.get('secondary_optimal_proven')) == secondary and
             bool(record.get('optimal_proven')) == (primary and secondary), 'false CP-SAT proof flag')
    if primary and secondary:
        _require(record['status'] == 'OPTIMAL' and not record.get('fallback'), 'inconsistent optimal status')
        _require(_same(stages[0]['objective'], sum(p['priority'] for p in patients) - objective[0]) and
                 _same(stages[1]['objective'], objective[1]), 'proven objective differs from returned assignment')
        return 'cp_sat_two_stage'
    _require(record['status'] != 'OPTIMAL', 'OPTIMAL without both proofs')
    return 'unproven'


def audit_branch(scenario, branch, *, exact_limit=20000):
    metrics = raw_branch_metrics(scenario, branch)
    _require(branch['metrics'].get('whole_episode_optimal_proven', False) is False,
             'current batch proof incorrectly applied to whole trajectory')
    counts = dict(decisions=0, single_decisions=0, batch_decisions=0, exact_rechecks=0,
        cp_sat_two_stage_proofs=0, logical_empty_proofs=0, unproven_cp_batches=0,
        assignment_constraint_violations=0, whole_trajectory_optimality_claimed=False)
    records = branch.get('solver_records', [])
    used_records = set()
    decision_commits = Counter()
    for index, decision in enumerate(branch.get('allocation_decisions', [])):
        patients, free, options, travel = _problem(scenario, decision)
        plan = decision['assignments']
        _require(_plan_feasible(patients, free, options, plan), 'assignment outside observed offers or current resource ledger')
        objective = _objective(patients, plan, travel)
        _require(decision['objective'] == dict(unassigned_priority_weight=objective[0],
            priority_weighted_travel_minutes=objective[1]), 'decision objective differs from raw plan')
        _require(isinstance(decision['elapsed_seconds'], (int, float)) and
            math.isfinite(decision['elapsed_seconds']) and decision['elapsed_seconds'] >= 0, 'invalid decision wall time')
        _require(decision['kind'] in ('single', 'batch'), 'unknown decision kind')
        if 'scope' in decision:
            _require(decision['scope'] == ('current_patient_accepted_offers' if decision['kind'] == 'single'
                else 'current_same_minute_accepted_offers'), 'invalid decision proof scope')
        if decision['kind'] == 'single':
            _require(len(patients) == 1 and not decision.get('optimal_proven'), 'single decision has batch proof')
        for patient in patients:
            for hid in patient['options']:
                _require(any(event['type'] == 'accept' and event.get('patient') == patient['id']
                    and event.get('hospital') == hid and event['t'] == patient['accepted_at'][hid]
                    for event in branch['events']), 'observed offer lacks matching received acceptance event')
        for patient_id, hid in plan.items():
            if hid is not None:
                decision_commits[decision['t'], patient_id, hid] += 1
                _require(any(event['type'] == 'commit' and event.get('patient') == patient_id and
                    event.get('hospital') == hid and event['t'] == decision['t'] for event in branch['events']),
                    'decision assignment lacks matching commit event')
        counts[decision['kind'] + '_decisions'] += 1
        counts['decisions'] += 1
        proven = False
        if decision['rule'] == 'C1' and decision['kind'] == 'batch':
            record_index = decision.get('solver_record_index')
            _require(isinstance(record_index, int) and 0 <= record_index < len(records), 'missing C1 solver record')
            _require(record_index not in used_records, 'solver record reused by multiple decisions')
            used_records.add(record_index)
            record = records[record_index]
            _require(record['decision_index'] == index, 'solver record points to another decision')
            proof = _solver_proof(record, decision, patients, options, objective)
            if 'optimal_proven' in decision:
                _require(decision['optimal_proven'] is bool(record['optimal_proven']),
                         'decision and solver proof flags differ')
            _require(decision['method'] == ('cp_sat_fallback' if record.get('fallback') else 'cp_sat'),
                     'solver fallback method incorrectly labelled')
            counts[{'cp_sat_two_stage': 'cp_sat_two_stage_proofs', 'logical_empty': 'logical_empty_proofs',
                    'unproven': 'unproven_cp_batches'}[proof]] += 1
            proven = proof != 'unproven'
        combinations = math.prod(len(options[p['id']]) + 1 for p in patients)
        if combinations <= exact_limit and decision['kind'] == 'batch' and (
                decision['method'] == 'exact_enumeration' or proven):
            _require(objective == _exact(patients, free, options, travel),
                     'claimed current-batch optimum differs from independent enumeration')
            counts['exact_rechecks'] += 1
    _require(used_records == set(range(len(records))), 'orphan solver record')
    if branch.get('source_experiment') == 'online_allocation_comparison':
        actual_commits = Counter((event['t'], event.get('patient'), event.get('hospital'))
            for event in branch['events'] if event['type'] == 'commit')
        _require(actual_commits == decision_commits, 'commits not covered exactly once by decision records')
    # Replays contain many commits per patient after rerouting/transfers.  Their
    # count is deliberately not interpreted as the number of selected patients.
    counts['first_selections'] = metrics['selection_denominator']
    counts['all_commit_events'] = sum(e['type'] == 'commit' for e in branch['events'])
    for name in ('patients', 'unassigned', 'not_arrived', 'untreated', 'high_priority_unassigned',
                 'high_priority_untreated', 'inquiries', 'redirects', 'transfers', 'notifications'):
        if name in branch['metrics']:
            _require(branch['metrics'][name] == metrics[name], 'legacy replay metric mismatch: ' + name)
    return dict(counts=counts, metrics=metrics)


def audit_report(report, *, expected_source_sha256=None, lock=None, exact_limit=20000):
    _require(report.get('kind') == 'online_allocation_comparison', 'wrong report kind')
    _require(report.get('synthetic') is True, 'missing synthetic flag')
    sources = report.get('source_sha256')
    _require(isinstance(sources, dict) and sources, 'missing source hashes')
    if expected_source_sha256 is not None:
        _require(sources == expected_source_sha256, 'source hashes differ from expected manifest')
    _require(report['phase'] in ('development', 'validation'), 'unknown phase')
    _require(len(set(report['seeds'])) == len(report['seeds']), 'duplicate phase seeds')
    _require(len(set(report['families'])) == len(report['families']), 'duplicate families')
    if lock is not None:
        for field in ('settings', 'source_sha256', 'runtime_versions', 'families'):
            _require(report[field] == lock[field], 'report differs from lock: ' + field)
        _require(report['seeds'] == lock[report['phase'] + '_seeds'], 'seed list differs from lock')
        if report['phase'] == 'validation':
            _require(report.get('lock_sha256') == _digest(lock), 'validation lock digest mismatch')
    seen, policy_ids = set(), None
    totals = Counter()
    for result in report['results']:
        scenario = result['scenario']
        key = (scenario['family'], scenario['seed'])
        _require(key not in seen, 'duplicate family/seed case')
        seen.add(key)
        branches = {branch['id']: branch for branch in result['branches']}
        _require(len(branches) == len(result['branches']) and branches, 'duplicate or missing branch IDs')
        if policy_ids is None:
            policy_ids = set(branches)
        _require(set(branches) == policy_ids, 'policy set differs between cases')
        configured = report.get('settings', {}).get('policies')
        if configured:
            _require(set(branches) == {policy['id'] for policy in configured},
                     'branch IDs differ from configured policy set')
        _require(set(result['summaries']) == set(branches), 'summary branch IDs mismatch')
        for code, branch in branches.items():
            _require(branch.get('source_experiment') == 'online_allocation_comparison',
                     'branch lacks online decision instrumentation marker')
            if configured:
                spec = next(p for p in configured if p['id'] == code)
                _require(branch['policy']['rule'] == spec['rule'], 'reported rule differs from configured policy')
                for field in ('k', 'wait', 'notify', 'near_minutes', 'expand_after'):
                    _require(branch['policy'].get(field) == spec.get(field),
                             'reported inquiry setting differs from configured policy: ' + field)
                if spec['rule'] == 'C1':
                    solver = branch['policy'].get('solver', {})
                    _require(solver.get('backend') == 'ortools_cp_sat' and solver.get('num_search_workers') == 1,
                             'wrong CP-SAT backend or worker count')
                    for field, default in (('time_limit_seconds', 1.0), ('seed', 0), ('require_backend', False)):
                        _require(solver.get(field) == spec.get(field, default),
                                 'reported solver setting differs from configured policy: ' + field)
            audited = audit_branch(scenario, branch, exact_limit=exact_limit)
            _require(_same(result['summaries'][code], audited['metrics']), f'{scenario["id"]}/{code}: raw full-flow summary mismatch')
            if 'online_summary' in branch:
                _require(_same(branch['online_summary'], audited['metrics']), 'branch online summary differs from raw metrics')
            totals.update({key: value for key, value in audited['counts'].items()
                           if isinstance(value, int) and not isinstance(value, bool)})
            totals['branches'] += 1
        if 'paired_vs_r1' in result:
            _require('R1' in branches and set(result['paired_vs_r1']) == set(branches),
                     'paired comparison branch IDs mismatch')
            for code, branch in branches.items():
                common = paired_common_metrics(scenario, branches['R1'], branch)
                expected = {name: {key: value for key, value in row.items()
                    if key not in ('patient_ids', 'total_difference_minutes')} for name, row in common.items()}
                _require(_same(result['paired_vs_r1'][code], expected), 'paired common-patient metric mismatch')
        totals['cases'] += 1
    _require(seen == {(family, seed) for family in report['families'] for seed in report['seeds']},
             'missing or unexpected family/seed cases')
    if 'summary' in report:
        summary = {row['family']: row for row in report['summary']}
        _require(len(summary) == len(report['summary']) and set(summary) == set(report['families']),
                 'aggregate family IDs mismatch')
        fields = ('unassigned', 'not_arrived', 'untreated', 'inquiries_per_patient',
            'selection_capped_mean_minutes', 'arrival_capped_mean_minutes', 'treatment_capped_mean_minutes',
            'redirects', 'transfers', 'notifications', 'notification_without_redirect')
        for family, group in summary.items():
            rows = [r for r in report['results'] if r['scenario']['family'] == family]
            _require(set(group['policies']) == policy_ids, 'aggregate policy IDs mismatch')
            for code, saved in group['policies'].items():
                expected = dict(cases=len(rows), **{field: statistics.mean(r['summaries'][code][field]
                    for r in rows) for field in fields})
                _require(_same(saved, expected), 'family aggregate differs from raw case means')
    return dict(totals, provenance_checked=expected_source_sha256 is not None or lock is not None,
                proof_scope='current_same_minute_accepted_offers_only', whole_trajectory_optimality_claimed=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('report', type=Path)
    parser.add_argument('--lock-file', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    report = json.loads(args.report.read_text(encoding='utf-8'))
    lock = json.loads(args.lock_file.read_text(encoding='utf-8')) if args.lock_file else None
    expected = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in report['source_sha256']}
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
