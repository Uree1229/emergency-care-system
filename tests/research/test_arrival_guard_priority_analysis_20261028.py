"""Small four-arm arithmetic fixtures; no replay or policy execution."""
from copy import deepcopy
import unittest

from simulation import arrival_guard_priority_analysis_20261028 as a
from test_final_assignment_analysis_20261028 import case, patient


def four(index=1, before=None, original=None, candidate=None, track='main', stratum='routine_single'):
    value=case(index,before,original,track,stratum)
    rows=deepcopy(value['audit']['patient_vectors']['H1'] if candidate is None else candidate)
    value['audit']['patient_vectors']['G2']=rows
    counts=a.reference._case_metrics(rows)
    value['audit']['branch_metrics']['G2']=dict(counts,
        unassigned_priority_fraction=counts['scenario_mean_unassigned_priority_fraction'])
    return value


class ArrivalGuardAnalysisTests(unittest.TestCase):
    def test_explicit_alias_preserves_original_H1_and_input(self):
        value=four(original=[patient(arrive=30)],candidate=[patient(arrive=12)])
        saved=deepcopy(value)
        output=a.analyze([value],phase='validation',goals={},bootstrap_samples=20)
        self.assertEqual(value,saved)
        self.assertEqual(output['frozen_bootstrap_projection'],{'R0':'R0','R1':'R1','H1':'G2'})
        group=output['groups'][0]
        self.assertEqual(group['policies']['H1']['arrival_capped_mean_minutes'],30)
        self.assertEqual(group['policies']['G2']['arrival_capped_mean_minutes'],12)
        self.assertEqual(group['direct_comparisons']['H1']['common_arrival']['mean_delta_minutes'],-18)
        self.assertEqual(output['frozen_bootstrap']['groups'][0]['policies']['H1']['arrival_capped_mean_minutes'],12)

    def test_missing_arrival_stays_in_fixed_R1_cohort_and_original_targets(self):
        value=four(before=[patient(arrive=10)],original=[patient(arrive=30)],candidate=[patient(selected=False)],stratum='surge')
        out=a.analyze([value],phase='validation',goals={},bootstrap_samples=10)
        tail=out['groups'][0]['arrival_tail_cohorts']
        self.assertEqual(tail['fixed_R1_arrived']['denominator'],1)
        self.assertEqual(tail['fixed_R1_arrived']['delayed_count'],1)
        self.assertEqual(tail['fixed_R1_arrived']['maximum_delay_minutes'],50)
        self.assertEqual(tail['fixed_R1_arrived']['candidate_missing_count'],1)
        self.assertEqual(tail['original_delayed_targets']['denominator'],1)
        self.assertEqual(tail['actual_shared_arrival']['denominator'],0)
        self.assertEqual(out['groups'][0]['direct_comparisons']['H1']['arrived_lost_count'],1)

    def test_one_family_success_gain_cannot_compensate_other_loss(self):
        first=four(index=1,original=[patient()],candidate=[patient(selected=False)],track='stress',stratum='one')
        second=four(index=2,original=[patient(selected=False)],candidate=[patient()],track='stress',stratum='two')
        out=a.analyze([first,second],phase='validation',goals={},bootstrap_samples=10)
        self.assertFalse(out['goals']['passed'])
        failed=[r['name'] for r in out['goals']['checks'] if not r['passed']]
        self.assertIn('stress/one/unassigned',failed)

    def test_exact_success_ids_reported_even_if_count_and_weight_unchanged(self):
        before=[patient('A'),patient('B',selected=False)]
        after=[patient('A',selected=False),patient('B')]
        out=a.analyze([four(before=before,original=before,candidate=after)],phase='validation',goals={},bootstrap_samples=10)
        self.assertTrue(out['goals']['passed'])
        self.assertFalse(out['groups'][0]['exact_old_H1_success_IDs_preserved'])
        comparison=out['groups'][0]['direct_comparisons']['H1']
        self.assertEqual(comparison['selected_lost_count'],1)
        self.assertEqual(comparison['selected_gained_count'],1)
        priority=out['groups'][0]['priority_group_comparisons']['1']['H1']
        self.assertEqual(priority['selected_lost_count'],1)
        self.assertEqual(priority['arrived_gained_count'],1)

    def test_priority4_gate_detects_equal_weight_equal_count_swap(self):
        original=[patient('A',priority=4),patient('B',selected=False,priority=2),patient('C',selected=False,priority=2)]
        new=[patient('A',selected=False,priority=4),patient('B',priority=2),patient('C',priority=2)]
        out=a.analyze([four(before=original,original=original,candidate=new)],phase='validation',goals={},bootstrap_samples=10)
        self.assertFalse(out['goals']['passed'])
        failed=[r['name'] for r in out['goals']['checks'] if not r['passed']]
        self.assertEqual(failed,['main/routine_single/priority4_unassigned'])

    def test_four_arm_source_mismatch_rejected(self):
        value=four();value['audit']['patient_vectors']['G2'][0]['id']='DIFFERENT'
        with self.assertRaises(ValueError):a.analyze([value],phase='validation',goals={},bootstrap_samples=10)

    def test_vector_metric_mismatch_cannot_falsely_pass(self):
        value=four();value['audit']['branch_metrics']['G2']['unassigned']=1
        with self.assertRaises(ValueError):a.analyze([value],phase='validation',goals={},bootstrap_samples=10)

    def test_regression_goal_strict_boundary_and_cohort_validation(self):
        groups=[]
        for track,stratum in [('main','surge'),('stress','large_simultaneous'),('stress','scarce_greedy_trap'),('stress','scarce_long_detour')]:
            row=dict(track=track,stratum=stratum,coverage_preservation={c:dict(unassigned=0,unassigned_priority_weight=0) for c in ('H1','G2')},
                priority_groups={'4':{c:dict(unassigned=0) for c in ('H1','G2')}})
            if track=='main':row['arrival_tail_cohorts']=dict(fixed_R1_arrived=dict(denominator=1,delayed_count=1,maximum_delay_minutes=4),
                original_delayed_targets=dict(denominator=1,delayed_count=1),actual_shared_arrival=dict(maximum_delay_minutes=4))
            groups.append(row)
        goals=dict(known_surge_unassigned_maximum=0,known_stress_large_simultaneous_unassigned_maximum=0,
            known_stress_scarce_greedy_trap_unassigned_maximum=0,known_stress_scarce_long_detour_unassigned_maximum=0,
            fixed_R1_arrived_known_surge_population=1,original_delayed_known_surge_target_count=1,
            known_fixed_R1_arrived_delayed_count_exclusive_maximum=1,known_fixed_R1_arrived_capped_delay_minutes_exclusive_maximum=4,
            original_target_remaining_delayed_count_exclusive_maximum=1,known_shared_arrival_delay_minutes_exclusive_maximum=4)
        result=a.evaluate_goals(groups,'regression',goals)
        self.assertEqual(sum(not r['passed'] for r in result['checks']),4)
        goals['original_delayed_known_surge_target_count']=2
        with self.assertRaises(ValueError):a.evaluate_goals(groups,'regression',goals)


if __name__=='__main__':unittest.main()
