"""Check whole-cohort censoring and independent state-area evaluation."""
from copy import deepcopy
import unittest

from simulation.total_wait_metrics import waiting_metrics


class TotalWaitMetricsTests(unittest.TestCase):
    def fixture(self):
        scenario=dict(max_minutes=20,patients=[dict(id='A',search_start_t=0,priority=4),
            dict(id='B',search_start_t=1,priority=3),dict(id='C',search_start_t=2,priority=2)])
        transitions={'A':[(0,'request'),(2,'commit'),(5,'arrive'),(6,'arrival_unable'),
            (8,'transfer_search'),(10,'commit'),(13,'arrive'),(16,'treatment_start')],
            'B':[(1,'request'),(4,'commit')],'C':[(2,'request'),(3,'no_compatible')]}
        events=sorted([dict(t=t,type=kind,patient=pid) for pid,values in transitions.items()
                       for t,kind in values],key=lambda e:e['t'])
        branch=dict(events=events,patient_outcomes=[dict(id=pid,treatment_start_t=16 if pid=='A' else None)
                                                 for pid in transitions])
        return scenario,branch

    def test_state_integral_excludes_transit_but_counts_transfer_and_unserviceable_search(self):
        scenario,branch=self.fixture();value=waiting_metrics(scenario,branch)
        self.assertEqual(value['total_wait_sum_minutes'],31)
        self.assertEqual((value['search_wait_sum_minutes'],value['queue_wait_sum_minutes'],
                          value['transfer_wait_sum_minutes']),(25,4,2))
        self.assertEqual(value['priority_weighted_total_wait_sum_minutes'],85)
        self.assertEqual(value['max_patient_wait_minutes'],18)

    def test_untreated_patients_remain_in_denominator_and_treatment_time_is_capped(self):
        scenario,branch=self.fixture();value=waiting_metrics(scenario,branch)
        self.assertEqual((value['patients'],value['treated_denominator'],value['untreated']),(3,1,2))
        self.assertEqual(value['request_to_treatment_sum_minutes'],16)
        self.assertEqual(value['request_to_treatment_capped_sum_minutes'],53)
        self.assertEqual(value['priority_weighted_request_to_treatment_capped_sum_minutes'],157)
        self.assertIsNone(value['patient_details'][1]['request_to_treatment_minutes'])

    def test_longer_unresolved_observation_increases_all_patient_wait(self):
        scenario,branch=self.fixture();before=waiting_metrics(scenario,branch)
        scenario['max_minutes']=25;after=waiting_metrics(scenario,branch)
        self.assertEqual(after['total_wait_sum_minutes']-before['total_wait_sum_minutes'],5)
        self.assertEqual(after['request_to_treatment_capped_sum_minutes']-before['request_to_treatment_capped_sum_minutes'],10)

    def test_missing_request_cannot_silently_drop_a_patient(self):
        scenario,branch=self.fixture();branch['events']=[e for e in branch['events'] if e['patient']!='C']
        with self.assertRaises(ValueError):waiting_metrics(scenario,branch)

    def test_metrics_do_not_mutate_raw_evidence(self):
        scenario,branch=self.fixture();before=deepcopy((scenario,branch))
        waiting_metrics(scenario,branch);self.assertEqual(before,(scenario,branch))


if __name__=='__main__':unittest.main()
