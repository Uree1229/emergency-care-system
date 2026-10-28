"""Isolated explicit-time adapter around the unchanged workflow/FIFO runner.

Only the new horizon policy receives an eighth public input. A ContextVar binds
the actual _apply snapshot and connection; a separate committed read snapshot
must agree with the applying job before public A/H provenance is constructed.
Like the frozen adapters, run_scenario patches process globals and permits one
active run per process; independent performance branches use worker processes.
"""
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path
import tempfile

from server import db, inquiry_experiment as ie, inquiry_policies, inquiry_workflow
from . import harness, runner, residual_policy, horizon_policy, horizon_context


JOB_FIELDS = ('patient_id', 'version', 'started_at', 'deadline_at', 'enabled', 'policy_name')
SNAPSHOT_FIELDS = {'patient_id', 'job_version', 'patient_version', 'revision', 'now'}
NEW_INSTRUMENTATION = dict(
    original_runner='inquiry_lab.runner._branch',
    actual_workflow='server.inquiry_workflow.run_once', record_only=False,
    epoch=ie.EPOCH, original_policy_inputs_unchanged=True,
    public_time_context_added=True,
    current_patient_source='snapshot.patient_id via ContextVar around unchanged _apply',
    current_job_source='committed workflow row equal to _matches-checked applying job',
    evaluation_end_source='committed patient_created.at + frozen lab 300 seconds')


def _job_projection(conn, patient_id):
    row = db.row(conn.execute('SELECT patient_id,version,started_at,deadline_at,enabled,policy_name '
        'FROM inquiry_workflows WHERE patient_id=?', (patient_id,)).fetchone())
    if row is None:
        raise ValueError('current public workflow row is missing')
    return {key:row[key] for key in JOB_FIELDS}


def _capture_time_context(inspection, active_apply, now):
    """Capture only committed public data after the unchanged version guard."""
    if type(active_apply) is not dict or set(active_apply) != {'conn', 'snapshot', 'now'}:
        raise ValueError('current apply identity is missing')
    snapshot = active_apply['snapshot']
    if type(snapshot) is not dict or set(snapshot) != SNAPSHOT_FIELDS:
        raise ValueError('current workflow snapshot identity is incomplete')
    if active_apply['now'] != now or snapshot['now'] > now:
        raise ValueError('policy clock differs from the current applying call')
    if not active_apply['conn'].in_transaction:
        raise ValueError('current apply must already hold its guarded transaction')
    matches, _, _ = inquiry_workflow._matches(active_apply['conn'], snapshot)
    if not matches:
        raise ValueError('current workflow snapshot no longer matches')
    applying_job = _job_projection(active_apply['conn'], snapshot['patient_id'])
    if (applying_job['version'] != snapshot['job_version'] or
            applying_job['enabled'] != 1 or applying_job['policy_name'] != horizon_policy.NAME):
        raise ValueError('current horizon workflow identity differs')
    inspection.execute('BEGIN')
    try:
        prefix = inspection.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]
        dprefix = inspection.execute('SELECT COALESCE(MAX(id),0) FROM inquiry_policy_decisions').fetchone()[0]
        committed_job = _job_projection(inspection, snapshot['patient_id'])
        if committed_job != applying_job:
            raise ValueError('committed public job differs from applying workflow')
        events = db.rows(inspection.execute('SELECT * FROM events WHERE id<=? ORDER BY id', (prefix,)))
        context = horizon_context.build_context(now=now, patient_id=snapshot['patient_id'],
            job_version=snapshot['job_version'], event_prefix_max=prefix,
            decision_prefix_max=dprefix, job=committed_job, events=events)
    finally:
        inspection.rollback()
    source = dict(apply_snapshot=deepcopy(snapshot), job=deepcopy(applying_job))
    return context, source


