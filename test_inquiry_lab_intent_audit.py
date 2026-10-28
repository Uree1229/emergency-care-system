"""Independent public-prefix reconstruction and saved-raw veto mutations.

The SHA-bound synthetic guard-evidence builder is a test fixture, not a policy
or performance execution. Context reconstruction tests use explicit event and
decision journals, including same-clock future rows invisible to the caller.
"""
from copy import deepcopy
import gzip
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import sys
import unittest

from inquiry_lab import intent_audit as audit
from test_inquiry_lab_guard_audit import inputs as guard_inputs,output as guard_fixture,proposal as guard_proposal

ROOT=Path(__file__).resolve().parent
GUARD_TEST_SHA256='83dc6b16c32a5cf0d6cc5638d1e850305cfdf80ccc2d4f11d149006a3b7a51fd'
if hashlib.sha256((ROOT/'test_inquiry_lab_guard_audit.py').read_bytes()).hexdigest()!=GUARD_TEST_SHA256:
    raise ValueError('frozen independent synthetic guard fixture builder differs')
OLD_FIXTURE=ROOT/'simulation/fixtures/eat_guard_20261020/mix7.json.gz'
OLD_COMPRESSED_SHA256='541760d9797dd53b65cff243d2fa1d20d948c4b70eb3966b3aa5dff0d6a8bef1'
OLD_RAW_SHA256='9a2552dffb41e161c5dc8ff1936b1db22fbe6d24a2c0ba4827453869360a97fc'
FIXTURE=ROOT/'simulation/fixtures/eat_intent_20261020/mix7.json.gz'
COMPRESSED_SHA256='4f03ee6dd943dcc02d8ce0a535164b7bd4410eabeea83e3897dca1fb5b0b32c9'
RAW_SHA256='3daa4796c8f93d3f211544c18b159e1292a28679c7a11b848303fbcaef35d4c3'


def journals():
    now=audit.EPOCH+100
    def event(identity,patient,hospital,typ='inquiry_sent',**data):
        return dict(id=identity,at=now-50+identity,type=typ,patient_id=patient,hospital_id=hospital,
            data=dict(inquiry_id=identity if typ=='inquiry_sent' else 3,**data))
    events=[event(1,'A','H90'),event(2,'B','H91'),event(3,'C','H92'),
        event(4,'C','H92','inquiry_responded',action='decline')]
    def decision(identity,patient,age,plan):
        return dict(id=identity,patient_id=patient,at=now-age,evidence=dict(candidate_plan=[
            dict(hospital=h,planned_offset_seconds=t) for h,t in plan]))
    decisions=[decision(1,'A',30,[('old',15)]),decision(2,'B',20,[('oldB',15)]),
        decision(3,'A',15,[('H01',90),('H90',15),('H02',None),('H03',0)]),
        decision(4,'B',2,[('H04',15)]),decision(5,'SELF',0,[('self',15)]),
        decision(6,'A',0,[('future-same-clock',15)]),decision(7,'C',0,[('closed',15)])]
    return now,events,decisions


def inputs(protect_added=True):
    value=guard_inputs();base=guard_fixture(value);proposal=base['evidence']['guard']['proposal']
    added=[h for h in base['send'] if h not in proposal['send']]
    assert added and base['evidence']['guard']['applied']
    hospital=added[0] if protect_added else next(h for h in base['send'] if h in proposal['send'])
    value['public_context']=dict(patient_id='SELF',event_prefix_max=4,decision_prefix_max=2,
        plans=[dict(patient='Q1',decision_id=2,at=value['now']-15,
            candidate_plan=[dict(hospital=hospital,planned_offset_seconds=120)])])
    return value


