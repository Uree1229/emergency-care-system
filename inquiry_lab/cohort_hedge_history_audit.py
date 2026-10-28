"""Post-result audit-only history provenance supplement.

The frozen owner-hedge policy, data and auditor bytes remain unchanged. This
module imports no producer, runner, model or server. It only corrects the leaf's
profile lookup for genuine patients created by the frozen base-history loader.
No runtime hook is installed on import. The replay wrapper restores its one
leaf hook in finally and leaves the measured scenario/outcome cohort intact.
"""
from copy import deepcopy
import hashlib
from pathlib import Path

from . import cohort_hedge_audit as frozen

ROOT=Path(__file__).resolve().parents[1]
FROZEN_AUDITOR_SHA256='43f4a13bb737311e7e5468595698955d77612d13856cdd213520da0efb74516f'
BASE_DATABASE_SOURCE_SHA256='14c2b7ca9d321b87169c39957b40f7420050bbea97b1d3e1efe95a2fc73e4e6a'
SERVICE_SOURCE_SHA256='d451a0dc2f3a8fd18f2ec5993ce7cf6c237b3bd75e90708cd4d460de3f82fd76'
for path,expected in [(Path(frozen.__file__),FROZEN_AUDITOR_SHA256),
        (ROOT/'server/inquiry_experiment.py',BASE_DATABASE_SOURCE_SHA256),
        (ROOT/'server/service.py',SERVICE_SOURCE_SHA256)]:
    if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
        raise ValueError('frozen history provenance source differs: '+str(path))

_ORIGINAL_LEAF=frozen.audit_owner_provenance
_ORIGINAL_REPLAY=frozen.audit_replay
require=frozen.require
exact_copy=frozen.exact_copy
EPOCH=frozen.EPOCH
NAME=frozen.NAME
HistoryAuditError=frozen.CohortHedgeAuditError
RULE='registered_base_history_patients_use_source_bound_first_origin_profile_for_leaf_audit_only'


def _id(value):return frozen.owner_key(value)


