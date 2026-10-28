"""Synthetic public-roster checks; no saved scenarios or performance runs."""
from copy import deepcopy
import unittest
from inquiry_lab import horizon_context, joint_budget_context as context, joint_tail_context


def fixture(count=4, hospitals=2):
    patients = [dict(id=f'P{i}',state='WAITING',version=1) for i in range(count)]
    jobs = [dict(patient_id=p['id'],version=1,started_at=1000,deadline_at=1300,
        next_evaluation_at=1000,enabled=1,policy_name=context.POLICY_NAME) for p in patients]
    registry = dict(now=1000,event_prefix_max=2*count,decision_prefix_max=0,
        patients=patients,jobs=jobs,inquiries=[],decisions=[])
    joint = context.build_context(**registry)
    events = []
    for index,patient in enumerate(patients):
        events.extend([dict(id=2*index+1,at=1000,type='patient_created',patient_id=patient['id'],data={}),
            dict(id=2*index+2,at=1000,type='inquiry_workflow_started',patient_id=patient['id'],data={'deadline_seconds':300})])
    captured = []
    for patient,job in zip(patients,jobs):
        time = horizon_context.build_context(now=1000,patient_id=patient['id'],job_version=1,
            event_prefix_max=2*count,decision_prefix_max=0,job=job,events=events)
        inputs = dict(now=1000,candidates=[dict(hospital_id=f'H{i}',travel={'duration_seconds':i},
            resources_enough=True) for i in range(hospitals)],observations=[],pending=[],patient_pending=[],
            max_new=10,last_contact_at=None,time_context=time)
        source = dict(apply_snapshot=dict(patient_id=patient['id'],job_version=1,patient_version=1,revision=7,now=1000),
            job={key:job[key] for key in context.JOB_FIELDS})
        captured.append(dict(patient_id=patient['id'],inputs=inputs,workflow_snapshot=source))
    return registry,joint,captured[:2],captured[2:]


class JointBudgetContextTests(unittest.TestCase):
    def test_two_copied_identity_projections_preserve_native_source(self):
        registry,joint,members,tails = fixture(5)
        before = deepcopy((registry,joint,members,tails))
        projected = context.base_context_projection(joint)
        projected_members = context.base_members_projection(members+tails)
        self.assertTrue(all(r['job']['policy_name']=='eat_joint_tail_l15_f' for r in projected['eligible']))
        first = joint_tail_context.base_context_projection(projected)
        self.assertTrue(all(r['job']['policy_name']=='eat_joint_first_l15_f' for r in first['eligible']))
        self.assertTrue(all(m['workflow_snapshot']['job']['policy_name']=='eat_joint_tail_l15_f' for m in projected_members))
        self.assertEqual((registry,joint,members,tails),before)
        self.assertEqual(joint_tail_context.POLICY_NAME,'eat_joint_tail_l15_f')

    def test_complete_tail_including_final_singleton_is_required(self):
        _,joint,members,tails = fixture(5)
        self.assertEqual([r['patient_id'] for r in context.validate_tail_members(joint,tails,members=members)],
            ['P2','P3','P4'])
        for bad in (tails[:-1],list(reversed(tails)),tails+[deepcopy(tails[-1])]):
            with self.assertRaises(ValueError): context.validate_tail_members(joint,bad,members=members)

    def test_pool_preserves_adapter_order_nearest_six_and_max_new(self):
        _,joint,members,tails = fixture(3,8)
        tails[0]['inputs']['candidates'].reverse()
        self.assertEqual(context.validate_tail_members(joint,tails,members=members)[0]['hospitals'],
            ['H7','H6','H5','H4','H3','H2'])
        tails[0]['inputs']['max_new']=2
        self.assertEqual(context.validate_tail_members(joint,tails,members=members)[0]['hospitals'],['H7','H6'])

    def test_only_native_due_waiting_unqueried_undecided_jobs_enter(self):
        registry,_,_,_ = fixture(8)
        registry['patients'][0]['state']='SELECTED'
        registry['jobs'][1]['enabled']=0
        registry['jobs'][2]['next_evaluation_at']=1001
        registry['jobs'][3]['policy_name']='eat_joint_tail_l15_f'
        registry['inquiries']=[dict(patient_id='P4')]
        registry['decision_prefix_max']=1
        registry['decisions']=[dict(id=1,at=1000,patient_id='P5')]
        result=context.build_context(**registry)
        self.assertEqual([r['patient']['id'] for r in result['eligible']],['P6','P7'])

    def test_singleton_source_clock_prefix_version_revision_and_ledger_fail_closed(self):
        _,joint,members,tails = fixture(5)
        variants=[]
        bad=deepcopy(tails);bad[-1]['inputs']['now']=1001;variants.append(bad)
        bad=deepcopy(tails);bad[-1]['inputs']['time_context']['decision_prefix_max']=1;variants.append(bad)
        bad=deepcopy(tails);bad[-1]['workflow_snapshot']['job']['version']=2;variants.append(bad)
        bad=deepcopy(tails);bad[-1]['workflow_snapshot']['apply_snapshot']['revision']=8;variants.append(bad)
        bad=deepcopy(tails);bad[-1]['inputs']['last_contact_at']=999;variants.append(bad)
        bad=deepcopy(tails);bad[-1]['inputs']['pending']=[dict(hospital='H0',patient='OTHER',submitted_at=990)];variants.append(bad)
        for bad in variants:
            with self.assertRaises(ValueError): context.validate_tail_members(joint,bad,members=members)

    def test_eligible_owner_pending_and_foreign_native_identity_fail_closed(self):
        _,joint,members,tails=fixture()
        for member in members+tails:
            member['inputs']['pending']=[dict(hospital='H0',patient='P2',submitted_at=990)]
        with self.assertRaises(ValueError): context.validate_tail_members(joint,tails,members=members)
        bad=deepcopy(joint);bad['eligible'][0]['job']['policy_name']='eat_joint_tail_l15_f'
        with self.assertRaises(ValueError): context.validate_context(bad)

    def test_original_public_order_and_explicit_pair_requirement_are_preserved(self):
        registry,_,_,_=fixture(4)
        registry['jobs'][2]['started_at']=999
        result=context.build_context(**registry)
        self.assertEqual(result['pair_patient_ids'],['P2','P0'])
        with self.assertRaises(ValueError): context.build_context(**registry,pair_patient_ids=['P0','P1'])

    def test_zero_tail_valid_and_unknown_or_missing_context_rejected(self):
        _,joint,members,tails=fixture(2)
        before=deepcopy((joint,members,tails))
        self.assertEqual(context.validate_tail_members(joint,tails,members=members),[])
        self.assertEqual((joint,members,tails),before)
        bad=deepcopy(joint);bad['unknown']=1
        with self.assertRaises(ValueError):context.validate_context(bad)
        with self.assertRaises(ValueError):context.validate_context({})


if __name__=='__main__':unittest.main()