def output(value,guarded=None):
    core_inputs={key:value[key] for key in audit.INPUT_KEYS};base=guard_fixture(core_inputs) if guarded is None else guarded
    proposal=base['evidence']['guard']['proposal'];applied=base['evidence']['guard']['applied']
    context=value['public_context'];added=[h for h in base['send'] if h not in proposal['send']]
    protected=sorted({p['hospital'] for row in context['plans'] for p in row['candidate_plan']})
    conflicts=[dict(hospital=h,patient=row['patient'],decision_id=row['decision_id'],at=row['at'],
        planned_offset_seconds=p['planned_offset_seconds']) for h in added for row in context['plans']
        for p in row['candidate_plan'] if p['hospital']==h]
    vetoed=applied and bool(conflicts);core=proposal if vetoed else base
    result=deepcopy(core);result['evidence']['policy']=audit.NAME
    result['evidence']['intent']=dict(schema_version=1,rule=audit.RULE,
        source_guard_sha256=audit.SOURCE_GUARD_SHA256,guard_output=base,public_context=deepcopy(context),
        guard_applied=applied,added_hospitals=added,protected_hospitals=protected,conflicts=conflicts,
        vetoed=vetoed,core_origin='residual_proposal' if vetoed else 'guard_output')
    return result


def load_fixture(path,compressed_sha,raw_sha):
    before=path.stat();compressed=path.read_bytes();after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) \
            or hashlib.sha256(compressed).hexdigest()!=compressed_sha:
        raise AssertionError('saved fixture compressed bytes changed')
    raw=gzip.decompress(compressed)
    if hashlib.sha256(raw).hexdigest()!=raw_sha:raise AssertionError('saved fixture raw bytes changed')
    return json.loads(raw)


class IntentIndependentContextTests(unittest.TestCase):
    def test_current_pending_latest_prior_decision_inclusive_ttl_and_filtered_top_plan(self):
        now,events,decisions=journals();context=audit.context_from_prefix(events,decisions,now,'SELF',4,4)
        self.assertEqual(context,dict(patient_id='SELF',event_prefix_max=4,decision_prefix_max=4,plans=[
            dict(patient='A',decision_id=3,at=now-15,candidate_plan=[dict(hospital='H01',planned_offset_seconds=90)]),
            dict(patient='B',decision_id=4,at=now-2,candidate_plan=[dict(hospital='H04',planned_offset_seconds=15)])]))
        checked=audit.audit_context(dict(now=now),context,'SELF',events,decisions,4,4,5)
        self.assertEqual(checked,2)

    def test_event_and_decision_prefix_are_independent_identity_domains(self):
        now,events,decisions=journals()
        context=audit.context_from_prefix(events,decisions,now,'SELF',4,3)
        self.assertEqual([r['patient'] for r in context['plans']],['A'])
        # Event prefix may be greater than decision prefix; no cross-domain ordering exists.
        self.assertEqual(audit.audit_context(dict(now=now),context,'SELF',events,decisions,4,3,4),1)

    def test_future_same_clock_decisions_are_not_visible_and_latest_stale_does_not_fallback(self):
        now,events,decisions=journals();context=audit.context_from_prefix(events,decisions,now,'SELF',4,4)
        self.assertNotIn('future-same-clock',str(context));self.assertNotIn('self',str(context))
        self.assertNotIn('closed',str(context));self.assertNotIn('oldB',str(context))
        later=audit.context_from_prefix(events,decisions,now+1,'SELF',4,4)
        self.assertEqual([r['patient'] for r in later['plans']],['B'])

    def test_already_contacted_even_completed_or_cancelled_is_filtered_by_prefix_events(self):
        now,events,decisions=journals()
        events.extend([dict(id=5,at=now-1,type='inquiry_sent',patient_id='A',hospital_id='H01',data=dict(inquiry_id=5)),
            dict(id=6,at=now,type='inquiry_cancelled',patient_id='A',hospital_id='H01',data=dict(inquiry_id=5))])
        context=audit.context_from_prefix(events,decisions,now,'SELF',6,4)
        self.assertEqual([r['patient'] for r in context['plans']],['B'])

    def test_nonpending_owner_self_empty_and_nested_counterfactual_plans_are_excluded(self):
        now,events,decisions=journals()
        decisions[2]['evidence']['candidate_plan']=[]
        decisions[2]['evidence']['guard']=dict(proposal=dict(evidence=dict(candidate_plan=[
            dict(hospital='nested-must-not-escape',planned_offset_seconds=15)])))
        context=audit.context_from_prefix(events,decisions,now,'SELF',4,4)
        self.assertEqual([r['patient'] for r in context['plans']],['B'])
        self.assertNotIn('nested-must-not-escape',str(context))

    def test_latest_empty_decision_without_new_event_replaces_older_positive_intent(self):
        now,events,decisions=journals()
        decisions=[row for row in decisions if row['id']<=4]+[
            dict(id=5,patient_id='A',at=now,evidence=dict(candidate_plan=[]))]
        context=audit.context_from_prefix(events,decisions,now,'SELF',4,5)
        self.assertEqual([row['patient'] for row in context['plans']],['B'])
        self.assertEqual(context['event_prefix_max'],4)
        self.assertEqual(audit.audit_context(dict(now=now),context,'SELF',events,decisions,4,5,6),1)

    def test_raw_context_reordering_omission_stale_self_or_future_prefix_rejected(self):
        now,events,decisions=journals();original=audit.context_from_prefix(events,decisions,now,'SELF',4,4)
        for kind in ('order','omit','self','future','prefix','extra','already_contacted'):
            context=deepcopy(original)
            if kind=='order':context['plans'].reverse()
            elif kind=='omit':context['plans'].pop()
            elif kind=='self':context['patient_id']='A'
            elif kind=='future':context['plans'][0]['decision_id']=6
            elif kind=='prefix':context['decision_prefix_max']=5
            elif kind=='extra':context['plans'][0]['workers']=1
            else:context['plans'][0]['candidate_plan'].append(dict(hospital='H90',planned_offset_seconds=15))
            with self.assertRaises(audit.IntentAuditError):
                audit.audit_context(dict(now=now),context,'SELF',events,decisions,4,4,5)