def _history_specs(scenario,events,registry,owners):
    """Only public history and committed journal fields determine extra specs."""
    require(type(scenario) is dict and type(scenario.get('patients')) is list and bool(scenario['patients']),
        'current public patient profiles missing')
    current=scenario['patients'];current_ids={_id(p['id']) for p in current}
    require(len(current_ids)==len(current),'duplicate current measured patient')
    first=current[0];require({'id','lat','lon','travel_seconds'}<=set(first),'first public travel origin missing')
    history=scenario.get('history',[]);require(type(history) is list,'public history must be an array')
    for row in history:
        require(type(row) is dict and {'patient_id','hospital_id','sent_at','reply_at','action'}<=set(row), 'malformed public history row')
        require(_id(row['patient_id']) not in current_ids, 'history owner collides with measured patient')
    now,prefix=owners['now'],owners['event_prefix_max']
    exact_copy(owners['patient_registry'],registry['patients'],'history complete registered patient source')
    visible=[e for e in events if e['id']<=prefix]
    require(all(type(e['id']) is int and e['id']>0 and type(e['at']) is int and e['at']<=now for e in visible)
        and [e['id'] for e in visible]==sorted({e['id'] for e in visible}), 'history prefix contains future/unordered event')
    exact_copy(owners['patient_created_events'],[{k:e[k] for k in ('id','at','type','patient_id')}
        for e in visible if e['type']=='patient_created'],'history complete public creation prefix')
    extra=[o for o in owners['inventory'] if _id(o['patient']['id']) not in current_ids]
    require(len({_id(o['patient']['id']) for o in extra})==len(extra),'duplicate historical owner')
    people={_id(p['id']):p for p in registry['patients']};jobs={_id(j['patient_id']):j for j in registry['jobs']}
    result=[];proof=[]
    for owner in extra:
        pid=owner['patient']['id'];key=_id(pid)
        rows=[r for r in history if type(r) is dict and _id(r['patient_id'])==key]
        require(bool(rows),'unknown owner has no frozen public history profile')
        for row in rows:
            require({'patient_id','hospital_id','sent_at','reply_at','action'}<=set(row)
                and type(row['hospital_id']) is str and row['hospital_id'] in first['travel_seconds']
                and type(row['sent_at']) is int and row['sent_at']<0 and row['action'] in ('decline','checking')
                and (row['reply_at'] is None or type(row['reply_at']) is int and row['sent_at']<=row['reply_at']<0),
                'historical owner profile has future or unsupported public history')
        born=EPOCH+min(r['sent_at'] for r in rows)-1
        births=[e for e in visible if e['type']=='patient_created' and e.get('patient_id') is not None and _id(e['patient_id'])==key]
        require(born>=0 and len(births)==1 and births[0]['at']==born,'historical creation differs from base-loader earliest-send rule')
        exact_copy(owner['patient_created_event'],{k:births[0][k] for k in ('id','at','type','patient_id')},'historical owner birth projection')
        require(key in people and people[key]==dict(id=pid,state='WAITING',version=1)
            and owner['patient']['state']=='WAITING' and type(owner['patient']['version']) is int and owner['patient']['version']==1,
            'historical patient state/version differs from base profile')
        require(owner['job'] is None and key not in jobs and born<=now<born+300 and owner['evaluation_end_at']==born+300,
            'historical owner invented an automatic job or evaluation horizon')
        require(owner['valid_acceptance_ids']==[],'past decline/checking history cannot supply a valid acceptance')
        # Reproduce the base loader's public send/response state machine. An
        # already-open inquiry is reused without another send event; an ended
        # inquiry is reopened with the same database identity.
        scheduled=[]
        for r in rows:
            scheduled.append((r['sent_at'],0,r['hospital_id'],r))
            if r['reply_at'] is not None:scheduled.append((r['reply_at'],1,r['hospital_id'],r))
        states={};expected=[]
        for at,action,hid,row in sorted(scheduled,key=lambda x:(x[0],x[1],x[2])):
            if action==0:
                if states.get(hid) in ('PENDING','CHECKING'):continue
                states[hid]='PENDING';expected.append(('inquiry_sent',EPOCH+at,hid,None))
            else:
                require(states.get(hid) in ('PENDING','CHECKING'),'public history response lacks an open inquiry')
                states[hid]='DECLINED' if row['action']=='decline' else 'CHECKING'
                expected.append(('inquiry_responded',EPOCH+at,hid,row['action']))
        actual=[e for e in visible if e.get('patient_id') is not None and _id(e['patient_id'])==key]
        require(actual[0]['id']==births[0]['id'],'historical creation was not first owner event')
        transitions=actual[1:]
        tuples=[(e['type'],e['at'],e.get('hospital_id'),frozen.parent._data(e).get('action') if e['type']=='inquiry_responded' else None) for e in transitions]
        exact_copy(tuples,expected,'historical public inquiry journal follows base-loader source')
        inquiry_ids={};all_identity={}
        for e in visible:
            if e['type'] not in ('inquiry_sent','inquiry_retried'):continue
            iid=frozen.parent._data(e).get('inquiry_id')
            require(type(iid) is int and iid>0,'public history inquiry id missing')
            identity=(_id(e['patient_id']),e['hospital_id'])
            require(iid not in all_identity or all_identity[iid]==identity,'historical inquiry id changed owner/hospital')
            all_identity[iid]=identity
        for e in transitions:
            hid=e['hospital_id'];iid=frozen.parent._data(e).get('inquiry_id')
            require(type(iid) is int and iid>0 and (hid not in inquiry_ids or inquiry_ids[hid]==iid),
                'historical inquiry attempt changed identity')
            if e['type']=='inquiry_sent':inquiry_ids[hid]=iid
            else:require(hid in inquiry_ids and inquiry_ids[hid]==iid,'historical response precedes public send')
        require(len(set(inquiry_ids.values()))==len(inquiry_ids),'historical hospitals shared inquiry id')
        public_contacts=[q for q in frozen.public_inquiries(events,now,prefix) if _id(q['patient_id'])==key]
        exact_copy(owner['inquiries'],public_contacts,'historical complete public inquiry projection')
        inputs=owner['inputs'];contacted={q['hospital_id'] for q in public_contacts}
        require(type(inputs['max_new']) is int and inputs['max_new']==max(0,10-len(contacted))
            and inputs['last_contact_at']==max((q['last_sent_at'] for q in public_contacts),default=None),
            'historical contact budget or last-contact clock differs from public inquiry prefix')
        # The only inferred profile fields are public origin/travel and birth.
        # No history owner is inserted into the measured scenario itself.
        result.append(dict(id=pid,at=born-EPOCH,lat=first['lat'],lon=first['lon'],travel_seconds=first['travel_seconds']))
        proof.append(dict(patient_id=pid,created_at=born,evaluation_end_at=born+300,
            travel_source_patient_id=first['id'],public_history_rows=len(rows),inquiry_count=len(inquiry_ids),
            inquiry_states=[states[h] for h in sorted(states)],automatic_job_present=False))
    return result,proof