def run_scenario(scenario, arms):
    scenario = deepcopy(scenario)
    ie.validate(scenario)
    arms = [tuple(a) for a in arms]
    if not arms or len(arms) != len(set(arms)):
        raise ValueError('unique arms required')
    old_registry = deepcopy(inquiry_policies._POLICIES)
    old_decide, old_apply = inquiry_policies.decide, inquiry_workflow._apply
    old_installed = set(harness._INSTALLED)
    current_apply = ContextVar('horizon_current_apply', default=None)
    inspection, calls = None, []
    try:
        harness.install(['eat_l15_f'])
        baseline_decide = inquiry_policies.decide
        inquiry_policies._POLICIES[residual_policy.NAME] = residual_policy.spec()
        inquiry_policies._POLICIES[horizon_policy.NAME] = horizon_policy.spec()
        for policy, rule in arms:
            inquiry_policies.spec(policy)
            runner.confirm_policy.parse_rule(rule)

        def identified_apply(conn, now, snapshot, candidates, ledger):
            token = current_apply.set(dict(conn=conn, snapshot=snapshot, now=now))
            try:
                return old_apply(conn, now, snapshot, candidates, ledger)
            finally:
                current_apply.reset(token)

        inquiry_workflow._apply = identified_apply

        def recorded_decide(name, **kwargs):
            before = deepcopy(kwargs)
            workflow_snapshot = None
            if name == horizon_policy.NAME:
                context, workflow_snapshot = _capture_time_context(inspection, current_apply.get(), kwargs['now'])
                original_context = deepcopy(context)
                result = horizon_policy.decide(name, **kwargs, time_context=context)
                if context != original_context:
                    raise AssertionError('policy mutated public time context')
                captured = dict(before, time_context=original_context)
                prefix = context['event_prefix_max']
            else:
                prefix = inspection.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]
                result = (residual_policy.decide(name, **kwargs) if name == residual_policy.NAME
                    else baseline_decide(name, **kwargs))
                captured = before
            if kwargs != before:
                raise AssertionError('policy mutated original workflow arguments')
            call = dict(name=name, inputs=captured, output=deepcopy(result), db_event_prefix_max=prefix)
            if workflow_snapshot is not None:
                call['workflow_snapshot'] = workflow_snapshot
            calls.append(call)
            return result

        inquiry_policies.decide = recorded_decide
        environment_hash = ie.digest(scenario)
        with tempfile.TemporaryDirectory(prefix='eat-horizon-') as directory:
            root = Path(directory)
            base = ie._base_database(root/'base.db', scenario)
            try:
                initial, branches = ie.snapshot_digest(base), []
                for index, (policy, rule) in enumerate(arms):
                    path = root/f'arm{index}.db'
                    branch_conn = db.connect(path)
                    base.backup(branch_conn)
                    branch_conn.close()
                    calls, inspection = [], db.connect(path)
                    try:
                        branch = runner._branch(path, scenario, policy, rule, initial, environment_hash)
                        decisions = db.rows(inspection.execute('SELECT * FROM inquiry_policy_decisions ORDER BY id'))
                        if len(calls) != len(decisions):
                            raise AssertionError('policy/DB decision count differs')
                        for call, decision in zip(calls, decisions):
                            output = call['output']
                            if (call['name'] != decision['policy_name'] or call['inputs']['now'] != decision['at']
                                    or output['send'] != decision['hospital_ids'] or output['reason'] != decision['reason']
                                    or output['evidence'] != decision['evidence']):
                                raise AssertionError('captured policy differs from committed decision')
                            if call['name'] == horizon_policy.NAME:
                                context = call['inputs']['time_context']
                                snapshot = call['workflow_snapshot']['apply_snapshot']
                                job = call['workflow_snapshot']['job']
                                if (context['patient_id'] != decision['patient_id'] or
                                        not context['decision_prefix_max'] < decision['id'] or
                                        not context['job_version'] == snapshot['job_version'] == job['version'] == decision['job_version']):
                                    raise AssertionError('committed horizon identity/prefix differs')
                            call.update(patient_id=decision['patient_id'], at=decision['at']-ie.EPOCH,
                                db_decision_id=decision['id'])
                        instrumentation = (deepcopy(NEW_INSTRUMENTATION) if policy == horizon_policy.NAME else
                            dict(original_runner='inquiry_lab.runner._branch',
                                actual_workflow='server.inquiry_workflow.run_once', record_only=True,
                                epoch=ie.EPOCH, policy_inputs_unchanged=True))
                        branch.update(policy_calls=calls,
                            db_events=db.rows(inspection.execute('SELECT * FROM events ORDER BY id')),
                            db_inquiries=db.rows(inspection.execute('SELECT * FROM inquiries ORDER BY id')),
                            db_decisions=decisions, instrumentation=instrumentation)
                        branches.append(branch)
                    finally:
                        inspection.close()
                        inspection = None
            finally:
                base.close()
        return dict(schema_version=1, scenario=deepcopy(scenario), initial_snapshot_sha256=initial,
            environment_sha256=environment_hash, arms=[list(a) for a in arms], branches=branches)
    finally:
        inquiry_workflow._apply, inquiry_policies.decide = old_apply, old_decide
        inquiry_policies._POLICIES.clear()
        inquiry_policies._POLICIES.update(old_registry)
        harness._INSTALLED.clear()
        harness._INSTALLED.update(old_installed)


def run_task(task):
    kind, seed, arms = task
    return run_scenario(harness.build(kind, seed), arms)
