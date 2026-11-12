"""Small arithmetic and bootstrap contracts; no policy or simulator imports."""
from copy import deepcopy
import math
import unittest

from simulation import final_assignment_analysis_20261028 as a


def patient(pid='P',selected=True,arrive=10,priority=1,inquiries=2,end=60):
    commit=3 if selected else None
    arrive=arrive if selected else None
    return dict(id=pid,priority=priority,occur_t=0,search_start_t=1,observation_end_t=end,
        first_commit_t=commit,final_arrive_t=arrive,treatment_start_t=arrive,
        selected=selected,arrived=arrive is not None,inquiries=inquiries,
        selection_elapsed_minutes=None if commit is None else commit-1,
        arrival_elapsed_minutes=arrive,
        selection_capped_minutes=end-1 if commit is None else commit-1,
        arrival_capped_minutes=end if arrive is None else arrive)


def case(index=1,before=None,after=None,track='main',stratum='routine_single'):
    before=[patient()] if before is None else before
    after=deepcopy(before) if after is None else after
    vectors={'R0':deepcopy(before),'R1':deepcopy(before),'H1':deepcopy(after)}
    metrics={}
    for code,rows in vectors.items():
        weight=sum(r['priority'] for r in rows);lost=sum(r['priority'] for r in rows if not r['selected'])
        metrics[code]=dict(patients=len(rows),unassigned=sum(not r['selected'] for r in rows),
            not_arrived=sum(not r['arrived'] for r in rows),unassigned_priority_weight=lost,
            priority_weight_total=weight,inquiries=sum(r['inquiries'] for r in rows),unassigned_priority_fraction=lost/weight)
    return dict(case_id=f'{track}-{stratum}-{index}',track=track,stratum=stratum,seed=index,
        scenario_sha256='1'*64,raw_file=f'unit-{index}.json.gz',raw_sha256='2'*64,compressed_sha256='3'*64,
        audit=dict(passed=True,patients=len(before),scenario_sha256='1'*64,patient_vectors=vectors,branch_metrics=metrics))


def comparison(value,baseline='R1'):return value['groups'][0]['comparisons'][baseline]


