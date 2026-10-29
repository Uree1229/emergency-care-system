"""Six native-budget environments; the preceding 149 inputs remain exact.

The 1ms setting is a declared common C1 model/search budget, not fault injection.
Future patients and interruptions are shared environment events, not inputs to
the initializer. A declared target is not a claim of realized activation.
"""
from copy import deepcopy

from .offer_reserve_scenarios import FAMILIES as ORIGINAL_FAMILIES, make_scenario as make_previous
from .joint_policy_scenarios import _validate_extra_inputs
from .map_replay import validate_scenario

DEVELOPMENT_SEEDS=(44101,44102,44103)
VALIDATION_SEEDS=(54101,54102,54103,54104,54105,54106)
ADDITIONAL_FAMILIES=(
    'c1_incumbent_native_budget',
    'c1_incumbent_late_scarce',
    'c1_incumbent_multiple_resources',
    'c1_incumbent_no_repair',
    'c1_incumbent_chain_limit',
    'c1_incumbent_adverse_resource',
)
FAMILIES=ORIGINAL_FAMILIES+ADDITIONAL_FAMILIES
BASE_FAMILIES=dict.fromkeys(ADDITIONAL_FAMILIES,'out_of_order')


def _start(patient,t):
    for key in ('occur_t','dispatch_t','ambulance_arrive_t','search_start_t'):
        patient[key]=t


def _new_case(seed,family):
    case=make_previous(seed,'out_of_order')
    case.update(id=f'c1-incumbent-{family}-{seed}',family=family,seed=seed,label=family,
        c1_incumbent_family=family)
    # The inherited independent environment has no explicit queue extras.
    # Empty declarations let the unchanged extra validator check new inputs.
    case.setdefault('response_matrix',{})
    case.setdefault('initial_inquiry_history',[])
    hospitals={h['id']:h for h in case['hospitals']}
    patients={p['id']:p for p in case['patients']}
    detail=dict(base_environment_family='out_of_order',native_budget_stress=True,
        no_fault_injection=True,no_clock_mocking=True,
        target='current accepted-offer single blocker opportunity; activation must be observed',
        future_events_not_initializer_inputs=True)
    if family=='c1_incumbent_late_scarce':
        _start(patients['P03'],2)
        detail['target']='later registration; immutable commitments are not movable blockers'
    elif family=='c1_incumbent_multiple_resources':
        hospitals['NEAR']['capacity']['ventilator']=1
        hospitals['FAR']['capacity']['ventilator']=0
        patients['P02']['resource_demands']['special_team']=1
        patients['P03']['resource_demands']['ventilator']=1
        detail['target']='bed, team and ventilator feasibility in the same candidate'
    elif family=='c1_incumbent_no_repair':
        hospitals['FAR']['initial_occupancy']=[dict(until=60,demand=dict(ed_bed=2))]
        detail['target']='no actual feasible alternate; no invented edge or free capacity'
    elif family=='c1_incumbent_chain_limit':
        near=hospitals['NEAR'];far=hospitals['FAR']
        middle=deepcopy(far);middle.update(id='MIDDLE',name='Synthetic chain middle',lat=35.19)
        case['hospitals'].append(middle)
        a,b,c='Synthetic chain A','Synthetic chain B','Synthetic chain C'
        near['capabilities']=[a,b,c];middle['capabilities']=[a,b];far['capabilities']=[a]
        # Remove A from NEAR: P01=MIDDLE/FAR, P02=NEAR/MIDDLE, P03=NEAR.
        near['capabilities']=[b,c]
        for h in case['hospitals']:
            h['capacity']=dict(ed_bed=1,special_team=1)
            h.pop('initial_occupancy',None)
        patients['P01']['required_capabilities']=[a]
        patients['P02']['required_capabilities']=[b]
        patients['P03']['required_capabilities']=[c]
        patients['P03']['resource_demands']=dict(ed_bed=1)
        case['response_environment']={'*':{h['id']:dict(delay=4,silent=False) for h in case['hospitals']}}
        detail['target']='a two-blocker chain is outside the single-augment rule'
    elif family=='c1_incumbent_adverse_resource':
        case['exogenous_events']=[dict(type='resource_unavailable',t=9,until=25,
            hospital='FAR',resource='ed_bed')]
        detail.update(target='feasible current alternate has a private later interruption',
            observed_prefix_twin='c1_incumbent_native_budget',private_outage_t=9)
    elif family!='c1_incumbent_native_budget':
        raise AssertionError('unhandled fixed incumbent family')
    case['assumptions'] += [
        'C1 reference and candidate share the declared native 1ms budget; H1 retains its original 50ms setting.',
        'No formal clock mocking, artificial solver delay, retry or failed-run exclusion is used.',
        'Current accepted offers and currently free resources alone enter the initializer; future event fields are environment metadata.',
        'One validated augment is not an optimum or a guarantee of better whole-patient outcomes.',
    ]
    case['experiment']=dict(seed=seed,patient_count=len(case['patients']),c1_incumbent_family=family,
        c1_budget_seconds=.001,clinical_values='synthetic',identity_source='synthetic',
        routing='synthetic_fixed_coordinates',timing_unit='real_minutes',
        additional_resource_change_channel_enabled=False,
        comparison_scope='same normal rank with one C1 initializer only',**detail)
    validate_scenario(case);_validate_extra_inputs(case)
    return case


def make_scenario(seed,family):
    if type(seed) is not int or seed<0:raise ValueError('nonnegative integer seed required')
    if type(family) is not str or family not in FAMILIES:raise ValueError('known incumbent family required')
    return deepcopy(make_previous(seed,family) if family in ORIGINAL_FAMILIES else _new_case(seed,family))


def build_c1_incumbent_scenarios(seed,families=None):
    if type(seed) is not int or seed<0:raise ValueError('nonnegative integer seed required')
    if isinstance(families,(str,bytes)):raise ValueError('distinct iterable of known families required')
    try:chosen=list(FAMILIES if families is None else families)
    except TypeError as exc:raise ValueError('iterable of known families required') from exc
    if any(type(f) is not str or f not in FAMILIES for f in chosen) or len(chosen)!=len(set(chosen)):
        raise ValueError('unknown or duplicate incumbent family')
    return [make_scenario(seed,f) for f in chosen]
