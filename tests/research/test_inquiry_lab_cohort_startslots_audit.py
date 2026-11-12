"""Independent public slot flow, marginal decisions and current-only audit."""
from copy import deepcopy
from functools import lru_cache
from itertools import permutations
from pathlib import Path
import math
import subprocess
import sys
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_startslots_audit as audit
from test_inquiry_lab_cohort_startslots_policy import initial_fixture,fixture as copied_fixture,invoke,_initial_fixture
from test_inquiry_lab_cohort_startslots_model import fixture as model_fixture
import test_inquiry_lab_cohort_tailwait_audit as prior

ROOT=Path(__file__).resolve().parents[2]


def flow(costs,caps=None,order=None):
    hospitals=['H'+str(i) for i in range(len(costs[0]))]
    edges=[dict(owner_index=i,hospital=h,total_cost_seconds=v) for i,r in enumerate(costs) for h,v in zip(hospitals,r) if v is not None]
    return audit.cover_slots(edges,owner_count=len(costs),hospital_capacities=caps or [dict(hospital=h,capacity=1) for h in hospitals],owner_order=list(range(len(costs))) if order is None else order)


@lru_cache(maxsize=6)
def _fixture(kind='matching'):
    if kind=='matching':args=initial_fixture()
    elif kind=='no_cover':args=initial_fixture(count=4,hospitals=3)
    elif kind=='pending':args=deepcopy(_initial_fixture(extras=1,statuses=True))
    elif kind=='one_tail':args=initial_fixture(count=3)
    elif kind=='no_tail':args=initial_fixture(count=2)
    else:raise ValueError(kind)
    result=invoke(args);j,ms,ts,p,owners,_=args
    return j,ms,ts,p,owners,result['decisions'],result['evidence']


def fixture(kind='matching'):return deepcopy(_fixture(kind))


def check(data):
    j,ms,ts,p,owners,d,e=data
    return audit.audit_pair(ms,j,ts,d,e,p,owners)


