"""Unchanged workflow with an explicit public prior-plan input for one policy.

The original seven arguments stay unchanged. A ContextVar around the frozen
_apply supplies current patient identity; a separate committed read snapshot
supplies only already recorded plans and event/decision watermarks.
"""
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path
import tempfile

from server import db, inquiry_experiment as ie, inquiry_policies, inquiry_workflow
from . import harness, runner, residual_policy, guard_policy, intent_policy, intent_context


def run_scenario(scenario, arms):
    scenario=deepcopy(scenario);ie.validate(scenario);arms=[tuple(a) for a in arms]
    if not arms or len(arms)!=len(set(arms)):raise ValueError('unique arms required')
    old_registry=deepcopy(inquiry_policies._POLICIES);old_decide=inquiry_policies.decide
    old_apply=inquiry_workflow._apply;old_installed=set(harness._INSTALLED)
    current_patient=ContextVar('intent_current_patient',default=None)
    inspection=None;calls=[]
    try:
        harness.install(['eat_l15_f']);baseline_decide=inquiry_policies.decide
        inquiry_policies._POLICIES[residual_policy.NAME]=residual_policy.spec(residual_policy.NAME)
        inquiry_policies._POLICIES[guard_policy.NAME]=guard_policy.spec(guard_policy.NAME)
        inquiry_policies._POLICIES[intent_policy.NAME]=intent_policy.spec(intent_policy.NAME)
        for policy,rule in arms:
            inquiry_policies.spec(policy);runner.confirm_policy.parse_rule(rule)

        def identified_apply(conn,now,snapshot,candidates,ledger):
            token=current_patient.set(snapshot['patient_id'])
            try:return old_apply(conn,now,snapshot,candidates,ledger)
            finally:current_patient.reset(token)

        inquiry_workflow._apply=identified_apply

        def recorded_decide(name,**kwargs):
            before=deepcopy(kwargs)
            if name==intent_policy.NAME:
                pid=current_patient.get()
                if pid is None:raise AssertionError('public patient identity missing from apply context')
                inspection.execute('BEGIN')
                try:
                    prefix=inspection.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]
                    dprefix=inspection.execute('SELECT COALESCE(MAX(id),0) FROM inquiry_policy_decisions').fetchone()[0]
                    events=db.rows(inspection.execute('SELECT * FROM events WHERE id<=? ORDER BY id',(prefix,)))
                    decisions=db.rows(inspection.execute('SELECT * FROM inquiry_policy_decisions WHERE id<=? ORDER BY id',(dprefix,)))
                    public=intent_context.build_context(now=kwargs['now'],patient_id=pid,
                        event_prefix_max=prefix,decision_prefix_max=dprefix,pending=kwargs['pending'],
                        events=events,decisions=decisions)
                finally:inspection.rollback()
                original_public=deepcopy(public)
                result=intent_policy.decide(name,**kwargs,public_context=public)
                if public!=original_public:raise AssertionError('policy mutated public plan context')
                captured=dict(before,public_context=original_public)
            else:
                prefix=inspection.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]
                result=(guard_policy.decide(name,**kwargs) if name==guard_policy.NAME
                        else residual_policy.decide(name,**kwargs) if name==residual_policy.NAME
                        else baseline_decide(name,**kwargs));captured=before
            if kwargs!=before:raise AssertionError('policy mutated original workflow arguments')
            calls.append(dict(name=name,inputs=captured,output=deepcopy(result),db_event_prefix_max=prefix))
            return result

        inquiry_policies.decide=recorded_decide;environment_hash=ie.digest(scenario)
        with tempfile.TemporaryDirectory(prefix='eat-intent-') as directory:
            root=Path(directory);base=ie._base_database(root/'base.db',scenario)
            try:
                initial=ie.snapshot_digest(base);branches=[]
                for index,(policy,rule) in enumerate(arms):
                    path=root/f'arm{index}.db';conn=db.connect(path);base.backup(conn);conn.close()
                    calls=[];inspection=db.connect(path)
                    try:
                        branch=runner._branch(path,scenario,policy,rule,initial,environment_hash)
                        decisions=db.rows(inspection.execute('SELECT * FROM inquiry_policy_decisions ORDER BY id'))
                        if len(calls)!=len(decisions):raise AssertionError('policy/DB coverage differs')
                        for call,decision in zip(calls,decisions):
                            output=call['output']
                            if (call['name']!=decision['policy_name'] or call['inputs']['now']!=decision['at']
                                    or output['send']!=decision['hospital_ids'] or output['reason']!=decision['reason']
                                    or output['evidence']!=decision['evidence']):
                                raise AssertionError('captured policy differs from committed decision')
                            if call['name']==intent_policy.NAME:
                                context=call['inputs']['public_context']
                                if context['patient_id']!=decision['patient_id'] or context['decision_prefix_max']>=decision['id']:
                                    raise AssertionError('current patient or prior decision watermark differs')
                            call.update(patient_id=decision['patient_id'],at=decision['at']-ie.EPOCH,
                                db_decision_id=decision['id'])
                        instrumentation=dict(original_runner='inquiry_lab.runner._branch',
                            actual_workflow='server.inquiry_workflow.run_once',record_only=True,
                            epoch=ie.EPOCH,policy_inputs_unchanged=True)
                        if policy==intent_policy.NAME:
                            instrumentation=dict(original_runner='inquiry_lab.runner._branch',
                                actual_workflow='server.inquiry_workflow.run_once',record_only=False,epoch=ie.EPOCH,
                                original_policy_inputs_unchanged=True,public_plan_context_added=True,
                                current_patient_source='snapshot.patient_id via ContextVar around unchanged _apply')
                        branch.update(policy_calls=calls,db_events=db.rows(inspection.execute('SELECT * FROM events ORDER BY id')),
                            db_inquiries=db.rows(inspection.execute('SELECT * FROM inquiries ORDER BY id')),
                            db_decisions=decisions,instrumentation=instrumentation);branches.append(branch)
                    finally:inspection.close();inspection=None
            finally:base.close()
        return dict(schema_version=1,scenario=deepcopy(scenario),initial_snapshot_sha256=initial,
            environment_sha256=environment_hash,arms=[list(a) for a in arms],branches=branches)
    finally:
        inquiry_workflow._apply=old_apply;inquiry_policies.decide=old_decide
        inquiry_policies._POLICIES.clear();inquiry_policies._POLICIES.update(old_registry)
        harness._INSTALLED.clear();harness._INSTALLED.update(old_installed)


def run_task(task):
    kind,seed,arms=task
    return run_scenario(harness.build(kind,seed),arms)
