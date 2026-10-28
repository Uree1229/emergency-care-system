from copy import deepcopy
import unittest
from inquiry_lab import intent_context as c


def decision(i,owner,at,plan):
    return dict(id=i,patient_id=owner,at=at,evidence=dict(candidate_plan=[
        dict(hospital=h,planned_offset_seconds=o) for h,o in plan]))


def event(i,owner,hospital,at=90,typ='inquiry_sent'):
    return dict(id=i,patient_id=owner,hospital_id=hospital,at=at,type=typ)


def view(**kwargs):
    values=dict(now=100,patient_id='CURRENT',event_prefix_max=10,decision_prefix_max=10,
        pending=[dict(patient=p,hospital='WAIT',submitted_at=90) for p in ('CURRENT','A','B')],
        events=[],decisions=[])
    values.update(kwargs);return c.build_context(**values)


class IntentContextTests(unittest.TestCase):
    def test_latest_global_ID_order_not_patient_lexicographic_order(self):
        rows=[decision(2,'A',90,[('OLD',15)]),decision(8,'A',95,[('HA',120)]),
              decision(3,'B',91,[('HB',90)]),decision(11,'B',100,[('FUTURE_ID',15)])]
        r=view(decisions=rows)
        self.assertEqual(r['plans'],[
            dict(patient='B',decision_id=3,at=91,candidate_plan=[dict(hospital='HB',planned_offset_seconds=90)]),
            dict(patient='A',decision_id=8,at=95,candidate_plan=[dict(hospital='HA',planned_offset_seconds=120)])])

    def test_current_patient_and_nonpending_owners_are_excluded(self):
        rows=[decision(1,'CURRENT',99,[('SELF',15)]),decision(2,'ENDED',99,[('OLD',15)]),
              decision(3,'A',99,[('HA',15)])]
        self.assertEqual([r['patient'] for r in view(decisions=rows)['plans']],['A'])

    def test_review_age_inclusive_fifteen_and_expiry(self):
        self.assertEqual(len(view(decisions=[decision(1,'A',85,[('H',180)])])['plans']),1)
        self.assertEqual(view(decisions=[decision(1,'A',84,[('H',15)])])['plans'],[])

    def test_future_latest_row_does_not_resurrect_an_older_intent(self):
        rows=[decision(1,'A',95,[('OLD',15)]),decision(2,'A',101,[('FUTURE',15)])]
        self.assertEqual(view(decisions=rows)['plans'],[])

    def test_same_second_future_event_ID_does_not_remove_intent(self):
        rows=[decision(1,'A',100,[('PAST',15),('FUTURE_ID',30),('FUTURE_TIME',60)])]
        events=[event(10,'A','PAST',100),event(11,'A','FUTURE_ID',100),event(9,'A','FUTURE_TIME',101)]
        self.assertEqual([p['hospital'] for p in view(decisions=rows,events=events)['plans'][0]['candidate_plan']],
            ['FUTURE_ID','FUTURE_TIME'])

    def test_actual_top_plan_only_and_original_order(self):
        d=decision(1,'A',95,[('NOW',0),('Z',180),('NONE',None),('A',15)])
        d['evidence']['guard']={'proposal':{'evidence':{'candidate_plan':[dict(hospital='COUNTERFACTUAL',planned_offset_seconds=15)]}}}
        plan=view(decisions=[d])['plans'][0]['candidate_plan']
        self.assertEqual([x['hospital'] for x in plan],['Z','A'])
        self.assertTrue(all(set(x)=={'hospital','planned_offset_seconds'} for x in plan))

    def test_already_contacted_retried_and_closed_hospitals_are_not_reprotected(self):
        d=decision(1,'A',95,[('SENT',15),('RETRIED',30),('FREE',60)])
        events=[event(1,'A','SENT'),event(2,'A','RETRIED',typ='inquiry_retried'),
                event(3,'A','SENT',typ='inquiry_closed')]
        self.assertEqual([p['hospital'] for p in view(decisions=[d],events=events)['plans'][0]['candidate_plan']],['FREE'])

    def test_empty_filtered_owners_are_omitted_and_no_older_fallback(self):
        rows=[decision(1,'A',95,[('OLD',15)]),decision(2,'A',99,[('NOW',0),('NONE',None)])]
        self.assertEqual(view(decisions=rows)['plans'],[])

    def test_no_input_mutation_and_once_only_iterables(self):
        rows=[decision(1,'A',95,[('H',15)])];before=deepcopy(rows)
        self.assertEqual(view(decisions=iter(rows)),view(decisions=rows))
        self.assertEqual(rows,before)

    def test_invalid_clock_identity_prefix_and_original_offsets(self):
        for kwargs in ({'now':True},{'patient_id':False},{'event_prefix_max':-1},{'decision_prefix_max':1.5}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):view(**kwargs)
        for offset in (False,True,45,15.0,'15'):
            with self.subTest(offset=offset),self.assertRaises(ValueError):
                view(decisions=[decision(1,'A',99,[('H',offset)])])


if __name__=='__main__':unittest.main()
