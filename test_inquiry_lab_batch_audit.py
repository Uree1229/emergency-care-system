"""Independent distribution arithmetic and immutable full-raw mutations.

Only synthetic unit inputs and already saved first-attempt fixtures are used.
These tests do not run a performance scenario or import any policy/kernel.
"""
from copy import deepcopy
import gzip
import hashlib
import inspect
from itertools import product
import json
import math
from pathlib import Path
import subprocess
import sys
import unittest

from inquiry_lab import batch_audit as audit

ROOT=Path(__file__).resolve().parent
OLD_FIXTURE=ROOT/'simulation/fixtures/eat_residual_20261020/mix7.json.gz'
OLD_COMPRESSED_SHA256='92e3ac5dcf65859e68c1f4756e5b1067d05f9740e94c37c90a33ca3e6b1c862f'
OLD_RAW_SHA256='643b8d83777d0f6ed71932e629ec46e9657938ce07d86bdbb4409f28e2179b96'
FIXTURE=ROOT/'simulation/fixtures/eat_batch_20261020/mix7.json.gz'
COMPRESSED_SHA256='b7990010b28e7dda861c2ac62c2e13f66299ad0d080a8e6620f6beec0ce5f55c'
RAW_SHA256='4130dfd9a88a083ee4cb2874cf1da488bc2090435f36ef1b400b4cd5237431a7'


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


def output(value):
    """Synthetic evidence for mutation tests; not performance raw or a policy."""
    checked=audit.enumerate_batches(value);model=checked['model'];send=checked['selected']
    if value['max_new']==0:reason='contact_budget'
    elif send:reason='eat_initial' if not value['patient_pending'] else 'eat_hedge'
    elif not value['candidates']:reason='no_candidates'
    elif model['room']<=0:reason='eat_concurrency_cap'
    else:reason='eat_wait'
    plan=[dict(hospital=row['hospital'],travel_seconds=row['travel_seconds'],inquiry_cost=15,
        planned_offset_seconds=0 if row['hospital'] in send else None) for row in model['pool']]
    trace={key:deepcopy(checked[key]) for key in ('subsets','maximum_subset_size','minimum_cost','optimal_subset','optimal_cost',
        'selected','selected_cost','forced','safety_hospital')}
    trace.update(schema_version=1,rule=audit.RULE,room=model['room'],
        pool=[row['hospital'] for row in model['pool']],evaluated_count=len(checked['subsets']))
    base=audit.expected_first_accept_cost(model['current'],model['cells'],model['horizon'])
    evidence=dict(policy=audit.NAME,sent_hospitals=list(send),candidate_plan=plan,batch=trace,
        residual_queue=dict(schema_version=1,rule='head_residual_plus_full_followers',
            hospitals=deepcopy(model['residual_rows'])),horizon_seconds=model['horizon'],lambda_seconds=15,
        congestion_aware=False,own_waiting_count=model['fresh'],
        own_observation_timed_out_count=len(value['patient_pending'])-model['fresh'],
        expected_cost_without_new=round(base,3),planned_expected_cost=round(checked['selected_cost'],3))
    return dict(send=list(send),reason=reason,evidence=evidence,next_review_at=value['now']+15)


def load_fixture(path,compressed_sha,raw_sha):
    before=path.stat();compressed=path.read_bytes();after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) \
            or hashlib.sha256(compressed).hexdigest()!=compressed_sha:
        raise AssertionError('saved fixture compressed bytes changed')
    raw=gzip.decompress(compressed)
    if hashlib.sha256(raw).hexdigest()!=raw_sha:raise AssertionError('saved fixture raw bytes changed')
    return json.loads(raw)


