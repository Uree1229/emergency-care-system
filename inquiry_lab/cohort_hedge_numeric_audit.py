"""Second disclosed, stored-only audit supplement for an empty-product type.

No producer or policy is imported. The original independent arithmetic and
comparison tolerances stay intact. Only its empty-item P=0/F=1 scalars are
represented as floats, matching the recorded metric schema. Import has no hook.
"""
import hashlib
from pathlib import Path

from . import cohort_hedge_history_audit as history

frozen=history.frozen
FROZEN_AUDITOR_SHA256='43f4a13bb737311e7e5468595698955d77612d13856cdd213520da0efb74516f'
HISTORY_ADDENDUM_SHA256='064d6069488887e5366c7b175570550c40b9c5d3072d8c8a50d84b5a61e83cc3'
for module,expected in ((frozen,FROZEN_AUDITOR_SHA256),(history,HISTORY_ADDENDUM_SHA256)):
    if hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()!=expected:
        raise ValueError('frozen numeric-audit dependency differs: '+module.__name__)

_ORIGINAL_OWNER_METRICS=frozen.owner_metrics
_ORIGINAL_HISTORY_REPLAY=history.audit_replay
RULE='empty_independent_product_P0_F1_float_representation_only'


def corrected_owner_metrics(items,bins,end,contact_cost):
    """Preserve the captured independent result, casting only its empty case."""
    result=_ORIGINAL_OWNER_METRICS(items,bins,end,contact_cost)
    if not items:
        frozen.require(type(result['selection_probability']) is int and result['selection_probability']==0
            and type(result['failure_probability']) is int and result['failure_probability']==1,
            'empty independent owner probabilities were not exact integer P0/F1')
        result=dict(result,selection_probability=float(result['selection_probability']),
            failure_probability=float(result['failure_probability']))
    return result


def audit_replay(replay):
    """Run the captured history supplement with one restored scalar-only hook."""
    frozen.require(frozen.owner_metrics is _ORIGINAL_OWNER_METRICS,'owner metric auditor was already replaced')
    count=0
    def typed(items,bins,end,contact_cost):
        nonlocal count
        result=corrected_owner_metrics(items,bins,end,contact_cost)
        if not items:count+=1
        return result
    frozen.owner_metrics=typed
    try:
        result=_ORIGINAL_HISTORY_REPLAY(replay)
    finally:
        frozen.owner_metrics=_ORIGINAL_OWNER_METRICS
    if not any(branch.get('policy')==frozen.NAME for branch in replay['branches']):
        return result
    return dict(result,numeric_audit_addendum=dict(rule=RULE,empty_products_typed=count,
        frozen_owner_auditor_sha256=FROZEN_AUDITOR_SHA256,history_addendum_sha256=HISTORY_ADDENDUM_SHA256,
        values_unchanged=True,comparison_tolerances_unchanged=True,new_performance_runs=0))
