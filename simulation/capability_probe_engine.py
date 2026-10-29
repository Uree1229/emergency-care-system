"""Preserve independent probes where cooperative hospital controls are absent.

The wrapper chooses a frozen implementation from the environment's declared
queue capability. It adds three descriptive namespace fields to that full raw
branch, without changing any selector, clinical response, worker or calendar.
"""
from __future__ import annotations
from copy import deepcopy

from . import cooperative_probe_engine as cooperative
from . import shortage_probe_engine as independent

EXPERIMENT_ID='capability_probe_20261017'
CAPABILITY_PROBE_SPEC=dict(mode_source='scenario.inquiry_queue.enabled',
    queue_enabled_delegate='simulation.cooperative_probe_engine.run_cooperative_probe_branch',
    queue_disabled_delegate='simulation.shortage_probe_engine.run_shortage_probe_branch',
    underlying_raw_branch_retained=True,underlying_branch_id_assigned_before_execution=True,
    wrapper_policy_or_queue_change=False,resource_update_channel_added=False,
    ack_or_worker_added_when_queue_disabled=False)
NAMESPACE_FIELDS={'capability_probe_enabled','capability_probe_spec','capability_mode'}


def run_capability_probe_branch(scenario,*,inquiry='v4',allocation='H1',cohort_registration=True,
        scarce_routing=True,late_reply_recovery=True,late_pending_guard=True,shortage_probe=False,
        cooperative_probe=False,capability_probe=False,routing=None,branch_id=None,label=None):
    cooperative.v1.validate_joint_scenario(scenario)
    if type(capability_probe) is not bool or type(cooperative_probe) is not bool:
        raise ValueError('explicit capability and cooperative flags required')
    if not capability_probe:
        return cooperative.run_cooperative_probe_branch(scenario,inquiry=inquiry,allocation=allocation,
            cohort_registration=cohort_registration,scarce_routing=scarce_routing,
            late_reply_recovery=late_reply_recovery,late_pending_guard=late_pending_guard,
            shortage_probe=shortage_probe,cooperative_probe=cooperative_probe,
            routing=routing,branch_id=branch_id,label=label)
    if inquiry not in ('v3','v4') or allocation not in ('H1','C1') or any(type(flag) is not bool or not flag
            for flag in (cohort_registration,scarce_routing,late_reply_recovery,late_pending_guard,shortage_probe)):
        raise ValueError('capability mode requires the full explicit safe-probe foundation')
    queue_enabled=scenario.get('inquiry_queue',{}).get('enabled',False)
    if type(queue_enabled) is not bool:raise ValueError('declared queue capability must be boolean')
    code=branch_id or f'{inquiry}_{allocation}_capability'
    arguments=dict(inquiry=inquiry,allocation=allocation,cohort_registration=cohort_registration,
        scarce_routing=scarce_routing,late_reply_recovery=late_reply_recovery,
        late_pending_guard=late_pending_guard,shortage_probe=shortage_probe,
        routing=routing,branch_id=code,label=label or code)
    if queue_enabled:
        branch=cooperative.run_cooperative_probe_branch(scenario,cooperative_probe=True,**arguments)
        mode='cooperative_queue'
    else:
        branch=independent.run_shortage_probe_branch(scenario,**arguments)
        mode='independent_replies'
    if NAMESPACE_FIELDS.intersection(branch):raise ValueError('delegated raw branch already contains capability metadata')
    branch.update(capability_probe_enabled=True,capability_probe_spec=deepcopy(CAPABILITY_PROBE_SPEC),capability_mode=mode)
    return branch


DEFAULT_CAPABILITY_PROBE_POLICIES=tuple(dict(deepcopy(p),capability_probe=False)
    for p in cooperative.DEFAULT_COOPERATIVE_PROBE_POLICIES)+tuple(
    dict(deepcopy(p),id=f'v4_{p["allocation"]}_capability',label=f'v4_{p["allocation"]}_capability',capability_probe=True)
    for p in cooperative.DEFAULT_COOPERATIVE_PROBE_POLICIES[-2:])


def run_capability_probe_replay(scenario,policies=None,execution_order=None,routing=None):
    cooperative.v1.validate_joint_scenario(scenario)
    specs=deepcopy(list(DEFAULT_CAPABILITY_PROBE_POLICIES if policies is None else policies));ids=[r['id'] for r in specs]
    if not ids or len(ids)!=len(set(ids)) or any(not independent._id(code) for code in ids):
        raise ValueError('unique policy IDs required')
    order=list(ids if execution_order is None else execution_order)
    if len(order)!=len(ids) or set(order)!=set(ids):raise ValueError('each policy must execute once')
    by_id={r['id']:r for r in specs};output={}
    keys={'id','inquiry','allocation','cohort_registration','scarce_routing','late_reply_recovery',
          'late_pending_guard','shortage_probe','cooperative_probe','capability_probe'}
    for code in order:
        row=by_id[code]
        if not keys<=set(row) or set(row)-(keys|{'label'}):raise ValueError('exact capability-probe policy row required')
        output[code]=run_capability_probe_branch(scenario,**{k:row[k] for k in keys-{'id'}},
            routing=routing,branch_id=code,label=row.get('label',code))
    return dict(schema_version=1,experiment_id=EXPERIMENT_ID,scenario=deepcopy(scenario),
        branches=[output[code] for code in ids],execution_order=order)
