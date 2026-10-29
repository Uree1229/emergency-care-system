"""Synthetic complete native capture and calculation-only identity aliases."""
from copy import deepcopy
import unittest
from inquiry_lab import cohort_chainplanwake_context as context
from test_inquiry_lab_cohort_chainplanwake_policy import fixture as policy_fixture


def fixture():
    return policy_fixture()[0]


class CohortChainplanwakeContextTests(unittest.TestCase):
    def test_full_snapshot_validation_and_pure_identity_projection(self):
        j,ms,ts,p,owners,_=fixture();before=deepcopy((j,ms,ts,p,owners))
        self.assertEqual(context.validate_owner_context(owners,joint_context=j,members=ms,tail_members=ts,priority_context=p),owners)
        base=context.base_owner_projection(owners)
        self.assertTrue(all(o['job'] is None or o['job']['policy_name']==context.BASE_POLICY_NAME for o in base['inventory']))
        self.assertEqual((j,ms,ts,p,owners),before)
        context.original.validate_owner_context(base,joint_context=context.base_context_projection(j),
            members=context.base_members_projection(ms),tail_members=context.base_members_projection(ts),priority_context=p)

    def test_foreign_Hedge_job_remains_unsupported_in_calculation_copy(self):
        j,ms,ts,p,owners,_=fixture();owner=next(o for o in owners['inventory'] if o['patient']['id']=='OWNER0')
        owner['job']['policy_name']=context.BASE_POLICY_NAME
        context.validate_owner_context(owners,joint_context=j,members=ms,tail_members=ts,priority_context=p)
        copied=context.base_owner_projection(owners)
        value=next(o for o in copied['inventory'] if o['patient']['id']=='OWNER0')
        self.assertEqual(value['job']['policy_name'],'__foreign_cohort_tailwait__')
        self.assertEqual(owner['job']['policy_name'],context.BASE_POLICY_NAME)

    def test_omitted_owner_stale_version_and_wrong_native_label_rejected(self):
        for edit in (lambda x:x['inventory'].pop(),lambda x:x['inventory'][0]['patient'].update(version=999)):
            j,ms,ts,p,owners,_=fixture();edit(owners)
            with self.assertRaises(ValueError):context.validate_owner_context(owners,joint_context=j,members=ms,tail_members=ts,priority_context=p)
        j,ms,ts,p,owners,_=fixture();j['eligible'][0]['job']['policy_name']=context.BASE_POLICY_NAME
        with self.assertRaises(ValueError):context.validate_context(j)

    def test_priority_contract_unknown_default_and_expert_tiers_unchanged(self):
        for value,source,expected in [(None,'unknown',4),(1,'unknown',4),(4,'expert',4),(2,'expert',2),(1,'expert',1)]:
            self.assertEqual(context.effective_priority(value,source),expected)
        with self.assertRaises(ValueError):context.effective_priority(3,'expert')


class PublicWakeSourceTests(unittest.TestCase):
    def fixture(self):
        pid='P';snapshot=dict(patient_id=pid,patient_version=1,job_version=2,revision=9,now=1005)
        job=dict(patient_id=pid,version=2,started_at=1000,deadline_at=1300,next_evaluation_at=1015,enabled=1,policy_name=context.POLICY_NAME)
        decision=dict(id=3,at=1000,patient_id=pid,policy_name=context.POLICY_NAME,job_version=1,
            evidence=dict(time_horizon=dict(context=dict(patient_id=pid,event_prefix_max=10))))
        inquiries=[dict(id=1,patient_id=pid,hospital_id='H1',state='DECLINED',responded_at=1005,last_sent_at=1000,version=2),
            dict(id=2,patient_id=pid,hospital_id='H2',state='PENDING',responded_at=None,last_sent_at=1000,version=1)]
        event=dict(id=11,at=1005,type='inquiry_responded',patient_id=pid,hospital_id='H1',data=dict(inquiry_id=1,action='decline'))
        return dict(now=1005,snapshot=snapshot,job=job,latest_decision=decision,event_prefix_max=11,decision_prefix_max=3,inquiries=inquiries,events=[event])

    def test_source_is_immutable_and_exact_new_same_second_ID_is_required(self):
        args=self.fixture();before=deepcopy(args);value=context.build_wake_context(**args)
        self.assertEqual(args,before);self.assertEqual(value['terminal_declines'][0]['event']['id'],11)
        args['events'][0]['id']=10;self.assertIsNone(context.build_wake_context(**args))

    def test_foreign_question_wrong_qid_current_state_or_attempt_are_not_wakes(self):
        changes=[lambda a:a['events'][0].update(patient_id='OTHER'),lambda a:a['events'][0].update(hospital_id='OTHER'),
            lambda a:a['events'][0]['data'].update(action='checking'),lambda a:a['events'][0]['data'].update(inquiry_id=999),
            lambda a:a['inquiries'][0].update(state='PENDING'),lambda a:a['inquiries'][0].update(responded_at=1004),
            lambda a:a['inquiries'][0].update(last_sent_at=1006),lambda a:a['events'][0].update(at=1005.5)]
        for change in changes:
            args=self.fixture();change(args);self.assertIsNone(context.build_wake_context(**args))

    def test_missing_malformed_watermark_is_inactive_not_zero_guessed(self):
        for evidence in ({},dict(time_horizon=dict(context=dict(patient_id='P',event_prefix_max=True))),dict(time_horizon=dict(context=dict(patient_id='OTHER',event_prefix_max=10)))):
            args=self.fixture();args['latest_decision']['evidence']=evidence
            self.assertIsNone(context.build_wake_context(**args))

    def test_validate_rejects_future_duplicate_and_consumed_events(self):
        args=self.fixture();value=context.build_wake_context(**args)
        for edit in (lambda v:v['terminal_declines'].append(deepcopy(v['terminal_declines'][0])),
            lambda v:v['terminal_declines'][0]['event'].update(at=1006),lambda v:v['terminal_declines'][0]['event'].update(id=10)):
            bad=deepcopy(value);edit(bad)
            with self.assertRaises(ValueError):context.validate_wake_context(bad,now=1005,patient_id='P')


if __name__=='__main__':unittest.main()
