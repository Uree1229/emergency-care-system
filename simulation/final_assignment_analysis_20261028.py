"""Preregistered arithmetic and scenario-cluster bootstrap for R0/R1/H1.

Standard library only: this module neither runs nor imports a simulator,
allocation algorithm, solver, generator, or project auditor. It analyzes full
stored patient vectors. Main strata are never pooled for efficacy; stress
families remain descriptive. Primary six-comparison multiplicity correction is
fixed before observing any performance result.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import random

POLICIES=('R0','R1','H1')
BASELINES=('R0','R1')
MAIN_STRATA=('routine_single','routine_stream','surge')
PRIMARY_METRIC='scenario_mean_unassigned_priority_fraction'
SECONDARY_METRICS=('scenario_mean_arrival_capped_minutes','scenario_mean_inquiries_per_patient')
METRICS=(PRIMARY_METRIC,)+SECONDARY_METRICS
PRIMARY_FAMILY_SIZE=6
BOOTSTRAP_NAMESPACE='final_assignment_20261028_scenario_cluster_bootstrap_v1'
VECTOR_FIELDS={'id','priority','occur_t','search_start_t','observation_end_t',
    'first_commit_t','final_arrive_t','treatment_start_t','selected','arrived','inquiries',
    'selection_elapsed_minutes','arrival_elapsed_minutes','selection_capped_minutes','arrival_capped_minutes'}


def require(value,message):
    if not value:raise ValueError(message)


def finite(value):
    if type(value) not in (int,float):return False
    try:return math.isfinite(value)
    except OverflowError:return False


def number(value,label,minimum=None):
    require(finite(value) and (minimum is None or value>=minimum),label+': finite number required')
    return value


def integer(value,label,minimum=0):
    require(type(value) is int and value>=minimum and finite(value),label+': integer required')
    return value


def identity(value):
    require(type(value) in (str,int) and (type(value) is int or bool(value)), 'typed patient identifier required')
    return type(value).__name__,str(value)


def mean(values):return math.fsum(values)/len(values) if values else None


def percentile(values,fraction=.95):
    """Nearest-rank descriptive patient percentile; empty denominator is None."""
    number(fraction,'percentile fraction',0);require(fraction<=1,'percentile fraction exceeds1')
    require(type(values) in (list,tuple),'percentile vector required')
    require(all(finite(v) for v in values),'nonfinite percentile observation')
    return sorted(values)[max(0,math.ceil(len(values)*fraction)-1)] if values else None


def linear_quantile(values,fraction):
    """Bootstrap percentile endpoint: linear interpolation at (n-1)*fraction."""
    number(fraction,'quantile fraction',0);require(fraction<=1,'quantile fraction exceeds1')
    require(type(values) in (list,tuple) and bool(values),'nonempty quantile vector required')
    require(all(finite(v) for v in values),'nonfinite quantile observation')
    ordered=sorted(values);at=(len(ordered)-1)*fraction;lo=math.floor(at);hi=math.ceil(at)
    return ordered[lo]+(ordered[hi]-ordered[lo])*(at-lo)


def _hash(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,allow_nan=False,
        sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()


def _vectors(values):
    require(type(values) is list and bool(values),'nonempty patient vectors required')
    seen=set();output=[]
    for row in values:
        require(type(row) is dict and set(row)==VECTOR_FIELDS,'patient vector schema differs')
        key=identity(row['id']);require(key not in seen,'duplicate typed patient identifier');seen.add(key)
        require(type(row['priority']) is int and row['priority'] in (1,2,4),'priority must be experimental weight1/2/4')
        for field in ('occur_t','search_start_t','observation_end_t'):integer(row[field],field)
        require(row['occur_t']<=row['search_start_t']<=row['observation_end_t'],'source patient clocks differ')
        require(type(row['selected']) is bool and type(row['arrived']) is bool,'selected/arrived booleans required')
        integer(row['inquiries'],'inquiries')
        require(row['selected']==(row['first_commit_t'] is not None),'selection indicator differs')
        require(row['arrived']==(row['final_arrive_t'] is not None),'arrival indicator differs')
        require(not row['arrived'] or row['selected'],'arrival without selection')
        end=row['observation_end_t'];occur=row['occur_t'];start=row['search_start_t']
        for field in ('first_commit_t','final_arrive_t','treatment_start_t'):
            if row[field] is not None:integer(row[field],field);require(occur<=row[field]<=end,'endpoint outside observation')
        if row['selected']:require(row['first_commit_t']>=start,'selection before search')
        if row['arrived']:require(row['final_arrive_t']>=row['first_commit_t'],'arrival before selection')
        if row['treatment_start_t'] is not None:
            require(row['arrived'] and row['treatment_start_t']>=row['final_arrive_t'],'treatment before arrival')
        expected=dict(selection_elapsed_minutes=None if not row['selected'] else row['first_commit_t']-start,
            arrival_elapsed_minutes=None if not row['arrived'] else row['final_arrive_t']-occur,
            selection_capped_minutes=(row['first_commit_t'] if row['selected'] else end)-start,
            arrival_capped_minutes=(row['final_arrive_t'] if row['arrived'] else end)-occur)
        for field,value in expected.items():
            if value is None:require(row[field] is None,'unexpected conditional elapsed value')
            else:number(row[field],field,0);require(row[field]==value,'patient elapsed/capped arithmetic differs: '+field)
        output.append(dict(row))
    return sorted(output,key=lambda row:identity(row['id']))


def _case_metrics(rows):
    n=len(rows);weight=sum(row['priority'] for row in rows)
    lost=sum(row['priority'] for row in rows if not row['selected'])
    return dict(patients=n,unassigned=sum(not r['selected'] for r in rows),
        not_arrived=sum(not r['arrived'] for r in rows),
        unassigned_priority_weight=lost,priority_weight_total=weight,
        inquiries=sum(r['inquiries'] for r in rows),
        scenario_mean_unassigned_priority_fraction=lost/weight,
        scenario_mean_arrival_capped_minutes=mean([r['arrival_capped_minutes'] for r in rows]),
        scenario_mean_inquiries_per_patient=sum(r['inquiries'] for r in rows)/n)


def _checked_cases(cases):
    require(type(cases) is list and bool(cases),'nonempty cases required')
    seen_ids=set();seen_keys=set();result=[]
    for case in cases:
        require(type(case) is dict,'case must be a dictionary')
        for field in ('case_id','stratum','raw_file'):
            require(type(case.get(field)) is str and bool(case[field]),'case string field required: '+field)
        require(case.get('track') in ('main','stress'),'unknown evaluation track')
        if case['track']=='main':require(case['stratum'] in MAIN_STRATA,'unregistered main stratum')
        integer(case.get('seed'),'case seed')
        for field in ('scenario_sha256','raw_sha256','compressed_sha256'):
            value=case.get(field)
            require(type(value) is str and len(value)==64 and all(ch in '0123456789abcdef' for ch in value),'SHA256 required: '+field)
        key=case['track'],case['stratum'],case['seed']
        require(case['case_id'] not in seen_ids and key not in seen_keys,'duplicate case identifier or track/stratum/seed')
        seen_ids.add(case['case_id']);seen_keys.add(key)
        audit=case.get('audit')
        require(type(audit) is dict and audit.get('passed') is True,'passed case audit required')
        require(audit.get('scenario_sha256')==case['scenario_sha256'],'audit/input source differs')
        require(type(audit.get('patient_vectors')) is dict and set(audit['patient_vectors'])==set(POLICIES),'complete R0/R1/H1 patient partition required')
        require(type(audit.get('branch_metrics')) is dict and set(audit['branch_metrics'])==set(POLICIES),'complete branch metrics required')
        vectors={code:_vectors(audit['patient_vectors'][code]) for code in POLICIES}
        reference=vectors['R0']
        require(len({row['observation_end_t'] for row in reference})==1,'case observation endpoint must be shared')
        for code,rows in vectors.items():
            require([identity(v['id']) for v in rows]==[identity(v['id']) for v in reference],'different typed patient partitions')
            for left,right in zip(reference,rows):
                for field in ('priority','occur_t','search_start_t','observation_end_t'):
                    require(type(left[field]) is type(right[field]) and left[field]==right[field],'paired source patient differs: '+field)
            actual=_case_metrics(rows);stored=audit['branch_metrics'][code]
            require(type(stored) is dict,'branch metrics dictionary required')
            for field in ('patients','unassigned','not_arrived','unassigned_priority_weight','priority_weight_total','inquiries'):
                integer(stored.get(field),'stored '+field)
                require(stored[field]==actual[field],'stored branch count/vector mismatch: '+field)
            number(stored.get('unassigned_priority_fraction'),'stored failure fraction',0)
            require(stored['unassigned_priority_fraction']==actual[PRIMARY_METRIC],'stored primary metric/vector mismatch')
        if 'patients' in audit:
            integer(audit['patients'],'audited patient count',1);require(audit['patients']==len(reference),'audited patient denominator differs')
        result.append(dict(case_id=case['case_id'],track=case['track'],stratum=case['stratum'],seed=case['seed'],
            scenario_sha256=case['scenario_sha256'],raw_file=case['raw_file'],raw_sha256=case['raw_sha256'],
            compressed_sha256=case['compressed_sha256'],vectors=vectors,
            metrics={code:_case_metrics(rows) for code,rows in vectors.items()}))
    return sorted(result,key=lambda value:(value['track'],value['stratum'],value['seed'],value['case_id']))


def _policy_summary(cases,code):
    vectors=[row for case in cases for row in case['vectors'][code]]
    observed=[r['arrival_elapsed_minutes'] for r in vectors if r['arrived']]
    capped=[r['arrival_capped_minutes'] for r in vectors]
    selection=[r['selection_capped_minutes'] for r in vectors]
    counts=dict(patients=len(vectors),selected=sum(r['selected'] for r in vectors),
        unassigned=sum(not r['selected'] for r in vectors),arrived=len(observed),not_arrived=len(vectors)-len(observed),
        treated=sum(r['treatment_start_t'] is not None for r in vectors),
        untreated=sum(r['treatment_start_t'] is None for r in vectors),
        inquiries=sum(r['inquiries'] for r in vectors),
        priority_weight_total=sum(r['priority'] for r in vectors),
        unassigned_priority_weight=sum(r['priority'] for r in vectors if not r['selected']))
    counts.update(selection_capped_sum_minutes=math.fsum(selection),selection_capped_mean_minutes=mean(selection),
        selection_capped_p95_minutes=percentile(selection),arrival_capped_sum_minutes=math.fsum(capped),
        arrival_capped_mean_minutes=mean(capped),arrival_capped_p95_minutes=percentile(capped),
        conditional_arrival_mean_minutes=mean(observed),conditional_arrival_p95_minutes=percentile(observed),
        conditional_arrival_denominator=len(observed),inquiries_per_patient=counts['inquiries']/len(vectors),
        pooled_patient_unassigned_priority_fraction=counts['unassigned_priority_weight']/counts['priority_weight_total'],
        priority_groups={str(priority):dict(patients=sum(r['priority']==priority for r in vectors),
            unassigned=sum(r['priority']==priority and not r['selected'] for r in vectors),
            not_arrived=sum(r['priority']==priority and not r['arrived'] for r in vectors)) for priority in (4,2,1)})
    counts.update({metric:mean([case['metrics'][code][metric] for case in cases]) for metric in METRICS})
    return counts


def _reference(case,row):
    return dict(case_id=case['case_id'],seed=case['seed'],patient_id=row['id'],
        patient_type=type(row['id']).__name__,priority=row['priority'])


def _delta_summary(values):
    return dict(denominator=len(values),mean_delta_minutes=mean(values),p95_delta_minutes=percentile(values),
        sum_delta_minutes=math.fsum(values),maximum_delay_minutes=max(0,max(values)) if values else None,
        delayed_count=sum(v>0 for v in values),earlier_count=sum(v<0 for v in values),equal_count=sum(v==0 for v in values))


def _descriptive_comparison(cases,baseline,candidate='H1'):
    selected_lost=[];selected_gained=[];arrived_lost=[];arrived_gained=[];common=[];capped=[]
    left_cap=[];right_cap=[];left_selection=[];right_selection=[]
    for case in cases:
        for left,right in zip(case['vectors'][baseline],case['vectors'][candidate]):
            ref=_reference(case,left)
            if left['selected'] and not right['selected']:selected_lost.append(ref)
            if not left['selected'] and right['selected']:selected_gained.append(ref)
            if left['arrived'] and not right['arrived']:arrived_lost.append(ref)
            if not left['arrived'] and right['arrived']:arrived_gained.append(ref)
            if left['arrived'] and right['arrived']:
                common.append(dict(ref,before_arrival_minutes=left['arrival_elapsed_minutes'],
                    after_arrival_minutes=right['arrival_elapsed_minutes'],
                    delta_minutes=right['arrival_elapsed_minutes']-left['arrival_elapsed_minutes']))
            capped.append(dict(ref,before_arrival_capped_minutes=left['arrival_capped_minutes'],
                after_arrival_capped_minutes=right['arrival_capped_minutes'],
                delta_minutes=right['arrival_capped_minutes']-left['arrival_capped_minutes']))
            left_cap.append(left['arrival_capped_minutes']);right_cap.append(right['arrival_capped_minutes'])
            left_selection.append(left['selection_capped_minutes']);right_selection.append(right['selection_capped_minutes'])
    common_values=[r['delta_minutes'] for r in common];cap_values=[r['delta_minutes'] for r in capped]
    result=dict(baseline=baseline,candidate=candidate,patients=len(capped),
        selected_lost_count=len(selected_lost),selected_gained_count=len(selected_gained),
        selected_lost_identifiers=selected_lost,selected_gained_identifiers=selected_gained,
        arrived_lost_count=len(arrived_lost),arrived_gained_count=len(arrived_gained),
        arrived_lost_identifiers=arrived_lost,arrived_gained_identifiers=arrived_gained,
        common_arrival=_delta_summary(common_values),common_arrival_patient_differences=common,
        all_patient_arrival_capped=dict(_delta_summary(cap_values),
            before_mean_minutes=mean(left_cap),after_mean_minutes=mean(right_cap),
            before_p95_minutes=percentile(left_cap),after_p95_minutes=percentile(right_cap),
            p95_difference_minutes=percentile(right_cap)-percentile(left_cap)),
        all_patient_selection_capped=_delta_summary([b-a for a,b in zip(left_selection,right_selection)]),
        all_patient_arrival_capped_differences=capped,
        scenario_differences=[dict(case_id=case['case_id'],seed=case['seed'],**{
            metric:case['metrics'][candidate][metric]-case['metrics'][baseline][metric] for metric in METRICS}) for case in cases])
    return result


def _bootstrap(cases,baseline,samples):
    track,stratum=cases[0]['track'],cases[0]['stratum']
    seed_material=dict(namespace=BOOTSTRAP_NAMESPACE,track=track,stratum=stratum,baseline=baseline)
    seed_digest=_hash(seed_material);rng=random.Random(int(seed_digest,16));n=len(cases)
    differences={metric:[case['metrics']['H1'][metric]-case['metrics'][baseline][metric] for case in cases] for metric in METRICS}
    replicates={metric:[] for metric in METRICS}
    for _ in range(samples):
        indices=[rng.randrange(n) for _ in range(n)]
        for metric,values in differences.items():replicates[metric].append(math.fsum(values[i] for i in indices)/n)
    result={}
    for metric,values in differences.items():
        result[metric]=dict(mean_delta=mean(values),scenario_denominator=n,
            ci95=dict(lower=linear_quantile(replicates[metric],.025),upper=linear_quantile(replicates[metric],.975),confidence_level=.95))
        if metric==PRIMARY_METRIC:
            tail=.05/(2*PRIMARY_FAMILY_SIZE)
            result[metric]['familywise_adjusted_ci']=dict(lower=linear_quantile(replicates[metric],tail),
                upper=linear_quantile(replicates[metric],1-tail),confidence_level=1-.05/PRIMARY_FAMILY_SIZE,
                family_size=PRIMARY_FAMILY_SIZE,lower_quantile=tail,upper_quantile=1-tail)
    return dict(samples=samples,seed_material=seed_material,seed_sha256=seed_digest,
        resampling_unit='whole_scenario_cluster',scenario_weights='equal_within_registered_stratum',
        same_resample_indices_used_for_all_metrics=True,patient_rows_resampled_independently=False,
        quantile_method='linear_interpolation_at_(n_minus_one)_times_probability',metrics=result)


def _classification(bootstrap):
    primary=bootstrap['metrics'][PRIMARY_METRIC]['familywise_adjusted_ci']
    arrival=bootstrap['metrics'][SECONDARY_METRICS[0]]['ci95']
    if primary['upper']<0:
        return ('coverage_gain_capped_arrival_nonworsening' if arrival['upper']<=0 else
            'coverage_gain_with_arrival_tradeoff_or_uncertainty')
    if primary['lower']>0:return 'weighted_coverage_worse'
    return 'inconclusive'


def analyze(cases,bootstrap_samples=5000):
    """Analyze passed audited cases without editing or executing their sources."""
    integer(bootstrap_samples,'bootstrap sample count',1)
    checked=_checked_cases(cases);groups=[]
    keys=sorted({(case['track'],case['stratum']) for case in checked})
    for track,stratum in keys:
        rows=[case for case in checked if (case['track'],case['stratum'])==(track,stratum)]
        comparisons={}
        for baseline in BASELINES:
            value=_descriptive_comparison(rows,baseline)
            value['bootstrap']=_bootstrap(rows,baseline,bootstrap_samples) if track=='main' else None
            value['classification']=_classification(value['bootstrap']) if track=='main' else 'descriptive_only_stress'
            value['formal_primary_efficacy_test']=track=='main'
            comparisons[baseline]=value
        groups.append(dict(track=track,stratum=stratum,cases=len(rows),patients=sum(len(case['vectors']['R0']) for case in rows),
            case_identifiers=[case['case_id'] for case in rows],
            policies={code:_policy_summary(rows,code) for code in POLICIES},comparisons=comparisons,
            r1_vs_r0_descriptive=_descriptive_comparison(rows,'R0','R1')))
    return dict(schema_version=1,analysis='final_assignment_20261028_preregistered_scenario_cluster_v1',
        cases=len(checked),branches=3*len(checked),groups=groups,
        bootstrap_samples=bootstrap_samples,primary_metric=PRIMARY_METRIC,
        primary_family_size=PRIMARY_FAMILY_SIZE,primary_familywise_alpha=.05,
        main_primary_comparisons_present=2*sum(g['track']=='main' for g in groups),
        secondary_metrics=list(SECONDARY_METRICS),secondary_ci95_is_not_familywise_adjusted=True,
        arrival_comparison='all_patient_observation_capped_time_plus_separately_conditioned_shared_arrivals',
        clinical_priority_interpretation=False,experimental_priority_weights=[4,2,1],
        strata_pooled_for_efficacy=False,stress_pooled_into_main=False,stress_has_confidence_intervals=False,
        real_world_prevalence_claimed=False,clinical_effect_claimed=False,whole_online_optimality_claimed=False,
        policy_or_model_executions=0,input_modified=False,
        qualification='Fixed synthetic distribution, separate equal-scenario main strata and descriptive stress families. '
            'Coverage-first classification retains all patients and flags arrival tradeoffs; an inconclusive interval is not proof of no effect.')
