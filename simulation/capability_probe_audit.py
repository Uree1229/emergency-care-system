"""Audit capability selection before auditing the unchanged, complete raw branch.

The environment's declared queue support selects an existing protocol. No raw
field is stripped or rewritten: the same branch object reaches the frozen parent
auditor, which independently checks clinical observations and physical work.
"""
from __future__ import annotations
from collections import Counter

from . import cooperative_probe_audit as parent

CapabilityProbeAuditError = parent.CooperativeProbeAuditError
_require = parent._require
EXPERIMENT_ID = 'capability_probe_20261017'
CAPABILITY_PROBE_SPEC = dict(
    mode_source='scenario.inquiry_queue.enabled',
    queue_enabled_delegate='simulation.cooperative_probe_engine.run_cooperative_probe_branch',
    queue_disabled_delegate='simulation.shortage_probe_engine.run_shortage_probe_branch',
    underlying_raw_branch_retained=True,
    underlying_branch_id_assigned_before_execution=True,
    wrapper_policy_or_queue_change=False,
    resource_update_channel_added=False,
    ack_or_worker_added_when_queue_disabled=False,
)
NAMESPACE_FIELDS = {'capability_probe_enabled', 'capability_probe_spec', 'capability_mode'}
DEFAULT_CANDIDATE_IDS = {'v4_H1_capability', 'v4_C1_capability'}


def _same_literal(actual, expected):
    """Metadata booleans cannot be replaced by equal-valued integers."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(actual) == set(expected) and all(
            _same_literal(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _same_literal(left, right) for left, right in zip(actual, expected))
    return actual == expected


def audit_branch(scenario, branch, *, exact_limit=20000):
    namespace = {key for key in branch if key.startswith('capability_')}
    if not namespace:
        _require(branch.get('id') not in DEFAULT_CANDIDATE_IDS,
                 'capability candidate omitted its declared selection protocol')
        return parent.audit_branch(scenario, branch, exact_limit=exact_limit)

    _require(namespace == NAMESPACE_FIELDS,
             'capability branch has missing or unknown namespace fields')
    _require(branch['capability_probe_enabled'] is True,
             'capability namespace requires an explicit enabled boolean')
    _require(_same_literal(branch['capability_probe_spec'], CAPABILITY_PROBE_SPEC),
             'capability delegation differs from the unchanged declared-support contract')
    queue = scenario.get('inquiry_queue', {})
    _require(type(queue) is dict, 'declared inquiry queue capability is not a mapping')
    enabled = queue.get('enabled', False)
    _require(type(enabled) is bool, 'declared inquiry queue capability is not boolean')
    mode = 'cooperative_queue' if enabled else 'independent_replies'
    _require(branch['capability_mode'] == mode,
             'capability mode differs from the environment declared queue support')
    _require(all(branch.get(flag) is True for flag in (
        'shortage_probe_enabled', 'late_pending_guard_enabled',
        'late_reply_recovery_enabled', 'cohort_registration_enabled',
        'scarce_routing_enabled')),
        'capability branch changed its complete safe-probe foundation')
    _require(branch.get('inquiry_selector') in ('v3', 'v4')
             and branch.get('policy', {}).get('rule') in ('H1', 'C1'),
             'capability branch changed the supported selector or allocation rule')
    _require(branch.get('policy', {}).get('name') == branch.get('id'),
             'capability identity differs from the policy actually executed')
    if enabled:
        _require(branch.get('cooperative_probe_enabled') is True
                 and branch.get('cooperative_queue_enabled') is True
                 and branch.get('comparison_id') == 'cooperative_probe_20261017',
                 'queue-supported capability branch did not retain the cooperative raw protocol')
    else:
        _require(not any(key.startswith('cooperative_') for key in branch)
                 and branch.get('comparison_id') == 'shortage_probe_20261017'
                 and branch.get('inquiry_queue_report') is None,
                 'independent capability branch invented cooperative controls or queue workers')
    # Deliberately pass the very same object, including the namespace above.
    # The parent delegates independent replies to its frozen shortage auditor.
    return parent.audit_branch(scenario, branch, exact_limit=exact_limit)


def audit_capability_probe_replay(result, *, exact_limit=20000):
    _require(type(result.get('schema_version')) is int and result['schema_version'] == 1
             and result.get('experiment_id') == EXPERIMENT_ID,
             'unknown capability replay schema or experiment')
    ids = [branch['id'] for branch in result['branches']]
    _require(ids and all(type(code) is str and code for code in ids)
             and len(ids) == len(set(ids))
             and len(result['execution_order']) == len(ids)
             and set(result['execution_order']) == set(ids),
             'missing or duplicated capability replay branch')
    counts, details = Counter(), {}
    for branch in result['branches']:
        checked = audit_branch(result['scenario'], branch, exact_limit=exact_limit)
        details[branch['id']] = checked
        counts.update(checked['counts'])
    return dict(passed=True, counts=dict(counts), branches=len(ids), branch_audits=details,
                assignment_constraint_violations=0, actual_optimality_claimed=False)
