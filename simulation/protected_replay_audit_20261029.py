"""Independent feasibility and opportunity-protection audit; never reruns G3."""
from collections import Counter
from copy import deepcopy

from . import final_assignment_audit_20261028 as common


def _require(value, message):
    if not value:
        raise AssertionError(message)


def _snapshot(decision):
    value = decision['inputs']
    rows = value['patients']
    travel = {(r['patient'], r['hospital']):r['minutes'] for r in value['travel_minutes']}
    free = {(r['hospital'], r['resource']):r['free'] for r in value['free_resources']}
    _require(len({r['id'] for r in rows}) == len(rows), 'duplicate patient')
    return rows, travel, free


def _feasible(rows, plan, free):
    _require(set(plan) == {row['id'] for row in rows}, 'plan IDs differ')
    taken = Counter()
    for row in rows:
        hid = plan[row['id']]
        if hid is None:
            continue
        _require(hid in row['options'], 'assignment without accepted offer')
        for resource, quantity in row['demand'].items():
            if quantity:
                taken[hid, resource] += quantity
    _require(all(q <= free.get(key, 0) for key,q in taken.items()), 'resource capacity exceeded')


def _baseline(rows, travel, free):
    taken, plan = Counter(), {}
    for row in rows:
        options = [hid for hid in row['options'] if all(
            free.get((hid,res), 0)-taken[hid,res] >= quantity for res,quantity in row['demand'].items() if quantity)]
        hid = min(options, key=lambda h:(travel[row['id'],h],h)) if options else None
        plan[row['id']] = hid
        if hid is not None:
            for res,quantity in row['demand'].items():
                if quantity:
                    taken[hid,res] += quantity
    return plan


def _objective(rows, travel, baseline, plan):
    extras=[max(0,travel[row['id'],plan[row['id']]]-travel[row['id'],baseline[row['id']]])
            for row in rows if baseline[row['id']] is not None]
    return [sum(row['priority'] for row in rows if plan[row['id']] is None),
            sum(hid is None for hid in plan.values()),max(extras,default=0),sum(extras),
            sum(row['priority']*travel[row['id'],plan[row['id']]] for row in rows if plan[row['id']] is not None),
            sum(plan[pid]!=hid for pid,hid in baseline.items() if hid is not None)]


def audit_replay(replay, *, source_scenario=None):
    original = replay['original_scenario']
    fingerprint = common.canonical_sha256(original)
    if source_scenario is not None:
        _require(common.canonical_sha256(source_scenario)==fingerprint, 'source input differs')
    _require(fingerprint==replay['input_scenario_sha256'], 'input hash differs')
    _require(replay['execution_order']==['G3'] and len(replay['branches'])==1, 'G3 branch required')
    branch = replay['branches'][0]
    _require(branch['id']=='G3' and branch['policy']['rule']=='G3', 'policy label differs')
    contract = branch['execution_contract']
    _require(contract['future_events_supplied_to_rule'] is False, 'future information supplied')
    _require(contract['whole_episode_nonregression_guaranteed'] is False, 'unproven global guarantee')
    records = branch['protected_records']
    counters = Counter()
    batch_count = 0
    for index,decision in enumerate(branch['allocation_decisions']):
        rows,travel,free = _snapshot(decision)
        actual = decision['assignments']
        _feasible(rows, actual, free)
        baseline = _baseline(rows, travel, free)
        if decision['kind']=='single':
            _require(actual==baseline, 'single reroute must use nearest current offer')
            continue
        _require(decision['rule']=='G3', 'wrong batch label')
        record = records[decision['protected_record_index']]
        _require(record['decision_index']==index, 'decision record index differs')
        _require(record['actual_plan']==actual, 'logged assignments differ')
        _require(record['baseline_ready_order_r1_plan']==baseline, 'current baseline differs')
        commits={pid:None for pid in actual}
        for event in branch['events']:
            if event['t']==decision['t'] and event['type']=='commit' and event.get('patient') in commits:
                _require(commits[event['patient']] is None,'duplicate same-minute batch commitment')
                commits[event['patient']]=event['hospital']
        _require(commits==actual,'decision plan and commitment events differ')
        protected = {pid for pid,hid in baseline.items() if hid is not None}
        _require(set(record['protected_patient_ids'])==protected, 'protected IDs differ')
        _require(all(actual[pid] is not None for pid in protected), 'protected opportunity removed')
        actual_count = sum(hid is not None for hid in actual.values())
        _require(actual_count>=len(protected), 'admission count decreased')
        _require(record['baseline_assigned_count']==len(protected), 'baseline admission count differs')
        _require(record['actual_assigned_count']==actual_count, 'actual admission count differs')
        _require(record['baseline_objective']==_objective(rows,travel,baseline,baseline), 'baseline objective differs')
        _require(record['actual_objective']==_objective(rows,travel,baseline,actual), 'actual objective differs')
        expected_regret={}
        for row in rows:
            pid=row['id'];before=travel[pid,baseline[pid]] if baseline[pid] is not None else None
            after=travel[pid,actual[pid]] if actual[pid] is not None else None
            expected_regret[pid]=dict(protected=pid in protected,baseline_hospital=baseline[pid],
                actual_hospital=actual[pid],baseline_travel_minutes=before,actual_travel_minutes=after,
                positive_travel_regret_minutes=max(0,after-before) if before is not None else 0)
        _require(record['per_patient_regret']==expected_regret, 'patient travel regret differs')
        for prefix,plan in [('baseline',baseline),('actual',actual)]:
            count=sum(row['priority']==4 and plan[row['id']] is not None for row in rows)
            _require(record[prefix+'_priority4_assigned_count']==count,'priority-4 count differs')
        if actual_count==len(protected):
            _require(all(travel[pid,actual[pid]]<=travel[pid,baseline[pid]] for pid in protected),
                     'equal-count current patient travel worsened')
        _require(type(record['work_units']) is int and 0<=record['work_units']<=2048, 'work budget exceeded')
        _require(record['whole_episode_nonregression_guaranteed'] is False, 'record claims global guarantee')
        _require(record['max_work_units']==2048,'unexpected replay work budget')
        expected_reason=('protected_admission_augmentation' if actual_count>len(protected) else
                         'same_count_pareto_improvement' if actual!=baseline else 'baseline_preserved')
        _require(record['reason']==expected_reason,'policy reason differs')
        counters['batches'] += 1
        counters['protected_patients'] += len(protected)
        counters['extra_current_admissions'] += actual_count-len(protected)
        counters['equal_count_pareto_changes'] += actual_count==len(protected) and actual!=baseline
        counters['unchanged_current_baselines'] += actual==baseline
        batch_count += 1
    _require(batch_count==len(records), 'unused or missing protected record')
    # Canonical endpoint/event accounting is independent of the new search.
    metrics = common.branch_metrics(original, branch)
    vectors = common._vectors(original, branch)
    _require(len(vectors)==len(original['patients']), 'endpoint denominator differs')
    return dict(schema_version=1, passed=True, input_sha256=fingerprint,
                branch_metrics={'G3':metrics}, patient_vectors={'G3':vectors},
                counters=dict(counters), whole_episode_optimality_verified=False,
                clinical_effect_verified=False)
