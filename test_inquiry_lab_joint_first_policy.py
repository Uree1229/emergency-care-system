"""Finite synthetic pair models; no scenario or performance execution."""
from copy import deepcopy
import math
import unittest
from unittest.mock import patch

from inquiry_lab import horizon_context, horizon_policy, joint_first_context, joint_first_policy as policy


def fixture(n=2):
    patients = [dict(id=f'P{i}',state='WAITING',version=1) for i in range(2)]
    jobs = [dict(patient_id=p['id'],version=1,started_at=1000,deadline_at=1300,
        next_evaluation_at=1000,enabled=1,policy_name=policy.NAME) for p in patients]
    context = joint_first_context.build_context(now=1000,event_prefix_max=4,decision_prefix_max=0,
        patients=patients,jobs=jobs,inquiries=[],decisions=[])
    events = []
    for index,patient in enumerate(patients):
        events.extend([dict(id=2*index+1,at=1000,type='patient_created',patient_id=patient['id'],data={}),
            dict(id=2*index+2,at=1000,type='inquiry_workflow_started',patient_id=patient['id'],data={'deadline_seconds':300})])
    members = []
    for patient,job in zip(patients,jobs):
        time = horizon_context.build_context(now=1000,patient_id=patient['id'],job_version=1,
            event_prefix_max=4,decision_prefix_max=0,job=job,events=events)
        inputs = dict(now=1000,candidates=[dict(hospital_id=f'H{i}',travel={'duration_seconds':0},
            resources_enough=True) for i in range(n)],observations=[],pending=[],patient_pending=[],
            max_new=10,last_contact_at=None,time_context=time)
        snapshot = dict(patient_id=patient['id'],job_version=1,patient_version=1,revision=7,now=1000)
        projection = {key:job[key] for key in ('patient_id','version','started_at','deadline_at','enabled','policy_name')}
        members.append(dict(patient_id=patient['id'],inputs=inputs,
            workflow_snapshot=dict(apply_snapshot=snapshot,job=projection)))
    return members,context


def model(n=2, first=False, expensive=False):
    rows = []
    for index in range(n):
        travel = 1000 if index and (first or expensive) else 0
        rows.append(dict(hospital=f'H{index}',travel_seconds=travel,service_mu=math.log(10),service_sigma=.1,
            reply_probability=1.,accept_probability=1.,mean_service_seconds=math.exp(math.log(10)+.1*.1/2),
            assumed_workers=1,residual_wait_seconds=0.,inquiry_cost_seconds=15))
    return dict(horizon_seconds=300,cell_edges_seconds=list(range(5,301,5)),
        failure_penalty_seconds=1500,hospitals=rows)


def proposals(members, selected):
    result = []
    for member,hospitals in zip(members,selected):
        value = horizon_policy.decide(horizon_policy.NAME,**deepcopy(member['inputs']))
        value['send'] = list(hospitals)
        value['evidence']['sent_hospitals'] = list(hospitals)
        for row in value['evidence']['candidate_plan']:
            row['planned_offset_seconds'] = 0 if row['hospital'] in hospitals else 120
        result.append(value)
    return result


def evaluated(members, context, selected, models):
    base = proposals(members,selected)
    with patch.object(policy.original,'decide',side_effect=deepcopy(base)), \
            patch.object(policy,'_model',side_effect=deepcopy(models)):
        return policy.decide_joint(policy.NAME,members=members,joint_context=context)


