"""Soundness boundaries for offline capacity-target certificates."""
from copy import deepcopy
import unittest
from inquiry_lab.target_feasibility import exclusive_workload_certificate as certify


def scenario(durations=(170, 102, 42), workers=1, releases=None):
    releases = releases or [0]*len(durations)
    return dict(hospitals=[dict(id='A', workers=workers)],
        patients=[dict(id='N'+str(i), at=r) for i, r in enumerate(releases)],
        potentials=[dict(patient_id='N'+str(i), hospital_id='A', service_seconds=d,
            accept=True, no_response=False) for i, d in enumerate(durations)])


class TargetFeasibilityTests(unittest.TestCase):
    def test_exclusive_overload_proves_at_least_one_loss_without_a_rollout(self):
        s=scenario(); before=deepcopy(s); result=certify(s,['N0','N1','N2'])
        self.assertEqual(s,before)
        self.assertEqual(result['status'],'proven_infeasible')
        bound=result['obstructions'][0]
        self.assertEqual((bound['required_worker_seconds'],bound['available_worker_seconds']), (314,300))
        self.assertEqual((bound['optimistic_success_count_upper_bound'],bound['minimum_failures_in_this_obstruction']), (2,1))
        self.assertEqual(result['policy_runs'],0)
        self.assertEqual(result['clock_rollouts'],0)

    def test_exact_deadline_is_allowed(self):
        self.assertEqual(certify(scenario((170,102,28)),['N0','N1','N2'])['status'],'not_proven_infeasible')

    def test_more_workers_remove_this_bound_without_claiming_feasibility(self):
        result=certify(scenario(workers=2),['N0','N1','N2'])
        self.assertEqual(result['status'],'not_proven_infeasible')
        self.assertIn('absence does not establish feasibility',result['conclusion_scope'])

    def test_no_response_is_not_an_acceptance_path(self):
        s=scenario((50,));s['potentials'][0]['no_response']=True
        result=certify(s,['N0'])
        self.assertEqual(result['timely_responsive_accept_options']['N0'],[])
        self.assertEqual(result['status'],'proven_infeasible')

    def test_impossible_service_length_is_not_a_timely_path(self):
        self.assertEqual(certify(scenario((301,)),['N0'])['status'],'proven_infeasible')

    def test_alternative_hospital_prevents_false_exclusivity(self):
        s=scenario((170,170));s['hospitals'].append(dict(id='B',workers=1))
        s['potentials'] += [dict(patient_id='N'+str(i),hospital_id='B',service_seconds=50,
            accept=i==1,no_response=False) for i in range(2)]
        self.assertEqual(certify(s,['N0','N1'])['status'],'not_proven_infeasible')

    def test_different_release_windows_are_not_collapsed_to_one_300_second_window(self):
        result=certify(scenario((170,170),releases=[0,300]),['N0','N1'])
        self.assertEqual(result['status'],'not_proven_infeasible')

    def test_incomplete_matrix_cannot_be_used_to_prove_exclusivity(self):
        s=scenario();s['hospitals'].append(dict(id='B',workers=1))
        with self.assertRaisesRegex(ValueError,'complete potential matrix'):certify(s,['N0','N1'])

    def test_invalid_or_duplicate_inputs_are_rejected(self):
        for ids in ([],['N0','N0'],['missing']):
            with self.subTest(ids=ids),self.assertRaises(ValueError):certify(scenario(),ids)
        for field,value in (('workers',0),('workers',True)):
            s=scenario();s['hospitals'][0][field]=value
            with self.assertRaises(ValueError):certify(s,['N0'])


if __name__=='__main__':unittest.main()
