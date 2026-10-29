"""An explicit recovery preference inside an existing normal inquiry review.

The actual contact ledger is never discounted. A qualifying recovery changes
the candidate tier only; the original V4 kernel still decides whether to send.
"""
from copy import deepcopy
import math

from server import inquiry_v4
from .retry_dispatch_policy import (condition_fingerprint, _resources,
    validate_context, decide as frontier_decide)

VARIANTS = ('normal', 'latched')
RECOVERY_RANK_SPEC = dict(schema_version=1, variants=list(VARIANTS), base_dispatch='frontier',
    normal_foundation='frontier_without_reopening_or_added_after_tick_calls',
    latched_foundation='unchanged_recovery_dispatch_latched',
    initial_unaccepted_search_only=True, no_current_valid_offer=True, no_own_pending=True,
    original_offered_candidates_only=True, latest_actual_contact_must_be_latest_clinical_decline=True,
    unchanged_patient_condition_required=True, current_fresh_sufficient_required=True,
    actual_fresh_shortage_to_fresh_sufficient_quantity_recovery=True,
    required_resources_in_same_recovery_packet=True, recovery_source_after_actual_decline=True,
    later_semantic_loss_invalidates_recovery=True, other_unvisited_eligible_candidate_blocks=True,
    already_in_base_frontier_is_not_an_exception=True, actual_contact_counts_unchanged=True,
    maximum_actual_rank_asks_per_patient_condition=1,
    rank_consumption_separate_from_initial_reopen_consumption=True,
    original_v4_kernel_on_one_recovery_tier=True, original_seven_inputs_retained=True,
    rank_exception_adds_query_calls=False, rank_exception_changes_review_clock=False,
    rank_exception_changes_original_tried_or_episode=False,
    maximum_open_inquiries=3, maximum_episode_contacts=10,
    original_timeout_offer_ttl_fifo_readiness_resources_and_workers=True,
    extra_resource_change_publications=False, future_work_or_response_information=False)
RANK_CONTEXT_KEYS = {'schema_version', 'evaluated_t', 'variant', 'valid_offers',
    'declines', 'transitions', 'consumptions'}
DECLINE_KEYS = {'patient','hospital','t','decline_event_sequence','ask_event_sequence',
    'attempt','condition_fingerprint','resources'}
TRANSITION_KEYS = {'hospital','t','report_event_sequence','before_report_event_sequences',
    'before_resources','after_resources'}
CONSUMPTION_KEYS = {'patient','hospital','token_id','condition_fingerprint',
    'ask_event_sequence','inquiry_event_sequence','decline_event_sequence','report_event_sequence'}


def _quarter(value):
    return type(value) in (int,float) and math.isfinite(value) and value >= 0 and value*4 == int(value*4)


def _seq(value):
    return type(value) is int and value >= 0


def _id(value):
    return isinstance(value,str) and bool(value)


def _validate(inputs, context, rank_context):
    validate_context(inputs, context)
    if not isinstance(rank_context,dict) or set(rank_context) != RANK_CONTEXT_KEYS \
            or rank_context['schema_version'] != 1 or type(rank_context['schema_version']) is not int \
            or rank_context['variant'] not in VARIANTS or rank_context['evaluated_t'] != context['evaluated_t']:
        raise ValueError('exact current rank evidence context required')
    now, pid = context['evaluated_t'], context['patient']['id']
    for offer in rank_context['valid_offers']:
        if set(offer) != {'hospital','expires_at'} or not _id(offer['hospital']) or not _quarter(offer['expires_at']):
            raise ValueError('original current offer ledger required')
    for decline in rank_context['declines']:
        if set(decline) != DECLINE_KEYS or decline['patient'] != pid or not _id(decline['hospital']) \
                or not _quarter(decline['t']) or decline['t'] > now \
                or not _seq(decline['decline_event_sequence']) or not _seq(decline['ask_event_sequence']) \
                or decline['decline_event_sequence'] <= decline['ask_event_sequence'] \
                or type(decline['attempt']) is not int or decline['attempt'] < 1 \
                or not _id(decline['condition_fingerprint']):
            raise ValueError('actual past clinical decline and original ask proof required')
        _resources(decline['resources'], decline['t'])
    for change in rank_context['transitions']:
        if set(change) != TRANSITION_KEYS or not _id(change['hospital']) \
                or not _quarter(change['t']) or change['t'] > now or not _seq(change['report_event_sequence']):
            raise ValueError('actual past applied public resource transition required')
        before, after = change['before_resources'], change['after_resources']
        _resources(before,change['t']);_resources(after,change['t'])
        if set(before) != set(after) or set(change['before_report_event_sequences']) != set(before) \
                or any(ref is not None and not _seq(ref) for ref in change['before_report_event_sequences'].values()):
            raise ValueError('full before/after resource report provenance required')
    for used in rank_context['consumptions']:
        if set(used) != CONSUMPTION_KEYS or used['patient'] != pid or not _id(used['hospital']) \
                or not _id(used['token_id']) or not _id(used['condition_fingerprint']) \
                or any(not _seq(used[key]) for key in ('ask_event_sequence','inquiry_event_sequence',
                    'decline_event_sequence','report_event_sequence')):
            raise ValueError('actual rank inquiry consumption evidence required')