class JointFirstPolicyTests(unittest.TestCase):
    def test_spec_changes_only_H_identity_and_fallback_is_exact_H_core(self):
        old,new = horizon_policy.spec(),policy.spec()
        for key in ('name','label'): old.pop(key);new.pop(key)
        self.assertEqual(new,old)
        members,_ = fixture()
        args = members[0]['inputs']
        old = horizon_policy.decide(horizon_policy.NAME,**deepcopy(args))
        new = policy.decide(policy.NAME,**deepcopy(args))
        self.assertEqual(new['evidence'].pop('joint_first'),
            dict(schema_version=1,mode='horizon_fallback',current_only=False))
        new['evidence']['policy'] = horizon_policy.NAME
        self.assertEqual(new,old)

    def test_shared_hospital_adds_one_earlier_belief_job_only_to_later(self):
        models = [model(first=True),model()]
        baseline = policy._combination(models,[['H0'],['H0']])
        separated = policy._combination(models,[['H0'],['H1']])
        mean = models[0]['hospitals'][0]['mean_service_seconds']
        self.assertEqual(baseline['later_member_extra_wait_seconds'],{'H0':mean})
        self.assertEqual(separated['later_member_extra_wait_seconds'],{})
        self.assertEqual(baseline['member_metrics'][0],separated['member_metrics'][0])
        self.assertGreater(baseline['member_metrics'][1]['capped_wait_seconds'],
            separated['member_metrics'][1]['capped_wait_seconds'])
        models[1]['hospitals'][0]['mean_service_seconds'] = 999
        changed = policy._combination(models,[['H0'],['H0']])
        self.assertEqual(changed['later_member_extra_wait_seconds'],{'H0':mean})

    def test_strict_current_only_improvement_changes_membership_at_fixed_counts(self):
        members,context = fixture()
        before = deepcopy((members,context))
        result = evaluated(members,context,[['H0'],['H0']],[model(first=True),model()])
        self.assertTrue(result['evidence']['applied'])
        self.assertEqual([d['send'] for d in result['decisions']],[['H0'],['H1']])
        self.assertEqual(result['evidence']['cardinalities'],[1,1])
        self.assertEqual((members,context),before)
        for index,decision in enumerate(result['decisions']):
            self.assertEqual(decision['next_review_at'],1015)
            self.assertEqual(decision['evidence']['joint_first']['mode'],'pair_current_only')
            self.assertTrue(all(row['planned_offset_seconds'] in (0,None)
                for row in decision['evidence']['candidate_plan']))
            base = result['evidence']['baseline_combination']['member_metrics'][index]
            chosen = result['evidence']['combinations'][result['evidence']['selected_index']]['member_metrics'][index]
            self.assertGreaterEqual(chosen['selection_probability']+1e-12,base['selection_probability'])
            self.assertLessEqual(chosen['total_cost_seconds'],base['total_cost_seconds']+1e-9)

    def test_individual_arrival_cost_constraint_rejects_collective_wait_shortcut(self):
        members,context = fixture()
        result = evaluated(members,context,[['H0'],['H0']],
            [model(first=True),model(expensive=True)])
        self.assertFalse(result['evidence']['applied'])
        self.assertEqual([d['send'] for d in result['decisions']],[['H0'],['H0']])
        shorter = [row for row in result['evidence']['combinations'] if row['strict_wait_improvement']]
        self.assertTrue(shorter)
        self.assertTrue(all(not row['feasible'] for row in shorter))

    def test_baseline_best_tie_is_retained_even_if_another_row_enumerates_first(self):
        members,context = fixture()
        result = evaluated(members,context,[['H1'],['H0']],[model(),model()])
        self.assertFalse(result['evidence']['applied'])
        self.assertEqual([d['send'] for d in result['decisions']],[['H1'],['H0']])
        self.assertEqual(result['evidence']['selected_index'],result['evidence']['baseline_index'])

    def test_probability_guard_is_independent_of_arrival_cost_guard(self):
        members,context = fixture()
        first,later = model(first=True),model()
        later['hospitals'][0]['travel_seconds'] = 10000
        later['hospitals'][1]['accept_probability'] = .5
        result = evaluated(members,context,[['H0'],['H0']],[first,later])
        row = next(row for row in result['evidence']['combinations']
            if row['hospitals']==[['H0'],['H1']])
        baseline = result['evidence']['baseline_combination']['member_metrics'][1]
        self.assertLess(row['member_metrics'][1]['total_cost_seconds'],baseline['total_cost_seconds'])
        self.assertLess(row['member_metrics'][1]['selection_probability'],baseline['selection_probability'])
        self.assertFalse(row['feasible'])

    def test_other_equal_winners_use_original_pool_lexical_enumeration(self):
        members,context = fixture(3)
        result = evaluated(members,context,[['H0'],['H0']],[model(3,first=True),model(3)])
        self.assertTrue(result['evidence']['applied'])
        self.assertEqual([d['send'] for d in result['decisions']],[['H0'],['H1']])

    def test_complete_six_choose_three_pair_enumeration_is_bounded_at_400(self):
        members,context = fixture(6)
        result = evaluated(members,context,[['H0','H1','H2'],['H0','H1','H2']],
            [model(6),model(6)])
        self.assertEqual(len(result['evidence']['combinations']),400)
        self.assertEqual(result['evidence']['cardinalities'],[3,3])
        self.assertTrue(all(len(row['hospitals'][0])==3 and len(row['hospitals'][1])==3
            for row in result['evidence']['combinations']))

    def test_real_public_model_builds_without_potential_or_worker_truth(self):
        members,context = fixture()
        before = deepcopy((members,context))
        result = policy.decide_joint(policy.NAME,members=members,joint_context=context)
        self.assertEqual((members,context),before)
        self.assertLessEqual(len(result['evidence']['combinations']),400)
        self.assertTrue(all(row['assumed_workers']==1 for model_row in result['evidence']['model_inputs']
            for row in model_row['hospitals']))

    def test_member_clock_prefix_order_version_ledger_and_prior_inquiry_fail_closed(self):
        members,context = fixture()
        variants = []
        changed = deepcopy(members);changed.reverse();variants.append(changed)
        changed = deepcopy(members);changed[0]['inputs']['now']=1001;variants.append(changed)
        changed = deepcopy(members);changed[0]['inputs']['time_context']['event_prefix_max']=5;variants.append(changed)
        changed = deepcopy(members);changed[0]['workflow_snapshot']['job']['version']=2;variants.append(changed)
        changed = deepcopy(members);changed[0]['workflow_snapshot']['apply_snapshot']['revision']=True;variants.append(changed)
        changed = deepcopy(members);changed[0]['inputs']['last_contact_at']=999;variants.append(changed)
        changed = deepcopy(members);changed[0]['inputs']['patient_pending']=[{}];variants.append(changed)
        changed = deepcopy(members);changed[0]['inputs']['pending']=[dict(hospital='H0',patient='OTHER',submitted_at=990)];variants.append(changed)
        for changed in variants:
            with self.subTest(index=variants.index(changed)), self.assertRaises(ValueError):
                policy.decide_joint(policy.NAME,members=changed,joint_context=context)

    def test_partial_H_and_A_soft_stop_are_inherited_in_unpaired_fallback(self):
        members,_ = fixture()
        args = deepcopy(members[0]['inputs'])
        args['now'] = 1293
        result = policy.decide(policy.NAME,**args)
        self.assertEqual(result['evidence']['horizon_seconds'],7)
        self.assertEqual(result['evidence']['time_horizon']['cell_edges_seconds'],[5,7])
        self.assertEqual(result['evidence']['time_horizon']['allowed_plan_offsets_seconds'],[0])

    def test_each_pair_member_retains_its_own_H_and_partial_final_bin(self):
        members,context = fixture()
        context['now'] = 1015
        for member in members:
            member['inputs']['now'] = 1015
            member['workflow_snapshot']['apply_snapshot']['now'] = 1015
        later = context['eligible'][1]['job']
        later.update(started_at=1007,deadline_at=1307,next_evaluation_at=1007)
        members[1]['workflow_snapshot']['job'].update(started_at=1007,deadline_at=1307)
        events = [dict(id=3,at=1007,type='patient_created',patient_id='P1',data={}),
            dict(id=4,at=1007,type='inquiry_workflow_started',patient_id='P1',data={'deadline_seconds':300})]
        members[1]['inputs']['time_context'] = horizon_context.build_context(now=1015,
            patient_id='P1',job_version=1,event_prefix_max=4,decision_prefix_max=0,
            job=later,events=events)
        result = policy.decide_joint(policy.NAME,members=members,joint_context=context)
        self.assertEqual([m['horizon_seconds'] for m in result['evidence']['model_inputs']],[285,292])
        self.assertEqual(result['evidence']['model_inputs'][1]['cell_edges_seconds'][-2:],[290,292])


if __name__ == '__main__': unittest.main()
