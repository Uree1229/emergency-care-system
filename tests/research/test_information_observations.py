"""Causal, per-resource cache checks without a simulator or solver."""
import math
import unittest

from simulation.information_observations import ResourceObservationStore


class ResourceObservationStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = ResourceObservationStore()

    def apply(self, resources, *, observed=0, received=0, now=0, sequence=0, hospital='H'):
        return self.store.apply(hospital, resources, observed_at=observed,
            received_at=received, now=now, sequence=sequence)

    def test_strict_ten_minute_boundary_and_negative_initial_source_time(self):
        self.apply({'bed':dict(capacity=3, used=1)},observed=-3)
        self.assertEqual(self.store.get('H','bed',now=7)['status'],'fresh')
        view=self.store.get('H','bed',now=7.01)
        self.assertEqual(view['status'],'stale')
        self.assertAlmostEqual(view['age_minutes'],10.01)
        self.assertEqual(view['free'],2)

    def test_unknown_is_not_zero_and_zero_is_known(self):
        missing=self.store.get('H','bed',now=0,committed=1)
        self.assertEqual(missing['status'],'unknown')
        self.assertIsNone(missing['free']); self.assertIsNone(missing['age_minutes'])
        self.apply({'bed':dict(capacity=0,used=0)})
        zero=self.store.get('H','bed',now=0)
        self.assertEqual((zero['status'],zero['free']),('fresh',0))
        self.apply({'bed':None},observed=1,received=1,now=1)
        unknown=self.store.get('H','bed',now=50)
        self.assertEqual(unknown['status'],'unknown'); self.assertIsNone(unknown['free'])
        self.assertEqual(unknown['observed_at'],1)
        self.assertEqual(unknown['age_minutes'],49)

    def test_either_missing_quantity_is_unknown(self):
        for quantities in (dict(capacity=None,used=2),dict(capacity=3,used=None),
                           dict(capacity=None,used=None)):
            store=ResourceObservationStore()
            store.apply('H',{'bed':quantities},observed_at=0,received_at=0,now=0)
            view=store.get('H','bed',now=0)
            self.assertEqual(view['status'],'unknown'); self.assertIsNone(view['free'])

    def test_delayed_receipt_does_not_freshen_source_time(self):
        self.apply({'bed':dict(capacity=2,used=0)},observed=0,received=12,now=12)
        view=self.store.get('H','bed',now=12)
        self.assertEqual((view['status'],view['age_minutes']),('stale',12))
        self.assertEqual(view['received_at'],12)

    def test_older_packet_and_duplicate_stamp_cannot_replace_known_or_unknown(self):
        self.apply({'bed':dict(capacity=4,used=1)},observed=2,received=3,now=3,sequence=5)
        for observed,sequence in ((1,999),(2,4),(2,5)):
            self.assertFalse(self.apply({'bed':None},observed=observed,received=4,now=4,sequence=sequence))
        self.assertEqual(self.store.get('H','bed',now=4)['free'],3)
        self.assertTrue(self.apply({'bed':None},observed=2,received=4,now=4,sequence=6))
        self.assertFalse(self.apply({'bed':dict(capacity=9,used=0)},observed=2,received=5,now=5,sequence=5))
        self.assertIsNone(self.store.get('H','bed',now=5)['free'])

    def test_newer_source_time_wins_even_with_lower_sequence(self):
        self.apply({'bed':dict(capacity=2,used=0)},sequence=999)
        self.assertTrue(self.apply({'bed':dict(capacity=3,used=0)},observed=1,received=1,now=1))
        self.assertEqual(self.store.get('H','bed',now=1)['free'],3)

    def test_per_resource_versioning_and_omitted_resource_are_preserved(self):
        self.apply({'bed':dict(capacity=4,used=1)},observed=5,received=5,now=5)
        self.apply({'team':dict(capacity=1,used=1)},observed=1,received=5,now=5)
        self.assertTrue(self.apply({'bed':None,'team':dict(capacity=2,used=0)},
                                  observed=3,received=6,now=6))
        snapshot=self.store.snapshot('H',['bed','team'],now=6)
        self.assertEqual(snapshot['bed']['free'],3)
        self.assertEqual(snapshot['bed']['observed_at'],5)
        self.assertEqual(snapshot['team']['free'],2)
        self.apply({'team':None},observed=7,received=7,now=7)
        self.assertEqual(self.store.get('H','bed',now=7)['free'],3)

    def test_commitments_queue_and_over_capacity_produce_real_shortage(self):
        self.apply({'bed':dict(capacity=4,used=2)})
        view=self.store.get('H','bed',now=0,committed=2,queued=1)
        self.assertEqual(view['free'],-1)
        self.apply({'team':dict(capacity=1,used=3)})
        self.assertEqual(self.store.get('H','team',now=0)['free'],-2)
        self.assertEqual(self.store.get('H','team',now=11)['status'],'stale')

    def test_input_get_and_snapshot_mutations_do_not_change_store(self):
        packet={'bed':dict(capacity=3,used=1)}
        self.apply(packet); packet['bed']['capacity']=999
        view=self.store.get('H','bed',now=0); view['capacity']=999
        snapshot=self.store.snapshot('H',['bed','team'],now=0); snapshot['bed']['used']=999
        self.assertEqual(self.store.get('H','bed',now=0)['free'],2)
        self.assertIsNone(self.store.get('H','team',now=0)['free'])

    def test_hospitals_are_isolated_and_snapshot_uses_caller_demand(self):
        self.apply({'bed':dict(capacity=5,used=1)},hospital='A')
        self.apply({'bed':dict(capacity=2,used=0)},hospital='B')
        snap=self.store.snapshot('A',['bed','team'],now=0,committed={'bed':2},queued={'bed':1})
        self.assertEqual(snap['bed']['free'],1)
        self.assertEqual(self.store.get('B','bed',now=0)['free'],2)
        self.assertEqual(self.store.get('A','bed',now=0)['committed'],0)

    def test_future_poison_is_rejected_atomically(self):
        self.apply({'bed':dict(capacity=3,used=0)})
        before=self.store.snapshot('H',['bed','team'],now=0)
        for observed,received,now in ((1,0,0),(0,1,0),(20,20,1)):
            with self.subTest(observed=observed,received=received,now=now),self.assertRaises(ValueError):
                self.apply({'bed':None,'team':dict(capacity=100,used=0)},
                           observed=observed,received=received,now=now)
            self.assertEqual(self.store.snapshot('H',['bed','team'],now=0),before)

    def test_invalid_quantities_reject_whole_packet_even_if_old(self):
        self.apply({'bed':dict(capacity=3,used=0)})
        for field in ('capacity','used'):
            for value in (True,False,-1,1.5,math.inf,math.nan,'1'):
                packet={'bed':dict(capacity=99,used=0),'team':dict(capacity=1,used=0)}
                packet['team'][field]=value
                with self.subTest(field=field,value=value),self.assertRaises(ValueError): self.apply(packet)
                self.assertEqual(self.store.get('H','bed',now=0)['free'],3)
                self.assertIsNone(self.store.get('H','team',now=0)['free'])

    def test_invalid_times_sequence_and_threshold_are_rejected(self):
        for value in (True,math.nan,math.inf,'0'):
            for key in ('observed','received','now'):
                with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                    self.apply({'bed':None},**{key:value})
        for key in ('received','now'):
            with self.assertRaises(ValueError): self.apply({'bed':None},**{key:-1})
        for sequence in (True,-1,1.5,math.inf):
            with self.assertRaises(ValueError): self.apply({'bed':None},sequence=sequence)
        for threshold in (True,-1,math.nan,math.inf):
            with self.assertRaises(ValueError): ResourceObservationStore(threshold)

    def test_invalid_payload_and_current_demand_are_rejected(self):
        for payload in (None,[],{'bed':{}},{'bed':{'capacity':1}},{'bed':1},
                        {'bed':dict(capacity=1,used=0,extra=1)}):
            with self.subTest(payload=payload),self.assertRaises(ValueError): self.apply(payload)
        for field in ('committed','queued'):
            for value in (None,True,-1,1.5,math.inf):
                with self.subTest(field=field,value=value),self.assertRaises(ValueError):
                    self.store.get('H','bed',now=0,**{field:value})

    def test_empty_packet_is_noop_and_old_read_cannot_expose_future_receipt(self):
        self.assertFalse(self.apply({}))
        self.apply({'bed':dict(capacity=2,used=0)},observed=1,received=2,now=2)
        with self.assertRaises(ValueError): self.store.get('H','bed',now=1)


if __name__ == '__main__':
    unittest.main()
