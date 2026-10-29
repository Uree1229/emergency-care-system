"""Record-only adapter around the unchanged confirmation/FIFO runner.

The actual server workflow executes against independent temporary databases.
Full policy arguments, outputs and the committed DB event prefix are saved.
No policy receives the trace, inspection connection, workers or potentials.
"""
from copy import deepcopy
from pathlib import Path
import tempfile

from server import db, inquiry_experiment as ie, inquiry_policies
from . import harness, runner, residual_policy, fifo_policy


def run_scenario(scenario, arms):
    scenario=deepcopy(scenario);ie.validate(scenario)
    arms=[tuple(a) for a in arms]
    if not arms or len(arms)!=len(set(arms)):raise ValueError('unique arms required')
    old_registry=deepcopy(inquiry_policies._POLICIES)
    old_decide=inquiry_policies.decide
    old_installed=set(harness._INSTALLED)
    inspection=None;calls=[]
    try:
        harness.install(['eat_l15_f'])
        baseline_decide=inquiry_policies.decide
        inquiry_policies._POLICIES[residual_policy.NAME]=residual_policy.spec(residual_policy.NAME)
        inquiry_policies._POLICIES[fifo_policy.NAME]=fifo_policy.spec(fifo_policy.NAME)
        for policy,rule in arms:
            inquiry_policies.spec(policy);runner.confirm_policy.parse_rule(rule)

        def recorded_decide(name,**kwargs):
            before=deepcopy(kwargs)
            prefix=inspection.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]
            result=(fifo_policy.decide(name,**kwargs) if name==fifo_policy.NAME
                    else residual_policy.decide(name,**kwargs) if name==residual_policy.NAME
                    else baseline_decide(name,**kwargs))
            if kwargs!=before:raise AssertionError('policy mutated actual arguments')
            calls.append(dict(name=name,inputs=before,output=deepcopy(result),db_event_prefix_max=prefix))
            return result

        inquiry_policies.decide=recorded_decide
        environment_hash=ie.digest(scenario)
        with tempfile.TemporaryDirectory(prefix='eat-residual-') as directory:
            root=Path(directory);base=ie._base_database(root/'base.db',scenario)
            try:
                initial=ie.snapshot_digest(base);branches=[]
                for index,(policy,rule) in enumerate(arms):
                    path=root/f'arm{index}.db';branch_conn=db.connect(path)
                    base.backup(branch_conn);branch_conn.close()
                    calls=[];inspection=db.connect(path)
                    try:
                        branch=runner._branch(path,scenario,policy,rule,initial,environment_hash)
                        decisions=db.rows(inspection.execute('SELECT * FROM inquiry_policy_decisions ORDER BY id'))
                        if len(calls)!=len(decisions):raise AssertionError('policy/DB decision count differs')
                        for call,decision in zip(calls,decisions):
                            output=call['output']
                            if (call['name']!=decision['policy_name'] or call['inputs']['now']!=decision['at']
                                or output['send']!=decision['hospital_ids'] or output['reason']!=decision['reason']
                                or output['evidence']!=decision['evidence']):
                                raise AssertionError('captured decision differs from actual database decision')
                            call.update(patient_id=decision['patient_id'],at=decision['at']-ie.EPOCH,
                                db_decision_id=decision['id'])
                        branch.update(policy_calls=calls,
                            db_events=db.rows(inspection.execute('SELECT * FROM events ORDER BY id')),
                            db_inquiries=db.rows(inspection.execute('SELECT * FROM inquiries ORDER BY id')),
                            db_decisions=decisions,
                            instrumentation=dict(original_runner='inquiry_lab.runner._branch',
                                actual_workflow='server.inquiry_workflow.run_once',record_only=True,
                                epoch=ie.EPOCH,policy_inputs_unchanged=True))
                        branches.append(branch)
                    finally:
                        inspection.close();inspection=None
            finally:base.close()
        return dict(schema_version=1,scenario=deepcopy(scenario),initial_snapshot_sha256=initial,
            environment_sha256=environment_hash,arms=[list(a) for a in arms],branches=branches)
    finally:
        inquiry_policies.decide=old_decide
        inquiry_policies._POLICIES.clear();inquiry_policies._POLICIES.update(old_registry)
        harness._INSTALLED.clear();harness._INSTALLED.update(old_installed)


def run_task(task):
    kind,seed,arms=task
    return run_scenario(harness.build(kind,seed),arms)
