"""Synthetic current-public eligibility checks, without scenarios."""
from copy import deepcopy
import unittest
from inquiry_lab import joint_first_context as context


def sources(count=3):
    people = [dict(id=f'P{i}',state='WAITING',version=1) for i in range(count)]
    jobs = [dict(patient_id=p['id'],version=1,started_at=1000,deadline_at=1300,
        next_evaluation_at=1000,enabled=1,policy_name=context.POLICY_NAME) for p in people]
    return dict(now=1000,event_prefix_max=10,decision_prefix_max=0,
        patients=people,jobs=jobs,inquiries=[],decisions=[])


class JointFirstContextTests(unittest.TestCase):
    def test_only_first_two_in_existing_start_then_patient_order(self):
        args = sources()
        args['patients'].reverse()
        args['jobs'].reverse()
        result = context.build_context(**args)
        self.assertEqual(result['pair_patient_ids'],['P0','P1'])
        self.assertEqual([r['patient']['id'] for r in result['eligible']],['P0','P1','P2'])
        args['jobs'][0]['started_at'] = 999
        self.assertEqual(context.build_context(**args)['pair_patient_ids'],['P2','P0'])

    def test_no_future_unregistered_patient_is_invented(self):
        for count in (0,1):
            result = context.build_context(**sources(count))
            self.assertEqual(result['pair_patient_ids'],[])
            self.assertEqual(len(result['eligible']),count)

    def test_ineligible_state_job_due_deadline_policy_and_contact_are_excluded(self):
        changes = [('state','CONFIRMED'),('enabled',0),('next_evaluation_at',1001),
            ('started_at',1001),('deadline_at',1000),('policy_name','fixed_3')]
        for key,value in changes:
            args = sources(3)
            target = args['patients'][0] if key=='state' else args['jobs'][0]
            target[key] = value
            with self.subTest(key=key):
                self.assertEqual(context.build_context(**args)['pair_patient_ids'],['P1','P2'])
        args = sources()
        args['inquiries'] = [dict(patient_id='P0',state='CLOSED')]
        self.assertEqual(context.build_context(**args)['pair_patient_ids'],['P1','P2'])

    def test_prior_zero_send_decision_excludes_first_patient(self):
        args = sources()
        args['decision_prefix_max'] = 2
        args['decisions'] = [dict(id=2,at=1000,patient_id='P0',hospital_ids=[])]
        self.assertEqual(context.build_context(**args)['pair_patient_ids'],['P1','P2'])
        args['decision_prefix_max'] = 1
        self.assertEqual(context.build_context(**args)['pair_patient_ids'],['P0','P1'])

    def test_future_or_duplicate_decision_inside_prefix_fails_closed(self):
        args = sources()
        args['decision_prefix_max'] = 2
        args['decisions'] = [dict(id=1,at=1001,patient_id='P0')]
        with self.assertRaises(ValueError): context.build_context(**args)
        args['decisions'] = [dict(id=1,at=1000,patient_id='P0')]*2
        with self.assertRaises(ValueError): context.build_context(**args)

    def test_missing_duplicate_and_mismatched_requested_pair_fail_closed(self):
        args = sources()
        with self.assertRaises(ValueError):
            context.build_context(**args,pair_patient_ids=['P1','P0'])
        args['patients'].append(deepcopy(args['patients'][0]))
        with self.assertRaises(ValueError): context.build_context(**args)
        args = sources()
        args['jobs'].append(deepcopy(args['jobs'][0]))
        with self.assertRaises(ValueError): context.build_context(**args)
        args = sources()
        del args['jobs'][0]['next_evaluation_at']
        with self.assertRaises(ValueError): context.build_context(**args)

    def test_invalid_context_types_unknown_fields_and_edited_order_fail_closed(self):
        valid = context.build_context(**sources())
        for key,value in [('now',True),('event_prefix_max',-1),('schema_version',True),
                          ('pair_patient_ids',['P1','P0']),('hidden_workers',2)]:
            changed = deepcopy(valid)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): context.validate_context(changed)
        changed = deepcopy(valid)
        changed['eligible'].reverse()
        with self.assertRaises(ValueError): context.validate_context(changed)

    def test_source_and_validated_context_are_detached(self):
        args = sources()
        before = deepcopy(args)
        result = context.build_context(**args)
        copy = context.validate_context(result,now=1000,event_prefix_max=10,decision_prefix_max=0)
        copy['eligible'][0]['patient']['version'] = 99
        self.assertEqual(args,before)
        self.assertEqual(result['eligible'][0]['patient']['version'],1)


if __name__ == '__main__': unittest.main()
