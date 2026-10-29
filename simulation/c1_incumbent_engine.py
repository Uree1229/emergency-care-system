"""Original normal recovery-rank workflow with only the C1 rule substituted."""
from copy import deepcopy
import math
from .recovery_rank_engine import (cooperative, VARIANTS,
    CooperativeNormalRecoveryRankReplay, IndependentNormalRecoveryRankReplay,
    CooperativeLatchedRecoveryRankReplay, IndependentLatchedRecoveryRankReplay)
from .c1_incumbent_assignment import (StableSeededCPSATAssignmentRule, VARIANT,
    MAX_CANDIDATES,INITIALIZER_LIMIT_SECONDS,BUDGET_SCOPE)

EXPERIMENT_ID='c1_incumbent_20261020'
NAMESPACE_FIELDS={'allocation_intervention_id','c1_incumbent'}

def run_c1_incumbent_branch(scenario,*,allocation='C1',seeded=True,budget_seconds=.05,branch_id=None,label=None):
    cooperative.v1.validate_joint_scenario(scenario)
    if type(seeded) is not bool:
        raise ValueError('seeded must be boolean')
    if isinstance(budget_seconds,bool) or not isinstance(budget_seconds,(int,float)) or not math.isfinite(budget_seconds) or budget_seconds < 0:
        raise ValueError('finite nonnegative shared C1 budget required')
    variant='normal'
    if allocation not in ('H1','C1') or variant not in VARIANTS:
        raise ValueError('known fixed recovery rank allocation and variant required')
    enabled=scenario.get('inquiry_queue',{}).get('enabled',False)
    if type(enabled) is not bool:
        raise ValueError('explicit declared queue support required')
    code=branch_id or f'c1_incumbent_{allocation}_{"single_augment" if seeded and allocation == "C1" else "original"}'
    rule=cooperative.v1.HeuristicAssignmentRule(time_limit_seconds=.05,max_moves=2048) if allocation == 'H1' else \
        (StableSeededCPSATAssignmentRule if seeded else cooperative.v1.StableCPSATAssignmentRule)(
            time_limit_seconds=budget_seconds,seed=0,require_backend=True)
    policy=cooperative.v1.Policy(rule=rule,k=3,wait=2,notify=True,name=code,observed_inquiry={})
    cls=(CooperativeNormalRecoveryRankReplay if enabled else IndependentNormalRecoveryRankReplay) if variant == 'normal' else \
        (CooperativeLatchedRecoveryRankReplay if enabled else IndependentLatchedRecoveryRankReplay)
    arguments=dict(inquiry='v4',recovery=True,pending_protection=True,cohort_registration=True,
        scarce_routing=True,late_reply_recovery=True,late_pending_guard=True,shortage_probe=True,rank_variant=variant)
    if enabled:arguments['cooperative_probe']=True
    branch=cls(deepcopy(scenario),policy,**arguments).run_replay()
    branch.update(id=code,label=label or code,policy=cooperative.v1._policy_spec(policy),
        scarce_delay_spec=dict(inquiry='v4',allocation=allocation,cohort_registration=True,scarce_routing=True,
            actual_action='parallel_scarce_supplement',original_selector_candidates_unchanged=False,
            maximum_supplements_per_patient=1,recovery=True,pending_protection=True,pending_window_minutes=1,
            time_step_seconds=15,actual_selector_reused=False,original_seven_inputs_retained=True,
            base_selector_reused_as_kernel=True,extra_resource_change_publications=False),
        deadline_recovery_spec=dict(late_reply_recovery=True,scope='one_initial_scarce_supplement',
            late_reply_window_minutes=cooperative.v1.LATE_REPLY_WINDOW_MINUTES,original_timeout_unchanged=True,
            original_timeout_feedback_unchanged=True,blocking_wait_added=False,hospital_work_calendar_unchanged=not enabled,
            patient_condition_fingerprint_required=True,new_offer_ttl_from_actual_receipt=True,
            actual_reply_outcome_required=True,current_fresh_resource_check_required=True,
            current_valid_offer_rejects=True,newer_same_hospital_attempt_rejects=True))
    actual_seeded=bool(seeded and allocation == 'C1')
    branch.update(allocation_intervention_id=EXPERIMENT_ID,c1_incumbent=dict(
        schema_version=1,allocation=allocation,seeded=actual_seeded,
        variant=VARIANT if actual_seeded else None,
        actual_assignment_class=f'{type(rule).__module__}.{type(rule).__name__}',
        base_assignment_class=('simulation.stable_cp_sat_assignment.StableCPSATAssignmentRule'
            if allocation == 'C1' else 'simulation.heuristic_assignment.HeuristicAssignmentRule'),
        budget_seconds=float(budget_seconds if allocation == 'C1' else .05),
        initializer_max_candidates=MAX_CANDIDATES if actual_seeded else None,
        initializer_limit_seconds=INITIALIZER_LIMIT_SECONDS if actual_seeded else None,
        budget_scope=BUDGET_SCOPE if actual_seeded else 'original_assignment_budget',
        actual_replay_class=f'{cls.__module__}.{cls.__name__}',
        original_normal_rank_workflow_descriptors_retained=True,
        inquiry_readiness_and_physical_gates_unchanged=True,
        actual_assignment_paths_may_change=True))
    return branch