class StartSlotIndependentAuditTests(unittest.TestCase):
    def test_flat_residual_flow_reverses_earlier_assignment_and_matches_enumeration(self):
        proof=flow([[1.,2.],[1.,100.]])
        self.assertEqual(proof['total_edge_cost_seconds'],3.);self.assertTrue(any(e['reverse'] for p in proof['augmentations'] for e in p['path']))
        costs=[[5.,1.,8.],[2.,6.,4.],[3.,7.,2.]];proof=flow(costs)
        self.assertEqual(proof['total_edge_cost_seconds'],min(sum(costs[i][j] for i,j in enumerate(p)) for p in permutations(range(3))))
        self.assertTrue(proof['residual_edges_above_negative_epsilon']);self.assertFalse(proof['exact_minimum_cost_certificate'])
        self.assertEqual(proof['maximum_simple_cycle_epsilon_allowance_seconds'],len(proof['node_order'])*1e-9)

    def test_partial_coverage_public_capacity_and_priority_native_ties(self):
        partial=flow([[1.,None],[2.,None],[3.,None]])
        self.assertEqual(partial['flow'],1);self.assertFalse(partial['complete'])
        proof=flow([[1.],[1.]],caps=[dict(hospital='H0',capacity=2)],order=[1,0])
        self.assertTrue(proof['complete']);self.assertEqual(proof['augmentations'][0]['path'][0]['owner_index'],1)
        self.assertEqual(flow([[1.,1.],[1.,1.]]),flow([[1.,1.],[1.,1.]]))

    def test_all_edges_capacity_types_and_nonfinite_inputs_rejected(self):
        args=dict(owner_count=1,hospital_capacities=[dict(hospital='H0',capacity=1)],owner_order=[0])
        for edges in ([dict(owner_index=True,hospital='H0',total_cost_seconds=1.)],
                [dict(owner_index=0,hospital='H0',total_cost_seconds=float('nan'))],
                [dict(owner_index=0,hospital='H0',total_cost_seconds=True)],
                [dict(owner_index=0,hospital='H0',total_cost_seconds=1.)]*2):
            with self.assertRaises(ValueError):audit.cover_slots(edges,**args)
        with self.assertRaises(ValueError):audit.cover_slots([],**dict(args,owner_order=[True]))
        with self.assertRaises(ValueError):audit.cover_slots([],**dict(args,hospital_capacities=[dict(hospital='H0',capacity=True)]))

    def test_independent_flow_trace_matches_producer_with_reverse_and_epsilon_ties(self):
        from inquiry_lab import cohort_startslots_model as producer
        for costs in ([[1.,2.],[1.,100.]],[[0.,-2.],[-3.,0.]],[[1.,1.+5e-10],[1.+5e-10,1.]],[[1.,None],[2.,None]]):
            hospitals=['H'+str(i) for i in range(len(costs[0]))]
            edges=[dict(owner_index=i,hospital=h,total_cost_seconds=v) for i,r in enumerate(costs) for h,v in zip(hospitals,r) if v is not None]
            args=dict(owner_count=len(costs),hospital_capacities=[dict(hospital=h,capacity=1) for h in hospitals],owner_order=list(range(len(costs))))
            self.assertEqual(audit.cover_slots(edges,**args),producer.cover_slots(edges,**args))

    def test_public_H_edges_models_extras_and_terminal_pass_rebuilt(self):
        from inquiry_lab import cohort_startslots_model as producer
        members,proposals,p=model_fixture();before=deepcopy((members,proposals,p))
        independent=audit.allocate_slots(members,baseline_proposals=proposals,priority_context=p)
        expected=producer.allocate_slots(members,baseline_proposals=proposals,priority_context=p)
        audit.precise_same(independent,expected,'independent public metric reconstruction')
        self.assertEqual((members,proposals,p),before);self.assertTrue(independent['coverage_complete'])
        self.assertEqual(independent['public_assumed_workers'],1);self.assertEqual(independent['extra_passes'][-1]['selected'],None)
        self.assertTrue(all(r['used']<=r['capacity']==1 for r in independent['hospital_slot_usage']))
        self.assertFalse(independent['original_probability_wait_guards_applicable']);self.assertFalse(independent['remaining_assignments_are_reservations'])

    def test_scope_size_common_start_and_empty_global_queue_are_structural(self):
        ms,_,_=model_fixture();self.assertTrue(audit.scope_members(ms)[0])
        self.assertFalse(audit.scope_members(ms[:3])[0]);self.assertFalse(audit.scope_members(ms*5)[0])
        changed=deepcopy(ms);changed[-1]['workflow_snapshot']['job']['started_at']-=1;self.assertEqual(audit.scope_members(changed)[1],'different_public_workflow_start_clocks')
        changed=deepcopy(ms);changed[0]['inputs']['pending']=[dict(hospital='H0')];self.assertEqual(audit.scope_members(changed)[1],'nonempty_common_public_pending')
        changed=deepcopy(ms);changed[0]['inputs']['last_contact_at']=0;self.assertFalse(audit.scope_members(changed)[0])

    def test_extra_contact_positive_gain_and_cardinality_are_independent_requirements(self):
        # A metric stub exercises only the allocation layer, not a complete H
        # or parent proof. Every real public H/CDF record is checked separately.
        members,proposals,p=model_fixture(hospitals=5)
        models=[dict(owner=i,hospitals=[dict(hospital='H'+str(k),residual_wait_seconds=0.,assumed_workers=1) for k in range(5)]) for i in range(4)]
        for proposal in proposals:proposal['send']=['H0','H1']
        def metric(model,selected,unused):
            value=1500.-sum(100. if h=='H'+str(model['owner']) else 0. for h in selected)
            return dict(total_cost_seconds=value,capped_wait_seconds=value,selection_probability=.1*len(selected))
        with patch.object(audit.joint,'member_model',side_effect=deepcopy(models)),patch.object(audit.joint,'member_metrics',side_effect=metric):
            proof=audit.allocate_slots(members,baseline_proposals=proposals,priority_context=p)
        self.assertEqual(proof['extras'],[]);self.assertTrue(proof['extra_passes'][-1]['candidate_evaluations'])
        self.assertTrue(all(not r['strictly_positive'] for r in proof['extra_passes'][-1]['candidate_evaluations']))

    def test_full_matching_parent_reference_current_only_actions_and_new_cardinalities(self):
        data=fixture();before=deepcopy(data);counts=check(data);self.assertEqual(data,before);e=data[-1]
        self.assertTrue(e['evaluated']);self.assertEqual(counts['startslots_groups_evaluated'],1)
        self.assertEqual(counts['startslots_public_H_proposals_checked'],4);self.assertEqual(counts['startslots_one_contact_edges_checked'],24)
        self.assertEqual(counts['startslots_augmentations_checked'],4);self.assertEqual(counts['startslots_residual_certificates_checked'],1)
        self.assertEqual([d['send'] for d in data[-2]],e['allocation']['planned_sends'][:2])
        self.assertEqual(len(data[-2]),2);self.assertFalse(e['future_assignments_committed'])
        self.assertFalse(e['old_probability_wait_guards_protect_novel_counts']);self.assertTrue(all(d['evidence']['cohort_startslots']['original_H_plan_reference_only'] for d in data[-2]))
        self.assertTrue(any(len(s)!=r['original_H_immediate_cardinality'] for s,r in zip(e['allocation']['planned_sends'],e['allocation']['owner_records'])))

    def test_no_cover_pending_no_tail_and_one_tail_copy_intact_parent(self):
        for kind in ('no_cover','pending','no_tail','one_tail'):
            data=fixture(kind);counts=check(data);self.assertEqual(counts['startslots_groups_copied'],1);self.assertFalse(data[-1]['evaluated'])
            if kind=='no_cover':self.assertTrue(data[-1]['scope_eligible']);self.assertEqual(counts['startslots_groups_coverage_incomplete'],1)
            else:self.assertEqual(counts['startslots_groups_scope_ineligible'],1)
            for actual,parent in zip(data[-2],data[-1]['original_tailwait_proposal']['decisions']):
                actual=deepcopy(actual);actual['evidence'].pop('cohort_startslots');actual['evidence']['policy']=audit.BASE_POLICY;self.assertEqual(actual,parent)
            self.assertTrue(all(not d['evidence']['cohort_startslots']['original_H_plan_reference_only'] for d in data[-2]))

    def test_partial_default_orchestration_fixture_does_not_waive_parent_boundary_audit(self):
        args,_=copied_fixture();result=invoke(args);j,ms,ts,p,owners,_=args
        with self.assertRaisesRegex(ValueError,'time boundaries, clipped cells, original offsets or scope differ'):
            audit.audit_pair(ms,j,ts,result['decisions'],result['evidence'],p,owners)

    def test_matching_edges_assignment_capacities_shortest_paths_and_dual_tampers_rejected(self):
        edits=(lambda a:a['one_contact_edges'][0]['metrics'].__setitem__('total_cost_seconds',0),
            lambda a:a['coverage']['edges'].pop(),lambda a:a['coverage']['assignment'][0].__setitem__('hospital','FOREIGN'),
            lambda a:a['coverage']['hospital_capacities'][0].__setitem__('capacity',2),
            lambda a:a['coverage']['augmentations'][0]['path'][0].__setitem__('residual_capacity_before',2),
            lambda a:a['coverage']['augmentations'][0].__setitem__('shortest_distance_seconds',-1),
            lambda a:a['coverage']['residual_potentials'].__setitem__(0,99),
            lambda a:a['coverage'].__setitem__('exact_minimum_cost_certificate',True))
        for edit in edits:
            data=fixture();edit(data[-1]['allocation'])
            with self.assertRaises(ValueError):check(data)

    def test_extra_candidates_marginals_ties_and_final_pool_order_tampers_rejected(self):
        for edit in (lambda a:a['extra_passes'][0]['candidate_evaluations'].pop(),
                lambda a:a['extra_passes'][0]['selected'].__setitem__('marginal_cost_decrease_seconds',0),
                lambda a:a['extra_passes'][-1].__setitem__('selected',{}),
                lambda a:a['planned_sends'][0].append('FOREIGN'),
                lambda a:a['final_owner_metrics'][0]['metrics'].__setitem__('selection_probability',0),
                lambda a:a['hospital_slot_usage'][0].__setitem__('used',99)):
            data=fixture();edit(data[-1]['allocation'])
            with self.assertRaises(ValueError):check(data)

    def test_baseline_H_cdf_public_workers_and_owner_priority_source_tampers_rejected(self):
        for edit in (lambda e:e['public_H_baseline_proposals'].pop(),
                lambda e:e['public_H_baseline_proposals'][0]['evidence']['candidate_plan'][0].__setitem__('reply_probability',0),
                lambda e:e['allocation'].__setitem__('public_assumed_workers',2),
                lambda e:e['allocation']['owner_records'][0].__setitem__('effective_priority',1),
                lambda e:e['allocation']['owner_records'][0].__setitem__('contact_limit',10),
                lambda e:e['matching_source_sha256'].__setitem__('horizon_policy','x')):
            data=fixture();edit(data[-1])
            with self.assertRaises(ValueError):check(data)

    def test_outputs_no_oracle_guard_flags_future_reservations_and_original_proof_tampers_rejected(self):
        for edit in (lambda e:e.__setitem__('future_assignments_committed',True),
                lambda e:e.__setitem__('old_probability_wait_guards_protect_novel_counts',True),
                lambda e:e.__setitem__('scope_eligible',False),lambda e:e['original_tailwait_proposal']['decisions'][0]['send'].append('FOREIGN')):
            data=fixture();edit(data[-1])
            with self.assertRaises(ValueError):check(data)
        data=fixture();data[-2][0]['send']=['FOREIGN']
        with self.assertRaises(ValueError):check(data)
        data=fixture();data[4]['inventory'].pop()
        with self.assertRaises(ValueError):check(data)
        data=fixture();data[4]['inventory'][-1]['patient']['version']=999
        with self.assertRaises(ValueError):check(data)

    def test_fallback_proof_flags_scope_old_guards_to_embedded_reference(self):
        raw=prior.previous.previous.prior_tests.prior_tests.prior_tests.horizon_tests.replay();branch=raw['branches'][0];branch['joint_groups']=[]
        for event in branch['db_events']:
            if event['type']=='inquiry_workflow_started':event['data']['policy_name']=audit.NAME
        for call,decision in zip(branch['policy_calls'],branch['db_decisions']):
            call['name']=decision['policy_name']=audit.NAME;call['workflow_snapshot']['job']['policy_name']=audit.NAME
            for candidate in call['inputs']['candidates']:candidate['resources_enough']=True
            e=call['output']['evidence'];e['policy']=audit.NAME
            e['joint_tail']=dict(schema_version=1,mode='horizon_fallback',guard_evaluated=False)
            e['joint_first']=dict(schema_version=1,mode='horizon_fallback',current_only=False)
            e['joint_budget']=dict(schema_version=1,mode='horizon_fallback',projection_evaluated=False)
            for name in ('cohort_success','cohort_dynamic','cohort_hedge','cohort_recourse','cohort_followplan','cohort_stoploss','cohort_fullmenu','cohort_pareto','cohort_waitpareto','cohort_envelope','cohort_pairscope','cohort_pairshield','cohort_tailwait','cohort_startslots'):
                e[name]=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False)
            e['cohort_startslots']['original_H_plan_reference_only']=False
            decision['evidence']=deepcopy(e)
        branch.update(policy=audit.NAME,instrumentation=deepcopy(audit.NEW_INSTRUMENTATION));raw['arms']=[[audit.NAME,'immediate']]
        proof=audit.audit_replay(raw)['branch_audits'][0]
        self.assertTrue(proof['passed']);self.assertFalse(proof['current_slot_path_original_probability_wait_guards'])
        self.assertFalse(proof['current_slot_path_original_JOINT_domain']);self.assertFalse(proof['exact_minimum_cost_claimed'])
        self.assertNotIn('every_owner_final_predicted_probability_guard_independently_checked',proof)
        self.assertTrue(proof['embedded_Tailwait_reference_assertions']['every_owner_final_predicted_probability_guard_independently_checked'])
        bad=deepcopy(raw);bad['branches'][0]['instrumentation']['protected_wait_first_selection']=True
        with self.assertRaises(ValueError):audit.audit_replay(bad)

    def test_old_controls_delegate_and_import_contains_no_producer(self):
        raw=dict(schema_version=1,arms=[['old','immediate']],branches=[dict(policy='old',rule='immediate',environment_sha256='x',snapshot_sha256='y')],environment_sha256='x',initial_snapshot_sha256='y');wanted=dict(passed=True,new_executions=0)
        with patch.object(audit.control_audit,'audit_replay',return_value=wanted) as call:self.assertIs(audit.audit_replay(raw),wanted)
        call.assert_called_once_with(raw)
        code="import sys;from inquiry_lab import cohort_hedge_audit as h;o=h.owner_metrics;from inquiry_lab import cohort_startslots_audit;assert h.owner_metrics is o;print([k for k in sys.modules if k.startswith('inquiry_lab.') and k.endswith(('_policy','_runner','_model','_context','_experiment'))])"
        result=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,check=True);self.assertEqual(result.stdout.strip(),'[]')


if __name__=='__main__':unittest.main()
