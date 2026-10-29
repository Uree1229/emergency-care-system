"""One explicit inquiry eligibility reopening from received recovery evidence.

This is experimental public-information scheduling. A recovery token authorizes
a real new inquiry, never an acceptance, reservation, refreshed old deadline or
prediction of hospital response time.
"""
from copy import deepcopy
import math

from .retry_dispatch_policy import condition_fingerprint, _resources

VARIANTS=('eager','latched')
RECOVERY_DISPATCH_SPEC=dict(schema_version=1,variants=list(VARIANTS),base_dispatch='frontier',
    initial_unaccepted_search_only=True,actual_clinical_decline_required=True,
    fresh_shortage_to_fresh_sufficient_content_recovery_required=True,
    all_required_resource_source_times_after_decline=True,
    required_resources_in_same_recovery_report=True,
    recovery_history='actual_applied_semantic_public_report_transitions',
    maximum_actual_reopens_per_patient_condition=1,minimum_original_tried_hospitals=2,
    eager_requires_other_pending=True,latched_requires_no_pending=True,
    latched_requires_only_target_normal_candidate=True,
    latched_may_interrupt_original_retry_once=True,any_valid_offer_blocks=True,
    actual_original_selector_and_review_clock=True,temporary_target_eligibility_only=True,
    restore_target_if_not_actually_sent=True,consume_only_actual_target_inquiry=True,
    same_content_refresh_renews_token=False,old_inquiry_deadline_or_offer_ttl_renewed=False,
    episode_transition_and_contact_budget_unchanged=True,maximum_open_inquiries=3,
    maximum_episode_contacts=10,extra_resources_workers_or_reports=False,
    fifo_work_acceptance_readiness_and_allocation_unchanged=True,
    future_response_or_resource_schedule_visible=False)
SNAPSHOT_KEYS={'t','variant','patient','scheduling','tried_hospitals','pending_inquiries',
    'valid_offers','candidates','contacts','declines','transitions','consumptions'}
PATIENT_KEYS={'id','state','first_commit_t','first_accept_event_sequence','condition',
    'condition_fingerprint','request_event_sequence'}
SCHEDULING_KEYS={'episode_id','episode_contacts','retry_at','next_policy_review_at','last_contact_at'}
DECLINE_KEYS={'patient','hospital','t','decline_event_sequence','ask_event_sequence','attempt',
    'condition_fingerprint','resources'}
TRANSITION_KEYS={'hospital','t','report_event_sequence','before_report_event_sequences',
    'before_resources','after_resources'}
CONSUMPTION_KEYS={'patient','hospital','token_id','condition_fingerprint','ask_event_sequence','review_event_sequence','reopen_event_sequence'}


def _time(value):
    return type(value) in (int,float) and math.isfinite(value) and value>=0 and value*4==int(value*4)


def _seq(value):
    return type(value) is int and value>=0


def _id(value):
    return isinstance(value,str) and bool(value)