def corrected_audit_owner_provenance(scenario,events,registry,owner_context):
    """Four-argument leaf adapter; current measured profiles are unchanged."""
    specs,_=_history_specs(scenario,events,registry,owner_context)
    # The frozen leaf reads only this profile lookup. The original scenario,
    # its patient list, outcomes, potentials and trace accounting are untouched.
    leaf_view={'patients':list(scenario['patients'])+specs}
    return _ORIGINAL_LEAF(leaf_view,events,registry,owner_context)


def audit_history_provenance(scenario,events,registry,owner_context,owner_source_snapshot):
    """Bind genuine history profiles to the complete current source guard."""
    _,proof=_history_specs(scenario,events,registry,owner_context)
    frozen.audit_owner_source(owner_source_snapshot,owner_context,events)
    first=scenario['patients'][0]
    by_id={_id(r['patient_id']):r for r in owner_source_snapshot['rows']}
    for item in proof:
        row=by_id[_id(item['patient_id'])];patient=row['patient']
        expected=dict(reporter_role='transporter',consent=1,symptoms='가상 실험 환자',
            required_capabilities=['응급의학'],resource_demands={'ed_bed':1},priority=None,priority_source='unknown',
            lat=first['lat'],lon=first['lon'],requested_at=item['created_at'],state='WAITING',version=1,
            danger_signs=[],extra={})
        exact_copy({k:patient[k] for k in expected},expected,'historical full public clinical payload from first origin')
        require(row['job'] is None,'historical full source has an automatic job')
    return dict(historical_owners_verified=len(proof),historical_source_profiles_verified=len(proof),profiles=proof,
        source_rule='frozen _base_database/_payload(first patient), public history and MatrixEstimator',
        policy_runs=0,new_performance_runs=0)


def audit_replay(replay):
    """Stored-only original replay with one temporary source-bound leaf hook."""
    require(frozen.audit_owner_provenance is _ORIGINAL_LEAF,'history leaf was already replaced')
    if not any(branch.get('policy')==NAME for branch in replay['branches']):
        return _ORIGINAL_REPLAY(replay)
    contexts={};groups=0;profiles=0
    for branch in replay['branches']:
        if branch.get('policy')!=NAME:continue
        for group in branch['joint_groups']:
            owner_context=group['owner_context'];key=id(owner_context)
            require(key not in contexts,'same owner context object reused by multiple groups')
            stats=audit_history_provenance(replay['scenario'],branch['db_events'],group['registry_snapshot'],
                owner_context,group['owner_source_snapshot'])
            contexts[key]=(branch,group);groups+=1;profiles+=stats['historical_owners_verified']
    covered=set()
    def leaf(scenario,events,registry,owners):
        key=id(owners);require(key in contexts,'owner context has no corresponding current full source guard')
        branch,group=contexts[key]
        require(scenario is replay['scenario'] and events is branch['db_events'] and registry is group['registry_snapshot'],
            'history leaf was detached from its actual replay group')
        covered.add(key)
        return corrected_audit_owner_provenance(scenario,events,registry,owners)
    frozen.audit_owner_provenance=leaf
    try:
        result=_ORIGINAL_REPLAY(replay)
        require(covered==set(contexts),'source-checked owner group was not visited by the original auditor')
    finally:
        frozen.audit_owner_provenance=_ORIGINAL_LEAF
    return dict(result,history_provenance_addendum=dict(rule=RULE,groups_checked=groups,historical_owner_occurrences_checked=profiles,
        frozen_auditor_sha256=FROZEN_AUDITOR_SHA256,measured_scenario_and_outputs_unchanged=True,
        method='post-result audit-only correction; no producer/performance/gate/namespace change',new_performance_runs=0))
