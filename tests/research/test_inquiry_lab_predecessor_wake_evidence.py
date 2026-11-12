"""Small pure public-evidence fixtures; no policy, model or database execution."""
from copy import deepcopy
import unittest
from inquiry_lab import predecessor_wake_evidence as helper

NAME = 'synthetic_predecessor_component'


def typed(value):
    if type(value) is dict:
        return ('dict',tuple((typed(key),typed(item)) for key,item in value.items()))
    if type(value) in (list,tuple):
        return (type(value).__name__,tuple(typed(item) for item in value))
    if type(value) is float:
        return ('float',value.hex())
    return (type(value).__name__,value)


def fixture(pid='P',foreign_pid='OTHER'):
    own = dict(id=2,patient_id=pid,hospital_id='H',state='PENDING',responded_at=None,last_sent_at=1000,version=1)
    foreign = dict(id=1,patient_id=foreign_pid,hospital_id='H',state='DECLINED',responded_at=1005,last_sent_at=990,version=2)
    return dict(now=1005,snapshot=dict(patient_id=pid,patient_version=1,job_version=2,revision=9,now=1005),
        job=dict(patient_id=pid,version=2,started_at=1000,deadline_at=1300,next_evaluation_at=1015,enabled=1,policy_name=NAME),
        latest_decision=dict(id=3,at=1000,patient_id=pid,policy_name=NAME,job_version=1,
            evidence=dict(time_horizon=dict(context=dict(patient_id=pid,event_prefix_max=10)))),
        event_prefix_max=11,decision_prefix_max=3,own_inquiries=[own],all_inquiries=[deepcopy(own),foreign],
        events=[dict(id=11,at=1005,type='inquiry_responded',patient_id=foreign_pid,hospital_id='H',
            data=dict(inquiry_id=1,action='decline'))],policy_name=NAME)


def set_own(args,**changes):
    args['own_inquiries'][0].update(changes)
    next(row for row in args['all_inquiries'] if row['id']==2).update(changes)