def _validate(snapshot):
    if not isinstance(snapshot,dict) or set(snapshot)!=SNAPSHOT_KEYS or not _time(snapshot['t']) \
            or snapshot['variant'] not in VARIANTS:
        raise ValueError('exact current recovery snapshot and variant required')
    t,p=snapshot['t'],snapshot['patient']
    if not isinstance(p,dict) or set(p)!=PATIENT_KEYS or not _id(p['id']) or not _id(p['state']) \
            or not _seq(p['request_event_sequence']) or p['condition']['id']!=p['id'] \
            or condition_fingerprint(p['condition'])!=p['condition_fingerprint']:
        raise ValueError('registered current public patient condition required')
    for key in ('first_commit_t',):
        if p[key] is not None and (not _time(p[key]) or p[key]>t):
            raise ValueError('actual previous commitment required')
    if p['first_accept_event_sequence'] is not None and not _seq(p['first_accept_event_sequence']):
        raise ValueError('actual previous acceptance required')
    scheduling=snapshot['scheduling']
    if not isinstance(scheduling,dict) or set(scheduling)!=SCHEDULING_KEYS \
            or not _seq(scheduling['episode_id']) or not _seq(scheduling['episode_contacts']):
        raise ValueError('original inquiry scheduling state required')
    for key in ('retry_at','next_policy_review_at','last_contact_at'):
        if scheduling[key] is not None and not _time(scheduling[key]):
            raise ValueError('original quarter-grid scheduling clock required')
    if scheduling['next_policy_review_at'] is None:
        raise ValueError('actual next policy review required')
    tried=snapshot['tried_hospitals']
    if not isinstance(tried,list) or any(not _id(h) for h in tried) or tried!=sorted(set(tried)):
        raise ValueError('exact original tried set required')
    for row in snapshot['pending_inquiries']:
        if not isinstance(row,dict) or set(row)!={'hospital','attempt','submitted_at','timeout_at','ask_event_sequence'} \
                or not _id(row['hospital']) or type(row['attempt']) is not int or row['attempt']<1 \
                or not _time(row['submitted_at']) or row['submitted_at']>t \
                or not _time(row['timeout_at']) or row['timeout_at']<row['submitted_at'] \
                or not _seq(row['ask_event_sequence']):
            raise ValueError('actual original inquiry handles required')
    for row in snapshot['valid_offers']:
        if set(row)!={'hospital','expires_at'} or not _id(row['hospital']) or not _time(row['expires_at']):
            raise ValueError('original offer expiry required')
    seen=set()
    for row in snapshot['candidates']:
        if set(row)!={'hospital','travel_minutes','resources'} or not _id(row['hospital']) \
                or row['hospital'] in seen or type(row['travel_minutes']) not in (int,float) \
                or not math.isfinite(row['travel_minutes']) or row['travel_minutes']<0:
            raise ValueError('unique current structural candidates required')
        seen.add(row['hospital']);_resources(row['resources'],t)
        if set(row['resources'])!=set(p['condition']['demand']):
            raise ValueError('exact current required-resource candidate views required')
    for row in snapshot['contacts']:
        if set(row)!={'patient','hospital','attempt','submitted_at','ask_event_sequence','condition_fingerprint','resources'} \
                or row['patient']!=p['id'] or not _id(row['hospital']) or not _time(row['submitted_at']) \
                or row['submitted_at']>t or not _seq(row['ask_event_sequence']) \
                or type(row['attempt']) is not int or row['attempt']<1 or not _id(row['condition_fingerprint']):
            raise ValueError('actual received contact ledger required')
        _resources(row['resources'],row['submitted_at'])
    for row in snapshot['declines']:
        if set(row)!=DECLINE_KEYS or row['patient']!=p['id'] or not _id(row['hospital']) \
                or not _time(row['t']) or row['t']>t or not _seq(row['decline_event_sequence']) \
                or not _seq(row['ask_event_sequence']) or type(row['attempt']) is not int or row['attempt']<1 \
                or not _id(row['condition_fingerprint']):
            raise ValueError('actual clinical decline source event required')
        _resources(row['resources'],row['t'])
    for row in snapshot['transitions']:
        if set(row)!=TRANSITION_KEYS or not _id(row['hospital']) or not _time(row['t']) or row['t']>t \
                or not _seq(row['report_event_sequence']):
            raise ValueError('actual past applied report transition required')
        _resources(row['before_resources'],row['t']);_resources(row['after_resources'],row['t'])
        if set(row['before_resources'])!=set(row['after_resources']) \
                or set(row['before_report_event_sequences'])!=set(row['before_resources']) \
                or any(ref is not None and not _seq(ref) for ref in row['before_report_event_sequences'].values()):
            raise ValueError('original report provenance required for every resource')
    for row in snapshot['consumptions']:
        if set(row)!=CONSUMPTION_KEYS or row['patient']!=p['id'] or not _id(row['hospital']) \
                or not _id(row['token_id']) or not _id(row['condition_fingerprint']) or any(not _seq(row[k]) for k in
                    ('ask_event_sequence','review_event_sequence','reopen_event_sequence')):
            raise ValueError('actual token consumption proof required')
    return p['condition_fingerprint']