def decide_recovery_rank(*, inputs, context, rank_context):
    _validate(inputs,context,rank_context)
    base = frontier_decide('frontier',inputs=deepcopy(inputs),context=deepcopy(context))
    now, patient = context['evaluated_t'], context['patient']
    fp, pid, demand = patient['condition_fingerprint'], patient['id'], patient['condition']['demand']
    base_detail = base['evidence']['retry_dispatch']
    detail = dict(schema_version=1,variant=rank_context['variant'],scope_active=False,
        reason='NO_QUALIFYING_RECOVERY',base_frontier_decision=deepcopy(base),candidate_checks=[],
        recovery_tier=[],kernel_decision=None,selected_recovery_proofs={},rank_send=[])
    def finish(output, reason=None):
        result = deepcopy(output)
        if reason is not None:detail['reason'] = reason
        result['evidence'].update(policy='recovery_rank_'+rank_context['variant'],
            base_policy='retry_dispatch_frontier',recovery_rank=deepcopy(detail))
        return result
    if patient['state'] != 'SEARCH' or patient['first_commit_t'] is not None \
            or patient['first_accept_event_sequence'] is not None:
        return finish(base,'NOT_INITIAL_UNACCEPTED_SEARCH')
    if inputs['patient_pending']:
        return finish(base,'OWN_PENDING')
    if any(offer['expires_at'] > now for offer in rank_context['valid_offers']):
        return finish(base,'VALID_OFFER')
    if inputs['max_new'] <= 0 or context['episode']['contacts'] >= 10:
        return finish(base,'CONTACT_OR_OPEN_LIMIT')
    if any(used['condition_fingerprint'] == fp for used in rank_context['consumptions']):
        return finish(base,'RANK_ALREADY_CONSUMED')
    detail['scope_active'] = True
    counts = {row['hospital']:row['contacts'] for row in base_detail['contact_counts']}
    offered = {row['hospital_id'] for row in inputs['candidates']}
    universe = {row['hospital']:row for row in context['universe']}
    contacts = {}
    for contact in sorted(context['contacts'],key=lambda row:row['ask_event_sequence']):
        contacts[contact['hospital']] = contact
    declines = {}
    for decline in sorted(rank_context['declines'],key=lambda row:row['decline_event_sequence']):
        declines[decline['hospital']] = decline
    proofs = {}
    for hid, decline in sorted(declines.items()):
        reason, proof = 'NO_POST_DECLINE_RECOVERY', None
        contact = contacts.get(hid)
        if decline['condition_fingerprint'] != fp:
            reason = 'CONDITION_CHANGED'
        elif contact is None or contact['ask_event_sequence'] != decline['ask_event_sequence'] \
                or contact['attempt'] != decline['attempt'] or contact['condition_fingerprint'] != fp:
            reason = 'LATEST_CONTACT_IS_NOT_THIS_CLINICAL_DECLINE'
        elif hid not in offered:
            reason = 'NOT_ORIGINAL_OFFERED_CANDIDATE'
        elif hid in base_detail['frontier_hospitals']:
            reason = 'ALREADY_IN_BASE_FRONTIER'
        elif any(other != hid and counts.get(other,0) == 0 for other in universe):
            reason = 'OTHER_UNVISITED_ELIGIBLE_CANDIDATE'
        elif hid not in universe or set(universe[hid]['resources']) != set(demand) \
                or any(universe[hid]['resources'][r]['status'] != 'fresh'
                    or universe[hid]['resources'][r]['free'] is None
                    or universe[hid]['resources'][r]['free'] < quantity for r,quantity in demand.items()):
            reason = 'CURRENT_RESOURCES_NOT_FRESH_SUFFICIENT'
        else:
            for change in rank_context['transitions']:
                if change['hospital'] != hid or change['t'] <= decline['t'] \
                        or change['report_event_sequence'] <= decline['decline_event_sequence']:
                    continue
                before,after = change['before_resources'],change['after_resources']
                if not set(demand) <= set(before) or any(before[r]['status'] != 'fresh' for r in demand) \
                        or not any(before[r]['free'] < quantity for r,quantity in demand.items()) \
                        or any(after[r]['status'] != 'fresh' or after[r]['free'] is None
                            or after[r]['free'] < quantity or after[r]['observed_at'] <= decline['t']
                            or after[r]['received_at'] != change['t'] for r,quantity in demand.items()) \
                        or len({after[r]['sequence'] for r in demand}) != 1 \
                        or not any(after[r]['capacity'] > before[r]['capacity'] or after[r]['used'] < before[r]['used']
                            for r in demand):
                    continue
                later = [row for row in rank_context['transitions'] if row['hospital'] == hid
                    and row['report_event_sequence'] > change['report_event_sequence']]
                if any(not set(demand) <= set(row['after_resources']) or any(
                        row['after_resources'][r]['status'] != 'fresh' or row['after_resources'][r]['free'] is None
                        or row['after_resources'][r]['free'] < quantity for r,quantity in demand.items()) for row in later):
                    continue
                proof = dict(hospital=hid,decline_event_sequence=decline['decline_event_sequence'],
                    report_event_sequence=change['report_event_sequence'],
                    token_id=f'rank:{pid}:{hid}:{decline["decline_event_sequence"]}:{change["report_event_sequence"]}:{fp}')
                reason = 'ACTUAL_POST_DECLINE_RECOVERY_OUTSIDE_FRONTIER'
                break
        detail['candidate_checks'].append(dict(hospital=hid,eligible=proof is not None,reason=reason,proof=proof))
        if proof is not None:proofs[hid] = proof
    if not proofs:
        return finish(base)
    tier = [row['hospital_id'] for row in inputs['candidates'] if row['hospital_id'] in proofs]
    kernel_inputs = deepcopy(inputs)
    kernel_inputs['candidates'] = [row for row in kernel_inputs['candidates'] if row['hospital_id'] in proofs]
    kernel = inquiry_v4.decide(**kernel_inputs)
    detail.update(reason='RECOVERY_TIER_AT_ORIGINAL_REVIEW',recovery_tier=tier,
        kernel_decision=deepcopy(kernel),selected_recovery_proofs=deepcopy(proofs),rank_send=list(kernel['send']))
    return finish(kernel)
