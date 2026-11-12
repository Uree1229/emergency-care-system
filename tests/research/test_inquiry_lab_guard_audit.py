"""Guard mathematics and saved evidence mutations, without policy execution.

Synthetic proposals below test recorded-plan consistency only. They do not
claim to reproduce the frozen residual search or represent performance data.
"""
from copy import deepcopy
import gzip
import hashlib
import inspect
import json
import math
from pathlib import Path
import subprocess
import sys
import unittest

from inquiry_lab import guard_audit as audit

ROOT=Path(__file__).resolve().parents[2]
OLD_FIXTURE=ROOT/'simulation/fixtures/eat_batch_20261020/mix7.json.gz'
OLD_COMPRESSED_SHA256='b7990010b28e7dda861c2ac62c2e13f66299ad0d080a8e6620f6beec0ce5f55c'
OLD_RAW_SHA256='4130dfd9a88a083ee4cb2874cf1da488bc2090435f36ef1b400b4cd5237431a7'
FIXTURE=ROOT/'simulation/fixtures/eat_guard_20261020/mix7.json.gz'
COMPRESSED_SHA256='541760d9797dd53b65cff243d2fa1d20d948c4b70eb3966b3aa5dff0d6a8bef1'
RAW_SHA256='9a2552dffb41e161c5dc8ff1936b1db22fbe6d24a2c0ba4827453869360a97fc'


def inputs():
    now=audit.EPOCH+200
    return dict(now=now,candidates=[dict(hospital_id=f'H{i:02d}',travel=dict(duration_seconds=100+i))
        for i in range(1,9)],observations=[],pending=[
            dict(hospital='H01',patient='Q1',submitted_at=now-100),
            dict(hospital='H01',patient='Q2',submitted_at=now-40)],patient_pending=[],
        max_new=10,last_contact_at=None)


def add_own(value,ages):
    for index,age in enumerate(ages):
        row=dict(hospital=f'O{index}',patient='P',submitted_at=value['now']-age)
        value['pending'].append(row)
        value['patient_pending'].append(dict(row,travel_seconds=120+index))


