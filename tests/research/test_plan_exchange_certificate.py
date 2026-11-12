"""Pure component laws; no policy connection, CDF calls or candidate reruns."""
from copy import deepcopy
from itertools import permutations
import ast
from pathlib import Path
import unittest
from inquiry_lab import plan_exchange_certificate as c


def own(count=3, submitted=0, patient='OWNER'):
    return [dict(hospital='OWN'+str(i),patient=patient,submitted_at=submitted,travel_seconds=100+i) for i in range(count)]


def certify(now=75, rows=None, reference=None, trial=None):
    return c.certify_exchange(now=now, normalized_own_pending=own() if rows is None else rows,
        reference_plan=dict(H6=0,H7=15) if reference is None else reference,
        trial_plan=dict(H6=15,H7=0) if trial is None else trial)


class PlanExchangeCertificateTests(unittest.TestCase):
    def test_handbuilt_overcap_reference_rejects_exchange_without_rewriting_reference(self):
        # Algebraic regression model only, not a replay of the observed case.
        value=certify()
        self.assertFalse(value['admitted']);self.assertFalse(value['reference']['feasible'])
        self.assertFalse(value['trial']['feasible']);self.assertTrue(value['offset_histogram_equal'])
        self.assertEqual([r['total_fresh_count'] for r in value['reference']['occupancy']],[4,5])
        self.assertEqual(value['reference']['violating_starts'][0]['at'],90)
        self.assertIn('reference_forecast_exceeds_cap',value['rejection_reasons'])
        self.assertFalse(value['reference_decision_replaced']);self.assertFalse(value['plan_reoptimization_performed'])

    def test_later_expiry_fixture_can_pass_but_is_not_an_actual_good_case_claim(self):
        # Hypothesis fixture: no actual beneficial candidate has been verified.
        value=certify(now=165,reference=dict(X=0,Y=30),trial=dict(X=30,Y=0))
        self.assertTrue(value['admitted'])
        self.assertEqual([r['at'] for r in value['reference']['occupancy']],[165,195])
        self.assertEqual([r['total_fresh_count'] for r in value['reference']['occupancy']],[4,2])
        self.assertFalse(value['actual_benefit_validated']);self.assertFalse(value['actual_success_guaranteed'])

    def test_reference_and_trial_both_certified_not_only_trial(self):
        value=certify(reference=dict(A=0,B=15),trial=dict(A=None,B=181))
        self.assertFalse(value['reference']['feasible']);self.assertTrue(value['trial']['feasible'])
        self.assertFalse(value['admitted']);self.assertIn('reference_forecast_exceeds_cap',value['rejection_reasons'])

    def test_exact_original_age180_counts_and181_expires(self):
        a=certify(now=180,reference=dict(A=0,B=1),trial=dict(A=1,B=0))
        self.assertEqual([r['original_fresh_count'] for r in a['reference']['occupancy']],[3,0])
        self.assertEqual([r['total_fresh_count'] for r in a['reference']['occupancy']],[4,2])

    def test_exact_planned_age180_counts_and181_expires(self):
        a=certify(now=0,rows=[],reference=dict(A=0,B=180,C=181),trial=dict(A=181,B=0,C=180))
        self.assertEqual([r['planned_fresh_count'] for r in a['reference']['occupancy']],[1,2,2])
        b=certify(now=0,rows=[],reference=dict(A=0,B=181),trial=dict(A=181,B=0))
        self.assertEqual([r['planned_fresh_count'] for r in b['reference']['occupancy']],[1,1])

    def test_same_wave_counts_all_contacts_at_once(self):
        value=certify(reference=dict(A=0,B=0,C=None),trial=dict(A=None,B=0,C=0))
        self.assertFalse(value['admitted']);self.assertEqual(len(value['reference']['occupancy']),1)
        self.assertEqual(value['reference']['occupancy'][0]['total_fresh_count'],5)

    def test_equal_histogram_implies_equal_occupancy_for_all_permutations(self):
        shapes=[(None,0,15,180),(None,None,0,181),(0,0,30,195)]
        for now in (0,75,165,180,181,500):
            rows=own(3,submitted=0)
            for shape in shapes:
                reference=dict(zip(('A','B','C','D'),shape))
                for offsets in set(permutations(shape)):
                    value=certify(now=now,rows=rows,reference=reference,trial=dict(zip(reference,offsets)))
                    self.assertTrue(value['offset_histogram_equal']);self.assertTrue(value['occupancy_equal'])
                    self.assertEqual(value['reference']['occupancy'],value['trial']['occupancy'])

    def test_stale_source_is_kept_without_counting_it_as_fresh(self):
        rows=own(1,submitted=0)+[dict(hospital='RECENT',patient='OWNER',submitted_at=190.5,travel_seconds=12.25)]
        value=certify(now=200,rows=rows,reference=dict(A=0,B=15),trial=dict(A=15,B=0))
        self.assertEqual(value['normalized_own_pending'],rows)
        self.assertEqual(value['current_original_fresh_count'],1)
        self.assertEqual(value['reference']['occupancy'][0]['original_fresh_count'],1)

    def test_input_values_and_dictionary_insertion_order_are_not_mutated(self):
        rows=own();reference=dict(Z=None,B=0,A=15);trial=dict(A=0,Z=None,B=15)
        before=deepcopy((rows,reference,trial));value=certify(rows=rows,reference=reference,trial=trial)
        self.assertEqual((rows,reference,trial),before)
        self.assertEqual(list(value['reference_plan']),['Z','B','A']);self.assertEqual(list(value['trial_plan']),['A','Z','B'])
        value['normalized_own_pending'][0]['hospital']='MUTATED_COPY';value['reference_plan']['B']=999
        self.assertEqual((rows,reference,trial),before)

    def test_pool_keyset_difference_rejects_even_if_histogram_and_occupancy_match(self):
        value=certify(now=165,reference=dict(A=0,B=30),trial=dict(C=30,D=0))
        self.assertTrue(value['offset_histogram_equal']);self.assertTrue(value['occupancy_equal'])
        self.assertFalse(value['candidate_pool_keyset_equal']);self.assertFalse(value['admitted'])
        self.assertIn('candidate_pool_keyset_differs',value['rejection_reasons'])

    def test_none_swap_keeps_full_pool_keys_and_histogram(self):
        value=certify(now=165,reference=dict(A=0,B=None),trial=dict(A=None,B=0))
        self.assertTrue(value['candidate_pool_keyset_equal']);self.assertTrue(value['offset_histogram_equal']);self.assertTrue(value['admitted'])

    def test_histogram_mismatch_rejects_even_when_both_plans_are_feasible(self):
        value=certify(now=165,reference=dict(A=0,B=30),trial=dict(A=0,B=31))
        self.assertTrue(value['reference']['feasible']);self.assertTrue(value['trial']['feasible'])
        self.assertFalse(value['offset_histogram_equal']);self.assertFalse(value['admitted'])

    def test_normalized_duplicates_fail_closed_instead_of_deduplicating(self):
        rows=own(1);rows.append(deepcopy(rows[0]))
        with self.assertRaisesRegex(ValueError,'duplicate normalized'):certify(rows=rows)
        rows=own(1);rows.append(dict(rows[0],submitted_at=0.0))
        with self.assertRaisesRegex(ValueError,'duplicate normalized'):certify(rows=rows)

    def test_one_typed_owner_and_exact_normalized_fields_required(self):
        for rows in (own(1,patient=1)+[dict(hospital='OTHER',patient='1',submitted_at=0,travel_seconds=1)],
            [dict(own(1)[0],extra='raw')],[{k:v for k,v in own(1)[0].items() if k!='travel_seconds'}],own(1,patient=True)):
            with self.assertRaises(ValueError):certify(rows=rows)
        with self.assertRaises(ValueError):certify(rows=tuple(own()))
        with self.assertRaises(ValueError):certify(rows=iter(own()))
        self.assertTrue(certify(now=165,rows=own(1,patient=1),reference=dict(A=0),trial=dict(A=0))['admitted'])

    def test_future_negative_nonfinite_or_wrong_type_normalized_rows_rejected(self):
        for key,bad in [('submitted_at',76),('submitted_at',-1),('submitted_at',float('nan')),('submitted_at',float('inf')),
            ('submitted_at',True),('travel_seconds',-1),('travel_seconds',float('nan')),('travel_seconds',float('inf')),('travel_seconds',True),
            ('hospital',''),('hospital',1),('patient',None)]:
            rows=own(1);rows[0][key]=bad
            with self.subTest(key=key,bad=bad):
                with self.assertRaises(ValueError):certify(rows=rows)

    def test_now_and_plan_offsets_have_strict_integer_clock_contract(self):
        for now in (-1,True,75.,float('nan'),float('inf'),'75',None):
            with self.assertRaises(ValueError):certify(now=now)
        for offset in (-1,True,15.,float('nan'),float('inf'),'15'):
            with self.assertRaises(ValueError):certify(reference=dict(A=offset),trial=dict(A=offset))
        for plan in ([],{'':0},{1:0}):
            with self.assertRaises(ValueError):certify(reference=plan)

    def test_empty_and_no_planned_start_certificate_is_explicitly_vacuous(self):
        for plan in ({},dict(A=None,B=None)):
            value=certify(reference=plan,trial=plan)
            self.assertTrue(value['admitted']);self.assertEqual(value['reference']['occupancy'],[])
            self.assertEqual(value['reference']['planned_count'],0)

    def test_module_is_standalone_stdlib_only_and_does_not_claim_benefit(self):
        tree=ast.parse(Path(c.__file__).read_text(encoding='utf-8'));modules=[]
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):modules.extend(item.name for item in node.names)
            elif isinstance(node,ast.ImportFrom):modules.append(node.module)
        self.assertEqual(set(modules),{'copy','math'})
        value=certify(now=165,reference=dict(A=0,B=30),trial=dict(A=30,B=0))
        for flag in ('reference_decision_replaced','plan_reoptimization_performed','eligibility_checked','legal_policy_offsets_checked',
            'exchange_topology_checked','utility_or_CDF_scored','actual_benefit_validated','actual_success_guaranteed'):
            self.assertFalse(value[flag])
        self.assertEqual(value['integration_status'],'standalone_not_wired')


if __name__=='__main__':unittest.main()