def decide_recovery_reopen(snapshot):
    fp=_validate(snapshot)
    t,p,s=snapshot['t'],snapshot['patient'],snapshot['scheduling']
    output=dict(hospital=None,token_id=None,decline_event_sequence=None,
        transition_report_event_sequence=None,reason='NO_RECOVERY',candidate_checks=[])
    def stop(reason):return dict(output,reason=reason)
    if p['state']!='SEARCH' or p['first_commit_t'] is not None or p['first_accept_event_sequence'] is not None:
        return stop('NOT_INITIAL_UNACCEPTED_SEARCH')
    if any(o['expires_at']>t for o in snapshot['valid_offers']):return stop('VALID_OFFER')
    if s['episode_contacts']>=10 or len(snapshot['pending_inquiries'])>=3:return stop('CONTACT_OR_OPEN_LIMIT')
    if len(snapshot['tried_hospitals'])<2:return stop('WOULD_RENEW_ORIGINAL_EPISODE')
    if t<s['next_policy_review_at']:return stop('ORIGINAL_REVIEW_NOT_DUE')
    pending=snapshot['pending_inquiries']
    if snapshot['variant']=='eager' and not pending:return stop('NO_OTHER_PENDING')
    if snapshot['variant']=='latched' and pending:return stop('WAIT_FOR_ORIGINAL_PENDING')
    if snapshot['variant']=='eager' and s['retry_at'] is not None and s['retry_at']>t:
        return stop('ORIGINAL_RETRY_NOT_DUE')
    if any(row['condition_fingerprint']==fp for row in snapshot['consumptions']):
        return stop('PATIENT_REOPEN_ALREADY_CONSUMED')
    contacts={}
    for row in snapshot['contacts']:
        contacts[row['hospital']]=row
    declines={}
    for row in snapshot['declines']:
        declines[row['hospital']]=row
    candidates={row['hospital']:row for row in snapshot['candidates']}
    eligible=[]
    demand=p['condition']['demand']
    for hid,decline in sorted(declines.items()):
        reason='NO_POST_DECLINE_RECOVERY';token=None;transition=None
        contact=contacts.get(hid)
        if decline['condition_fingerprint']!=fp:reason='CONDITION_CHANGED'
        elif contact is None or contact['ask_event_sequence']!=decline['ask_event_sequence'] \
                or contact['condition_fingerprint']!=fp:reason='NEWER_OR_DIFFERENT_CONTACT'
        elif hid not in snapshot['tried_hospitals']:reason='NOT_ORIGINALLY_TRIED'
        elif any(a['hospital']==hid for a in pending):reason='TARGET_PENDING'
        elif hid not in candidates:reason='NOT_CURRENT_STRUCTURAL_CANDIDATE'
        elif snapshot['variant']=='latched' and any(
                other!=hid and other not in snapshot['tried_hospitals']
                and not any(c['resources'][r]['status']=='fresh' and c['resources'][r]['free']<q
                            for r,q in demand.items())
                for other,c in candidates.items()):reason='OTHER_NORMAL_CANDIDATE'
        elif any(v['status']!='fresh' or v['free'] is None or v['free']<demand[r]
                 for r,v in candidates[hid]['resources'].items()):reason='CURRENT_RESOURCES_NOT_FRESH_SUFFICIENT'
        else:
            for change in snapshot['transitions']:
                if change['hospital']!=hid or change['t']<=decline['t'] \
                        or change['report_event_sequence']<=decline['decline_event_sequence']:
                    continue
                before,after=change['before_resources'],change['after_resources']
                if not set(demand)<=set(before) or any(before[r]['status']!='fresh' for r in demand) \
                        or not any(before[r]['free']<q for r,q in demand.items()) \
                        or any(after[r]['status']!='fresh' or after[r]['free'] is None or after[r]['free']<q
                               or after[r]['observed_at']<=decline['t'] or after[r]['received_at']!=change['t']
                               for r,q in demand.items()) \
                        or len({after[r]['sequence'] for r in demand})!=1 \
                        or not any(after[r]['capacity']>before[r]['capacity']
                                   or after[r]['used']<before[r]['used'] for r in demand):
                    continue
                # A later semantic loss invalidates the old token even when a
                # subsequent timestamp-only refresh still looks optimistic.
                later=[row for row in snapshot['transitions'] if row['hospital']==hid
                       and row['report_event_sequence']>change['report_event_sequence']]
                if any(any(row['after_resources'][r]['status']!='fresh'
                           or row['after_resources'][r]['free'] is None
                           or row['after_resources'][r]['free']<q for r,q in demand.items()) for row in later):
                    continue
                transition=change
                token=f"{p['id']}:{hid}:{decline['decline_event_sequence']}:{change['report_event_sequence']}:{fp}"
                reason='ACTUAL_DECLINE_AND_RECEIVED_RECOVERY'
                break
        check=dict(hospital=hid,eligible=token is not None,reason=reason,
            decline_event_sequence=decline['decline_event_sequence'],
            transition_report_event_sequence=None if transition is None else transition['report_event_sequence'],
            token_id=token)
        output['candidate_checks'].append(check)
        if token is not None:eligible.append(check)
    if eligible:
        choice=min(eligible,key=lambda row:(candidates[row['hospital']]['travel_minutes'],row['hospital']))
        output.update({key:choice[key] for key in ('hospital','token_id','decline_event_sequence','transition_report_event_sequence')})
        output['reason']='REOPEN_ONE_TRIED_HOSPITAL'
    return output