class IntentIndependentVetoTests(unittest.TestCase):
    def test_added_only_overlap_veto_recovers_exact_original_R_core_and_review(self):
        value=inputs();result=output(value);before=deepcopy(value)
        checked=audit.audit_intent(value,result)
        self.assertEqual(checked['intent_vetoed'],1);self.assertEqual(checked['intent_conflicts_checked'],1)
        proposal=result['evidence']['intent']['guard_output']['evidence']['guard']['proposal']
        self.assertEqual(result['send'],proposal['send']);self.assertEqual(result['next_review_at'],proposal['next_review_at'])
        self.assertEqual(value,before);self.assertNotIn('guard',result['evidence'])

    def test_hospital_already_in_R_current_send_does_not_veto_guard_swap(self):
        value=inputs(False);result=output(value);checked=audit.audit_intent(value,result)
        self.assertEqual(checked['intent_vetoed'],0)
        self.assertEqual(result['send'],result['evidence']['intent']['guard_output']['send'])
        self.assertEqual(result['evidence']['intent']['conflicts'],[])

    def test_empty_context_keeps_exact_guard_core(self):
        value=inputs();value['public_context']['plans']=[];result=output(value)
        self.assertEqual(audit.audit_intent(value,result)['intent_vetoed'],0)
        self.assertEqual(result['evidence']['intent']['core_origin'],'guard_output')

    def test_unapplied_guard_never_changes_an_original_R_query_due_to_intent(self):
        value=inputs();core={key:value[key] for key in audit.INPUT_KEYS}
        guarded=guard_fixture(core,guard_proposal(core,[0,0,0,0,120,180]))
        result=output(value,guarded);checked=audit.audit_intent(value,result)
        self.assertFalse(result['evidence']['intent']['guard_applied'])
        self.assertEqual(checked['intent_vetoed'],0);self.assertEqual(result['send'],guarded['send'])
        self.assertEqual(result['next_review_at'],guarded['next_review_at'])

    def test_conflict_witness_order_and_positive_far_offset_are_observed_intent_only(self):
        value=inputs();row=deepcopy(value['public_context']['plans'][0]);row.update(patient='Q2',decision_id=3)
        value['public_context']['decision_prefix_max']=3;value['public_context']['plans'].append(row)
        result=output(value);witness=result['evidence']['intent']['conflicts']
        self.assertEqual([r['decision_id'] for r in witness],[2,3])
        self.assertEqual([r['planned_offset_seconds'] for r in witness],[120,120])
        self.assertEqual(audit.audit_intent(value,result)['intent_conflicts_checked'],2)

    def test_changed_overlap_witness_core_source_or_metadata_context_is_rejected(self):
        value=inputs()
        for kind in ('veto','added','protected','witness','origin','source','context'):
            result=output(value);metadata=result['evidence']['intent']
            if kind=='veto':metadata['vetoed']=False
            elif kind=='added':metadata['added_hospitals']=[]
            elif kind=='protected':metadata['protected_hospitals']=[]
            elif kind=='witness':metadata['conflicts'][0]['decision_id']+=1
            elif kind=='origin':metadata['core_origin']='guard_output'
            elif kind=='source':metadata['source_guard_sha256']='0'*64
            else:metadata['public_context']['plans']=[]
            with self.assertRaises(audit.IntentAuditError):audit.audit_intent(value,result)

    def test_top_output_cannot_keep_swap_change_count_cost_or_delay_R_query(self):
        value=inputs()
        for kind in ('send','review','reason','cost','plan'):
            result=output(value)
            if kind=='send':result['send']=result['evidence']['intent']['guard_output']['send']
            elif kind=='review':result['next_review_at']+=1
            elif kind=='reason':result['reason']='new_reason'
            elif kind=='cost':result['evidence']['planned_expected_cost']+=1
            else:result['evidence']['candidate_plan'][0]['planned_offset_seconds']=15
            with self.assertRaises(audit.IntentAuditError):audit.audit_intent(value,result)

    def test_nested_guard_cost_and_source_search_scope_are_preserved(self):
        value=inputs();result=output(value)
        result['evidence']['intent']['guard_output']['evidence']['guard']['proposal']['evidence']['planned_expected_cost']+=1
        with self.assertRaises(audit.IntentAuditError):audit.audit_intent(value,result)

    def test_sha_bound_dependencies_and_full_call_audit_restore_original_body(self):
        self.assertEqual(hashlib.sha256(Path(audit.guard.__file__).read_bytes()).hexdigest(),audit.GUARD_AUDITOR_SHA256)
        self.assertIs(audit._ledger,audit.guard._ledger);self.assertIs(audit._audit_trace_fixed,audit.guard._audit_trace_fixed)
        source=inspect.getsource(audit._audit_calls_intent);original=inspect.getsource(audit.guard._audit_calls_guard)
        a=source.index("        if call['name']==NAME:\n            audit_context(")
        b=source.index('    for event in inquiry_events:',a)
        oa=original.index("        if call['name']==NAME:changed.update(audit_guard(inputs,output))")
        ob=original.index('    for event in inquiry_events:',oa)
        restored=source[:a]+original[oa:ob]+source[b:]
        restored=restored.replace('def _audit_calls_intent(','def _audit_calls_guard(',1)
        restored=restored.replace("        expected_inputs=INPUT_KEYS|{'public_context'} if call['name']==NAME else INPUT_KEYS\n",'',1)
        restored=restored.replace('set(inputs)==expected_inputs','set(inputs)==INPUT_KEYS',1)
        restored=restored.replace('        previous_decision=last_decision\n','',1)
        restored=restored.replace("('intent_context_plans_checked','intent_guard_applied','intent_conflicts_checked','intent_vetoed')",
            "('utility_subsets_checked','guard_triggered','guard_batch_evaluated','guard_applied')",1)
        self.assertEqual(restored,original)

    def test_clean_import_has_no_producer_server_kernel_or_solver(self):
        code=("import json,sys; import inquiry_lab.intent_audit; print(json.dumps(sorted(n for n in sys.modules "
            "if n.startswith(('server.','simulation.','ortools')) or n in {'inquiry_lab.eat_policy',"
            "'inquiry_lab.residual_policy','inquiry_lab.guard_policy','inquiry_lab.batch_policy',"
            "'inquiry_lab.intent_policy','inquiry_lab.intent_runner','inquiry_lab.intent_context'})))")
        result=subprocess.check_output([sys.executable,'-B','-c',code],cwd=ROOT,text=True)
        self.assertEqual(json.loads(result),[])


class IntentFrozenReferenceRawTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.raw=load_fixture(OLD_FIXTURE,OLD_COMPRESSED_SHA256,OLD_RAW_SHA256)

    def test_old_six_full_raw_anchors_remain_unchanged_and_audited(self):
        value=deepcopy(self.raw);before=deepcopy(value);result=audit.audit_replay(value)
        self.assertTrue(result['passed']);self.assertEqual(result['branches'],6);self.assertEqual(value,before)

    def test_old_reference_cannot_receive_extra_context_or_intent_intervention(self):
        value=deepcopy(self.raw);branch=next(b for b in value['branches'] if b['policy']==audit.BASE_POLICY)
        branch['policy_calls'][0]['inputs']['public_context']={}
        with self.assertRaises(audit.IntentAuditError):audit.audit_replay(value)


class IntentActualSavedRawAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw=load_fixture(FIXTURE,COMPRESSED_SHA256,RAW_SHA256)
        if cls.raw['scenario']['seed']!=7 or len(cls.raw['branches'])!=7:
            raise AssertionError('actual first public-intent fixture identity differs')

    def pair(self):
        value=deepcopy(self.raw)
        return value['scenario'],next(b for b in value['branches'] if b['policy']==audit.NAME)

    def plan_call(self,branch):
        return next(c for c in branch['policy_calls'] if c['inputs']['public_context']['plans'])

    def applied_call(self,branch):
        return next(c for c in branch['policy_calls'] if c['output']['evidence']['intent']['guard_applied'])

    def sync(self,branch,call,sync_context=False):
        if sync_context:call['output']['evidence']['intent']['public_context']=deepcopy(call['inputs']['public_context'])
        decision=next(d for d in branch['db_decisions'] if d['id']==call['db_decision_id'])
        decision.update(evidence=deepcopy(call['output']['evidence']),reason=call['output']['reason'],
            hospital_ids=deepcopy(call['output']['send']))

    def reject(self,scenario,branch):
        with self.assertRaises(audit.IntentAuditError):audit.audit_branch(scenario,branch)

    def test_seven_actual_full_raw_arms_and_every_context_without_mutation(self):
        value=deepcopy(self.raw);before=deepcopy(value);checked=audit.audit_replay(value)
        self.assertTrue(checked['passed']);self.assertEqual(checked['branches'],7);self.assertEqual(value,before)
        row=next(r for r in checked['branch_audits'] if r['intent_context_plans_checked'])
        self.assertEqual(row['policy_calls'],17);self.assertEqual(row['intent_context_plans_checked'],7)
        self.assertEqual(row['intent_guard_applied'],3);self.assertEqual(row['intent_vetoed'],0)
        self.assertEqual(row['intent_conflicts_checked'],0)
        # This preflight has no veto; the explicit unit witness exercises that branch.
        self.assertTrue(row['public_context_independently_reconstructed'])
        self.assertFalse(row['original_guard_search_independently_reexecuted']);self.assertFalse(row['optimality_claimed'])

    def test_actual_current_patient_and_strict_immediately_previous_decision_prefix_checked(self):
        for kind in ('self','future','older','event'):
            scenario,branch=self.pair();call=self.plan_call(branch);context=call['inputs']['public_context']
            if kind=='self':context['patient_id']=context['plans'][0]['patient']
            elif kind=='future':context['decision_prefix_max']=call['db_decision_id']
            elif kind=='older':context['decision_prefix_max']=0
            else:context['event_prefix_max']-=1
            self.sync(branch,call,True);self.reject(scenario,branch)

    def test_actual_context_is_rebuilt_even_when_input_metadata_and_DB_copies_match(self):
        for kind in ('omit','owner','hospital','offset','time','decision','extra'):
            scenario,branch=self.pair();call=self.plan_call(branch);context=call['inputs']['public_context']
            if kind=='omit':context['plans'].pop()
            elif kind=='owner':context['plans'][0]['patient']='nonpending-owner'
            elif kind=='hospital':context['plans'][0]['candidate_plan'][0]['hospital']='invented'
            elif kind=='offset':
                row=context['plans'][0]['candidate_plan'][0]
                row['planned_offset_seconds']=180 if row['planned_offset_seconds']!=180 else 15
            elif kind=='time':context['plans'][0]['at']-=1
            elif kind=='decision':context['plans'][0]['decision_id']+=1
            else:context['plans'][0]['running']=True
            self.sync(branch,call,True);self.reject(scenario,branch)

    def test_actual_previously_contacted_hospital_cannot_be_passed_as_a_future_intent(self):
        scenario,branch=self.pair();call=self.plan_call(branch);context=call['inputs']['public_context']
        owner=context['plans'][0]['patient'];prefix=context['event_prefix_max']
        contacted=next(e['hospital_id'] for e in branch['db_events'] if e['id']<=prefix
            and e['patient_id']==owner and e['type']=='inquiry_sent')
        context['plans'][0]['candidate_plan'][0]['hospital']=contacted
        self.sync(branch,call,True);self.reject(scenario,branch)

    def test_actual_no_conflict_cannot_fabricate_veto_witness_or_restore_R_membership(self):
        for kind in ('veto','witness','origin','Rsend'):
            scenario,branch=self.pair();call=self.applied_call(branch);metadata=call['output']['evidence']['intent']
            if kind=='veto':metadata['vetoed']=True
            elif kind=='witness':metadata['conflicts']=[dict(hospital='fake',patient='fake',decision_id=1,at=0,planned_offset_seconds=15)]
            elif kind=='origin':metadata['core_origin']='residual_proposal'
            else:
                call['output']['send']=deepcopy(metadata['guard_output']['evidence']['guard']['proposal']['send'])
                call['output']['evidence']['sent_hospitals']=deepcopy(call['output']['send'])
            self.sync(branch,call);self.reject(scenario,branch)

    def test_actual_nested_guard_plan_math_cost_review_and_original_core_remain_checked(self):
        for kind in ('guardcost','review','count','topcost','source'):
            scenario,branch=self.pair();call=self.applied_call(branch);result=call['output']
            if kind=='guardcost':result['evidence']['intent']['guard_output']['evidence']['guard']['proposal']['evidence']['planned_expected_cost']+=1
            elif kind=='review':result['next_review_at']+=1
            elif kind=='count':result['send'].pop();result['evidence']['sent_hospitals'].pop()
            elif kind=='topcost':result['evidence']['planned_expected_cost']+=1
            else:result['evidence']['intent']['source_guard_sha256']='0'*64
            self.sync(branch,call);self.reject(scenario,branch)

    def test_actual_private_information_or_original_event_ledger_forgery_is_rejected(self):
        for kind in ('private','pending','future','prefix'):
            scenario,branch=self.pair();call=self.plan_call(branch)
            if kind=='private':call['inputs']['public_context']['workers']=1
            elif kind=='pending':call['inputs']['pending'].pop()
            elif kind=='future':call['inputs']['observations'].append(dict(hospital='H01',patient='future',
                submitted_at=call['inputs']['now'],observed_at=call['inputs']['now']+1,outcome='accept'))
            else:call['db_event_prefix_max']=-1
            self.reject(scenario,branch)

    def test_actual_FIFO_workflow_response_service_and_wait_work_accounting_still_checked(self):
        for kind in ('service','outcome','wait','work'):
            scenario,branch=self.pair()
            if kind=='service':next(t for t in branch['trace'] if t['type']=='review_started')['service_seconds']+=1
            elif kind=='outcome':next(t for t in branch['trace'] if t['type']=='reply_accept')['type']='reply_decline'
            elif kind=='wait':branch['metrics']['selection_wait_capped_mean']+=1
            else:branch['metrics']['review_work_seconds']+=1
            self.reject(scenario,branch)

    def test_extra_public_coordination_input_cannot_be_described_as_record_only(self):
        scenario,branch=self.pair();self.assertEqual(branch['instrumentation'],audit.NEW_INSTRUMENTATION)
        branch['instrumentation']['record_only']=True
        self.reject(scenario,branch)


if __name__=='__main__':unittest.main()