def proposal(value,offsets=None):
    model=audit.batch.batch_model(value);rows=[];beliefs=audit.parent._beliefs(value['observations'],value['now'])
    offsets=offsets or [0,0,0,0,15,15];chosen=[]
    for item,offset in zip(model['pool'],offsets):
        hid=item['hospital'];r,a,_,_=beliefs(hid)
        rows.append(dict(hospital=hid,planned_offset_seconds=offset,inquiry_cost=15,
            reply_probability=round(r,4),accept_probability=round(a,4),
            queue_length=sum(p['hospital']==hid for p in value['pending']),travel_seconds=item['travel_seconds']))
        if offset is not None:chosen.append((offset,hid))
    items=list(model['current'])
    for offset,hid in chosen:
        travel,masses=model['items'][hid];shift=offset//5
        items.append((travel,[0.]*shift+masses[:model['cells']-shift] if shift else list(masses)))
    cost=audit.batch.expected_first_accept_cost(items,model['cells'],model['horizon'])
    for offset,_ in chosen:
        cost+=15*(math.prod(max(0.,1-sum(m[:offset//5])) for _,m in items) if offset else 1.)
    base=audit.batch.expected_first_accept_cost(model['current'],model['cells'],model['horizon'])
    send=[hid for offset,hid in chosen if offset==0]
    if value['max_new']==0:reason='contact_budget'
    elif send:reason='eat_initial' if not value['patient_pending'] else 'eat_hedge'
    elif not value['candidates']:reason='no_candidates'
    elif model['room']<=0:reason='eat_concurrency_cap'
    else:reason='eat_wait'
    evidence=dict(evaluated_at=value['now'],policy=audit.RESIDUAL_NAME,horizon_seconds=model['horizon'],
        lambda_seconds=15,congestion_aware=False,expected_cost_without_new=round(base,3),
        planned_expected_cost=round(cost,3),reply_probability_global=.79,accept_probability_global=.4,
        service_median_seconds=66.,history_used_count=len(value['observations']),
        own_waiting_count=model['fresh'],own_observation_timed_out_count=len(value['patient_pending'])-model['fresh'],
        unresolved_inquiry_count=len(value['pending']),candidate_plan=rows,sent_hospitals=list(send),
        residual_queue=dict(schema_version=1,rule='head_residual_plus_full_followers',hospitals=model['residual_rows']))
    return dict(send=send,reason=reason,evidence=evidence,next_review_at=value['now']+15)


def utility(value):
    # Only unit evidence construction. The audit independently rebuilds costs.
    result=proposal(value,[None]*6);checked=audit.batch.enumerate_batches(value);model=checked['model']
    send=checked['selected'];result['send']=list(send);evidence=result['evidence']
    evidence['policy']=audit.BATCH_NAME;evidence['sent_hospitals']=list(send)
    evidence['planned_expected_cost']=round(checked['selected_cost'],3)
    for row in evidence['candidate_plan']:row['planned_offset_seconds']=0 if row['hospital'] in send else None
    if value['max_new']==0:result['reason']='contact_budget'
    elif send:result['reason']='eat_initial' if not value['patient_pending'] else 'eat_hedge'
    elif not value['candidates']:result['reason']='no_candidates'
    elif model['room']<=0:result['reason']='eat_concurrency_cap'
    else:result['reason']='eat_wait'
    trace={key:deepcopy(checked[key]) for key in ('maximum_subset_size','subsets','minimum_cost',
        'optimal_subset','optimal_cost','selected','selected_cost','forced','safety_hospital')}
    trace.update(schema_version=1,rule=audit.batch.RULE,room=model['room'],
        pool=[r['hospital'] for r in model['pool']],evaluated_count=len(checked['subsets']))
    evidence['batch']=trace
    return result


def output(value,original=None):
    original=deepcopy(original) if original is not None else proposal(value)
    model=audit.proposal_plan(value,original)
    checks,conflicts,full,trigger=audit.imminent_conflicts(value,original,model)
    count=len(original['send']);evaluated=trigger and count>0
    metadata=dict(schema_version=1,rule=audit.RULE,source_sha256=dict(audit.SOURCE_SHA256),
        proposal=original,review_window_seconds=15,max_concurrent=4,fresh_own_count=model['fresh'],
        proposal_send_count=count,current_planned_fresh_count=model['fresh']+count,
        currently_full=full,future_checks=checks,conflict_offsets_seconds=conflicts,triggered=trigger,
        batch_evaluated=evaluated,evaluation_skip_reason=None if evaluated else 'zero_cardinality' if trigger else 'not_triggered',
        batch_output=None,same_count_costs=[],same_count_minimum_cost=None,best_same_count_hospitals=None,
        best_same_count_cost=None,proposal_immediate_cost=None,improvement_seconds=None,applied=False,
        output_cost_scope='original_residual_future_plan')
    result=deepcopy(original);result['evidence']['policy']=audit.NAME
    if evaluated:
        item=utility(value)
        rows=[dict(hospitals=r['hospitals'],cost=r['cost']) for r in item['evidence']['batch']['subsets']
            if len(r['hospitals'])==count]
        minimum=min(r['cost'] for r in rows);best=next(r for r in rows if r['cost']<=minimum+1e-9)
        prior=next(r for r in rows if r['hospitals']==original['send'])
        improvement=prior['cost']-best['cost'];applied=improvement>1e-9
        metadata.update(batch_output=item,same_count_costs=rows,same_count_minimum_cost=minimum,
            best_same_count_hospitals=best['hospitals'],best_same_count_cost=best['cost'],
            proposal_immediate_cost=prior['cost'],improvement_seconds=improvement,applied=applied)
        if applied:
            result['send']=list(best['hospitals']);result['evidence']['sent_hospitals']=list(best['hospitals'])
            result['evidence']['planned_expected_cost']=round(best['cost'],3)
            for row in result['evidence']['candidate_plan']:
                row['planned_offset_seconds']=0 if row['hospital'] in best['hospitals'] else None
            metadata['output_cost_scope']='immediate_same_count_batch'
    result['evidence']['guard']=metadata
    return result


def load_fixture(path,compressed_sha,raw_sha):
    before=path.stat();compressed=path.read_bytes();after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) \
            or hashlib.sha256(compressed).hexdigest()!=compressed_sha:
        raise AssertionError('saved fixture compressed bytes changed')
    raw=gzip.decompress(compressed)
    if hashlib.sha256(raw).hexdigest()!=raw_sha:raise AssertionError('saved fixture raw bytes changed')
    return json.loads(raw)


class GuardIndependentMathTests(unittest.TestCase):
    def test_legal_recorded_plan_and_future_contact_cost_reconstructed_without_search(self):
        value=inputs();result=proposal(value);before=deepcopy(result)
        model=audit.proposal_plan(value,result)
        self.assertEqual(model['fresh'],0);self.assertEqual(result,before)
        result['evidence']['planned_expected_cost']+=1
        with self.assertRaises(audit.GuardAuditError):audit.proposal_plan(value,result)

    def test_imminent_full_conflict_swaps_only_same_count_membership(self):
        value=inputs();result=output(value);guard=result['evidence']['guard']
        self.assertTrue(guard['triggered']);self.assertTrue(guard['applied'])
        self.assertEqual(guard['conflict_offsets_seconds'],[15]);self.assertEqual(guard['future_checks'][0]['total_fresh_count'],6)
        self.assertEqual(len(result['send']),4);self.assertNotEqual(result['send'],guard['proposal']['send'])
        self.assertEqual(len(guard['same_count_costs']),15)
        self.assertEqual(audit.audit_guard(value,result),dict(utility_subsets_checked=57,guard_triggered=1,
            guard_batch_evaluated=1,guard_applied=1))

    def test_beyond_next_review_future_plan_is_preserved_and_inactive(self):
        value=inputs();result=output(value,proposal(value,[0,0,0,0,120,180]))
        guard=result['evidence']['guard'];self.assertEqual(guard['future_checks'],[])
        self.assertFalse(guard['triggered']);self.assertIsNone(guard['batch_output'])
        self.assertEqual(result['evidence']['candidate_plan'],guard['proposal']['evidence']['candidate_plan'])
        self.assertEqual(audit.audit_guard(value,result)['guard_applied'],0)

    def test_nonfull_current_plan_does_not_trigger_despite_future_overflow(self):
        value=inputs();result=output(value,proposal(value,[0,0,0,15,15,15]));guard=result['evidence']['guard']
        self.assertFalse(guard['currently_full']);self.assertTrue(guard['future_checks'][0]['conflict'])
        self.assertFalse(guard['triggered']);self.assertIsNone(guard['batch_output'])
        self.assertEqual(audit.audit_guard(value,result)['guard_batch_evaluated'],0)

    def test_inclusive_180_at_imminent_send_and_expiry_before_it(self):
        value=inputs();add_own(value,[165]);original=proposal(value,[0,0,0,15,None,None])
        result=output(value,original);self.assertTrue(result['evidence']['guard']['triggered'])
        self.assertEqual(result['evidence']['guard']['future_checks'][0]['existing_fresh_hospitals'],['O0'])
        value['now']+=1;original=proposal(value,[0,0,0,15,None,None]);result=output(value,original)
        self.assertTrue(result['evidence']['guard']['currently_full']);self.assertFalse(result['evidence']['guard']['triggered'])
        self.assertEqual(result['evidence']['guard']['future_checks'][0]['total_fresh_count'],4)
        audit.audit_guard(value,result)

    def test_zero_cardinality_trigger_skips_utility_without_changing_proposal(self):
        value=inputs();add_own(value,[10]*4);result=output(value,proposal(value,[15,None,None,None,None,None]))
        guard=result['evidence']['guard'];self.assertTrue(guard['triggered']);self.assertFalse(guard['batch_evaluated'])
        self.assertEqual(guard['evaluation_skip_reason'],'zero_cardinality');self.assertIsNone(guard['batch_output'])
        self.assertEqual(result['send'],[]);self.assertEqual(audit.audit_guard(value,result)['guard_applied'],0)

    def test_no_strict_improvement_preserves_original_future_plan(self):
        value=inputs();initial=output(value);best=initial['evidence']['guard']['best_same_count_hospitals']
        original=proposal(value,[0 if f'H{i:02d}' in best else 15 for i in range(1,7)])
        result=output(value,original);guard=result['evidence']['guard']
        self.assertTrue(guard['triggered']);self.assertFalse(guard['applied']);self.assertEqual(guard['improvement_seconds'],0)
        self.assertEqual(result['evidence']['candidate_plan'],original['evidence']['candidate_plan'])
        audit.audit_guard(value,result)

    def test_original_proposal_pool_order_plan_offsets_and_plan_send_relation_checked(self):
        value=inputs()
        for kind in ('cost','pool','offset','send','belief'):
            original=proposal(value)
            if kind=='cost':original['evidence']['planned_expected_cost']+=1
            elif kind=='pool':original['evidence']['candidate_plan'].reverse()
            elif kind=='offset':original['evidence']['candidate_plan'][4]['planned_offset_seconds']=5
            elif kind=='send':original['send'].pop();original['evidence']['sent_hospitals'].pop()
            else:original['evidence']['candidate_plan'][0]['reply_probability']=0
            with self.assertRaises(audit.GuardAuditError):audit.proposal_plan(value,original)

    def test_hidden_source_trigger_counts_and_window_mutations_rejected(self):
        value=inputs()
        for key,replacement in (('review_window_seconds',120),('max_concurrent',5),('currently_full',False),
                ('current_planned_fresh_count',3),('triggered',False),('batch_evaluated',False),
                ('source_sha256',{}),('conflict_offsets_seconds',[]),('extra_field',1)):
            result=output(value);result['evidence']['guard'][key]=replacement
            with self.assertRaises(audit.GuardAuditError):audit.audit_guard(value,result)
        result=output(value);result['evidence']['guard']['future_checks'][0]['total_fresh_count']-=1
        with self.assertRaises(audit.GuardAuditError):audit.audit_guard(value,result)

    def test_strict_improvement_cannot_be_suppressed_or_hidden_in_unknown_proposal_fields(self):
        value=inputs();result=output(value);self.assertTrue(result['evidence']['guard']['applied'])
        result['evidence']['guard']['applied']=False
        with self.assertRaises(audit.GuardAuditError):audit.audit_guard(value,result)
        original=proposal(value);original['evidence']['private_new_constraint']=1
        with self.assertRaises(audit.GuardAuditError):audit.proposal_plan(value,original)

    def test_full_batch_table_and_same_count_comparison_cannot_be_tampered(self):
        value=inputs()
        for kind in ('table','filtered','minimum','best','improvement'):
            result=output(value);guard=result['evidence']['guard']
            if kind=='table':guard['batch_output']['evidence']['batch']['subsets'][1]['cost']+=1
            elif kind=='filtered':guard['same_count_costs'].pop()
            elif kind=='minimum':guard['same_count_minimum_cost']+=1
            elif kind=='best':guard['best_same_count_hospitals']=[]
            else:guard['improvement_seconds']+=1
            with self.assertRaises(audit.GuardAuditError):audit.audit_guard(value,result)

    def test_execution_cardinality_review_core_and_cost_scope_are_preserved(self):
        value=inputs()
        for kind in ('count','review','core','scope','final_cost','future'):
            result=output(value)
            if kind=='count':result['send'].pop();result['evidence']['sent_hospitals'].pop()
            elif kind=='review':result['next_review_at']+=1
            elif kind=='core':result['evidence']['residual_queue']['hospitals'][0]['estimated_wait_seconds']+=1
            elif kind=='scope':result['evidence']['guard']['output_cost_scope']='original_residual_future_plan'
            elif kind=='final_cost':result['evidence']['planned_expected_cost']+=1
            else:result['evidence']['candidate_plan'][0]['planned_offset_seconds']=15
            with self.assertRaises(audit.GuardAuditError):audit.audit_guard(value,result)

    def test_inactive_and_zero_cardinality_cannot_fabricate_utility(self):
        value=inputs();result=output(value,proposal(value,[0,0,0,0,120,180]))
        result['evidence']['guard']['batch_output']={}
        with self.assertRaises(audit.GuardAuditError):audit.audit_guard(value,result)
        result=output(value,proposal(value,[0,0,0,0,120,180]))
        result['evidence']['guard']['applied']=True
        with self.assertRaises(audit.GuardAuditError):audit.audit_guard(value,result)

    def test_auditor_foundations_and_frozen_policy_bytes_are_sha_bound_without_import(self):
        self.assertEqual(hashlib.sha256(Path(audit.batch.__file__).read_bytes()).hexdigest(),audit.BATCH_AUDITOR_SHA256)
        for name,expected in audit.SOURCE_SHA256.items():
            self.assertEqual(hashlib.sha256((ROOT/'inquiry_lab'/(name+'.py')).read_bytes()).hexdigest(),expected)
        self.assertIs(audit._ledger,audit.batch._ledger);self.assertIs(audit._audit_trace_fixed,audit.batch._audit_trace_fixed)

    def test_full_call_ledger_validation_only_changes_evidence_hook_and_counters(self):
        source=inspect.getsource(audit._audit_calls_guard)
        start=source.index("        if call['name']==NAME:changed.update(audit_guard(inputs,output))")
        end=source.index('    for event in inquiry_events:',start)
        original=inspect.getsource(audit.batch._audit_calls_batch)
        original_start=original.index("        if call['name']==NAME:changed+=audit_batch(inputs,output)")
        original_end=original.index('    for event in inquiry_events:',original_start)
        restored=source[:start]+original[original_start:original_end]+source[end:]
        restored=restored.replace('def _audit_calls_guard(','def _audit_calls_batch(',1)
        restored=restored.replace('changed=Counter()','changed=0',1)
        final=restored.index('    return dict(policy_calls=len(calls),**')
        restored=restored[:final]+'    return dict(policy_calls=len(calls),immediate_subsets_checked=changed)\n'
        self.assertEqual(restored,original)

    def test_clean_import_has_no_producer_server_kernel_or_solver(self):
        code=("import json,sys; import inquiry_lab.guard_audit; print(json.dumps(sorted(n for n in sys.modules "
            "if n.startswith(('server.','simulation.','ortools')) or n in {'inquiry_lab.eat_policy',"
            "'inquiry_lab.residual_policy','inquiry_lab.residual_runner','inquiry_lab.batch_policy',"
            "'inquiry_lab.batch_runner','inquiry_lab.guard_policy','inquiry_lab.guard_runner'})))")
        result=subprocess.check_output([sys.executable,'-B','-c',code],cwd=ROOT,text=True)
        self.assertEqual(json.loads(result),[])


class GuardFrozenReferenceRawTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.raw=load_fixture(OLD_FIXTURE,OLD_COMPRESSED_SHA256,OLD_RAW_SHA256)

    def test_six_old_raw_anchors_including_rejected_batch_remain_fully_audited(self):
        value=deepcopy(self.raw);before=deepcopy(value);checked=audit.audit_replay(value)
        self.assertTrue(checked['passed']);self.assertEqual(checked['branches'],6);self.assertEqual(value,before)
        self.assertTrue(all(row['guard_triggered']==0 for row in checked['branch_audits']))

    def test_old_references_cannot_contain_new_guard_evidence(self):
        for name in (audit.RESIDUAL_NAME,audit.BATCH_NAME,audit.BASE_POLICY):
            value=deepcopy(self.raw);branch=next(row for row in value['branches'] if row['policy']==name)
            call=branch['policy_calls'][0];call['output']['evidence']['guard']={}
            decision=next(row for row in branch['db_decisions'] if row['id']==call['db_decision_id'])
            decision['evidence']=deepcopy(call['output']['evidence'])
            with self.assertRaises(audit.GuardAuditError):audit.audit_replay(value)


class GuardActualSavedRawAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw=load_fixture(FIXTURE,COMPRESSED_SHA256,RAW_SHA256)
        if cls.raw['scenario']['seed']!=7 or len(cls.raw['branches'])!=6:
            raise AssertionError('actual first guard fixture identity differs')

    def pair(self):
        value=deepcopy(self.raw)
        return value['scenario'],next(row for row in value['branches'] if row['policy']==audit.NAME)

    def call(self,branch):
        return next(row for row in branch['policy_calls'] if row['output']['evidence']['guard']['applied'])

    def sync(self,branch,call):
        decision=next(row for row in branch['db_decisions'] if row['id']==call['db_decision_id'])
        decision.update(evidence=deepcopy(call['output']['evidence']),reason=call['output']['reason'],
            hospital_ids=deepcopy(call['output']['send']))

    def reject(self,scenario,branch):
        with self.assertRaises(audit.GuardAuditError):audit.audit_branch(scenario,branch)

    def test_six_actual_full_raw_arms_proposals_and_batch_tables_without_mutation(self):
        value=deepcopy(self.raw);before=deepcopy(value);checked=audit.audit_replay(value)
        self.assertTrue(checked['passed']);self.assertEqual(checked['branches'],6);self.assertEqual(value,before)
        result=next(row for row in checked['branch_audits'] if row['guard_triggered'])
        self.assertEqual(result['policy_calls'],17);self.assertEqual(result['guard_triggered'],9)
        self.assertEqual(result['guard_batch_evaluated'],3);self.assertEqual(result['guard_applied'],3)
        self.assertEqual(result['utility_subsets_checked'],171)
        self.assertTrue(result['recorded_proposal_plan_cost_independently_reconstructed'])
        self.assertFalse(result['residual_proposal_search_independently_reexecuted'])
        self.assertFalse(result['optimality_claimed'])

    def test_actual_recorded_residual_plan_cost_and_plan_send_consistency_cannot_be_forged(self):
        for kind in ('cost','offset','send','core'):
            scenario,branch=self.pair();call=self.call(branch);proposal=call['output']['evidence']['guard']['proposal']
            if kind=='cost':proposal['evidence']['planned_expected_cost']+=1
            elif kind=='offset':proposal['evidence']['candidate_plan'][0]['planned_offset_seconds']=5
            elif kind=='send':proposal['send'].pop();proposal['evidence']['sent_hospitals'].pop()
            else:proposal['evidence']['residual_queue']['hospitals'][0]['head_expected_remaining_seconds']+=1
            self.sync(branch,call);self.reject(scenario,branch)

    def test_actual_trigger_full_clock_fresh_timeout_and_next_review_window_checked(self):
        for kind in ('window','full','future','source'):
            scenario,branch=self.pair();call=self.call(branch);guard=call['output']['evidence']['guard']
            if kind=='window':guard['review_window_seconds']=120
            elif kind=='full':guard['currently_full']=False
            elif kind=='future':guard['future_checks'][0]['total_fresh_count']-=1
            else:guard['source_sha256']['batch_policy']='0'*64
            self.sync(branch,call);self.reject(scenario,branch)

    def test_actual_entire_utility_table_same_count_minimum_and_strict_improvement_checked(self):
        for kind in ('allcost','filteredcost','omitted','minimum','best','improvement','applied'):
            scenario,branch=self.pair();call=self.call(branch);guard=call['output']['evidence']['guard']
            if kind=='allcost':guard['batch_output']['evidence']['batch']['subsets'][1]['cost']+=1
            elif kind=='filteredcost':guard['same_count_costs'][0]['cost']+=1
            elif kind=='omitted':guard['same_count_costs'].pop()
            elif kind=='minimum':guard['same_count_minimum_cost']+=1
            elif kind=='best':guard['best_same_count_hospitals']=[]
            elif kind=='improvement':guard['improvement_seconds']+=1
            else:guard['applied']=False
            self.sync(branch,call);self.reject(scenario,branch)

    def test_actual_zero_cardinality_keeps_trigger_but_skips_utility(self):
        scenario,branch=self.pair()
        call=next(row for row in branch['policy_calls'] if row['output']['evidence']['guard']['triggered']
            and not row['output']['evidence']['guard']['batch_evaluated'])
        guard=call['output']['evidence']['guard'];self.assertEqual(guard['proposal_send_count'],0)
        self.assertEqual(guard['evaluation_skip_reason'],'zero_cardinality')
        guard['batch_evaluated']=True;guard['evaluation_skip_reason']=None
        self.sync(branch,call);self.reject(scenario,branch)

    def test_actual_membership_only_count_review_final_cost_and_proposal_core_preserved(self):
        for kind in ('count','review','costscope','finalcost','core','future'):
            scenario,branch=self.pair();call=self.call(branch);result=call['output']
            if kind=='count':result['send'].pop();result['evidence']['sent_hospitals'].pop()
            elif kind=='review':result['next_review_at']+=1
            elif kind=='costscope':result['evidence']['guard']['output_cost_scope']='original_residual_future_plan'
            elif kind=='finalcost':result['evidence']['planned_expected_cost']+=1
            elif kind=='core':result['evidence']['reply_probability_global']+=.1
            else:result['evidence']['candidate_plan'][0]['planned_offset_seconds']=15
            self.sync(branch,call);self.reject(scenario,branch)

    def test_actual_input_prefix_original_ledger_and_private_future_information_rejected(self):
        for kind in ('private','future','pending','prefix'):
            scenario,branch=self.pair();call=self.call(branch)
            if kind=='private':call['inputs']['potentials']={}
            elif kind=='future':call['inputs']['observations'].append(dict(hospital='H01',patient='future',
                submitted_at=call['inputs']['now'],observed_at=call['inputs']['now']+1,outcome='accept'))
            elif kind=='pending':call['inputs']['pending'].append(dict(hospital='H01',patient='fake',submitted_at=call['inputs']['now']))
            else:call['db_event_prefix_max']=-1
            self.reject(scenario,branch)

    def test_actual_workflow_fifo_service_outcome_and_wait_work_accounting_still_checked(self):
        for kind in ('service','outcome','wait','work'):
            scenario,branch=self.pair()
            if kind=='service':next(row for row in branch['trace'] if row['type']=='review_started')['service_seconds']+=1
            elif kind=='outcome':next(row for row in branch['trace'] if row['type']=='reply_accept')['type']='reply_decline'
            elif kind=='wait':branch['metrics']['selection_wait_capped_mean']+=1
            else:branch['metrics']['review_work_seconds']+=1
            self.reject(scenario,branch)

    def test_actual_inactive_guard_cannot_fabricate_batch_or_change_original_timing(self):
        scenario,branch=self.pair()
        call=next(row for row in branch['policy_calls'] if not row['output']['evidence']['guard']['triggered'])
        call['output']['evidence']['guard']['batch_output']={}
        self.sync(branch,call);self.reject(scenario,branch)


if __name__=='__main__':unittest.main()
