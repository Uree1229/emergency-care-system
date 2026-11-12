from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simulation import retry_dispatch_experiment as experiment
from test_joint_policy_experiment import summary

def case(frontier=9,paced=8,balanced=7,reference=10,queries=2):
    policies=experiment.policies_for('development')
    rows={p['id']:summary(reference,3) for p in policies}
    for a in ('H1','C1'):
        for v,t in (('frontier',frontier),('paced',paced),('balanced',balanced)):
            rows[experiment.candidate_id(a,v)]=summary(t,queries)
    for r in rows.values():
        for field in experiment.AGGREGATE_FIELDS:r.setdefault(field,0)
    pairs=experiment.pairs_for(policies)
    return dict(family=experiment.FAMILIES[0],summaries=rows,
        comparisons={n:experiment.compare(rows[a],rows[b]) for n,a,b in pairs})

class RetryDispatchExperimentTests(unittest.TestCase):
    def test_development_has_all_ablations_and_unchanged_references(self):
        policies=experiment.policies_for('development')
        self.assertEqual(len(policies),18)
        self.assertEqual(len({p['id'] for p in policies}),18)
        self.assertEqual(policies[:12],list(experiment.old.POLICIES))

    def test_validation_uses_development_choices_not_all_trials(self):
        selected=experiment.select_policy([case()])
        policies=experiment.policies_for('validation',selected)
        self.assertEqual(len(policies),14)
        self.assertEqual({p['variant'] for p in policies if 'variant' in p},{'balanced'})
        selected['H1']['variant']='frontier'
        policies=experiment.policies_for('validation',selected)
        self.assertEqual([p['variant'] for p in policies if 'variant' in p],['frontier','balanced'])

    def test_research_can_pass_without_automatic_deployment(self):
        selected=experiment.select_policy([case()])['H1']
        self.assertTrue(selected['primary_passed'])
        self.assertTrue(selected['deployment_eligible'])
        self.assertFalse(selected['adopted'])
        self.assertEqual(selected['live_default'],'fixed_3')

    def test_one_patient_harmed_cannot_hide_in_aggregate_gain(self):
        rows=[case(frontier=1,paced=1,balanced=1,reference=20),case(frontier=11,paced=11,balanced=11)]
        selected=experiment.select_policy(rows)['H1']
        self.assertFalse(selected['primary_passed'])
        self.assertFalse(selected['primary_checks']['no_individual_treatment_worsening'])

    def test_lost_treatment_is_checked_even_at_censor_tie(self):
        c=case(frontier=10,paced=10,balanced=10)
        c['summaries']['retry_H1_frontier']['patient_rows'][0]['treated']=False
        self.assertFalse(experiment.assess([c],'H1','frontier')['primary_checks']['no_lost_treatments'])

    def test_final_arrival_checked_separately_from_treatment(self):
        c=case()
        c['summaries']['retry_H1_balanced']['patient_rows'][0]['final_arrival_capped_minutes']=11
        self.assertFalse(experiment.assess([c],'H1','balanced')['primary_checks']['no_individual_final_arrival_worsening'])

    def test_case_wait_increase_cannot_hide_in_lower_total(self):
        a,b=case(reference=20),case()
        b['summaries']['retry_H1_balanced']['total_wait_sum_minutes']=8
        result=experiment.assess([a,b],'H1','balanced')
        self.assertFalse(result['primary_checks']['no_case_total_wait_increase'])

    def test_case_capped_increase_checked_even_if_patient_rows_unchanged(self):
        c=case()
        c['summaries']['retry_H1_balanced']['request_to_treatment_capped_sum_minutes']=11
        self.assertFalse(experiment.assess([c],'H1','balanced')['primary_checks']['no_case_capped_treatment_increase'])

    def test_query_decrease_is_required_not_just_bounded_growth(self):
        result=experiment.select_policy([case(queries=3)])['H1']
        self.assertFalse(result['primary_passed'])
        self.assertFalse(result['primary_checks']['strict_total_inquiry_reduction'])

    def test_case_query_increase_cannot_hide_in_aggregate_reduction(self):
        a,b=case(queries=0),case(queries=4)
        result=experiment.assess([a,b],'H1','frontier')
        self.assertTrue(result['primary_checks']['strict_total_inquiry_reduction'])
        self.assertFalse(result['primary_checks']['no_case_inquiry_increase'])

    def test_no_wait_gain_is_not_primary_pass(self):
        self.assertFalse(experiment.assess([case(frontier=10)],'H1','frontier')['primary_passed'])

    def test_previous_probe_gate_is_preserved(self):
        c=case(frontier=9,paced=9,balanced=9)
        c['summaries']['v4_H1_probe']=summary(8,3)
        result=experiment.select_policy([c])['H1']
        self.assertTrue(result['primary_passed'])
        self.assertFalse(result['gates']['vs_previous_probe_population'])
        self.assertFalse(result['deployment_eligible'])

    def test_previous_safe_individual_gate_is_preserved(self):
        c=case()
        c['summaries']['v4_H1_safe']=summary(6,3)
        self.assertFalse(experiment.assess([c],'H1','balanced')['gates']['vs_previous_safe'])

    def test_selection_is_independent_for_each_allocation(self):
        c=case()
        c['summaries']['retry_H1_balanced']=summary(11,2)
        selected=experiment.select_policy([c])
        self.assertEqual(selected['H1']['variant'],'paced')
        self.assertEqual(selected['C1']['variant'],'balanced')

    def test_tie_prefers_less_policy_change(self):
        selected=experiment.select_policy([case(frontier=8,paced=8,balanced=8)])
        self.assertEqual({r['variant'] for r in selected.values()},{'frontier'})

    def test_selection_does_not_mutate_evidence(self):
        c=case();before=deepcopy(c)
        experiment.select_policy([c])
        self.assertEqual(c,before)

    def test_protocol_includes_all_old_sources_and_ten_new_sources(self):
        self.assertEqual(len(experiment.SOURCE_FILES),146)
        self.assertEqual(set(experiment.SOURCE_FILES),set(experiment.old.SOURCE_FILES)|set(experiment.NEW_SOURCES))
        self.assertEqual(tuple(experiment.DEVELOPMENT_SEEDS),(44101,44102,44103))
        self.assertEqual(tuple(experiment.VALIDATION_SEEDS),tuple(range(45101,45107)))

    def test_validation_needs_locked_valid_variant(self):
        with self.assertRaises(ValueError):experiment.policies_for('validation')
        with self.assertRaises(ValueError):experiment.policies_for('validation',{'H1':{'variant':'bad'},'C1':{'variant':'paced'}})

    def test_existing_raw_evidence_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'result';output.with_suffix('.json').write_text('keep')
            with self.assertRaises(ValueError):experiment.execute('development',output,Path(directory)/'lock.json')
            self.assertEqual(output.with_suffix('.json').read_text(),'keep')

    def test_changed_frozen_protocol_rejects_validation(self):
        with patch.object(experiment,'protocol',return_value={'changed':True}):
            with self.assertRaises(ValueError):experiment.verify_lock({'protocol':{}})

    def test_reserved_execution_requires_source_and_seed_freeze(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):experiment.verify_predevelopment_freeze(Path(directory)/'missing.json')

    def test_changed_predevelopment_protocol_is_rejected(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'freeze.json'
            path.write_text(json.dumps(dict(passed=True,frozen_protocol={})))
            with patch.object(experiment,'protocol',return_value={'changed':True}):
                with self.assertRaises(ValueError):experiment.verify_predevelopment_freeze(path)

    def test_freeze_without_input_or_seed_proof_is_rejected(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'freeze.json'
            path.write_text(json.dumps(dict(passed=True,frozen_protocol={})))
            with patch.object(experiment,'protocol',return_value={}):
                with self.assertRaises(ValueError):experiment.verify_predevelopment_freeze(path)

if __name__=='__main__':unittest.main()
