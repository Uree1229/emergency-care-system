"""Unwired, per-call mass-array memoization of frozen Recourse owner scoring.

The copied score_owner search, arithmetic, iteration and returned evidence stay
unchanged. Only repeated masses_at_edges evaluations for the same hospital and
typed offset within one fixed owner/row are reused. Cached arrays are tuples;
every consumer receives a fresh list. Statistics are external and opt-in.
This is a computation prototype, not a patient-wait or policy improvement.
"""
from copy import deepcopy
import hashlib
import math
from pathlib import Path
from . import cohort_recourse_model as original

FOUNDATION_SHA256='e776bd643611a9ce33cbfa3fdbd8f05042fae755c20be5e0a1f4bddfdcebc2de'
if hashlib.sha256(Path(original.__file__).read_bytes()).hexdigest()!=FOUNDATION_SHA256:
    raise ValueError('frozen Recourse source differs')

# Read-only aliases. No frozen module attribute or function globals is patched.
H=original.H
inquiry_policies=original.inquiry_policies
planning_reason=original.planning_reason
plan_occupancy=original.plan_occupancy
_descriptor_cdf=original._descriptor_cdf
_metrics=original._metrics
OWNER_RULE=original.OWNER_RULE
SCOPE=original.SCOPE


def _typed_scalar(value):
    """No coercion, bool/int collision, signed-zero merge or nonfinite key."""
    if type(value) is int:return ('int',value)
    if type(value) is float and math.isfinite(value):return ('float',value.hex())
    return None


def _mass_key(hospital,offset):
    if type(hospital) is not str:return None
    token=_typed_scalar(offset)
    return None if token is None else ('owner_mass_v1',('str',hospital),token)


def _cache_safe_context(new,edges):
    """Unsupported values bypass caching, leaving original errors untouched."""
    if type(edges) is not list or not edges or any(_typed_scalar(x) is None for x in edges):return False
    if type(new) is not dict:return False
    for hospital,row in new.items():
        if type(hospital) is not str or type(row) is not dict:return False
        if any(_typed_scalar(row.get(k)) is None for k in
            ('reply_probability','accept_probability','estimated_wait_seconds','service_mu','service_sigma')):return False
    return True


class _MassMemo:
    """One instance per score_owner call; no global or cross-row lifetime."""
    def __init__(self,enabled,context_safe,stats=None):
        self.enabled=enabled is True and context_safe
        self._values={}
        self.stats=stats
        if stats is not None:
            if type(stats) is not dict:raise TypeError('optional stats must be an external dict')
            stats.update(mechanism='per_call_mass_arrays_only',memoize=self.enabled,
                mass_requests=0,mass_computations=0,mass_cache_hits=0,
                uncacheable_requests=0,mass_cdf_evaluations=0,cache_entries=0)

    def _count(self,key):
        if self.stats is not None:self.stats[key]+=1

    def get(self,hospital,offset,compute):
        self._count('mass_requests')
        key=_mass_key(hospital,offset) if self.enabled else None
        if key is not None and key in self._values:
            self._count('mass_cache_hits')
            return list(self._values[key])
        if key is None:self._count('uncacheable_requests')
        self._count('mass_computations')
        values=compute()  # Same original computation; failed calls are not cached.
        if key is None or type(values) is not list or any(_typed_scalar(v) is None for v in values):return values
        self._values[key]=tuple(values)
        if self.stats is not None:self.stats['cache_entries']=len(self._values)
        return list(self._values[key])


# The complete frozen score_owner body follows, with only the local mass helper
# and make_items mass lookup changed. Tests compare normalized function ASTs.