class PredecessorWakeEvidenceTests(unittest.TestCase):
    def call(self,args):
        before = typed(deepcopy(args))
        result = helper.build_public_predecessor_wake(**args)
        self.assertEqual(typed(args),before)
        if result is not None:
            result_before = typed(deepcopy(result))
            self.assertEqual(helper.validate(result),result)
            self.assertEqual(typed(result),result_before)
        return result

    def test_valid_decline_captures_current_sources_without_mutation(self):
        args=fixture();value=self.call(args)
        self.assertEqual(value['rule'],helper.RULE)
        self.assertEqual(value['remaining_open_inquiry_ids'],[2])
        self.assertEqual(value['terminal_predecessors'][0]['inquiry']['version'],2)
        self.assertEqual(value['terminal_predecessors'][0]['own_inquiry'],args['own_inquiries'][0])

    def test_accepted_and_confirmed_current_rows_are_valid_accept_sources(self):
        for state,version in [('ACCEPTED',2),('CONFIRMED',3)]:
            with self.subTest(state=state):
                args=fixture();args['events'][0]['data']['action']='accept';args['all_inquiries'][1].update(state=state,version=version)
                self.assertIsNotNone(self.call(args))

    def test_consumed_prefix_excludes_same_event(self):
        args=fixture();args['latest_decision']['evidence']['time_horizon']['context']['event_prefix_max']=11
        self.assertIsNone(self.call(args))

    def test_same_clock_later_event_id_can_wake(self):
        args=fixture();args['latest_decision']['at']=1005
        self.assertIsNotNone(self.call(args))
        args['events'][0]['id']=10;self.assertIsNone(self.call(args))

    def test_same_or_later_sent_foreign_inquiry_is_not_strict_predecessor(self):
        for sent in (1000,1000.0,1001):
            args=fixture();args['all_inquiries'][1]['last_sent_at']=sent
            self.assertIsNone(self.call(args))

    def test_fractional_strict_predecessor_and_review_are_not_rounded(self):
        args=fixture();args['all_inquiries'][1].update(last_sent_at=999.75,responded_at=1004.5)
        args['events'][0]['at']=1004.5;args['job']['next_evaluation_at']=1014.5
        value=self.call(args)
        self.assertEqual(value['captured_job']['next_evaluation_at'],1014.5)
        self.assertEqual(value['terminal_predecessors'][0]['event']['at'],1004.5)

    def test_foreign_patient_identity_keeps_numeric_type(self):
        self.assertIsNotNone(self.call(fixture(pid='1',foreign_pid=1)))
        self.assertIsNotNone(self.call(fixture(pid=1,foreign_pid='1')))
        self.assertIsNone(self.call(fixture(pid='P',foreign_pid='P')))

    def test_boolean_patient_ids_are_unsupported(self):
        self.assertIsNone(self.call(fixture(pid=True)))
        self.assertIsNone(self.call(fixture(foreign_pid=False)))

    def test_missing_legacy_or_typed_wrong_watermark_is_not_zero_guessed(self):
        for evidence in [{},dict(time_horizon=dict(context=dict(patient_id='P',event_prefix_max=True))),
                dict(time_horizon=dict(context=dict(patient_id='OTHER',event_prefix_max=10)))]:
            args=fixture();args['latest_decision']['evidence']=evidence
            self.assertIsNone(self.call(args))

    def test_snapshot_job_or_last_decision_identity_mismatch_is_inactive(self):
        for part,key,value in [('snapshot','job_version',3),('snapshot','patient_id','OTHER'),
                ('snapshot','now',1004),('job','policy_name','OTHER'),('latest_decision','patient_id','OTHER'),
                ('latest_decision','policy_name','OTHER'),('latest_decision','job_version',3)]:
            args=fixture();args[part][key]=value;self.assertIsNone(self.call(args))

    def test_future_or_outside_prefix_events_are_not_observed(self):
        args=fixture();args['events'][0]['at']=1005.5;args['all_inquiries'][1]['responded_at']=1005.5
        self.assertIsNone(self.call(args))
        args=fixture();args['events'][0]['id']=12;self.assertIsNone(self.call(args))
        args=fixture();future=deepcopy(args['events'][0]);future.update(id=12,at=1006);future['data']['inquiry_id']=999
        args['events'].append(future);args['event_prefix_max']=12
        self.assertEqual([entry['event']['id'] for entry in self.call(args)['terminal_predecessors']],[11])

    def test_nonterminal_action_and_type_do_not_wake(self):
        for action in ('checking','question',None,True):
            args=fixture();args['events'][0]['data']['action']=action;self.assertIsNone(self.call(args))
        args=fixture();args['events'][0]['type']='inquiry_question';self.assertIsNone(self.call(args))

    def test_current_terminal_state_must_match_action(self):
        for state in ('PENDING','CHECKING','CANCELLED','EXPIRED','ACCEPTED','CONFIRMED'):
            args=fixture();args['all_inquiries'][1]['state']=state;self.assertIsNone(self.call(args))
        args=fixture();args['events'][0]['data']['action']='accept';self.assertIsNone(self.call(args))

    def test_current_qid_hospital_patient_and_response_time_are_joined(self):
        for key,value in [('hospital_id','OTHER'),('patient_id','UNJOINED'),('responded_at',1004),('last_sent_at',1006)]:
            args=fixture();args['all_inquiries'][1][key]=value;self.assertIsNone(self.call(args))
        for qid in (999,True):
            args=fixture();args['events'][0]['data']['inquiry_id']=qid;self.assertIsNone(self.call(args))

    def test_duplicate_visible_event_or_duplicate_foreign_qid_is_ambiguous(self):
        args=fixture();args['events'].append(deepcopy(args['events'][0]));self.assertIsNone(self.call(args))
        args=fixture();second=deepcopy(args['events'][0]);second['id']=12;args['events'].append(second);args['event_prefix_max']=12
        self.assertIsNone(self.call(args))

    def test_duplicate_current_or_own_ids_are_rejected(self):
        args=fixture();args['all_inquiries'].append(deepcopy(args['all_inquiries'][1]));self.assertIsNone(self.call(args))
        args=fixture();args['own_inquiries'].append(deepcopy(args['own_inquiries'][0]));self.assertIsNone(self.call(args))

    def test_multiple_strict_predecessors_are_sorted_and_each_joined(self):
        args=fixture();other=deepcopy(args['all_inquiries'][1]);other.update(id=3,patient_id='OTHER2',last_sent_at=995)
        args['all_inquiries'].append(other);event=deepcopy(args['events'][0]);event.update(id=12,patient_id='OTHER2');event['data']['inquiry_id']=3
        args['events']=[event,args['events'][0]];args['event_prefix_max']=12
        self.assertEqual([entry['event']['id'] for entry in self.call(args)['terminal_predecessors']],[11,12])

    def test_duplicate_own_hospital_or_omitted_current_open_own_is_rejected(self):
        args=fixture();other=deepcopy(args['own_inquiries'][0]);other['id']=4
        args['own_inquiries'].append(other);args['all_inquiries'].append(deepcopy(other));self.assertIsNone(self.call(args))
        args=fixture();other=deepcopy(args['own_inquiries'][0]);other.update(id=4,hospital_id='H2')
        args['all_inquiries'].append(other);self.assertIsNone(self.call(args))

    def test_boolean_nonfinite_clocks_and_boolean_versions_are_rejected(self):
        for part,key,value in [('job','started_at',True),('job','deadline_at',float('inf')),('job','next_evaluation_at',True),
                ('snapshot','revision',True),('latest_decision','at',True),('latest_decision','id',True)]:
            args=fixture();args[part][key]=value;self.assertIsNone(self.call(args))
        for key in ('responded_at','last_sent_at','version','id'):
            args=fixture();args['all_inquiries'][1][key]=True;self.assertIsNone(self.call(args))
        args=fixture();args['events'][0]['at']=True;self.assertIsNone(self.call(args))
        args=fixture();args['now']=True;self.assertIsNone(self.call(args))

    def test_regular_due_disabled_or_closed_window_does_not_wake(self):
        for key,value in [('next_evaluation_at',1005),('deadline_at',1005),('started_at',1006),('enabled',0),('enabled',True)]:
            args=fixture();args['job'][key]=value;self.assertIsNone(self.call(args))

    def test_validate_rejects_forged_order_prefix_identity_and_rows(self):
        value=self.call(fixture())
        changes=[lambda v:v.update(event_prefix_max=10),lambda v:v['terminal_predecessors'].append(deepcopy(v['terminal_predecessors'][0])),
            lambda v:v['terminal_predecessors'][0]['own_inquiry'].update(version=2),
            lambda v:v['terminal_predecessors'][0]['inquiry'].update(state='PENDING'),
            lambda v:v['remaining_open_inquiry_ids'].append(2),lambda v:v.update(now=True)]
        for change in changes:
            bad=deepcopy(value);change(bad);before=typed(deepcopy(bad))
            self.assertIsNone(helper.validate(bad));self.assertEqual(typed(bad),before)
        for bad in (None,[],{},True):self.assertIsNone(helper.validate(bad))

    def test_unsupported_shapes_are_none_and_valid_output_is_defensive_copy(self):
        for key,value in [('events',()),('own_inquiries',None),('all_inquiries',{}),('latest_decision',[])]:
            args=fixture();args[key]=value;self.assertIsNone(self.call(args))
        source=self.call(fixture());copy=helper.validate(source);copy['terminal_predecessors'][0]['inquiry']['version']=99
        self.assertEqual(source['terminal_predecessors'][0]['inquiry']['version'],2)

    def test_current_version_mismatch_is_stale_and_no_event_version_is_invented(self):
        args=fixture();args['all_inquiries'][0]['version']=2;self.assertIsNone(self.call(args))
        value=self.call(fixture());self.assertEqual(set(value['terminal_predecessors'][0]['event']['data']),{'inquiry_id','action'})

    def test_no_open_own_or_other_hospital_does_not_wake(self):
        args=fixture();set_own(args,state='CANCELLED');self.assertIsNone(self.call(args))
        args=fixture();set_own(args,hospital_id='OTHER');self.assertIsNone(self.call(args))
        args=fixture();args['own_inquiries']=[];self.assertIsNone(self.call(args))


if __name__ == '__main__':
    unittest.main()