class FinalAssignmentAnalysisTests(unittest.TestCase):
    def test_nearest_rank_patient_p95_and_linear_bootstrap_quantile_are_distinct(self):
        self.assertEqual(a.percentile([0,10,20],.95),20)
        self.assertEqual(a.linear_quantile([0,10,20],.95),19)
        self.assertIsNone(a.percentile([]))
        self.assertEqual(a.linear_quantile([7],.004),7)
        for values,fraction in (([float('nan')],.5),([1],float('inf')),([True],.5),([1],True),([1],1.1)):
            with self.assertRaises(ValueError):a.percentile(values,fraction)

    def test_identical_results_remain_inconclusive_and_do_not_force_a_winner(self):
        value=a.analyze([case(i) for i in range(4)],100)
        result=comparison(value)
        self.assertEqual(result['classification'],'inconclusive')
        primary=result['bootstrap']['metrics'][a.PRIMARY_METRIC]
        self.assertEqual(primary['ci95']['lower'],0)
        self.assertEqual(primary['familywise_adjusted_ci']['upper'],0)
        self.assertEqual(primary['familywise_adjusted_ci']['family_size'],6)
        self.assertAlmostEqual(primary['familywise_adjusted_ci']['lower_quantile'],.05/12)

    def test_strong_coverage_gain_and_arrival_nonworsening(self):
        rows=[case(i,[patient(selected=False)],[patient(arrive=10)]) for i in range(4)]
        result=comparison(a.analyze(rows,100))
        self.assertEqual(result['classification'],'coverage_gain_capped_arrival_nonworsening')
        self.assertEqual(result['selected_gained_count'],4)
        self.assertEqual(result['selected_lost_count'],0)
        self.assertEqual(result['bootstrap']['metrics'][a.PRIMARY_METRIC]['mean_delta'],-1)
        self.assertEqual(result['common_arrival']['denominator'],0)
        self.assertIsNone(result['common_arrival']['mean_delta_minutes'])
        self.assertEqual(result['all_patient_arrival_capped']['mean_delta_minutes'],-50)

    def test_weighted_coverage_worsening_is_reported_even_if_not_desired(self):
        rows=[case(i,[patient()],[patient(selected=False)]) for i in range(3)]
        result=comparison(a.analyze(rows,60))
        self.assertEqual(result['classification'],'weighted_coverage_worse')
        self.assertEqual(result['selected_lost_count'],3)

    def test_coverage_gain_with_existing_patient_delay_is_tradeoff(self):
        before=[patient('A',arrive=10),patient('B',selected=False)]
        after=[patient('A',arrive=59),patient('B',arrive=58)]
        result=comparison(a.analyze([case(i,before,after) for i in range(3)],80))
        self.assertEqual(result['classification'],'coverage_gain_with_arrival_tradeoff_or_uncertainty')
        self.assertEqual(result['common_arrival']['denominator'],3)
        self.assertEqual(result['common_arrival']['maximum_delay_minutes'],49)
        self.assertEqual(result['common_arrival']['delayed_count'],3)
        self.assertEqual(result['arrived_gained_count'],3)

    def test_cluster_weights_are_equal_scenario_not_patient_pooled(self):
        first=case(1,[patient('A',selected=False)],[patient('A')])
        before=[patient(str(i)) for i in range(8)]
        after=[patient(str(i),selected=i>=2) for i in range(8)]
        second=case(2,before,after)
        value=a.analyze([first,second],100)
        group=value['groups'][0];result=comparison(value)
        self.assertEqual(group['policies']['H1']['unassigned'],2)
        self.assertEqual(group['policies']['R1']['unassigned'],1)
        self.assertEqual(result['bootstrap']['metrics'][a.PRIMARY_METRIC]['mean_delta'],-.375)
        self.assertAlmostEqual(group['policies']['H1'][a.PRIMARY_METRIC],.125)
        self.assertAlmostEqual(group['policies']['H1']['pooled_patient_unassigned_priority_fraction'],2/9)
        self.assertEqual(result['classification'],'inconclusive')

    def test_bootstrap_repeatability_and_input_order_invariance_without_mutation(self):
        rows=[case(i,[patient(arrive=10+i)],[patient(arrive=9+i)]) for i in range(5)]
        before=deepcopy(rows);first=a.analyze(rows,130);second=a.analyze(list(reversed(rows)),130)
        self.assertEqual(first,second);self.assertEqual(rows,before)
        boot=comparison(first)['bootstrap']
        self.assertEqual(boot['resampling_unit'],'whole_scenario_cluster')
        self.assertFalse(boot['patient_rows_resampled_independently'])

    def test_stress_is_separate_and_descriptive_without_confidence_intervals(self):
        main=case(1)
        stress=case(1,[patient(selected=False)],[patient()],track='stress',stratum='scarcity_trap')
        value=a.analyze([stress,main],80)
        self.assertEqual(len(value['groups']),2)
        g=next(row for row in value['groups'] if row['track']=='stress')
        self.assertIsNone(g['comparisons']['R1']['bootstrap'])
        self.assertEqual(g['comparisons']['R1']['classification'],'descriptive_only_stress')
        self.assertFalse(value['stress_pooled_into_main'])
        self.assertEqual(value['main_primary_comparisons_present'],2)

    def test_typed_ids_and_lost_success_are_not_hidden_by_new_success(self):
        before=[patient(1),patient('1',selected=False)]
        after=[patient(1,selected=False),patient('1')]
        result=comparison(a.analyze([case(1,before,after)],60))
        self.assertEqual(result['selected_lost_count'],1);self.assertEqual(result['selected_gained_count'],1)
        self.assertEqual(result['selected_lost_identifiers'][0]['patient_type'],'int')
        self.assertEqual(result['selected_gained_identifiers'][0]['patient_type'],'str')
        self.assertEqual(result['classification'],'inconclusive')

    def test_primary_uses_priority_weight_even_when_success_count_is_unchanged(self):
        before=[patient('HIGH',selected=False,priority=4),patient('LOW',priority=1)]
        after=[patient('HIGH',priority=4),patient('LOW',selected=False,priority=1)]
        value=a.analyze([case(i,before,after) for i in range(3)],80)
        result=comparison(value)
        self.assertEqual(value['groups'][0]['policies']['R1']['selected'],3)
        self.assertEqual(value['groups'][0]['policies']['H1']['selected'],3)
        self.assertAlmostEqual(result['bootstrap']['metrics'][a.PRIMARY_METRIC]['mean_delta'],-.6)
        self.assertEqual(result['selected_lost_count'],3)
        self.assertEqual(result['selected_gained_count'],3)
        self.assertEqual(result['selected_lost_identifiers'][0]['priority'],1)

    def test_different_observation_windows_and_unregistered_main_strata_rejected(self):
        rows=[patient('A'),patient('B',end=80)]
        with self.assertRaises(ValueError):a.analyze([case(1,rows)],50)
        with self.assertRaises(ValueError):a.analyze([case(1,stratum='new_unregistered_condition')],50)
        value=case(1);value['raw_sha256']='not-a-sha'
        with self.assertRaises(ValueError):a.analyze([value],50)

    def test_conditional_arrivals_and_full_cap_denominators_are_explicit(self):
        rows=[patient('A',arrive=10),patient('B',selected=False),patient('C',arrive=None)]
        value=a.analyze([case(1,rows)],50)['groups'][0]['policies']['R1']
        self.assertEqual(value['patients'],3);self.assertEqual(value['conditional_arrival_denominator'],1)
        self.assertEqual(value['conditional_arrival_mean_minutes'],10)
        self.assertAlmostEqual(value['arrival_capped_mean_minutes'],130/3)
        self.assertEqual(value['arrival_capped_p95_minutes'],60)

    def test_duplicate_case_seed_or_patient_partition_is_rejected(self):
        value=case(1)
        with self.assertRaises(ValueError):a.analyze([value,deepcopy(value)],40)
        duplicate=deepcopy(value);duplicate['case_id']='DIFFERENT'
        with self.assertRaises(ValueError):a.analyze([value,duplicate],40)
        wrong=case(1);wrong['audit']['patient_vectors']['H1'][0]['id']='OTHER'
        with self.assertRaises(ValueError):a.analyze([wrong],40)

    def test_nonfinite_bool_invalid_counts_and_audit_mismatches_rejected(self):
        for field,new in (('inquiries',True),('priority',True),('arrival_capped_minutes',float('nan')),
                          ('first_commit_t',float('inf')),('selected',1),('selection_elapsed_minutes',7)):
            value=case(1);value['audit']['patient_vectors']['H1'][0][field]=new
            with self.assertRaises(ValueError):a.analyze([value],50)
        for field,new in (('patients',True),('inquiries',-1),('unassigned_priority_fraction',float('nan'))):
            value=case(1);value['audit']['branch_metrics']['R0'][field]=new
            with self.assertRaises(ValueError):a.analyze([value],50)
        for samples in (True,0,-1,float('nan')):
            with self.assertRaises(ValueError):a.analyze([case(1)],samples)
        for field,new in (('passed',False),('scenario_sha256','a'*64)):
            value=case(1);value['audit'][field]=new
            with self.assertRaises(ValueError):a.analyze([value],50)

if __name__=='__main__':unittest.main(verbosity=2)