def score_owner(owner,frozen,row_pending,jobs,*,stats=None,memoize=True):
    """Choose the fixed two-pass H-derived plan and score its original-time CDF."""
    inputs=owner['inputs'];now=inputs['now'];pid=owner['patient']['id'];config=H.spec()['config']
    if frozen['patient_id']!=pid or frozen['now']!=now or frozen['evaluation_end_at']!=owner['evaluation_end_at'] or frozen['original_pending']!=inputs['pending']:raise ValueError('fixed owner origin differs')
    if type(row_pending) is not list or row_pending[:len(inputs['pending'])]!=inputs['pending'] or any(q['submitted_at']!=now for q in row_pending[len(inputs['pending']):]):raise ValueError('row backlog must append current virtual sends only')
    if row_pending[len(inputs['pending']):]!=[j['source']['inquiry'] for j in jobs]:raise ValueError('expected work source differs from row ledger')
    pending=inquiry_policies._pending(row_pending,now);edges=frozen['cell_edges_seconds'];reason=planning_reason(owner)
    current=[(x['travel_seconds'],x['masses']) for x in frozen['own_items']]
    rank=inquiry_policies._candidates(inputs['candidates'])
    if type(inputs['max_new']) is not int or inputs['max_new']<0:raise ValueError('nonnegative owner contact budget required')
    pool=rank[:min(config['max_candidates_evaluated'],inputs['max_new'])]
    if reason=='plan' and not pool:reason='no_candidates'
    job=owner['job'];delta=None;offsets=[];records=[];plan={q['hospital']:None for q in pool}
    passes=0;forced=False
    if reason=='plan':
        # The actual capture kernel ignores next_evaluation_at with no pending.
        barrier=max(now,job['started_at'],job['next_evaluation_at'] if frozen['original_all_own'] else now)
        delta=barrier-now;window=min(job['deadline_at'],owner['evaluation_end_at'])-now
        offsets=sorted({delta+s for s in config['plan_offsets_seconds'] if delta+s<window})
        if not offsets:reason='barrier_outside_new_contact_window'
    history=frozen['normalized_history'];beliefs=H.Beliefs(history,now,config);new={}
    if reason=='plan':
        for q in pool:
            h=q['hospital'];queue=[p for p in pending if p['hospital']==h]
            wait,record=H._queue_wait_estimate(h,now,queue,history,beliefs,config['assumed_workers'])
            discount=sum((j['discount_seconds'] for j in jobs if j['hospital']==h),0.0)
            record=deepcopy(record);record.update(full_wait_before_discount_seconds=wait,virtual_discount_seconds=discount,
                estimated_wait_seconds=max(0.0,wait-discount))
            record.update(travel_seconds=q['travel_seconds'],accept_probability=beliefs.accept(h),
                inquiry_cost_seconds=config['lambda_seconds']*(1+len(queue) if config['congestion_aware'] else 1))
            records.append(record);new[h]=record
    def new_cdf(h,s,x):
        row=new[h]
        return row['reply_probability']*row['accept_probability']*H._lognorm_cdf(x-s-row['estimated_wait_seconds'],row['service_mu'],row['service_sigma'])
    mass_memo=_MassMemo(memoize,_cache_safe_context(new,edges),stats)
    def mass_item(h,s):
        def compute():
            if stats is None:
                return H.masses_at_edges(lambda x:new_cdf(h,s,x),1.0,edges)
            def counted_cdf(x):
                stats['mass_cdf_evaluations']+=1
                return new_cdf(h,s,x)
            return H.masses_at_edges(counted_cdf,1.0,edges)
        return mass_memo.get(h,s,compute)
    def make_items(p):
        return current+[(new[h]['travel_seconds'],mass_item(h,s)) for h,s in p.items() if s is not None]
    def feasible(p):return all(x['total_fresh_count']<=config['max_concurrent'] for x in plan_occupancy(p,frozen['original_all_own'],now,config['observation_timeout_seconds']))
    def contact_cost(p):
        total=0.0
        for hospital,s in p.items():
            if s is None:continue
            survival=1.0
            for item in frozen['own_items']:survival*=max(0.0,1-_descriptor_cdf(item,s))
            for h,t in p.items():
                if t is not None:survival*=max(0.0,1-new_cdf(h,t,s))
            total+=new[hospital]['inquiry_cost_seconds']*survival
        return total
    def cost(p):return H.bounded_expected_cost(make_items(p),edges,config['failure_penalty_seconds'])+contact_cost(p)
    if reason=='plan':
        best=cost(plan)
        for _ in range(config['plan_passes']):
            passes+=1;improved=False
            for q in pool:
                h=q['hospital']
                for offset in [None]+offsets:
                    if offset==plan[h]:continue
                    trial=dict(plan);trial[h]=offset
                    if not feasible(trial):continue
                    value=cost(trial)
                    if value<best-1e-9:plan,best,improved=trial,value,True
            if not improved:break
        fresh=sum(0<=now+delta-q['submitted_at']<=config['observation_timeout_seconds'] for q in frozen['original_all_own'])
        if fresh==0 and not any(s==delta for s in plan.values()) and pool and delta in offsets:
            options=[]
            for q in pool:
                trial=dict(plan);trial[q['hospital']]=delta
                if feasible(trial):options.append((cost(trial),q['travel_seconds'],trial))
            if options:plan=min(options,key=lambda x:(x[0],x[1]))[2];forced=True
        metrics=_metrics(make_items(plan),edges,contact_cost(plan))
    elif reason=='valid_acceptance':
        metrics=dict(selection_probability=1.0,failure_probability=0.0,capped_wait_seconds=0.0,
            arrival_failure_cost_seconds=0.0,planned_contact_cost_seconds=0.0,total_cost_seconds=0.0)
    else:metrics=_metrics(current,edges,0.0)
    return dict(schema_version=1,rule=OWNER_RULE,scope=SCOPE,patient_id=pid,effective_priority=owner['patient']['effective_priority'],
        planning_status=reason,barrier_offset_seconds=delta,allowed_offsets_seconds=offsets,
        queue_records=records,plan=[dict(hospital=q['hospital'],offset_seconds=plan[q['hospital']]) for q in pool],
        occupancy=plan_occupancy(plan,frozen['original_all_own'],now,config['observation_timeout_seconds']),
        passes=passes,forced=forced,metrics=metrics)