class BatchIndependentMathTests(unittest.TestCase):
    def test_empty_distribution_and_hand_calculated_single_hospital(self):
        self.assertEqual(audit.expected_first_accept_cost([],2,10),1510)
        self.assertEqual(audit.expected_first_accept_cost([(10,[.25,.25])],2,10),762.5)

    def test_disjoint_formula_matches_complete_discrete_outcome_enumeration(self):
        items=[(30,[.1,.4]),(10,[.4,.2]),(10,[.25,.1])]
        expected=0.
        # Independently enumerate 3^3 outcomes, including no reply in horizon.
        for outcomes in product((0,1,None),repeat=len(items)):
            probability=1.;accepted=[]
            for index,when in enumerate(outcomes):
                travel,masses=items[index]
                probability*=1-sum(masses) if when is None else masses[when]
                if when is not None:accepted.append((when,travel))
            if accepted:
                when,travel=min(accepted);cost=(when+.5)*5+travel
            else:cost=1510
            expected+=probability*cost
        self.assertAlmostEqual(audit.expected_first_accept_cost(items,2,10),expected,places=12)
        self.assertAlmostEqual(audit.expected_first_accept_cost(list(reversed(items)),2,10),expected,places=12)

    def test_invalid_probability_or_clock_grid_rejected(self):
        for items,cells,horizon in (([(2,[1.1])],1,5),([(2,[-.1])],1,5),
                ([(2,[float('nan')])],1,5),([(2,[.2])],2,5),([],False,5),([],1,-1)):
            with self.assertRaises(audit.BatchAuditError):audit.expected_first_accept_cost(items,cells,horizon)

    def test_acceptance_at_first_bin_stops_other_hospitals_and_failure_cost(self):
        # Certain same-bin replies select the nearer hospital. A later reply
        # cannot replace it under the unchanged immediate-confirmation model.
        self.assertEqual(audit.expected_first_accept_cost([(40,[1.,0.]),(10,[1.,0.]),
            (0,[0.,1.])],2,10),12.5)

    def test_capacity_one_still_compares_every_single_and_empty_batch(self):
        value=inputs();add_own(value,[10,10,10]);checked=audit.enumerate_batches(value)
        self.assertEqual(checked['model']['room'],1);self.assertEqual(checked['maximum_subset_size'],1)
        self.assertEqual(len(checked['subsets']),7)
        minimum=min(row['cost'] for row in checked['subsets'])
        self.assertLessEqual(checked['optimal_cost'],minimum+1e-9)

    def test_all_57_current_subsets_have_capacity_and_original_input_order(self):
        value=inputs();value['candidates'][0],value['candidates'][5]=value['candidates'][5],value['candidates'][0]
        checked=audit.enumerate_batches(value);rows=checked['subsets'];pool=[row['hospital'] for row in checked['model']['pool']]
        self.assertEqual(len(rows),57);self.assertEqual(rows[0]['hospitals'],[])
        self.assertEqual([row['hospitals'][0] for row in rows if len(row['hospitals'])==1],pool)
        for row in rows:
            self.assertLessEqual(len(row['hospitals']),4)
            self.assertEqual(row['hospitals'],[h for h in pool if h in row['hospitals']])
        self.assertEqual(sum(row['selected_before_safety'] for row in rows),1)
        self.assertLessEqual(checked['optimal_cost'],min(row['cost'] for row in rows)+1e-9)

    def test_inclusive_180_fresh_and_stale_181_room(self):
        value=inputs();add_own(value,[180,180,180,180]);checked=audit.enumerate_batches(value)
        self.assertEqual(checked['model']['fresh'],4);self.assertEqual(checked['model']['room'],0)
        self.assertEqual(len(checked['subsets']),1);self.assertEqual(checked['selected'],[])
        value['now']+=1;checked=audit.enumerate_batches(value)
        self.assertEqual(checked['model']['fresh'],0);self.assertEqual(checked['model']['room'],4)
        self.assertEqual(len(checked['subsets']),57)

    def test_contact_budget_and_small_pool_bound_enumeration(self):
        value=inputs();value['max_new']=2;checked=audit.enumerate_batches(value)
        self.assertEqual(len(checked['model']['pool']),2);self.assertEqual(len(checked['subsets']),4)
        value['max_new']=0;checked=audit.enumerate_batches(value)
        self.assertEqual(checked['model']['pool'],[]);self.assertEqual(checked['selected'],[])
        self.assertEqual(len(checked['subsets']),1)
        value=inputs();value['candidates']=[]
        self.assertEqual(audit.enumerate_batches(value)['subsets'][0]['hospitals'],[])

    def test_global_epsilon_tie_is_not_a_nontransitive_sequential_update(self):
        rows=[dict(hospitals=[],cost=10+1.5e-9),dict(hospitals=['H1'],cost=10+.8e-9),
            dict(hospitals=['H2'],cost=10)]
        index,minimum=audit.select_near_minimum(rows)
        self.assertEqual(minimum,10);self.assertEqual(index,1)
        rows[0]['cost']=10+.5e-9
        self.assertEqual(audit.select_near_minimum(rows)[0],0)
        rows=[dict(hospitals=['H2'],cost=10),dict(hospitals=['H1'],cost=10)]
        self.assertEqual(audit.select_near_minimum(rows)[0],0)

    def test_negative_raw_room_is_preserved_but_only_empty_is_enumerated(self):
        value=inputs();add_own(value,[10]*5);checked=audit.enumerate_batches(value)
        self.assertEqual(checked['model']['room'],-1)
        self.assertEqual(checked['maximum_subset_size'],0)
        self.assertEqual(len(checked['subsets']),1);self.assertEqual(checked['selected'],[])

    def test_safety_single_is_explicit_exception_to_empty_model_optimum(self):
        value=inputs();value['pending']=[]
        for index,row in enumerate(value['candidates']):row['travel']['duration_seconds']=10**9+index
        checked=audit.enumerate_batches(value)
        self.assertEqual(checked['optimal_subset'],[]);self.assertTrue(checked['forced'])
        self.assertEqual(checked['selected'],['H01']);self.assertEqual(checked['safety_hospital'],'H01')
        self.assertGreater(checked['selected_cost'],checked['optimal_cost'])
        self.assertEqual(audit.audit_batch(value,output(value)),57)
        add_own(value,[10]);checked=audit.enumerate_batches(value)
        self.assertEqual(checked['selected'],[]);self.assertFalse(checked['forced'])

    def test_old_pending_cdf_conditioning_and_original_horizon_are_retained(self):
        value=inputs();value['pending']=[];add_own(value,[100]);model=audit.batch_model(value)
        self.assertEqual(model['horizon'],200)
        r,a,mu,sigma=audit.parent._beliefs([],value['now'])('O0')
        f0=audit.parent._cdf(100,mu,sigma);alive=1-f0
        expected=r*alive/(1-r*f0)*a*(audit.parent._cdf(300,mu,sigma)-f0)/alive
        self.assertAlmostEqual(sum(model['current'][0][1]),expected,places=12)
        # Preserve, expose, and do not silently repair the original moving-horizon heuristic.
        value['patient_pending'][0]['submitted_at']=value['now']-20
        value['pending'][0]['submitted_at']=value['now']-20
        value['last_contact_at']=value['now']-10
        self.assertEqual(audit.batch_model(value)['horizon'],280)

    def test_synthetic_current_batch_is_pure_and_has_no_future_hedges(self):
        value=inputs();before=deepcopy(value);result=output(value)
        self.assertEqual(audit.audit_batch(value,result),57);self.assertEqual(value,before)
        self.assertTrue(all(row['planned_offset_seconds'] in (0,None) for row in result['evidence']['candidate_plan']))

    def test_missing_duplicate_reordered_or_corrupt_subset_rejected(self):
        value=inputs()
        for mutation in ('omit','duplicate','reorder','cost','selected_marker','membership'):
            result=output(value);rows=result['evidence']['batch']['subsets']
            if mutation=='omit':rows.pop()
            elif mutation=='duplicate':rows[-1]=deepcopy(rows[0])
            elif mutation=='reorder':rows[1],rows[2]=rows[2],rows[1]
            elif mutation=='cost':rows[3]['cost']+=1
            elif mutation=='selected_marker':rows[0]['selected_before_safety']=not rows[0]['selected_before_safety']
            else:rows[1]['hospitals']=['H08']
            with self.assertRaises(audit.BatchAuditError):audit.audit_batch(value,result)

    def test_changed_optimum_safety_capacity_cost_summary_or_future_plan_rejected(self):
        value=inputs()
        for key,replacement in (('room',3),('optimal_subset',[]),('optimal_cost',0),
                ('selected',[]),('selected_cost',0),('forced',True),('safety_hospital','H08'),('unknown',1)):
            result=output(value);result['evidence']['batch'][key]=replacement
            with self.assertRaises(audit.BatchAuditError):audit.audit_batch(value,result)
        result=output(value);result['evidence']['candidate_plan'][0]['planned_offset_seconds']=15
        with self.assertRaises(audit.BatchAuditError):audit.audit_batch(value,result)
        result=output(value);result['evidence']['planned_expected_cost']+=1
        with self.assertRaises(audit.BatchAuditError):audit.audit_batch(value,result)

    def test_residual_math_and_rejected_shortlist_remain_checked(self):
        value=inputs();result=output(value)
        result['evidence']['residual_queue']['hospitals'][0]['estimated_wait_seconds']+=1
        with self.assertRaises(audit.BatchAuditError):audit.audit_batch(value,result)
        result=output(value);result['evidence']['shortlist']={}
        with self.assertRaises(audit.BatchAuditError):audit.audit_batch(value,result)

    def test_parent_ledger_trace_and_residual_arithmetic_are_sha_bound(self):
        self.assertIs(audit._ledger,audit.residual._ledger)
        self.assertIs(audit._audit_trace_fixed,audit.residual._audit_trace_fixed)
        self.assertEqual(hashlib.sha256(Path(audit.residual.__file__).read_bytes()).hexdigest(),audit.RESIDUAL_AUDITOR_SHA256)

    def test_full_call_ledger_validation_only_changes_policy_evidence_hook(self):
        source=inspect.getsource(audit._audit_calls_batch)
        start=source.index("        if call['name']==NAME:changed+=audit_batch(inputs,output)")
        end=source.index('    for event in inquiry_events:',start)
        original=inspect.getsource(audit.residual._audit_calls_residual)
        original_start=original.index("        if call['name']==NAME:changed+=audit_residual(inputs,output)")
        original_end=original.index('    for event in inquiry_events:',original_start)
        restored=source[:start]+original[original_start:original_end]+source[end:]
        restored=restored.replace('def _audit_calls_batch(','def _audit_calls_residual(',1)
        restored=restored.replace('immediate_subsets_checked=changed','residual_queue_candidates_checked=changed',1)
        self.assertEqual(restored,original)

    def test_clean_import_has_no_producer_server_kernel_or_solver(self):
        code=("import json,sys; import inquiry_lab.batch_audit; print(json.dumps(sorted(n for n in sys.modules "
            "if n.startswith(('server.','simulation.','ortools')) or n in {'inquiry_lab.eat_policy',"
            "'inquiry_lab.residual_policy','inquiry_lab.residual_runner','inquiry_lab.batch_policy',"
            "'inquiry_lab.batch_runner','inquiry_lab.shortlist_policy'})))")
        result=subprocess.check_output([sys.executable,'-B','-c',code],cwd=ROOT,text=True)
        self.assertEqual(json.loads(result),[])


class BatchFrozenReferenceRawTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.raw=load_fixture(OLD_FIXTURE,OLD_COMPRESSED_SHA256,OLD_RAW_SHA256)

    def test_all_five_old_anchors_full_raw_without_mutation(self):
        value=deepcopy(self.raw);before=deepcopy(value);result=audit.audit_replay(value)
        self.assertEqual(result['branches'],5);self.assertTrue(result['passed']);self.assertEqual(value,before)
        self.assertTrue(all(row['immediate_subsets_checked']==0 for row in result['branch_audits']))

    def test_old_residual_cannot_include_new_batch_evidence(self):
        value=deepcopy(self.raw);branch=next(row for row in value['branches'] if row['policy']==audit.RESIDUAL_NAME)
        call=branch['policy_calls'][0];call['output']['evidence']['batch']={}
        decision=next(row for row in branch['db_decisions'] if row['id']==call['db_decision_id'])
        decision['evidence']=deepcopy(call['output']['evidence'])
        with self.assertRaises(audit.BatchAuditError):audit.audit_replay(value)


class BatchActualSavedRawAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw=load_fixture(FIXTURE,COMPRESSED_SHA256,RAW_SHA256)
        if cls.raw['scenario']['seed']!=7 or len(cls.raw['branches'])!=6:
            raise AssertionError('actual first batch fixture identity differs')

    def pair(self):
        raw=deepcopy(self.raw)
        return raw['scenario'],next(row for row in raw['branches'] if row['policy']==audit.NAME)

    def reject(self,scenario,branch):
        with self.assertRaises(audit.BatchAuditError):audit.audit_branch(scenario,branch)

    def call(self,branch):
        return next(row for row in branch['policy_calls'] if len(row['output']['evidence']['batch']['subsets'])>1)

    def sync(self,branch,call):
        decision=next(row for row in branch['db_decisions'] if row['id']==call['db_decision_id'])
        decision.update(evidence=deepcopy(call['output']['evidence']),reason=call['output']['reason'],
            hospital_ids=deepcopy(call['output']['send']))

    def test_actual_six_full_raw_arms_and_every_subset_without_mutation(self):
        value=deepcopy(self.raw);before=deepcopy(value);checked=audit.audit_replay(value)
        self.assertTrue(checked['passed']);self.assertEqual(checked['branches'],6);self.assertEqual(value,before)
        new=next(row for row in checked['branch_audits'] if row['immediate_subsets_checked'])
        self.assertEqual(new['policy_calls'],17);self.assertEqual(new['immediate_subsets_checked'],190)
        self.assertTrue(new['current_batch_enumeration_independently_reconstructed'])
        self.assertFalse(new['optimality_claimed']);self.assertFalse(new['policy_kernel_independently_reexecuted'])

    def test_actual_all_subset_costs_and_selected_minimum_are_independently_rebuilt(self):
        for field in ('cost','hospitals','selected_before_safety'):
            scenario,branch=self.pair();call=self.call(branch)
            row=call['output']['evidence']['batch']['subsets'][1]
            if field=='cost':row[field]+=1
            elif field=='hospitals':row[field]=['nonexistent']
            else:row[field]=not row[field]
            self.sync(branch,call);self.reject(scenario,branch)
        for field in ('minimum_cost','optimal_cost','selected_cost'):
            scenario,branch=self.pair();call=self.call(branch)
            call['output']['evidence']['batch'][field]+=1
            self.sync(branch,call);self.reject(scenario,branch)

    def test_actual_missing_subset_changed_room_or_bad_tie_cannot_hide_in_db_copy(self):
        for kind in ('missing','room','tie'):
            scenario,branch=self.pair();call=self.call(branch);trace=call['output']['evidence']['batch']
            if kind=='missing':trace['subsets'].pop();trace['evaluated_count']-=1
            elif kind=='room':trace['room']+=1;trace['maximum_subset_size']+=1
            else:trace['optimal_subset']=[]
            self.sync(branch,call);self.reject(scenario,branch)

    def test_actual_future_hedge_or_rejected_shortlist_cannot_be_added(self):
        scenario,branch=self.pair();call=self.call(branch)
        call['output']['evidence']['candidate_plan'][0]['planned_offset_seconds']=15
        self.sync(branch,call);self.reject(scenario,branch)
        scenario,branch=self.pair();call=self.call(branch);call['output']['evidence']['shortlist']={}
        self.sync(branch,call);self.reject(scenario,branch)

    def test_actual_residual_queue_math_remains_independently_checked(self):
        scenario,branch=self.pair()
        call=next(row for row in branch['policy_calls']
            if any(r['queue_count'] for r in row['output']['evidence']['residual_queue']['hospitals']))
        record=next(r for r in call['output']['evidence']['residual_queue']['hospitals'] if r['queue_count'])
        record['head_expected_remaining_seconds']+=1
        self.sync(branch,call);self.reject(scenario,branch)

    def test_actual_input_watermark_history_pending_and_private_information_rejected(self):
        for kind in ('private','prefix','future','pending'):
            scenario,branch=self.pair();call=self.call(branch)
            if kind=='private':call['inputs']['workers']=1
            elif kind=='prefix':call['db_event_prefix_max']=-1
            elif kind=='future':call['inputs']['observations'].append(dict(hospital='H01',patient='future',
                submitted_at=call['inputs']['now'],observed_at=call['inputs']['now']+1,outcome='accept'))
            else:call['inputs']['pending'].append(dict(hospital='H01',patient='invented',submitted_at=call['inputs']['now']))
            self.reject(scenario,branch)

    def test_actual_fifo_service_outcome_wait_and_work_checks_are_not_bypassed(self):
        for kind in ('service','outcome','wait','work'):
            scenario,branch=self.pair()
            if kind=='service':next(row for row in branch['trace'] if row['type']=='review_started')['service_seconds']+=1
            elif kind=='outcome':next(row for row in branch['trace'] if row['type']=='reply_accept')['type']='reply_decline'
            elif kind=='wait':branch['metrics']['selection_wait_capped_mean']+=1
            else:branch['metrics']['review_work_seconds']+=1
            self.reject(scenario,branch)

    def test_actual_first_six_order_and_declared_final_send_cannot_be_altered(self):
        scenario,branch=self.pair();call=self.call(branch)
        call['output']['evidence']['candidate_plan'].reverse()
        self.sync(branch,call);self.reject(scenario,branch)
        scenario,branch=self.pair();call=self.call(branch)
        call['output']['send']=[];call['output']['evidence']['sent_hospitals']=[]
        self.sync(branch,call);self.reject(scenario,branch)


if __name__=='__main__':unittest.main()
