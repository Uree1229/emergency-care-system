"""Unwired exact-string-key guarded dispatch between original and mass Memo.

The guard reads only exact builtin fields it needs. It neither walks prospective
row/jobs nor validates their contents before the selected original algorithm.
Unsupported shapes follow the original scorer without coercing custom objects.
"""
import hashlib
import math
from pathlib import Path
from . import cohort_recourse_memo_model as memo

MEMO_SHA256 = 'b5fdf966a6f11c61a1578c47ccc9e3d6b8cdb351a9541421dee59dcebdb068a0'
if hashlib.sha256(Path(memo.__file__).read_bytes()).hexdigest() != MEMO_SHA256:
    raise ValueError('frozen Memo source differs')
original = memo.original
RULE = 'builtin_active_candidates2_budget2_fixed_own_below4_contact_window45'


def _number(value):
    return type(value) is int or type(value) is float and math.isfinite(value)


def _string_dict(value):
    return type(value) is dict and all(type(key) is str for key in value)


def _eligible(owner, frozen):
    if not _string_dict(owner) or not _string_dict(frozen):
        return False
    inputs, patient, job = owner.get('inputs'), owner.get('patient'), owner.get('job')
    if not _string_dict(inputs) or not _string_dict(patient) or not _string_dict(job):
        return False
    acceptance = owner.get('valid_acceptance_ids')
    if type(acceptance) is not list or acceptance:
        return False
    if type(job.get('enabled')) is not int or job['enabled'] != 1:
        return False
    policy = job.get('policy_name')
    if type(policy) is not str or policy != original.foundation.NAME:
        return False
    candidates, budget = inputs.get('candidates'), inputs.get('max_new')
    if type(candidates) is not list or len(candidates) < 2 or type(budget) is not int or budget < 2:
        return False
    own = frozen.get('original_all_own')
    if type(own) is not list or len(own) >= 4:
        return False
    now, end = inputs.get('now'), owner.get('evaluation_end_at')
    if type(now) is not int or type(end) is not int or now < 0 or end <= now:
        return False
    # Origin comparisons are only performed after exact builtin type checks.
    if type(frozen.get('now')) is not int or frozen['now'] != now:
        return False
    if type(frozen.get('evaluation_end_at')) is not int or frozen['evaluation_end_at'] != end:
        return False
    pid, fixed_pid = patient.get('id'), frozen.get('patient_id')
    if type(pid) not in (str, int) or type(fixed_pid) is not type(pid) or fixed_pid != pid:
        return False
    started, deadline, review = job.get('started_at'), job.get('deadline_at'), job.get('next_evaluation_at')
    if not _number(started) or not _number(deadline) or not _number(review):
        return False
    barrier = max(now, started, review if own else now)
    if min(end, deadline)-barrier < 45:
        return False
    edges = frozen.get('cell_edges_seconds')
    if type(edges) is not list or not edges or any(not _number(value) for value in edges):
        return False
    return True


def dispatch_kind(owner, frozen, row_pending, jobs):
    """Describe the conservative route, without validating unused row/jobs."""
    try:
        return 'memo' if _eligible(owner, frozen) else 'original'
    except Exception:
        # Eligibility is an optimization. Original validation supplies the final
        # behavior and exception order whenever the guard cannot decide safely.
        return 'original'


def score_owner(owner, frozen, row_pending, jobs):
    function = memo.score_owner if dispatch_kind(owner, frozen, row_pending, jobs) == 'memo' else original.score_owner
    return function(owner, frozen, row_pending, jobs)
