import gzip
import json
from pathlib import Path
import tempfile
import unittest

from simulation import joint_policy_experiment as experiment


def summary(treatment=10, inquiries=3, treated=True):
    return dict(patients=1, untreated=int(not treated), total_wait_sum_minutes=treatment-3,
        queue_wait_sum_minutes=0,inquiry_service_minutes=0,
        request_to_treatment_capped_sum_minutes=treatment, inquiries=inquiries,
        resource_confirmation_requests=0, added_resource_publications=0,
        selection_capped_sum_minutes=treatment-3, lost_treatment_denominator=1,
        final_arrival_capped_sum_minutes=treatment,
        patient_rows=[dict(patient='P01', priority=4, treated=treated,
            treatment_capped_minutes=treatment, selection_capped_minutes=treatment-3,
            final_arrival_capped_minutes=treatment,
            treatment_start_t=treatment if treated else None, final_arrival_t=treatment if treated else None,
            first_commit_t=treatment-3, inquiries=inquiries)])


def case(v3=10, v4=9, inquiries_v3=10, inquiries_v4=10, v3_treated=True, v4_treated=True):
    summaries={code: summary() for code in experiment.POLICY_IDS}
    for allocation in ('H1','C1'):
        summaries['v3_'+allocation]=summary(v3,inquiries_v3,v3_treated)
        summaries['v4_'+allocation]=summary(v4,inquiries_v4,v4_treated)
    return dict(summaries=summaries,
        comparisons={name:experiment.compare(summaries[a],summaries[b]) for name,a,b in experiment.PAIRS})


class JointPolicyExperimentTests(unittest.TestCase):
    def test_candidate_requires_actual_treatment_improvement(self):
        chosen=experiment.select_inquiry([case(v3=10,v4=10)])
        self.assertEqual(chosen['H1']['inquiry'],'v3')

    def test_safe_strict_improvement_can_be_selected(self):
        chosen=experiment.select_inquiry([case()])
        self.assertEqual({k:v['inquiry'] for k,v in chosen.items()},{'H1':'v4','C1':'v4'})

    def test_one_harmed_patient_cannot_hide_in_average(self):
        chosen=experiment.select_inquiry([case(v3=20,v4=1),case(v3=10,v4=10.25)])
        self.assertEqual(chosen['H1']['inquiry'],'v3')
        self.assertFalse(chosen['H1']['checks']['no_individual_treatment_worsening'])

    def test_candidate_inquiry_cost_has_explicit_budget(self):
        self.assertEqual(experiment.select_inquiry([case(inquiries_v4=11)])['H1']['inquiry'],'v4')
        self.assertEqual(experiment.select_inquiry([case(inquiries_v4=12)])['H1']['inquiry'],'v3')

    def test_lost_treatment_is_rejected_even_at_censor_tie(self):
        chosen=experiment.select_inquiry([case(v3=10,v4=10,v3_treated=True,v4_treated=False)])
        self.assertFalse(chosen['C1']['checks']['no_lost_treatments'])

    def test_selection_separate_for_each_allocation(self):
        row=case()
        row['summaries']['v4_H1']=summary(11,10)
        row['comparisons']['v4_vs_v3_H1']=experiment.compare(row['summaries']['v3_H1'],row['summaries']['v4_H1'])
        chosen=experiment.select_inquiry([row])
        self.assertEqual(chosen['H1']['inquiry'],'v3')
        self.assertEqual(chosen['C1']['inquiry'],'v4')

    def test_patient_population_cannot_change(self):
        a,b=summary(),summary()
        b['patient_rows'][0]['patient']='OTHER'
        with self.assertRaises(ValueError):experiment.compare(a,b)

    def test_comparison_preserves_signed_tail_and_gained_treatment(self):
        a,b=summary(60,treated=False),summary(14)
        result=experiment.compare(a,b)
        self.assertEqual(result['newly_treated'],1)
        self.assertEqual(result['differences'][0]['treatment_delta_minutes'],-46)
        self.assertEqual(result['worsened'],0)

    def test_case_hashes_both_compressed_and_raw(self):
        with tempfile.TemporaryDirectory() as folder:
            raw=experiment._raw({'events':[{'t':.25}]})
            compressed=gzip.compress(raw,mtime=0)
            path=Path(folder)/'case.json.gz';path.write_bytes(compressed)
            index=dict(file=path.name,compressed_sha256=experiment._sha(compressed),raw_sha256=experiment._sha(raw))
            self.assertEqual(experiment.read_case(index,folder),{'events':[{'t':.25}]})
            index['raw_sha256']='0'*64
            with self.assertRaises(ValueError):experiment.read_case(index,folder)
            index['compressed_sha256']='0'*64
            with self.assertRaises(ValueError):experiment.read_case(index,folder)

    def test_artifact_paths_cannot_escape_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                experiment.read_case(dict(file='../escaped.json.gz'),folder)

    def test_write_new_never_overwrites_artifact(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'report.json'
            experiment._write_new(p,b'original')
            with self.assertRaises(FileExistsError):experiment._write_new(p,b'new')
            self.assertEqual(p.read_bytes(),b'original')

    def test_execution_order_has_ten_unique_factorial_branches(self):
        self.assertEqual(len(experiment.POLICY_IDS),10)
        self.assertEqual(len(set(experiment.POLICY_IDS)),10)
        for allocation in ('H1','C1'):
            for code in ('fixed3','v3','v4'):
                self.assertIn(code+'_'+allocation,experiment.POLICY_IDS)

    def test_late_arrival_cannot_hide_behind_same_treatment_time(self):
        a,b=summary(),summary()
        b['patient_rows'][0]['final_arrival_capped_minutes']+=2
        row=experiment.compare(a,b)
        self.assertEqual(row['worsened'],0)
        self.assertEqual(row['final_arrival_worsened'],1)
        self.assertEqual(row['maximum_final_arrival_worsening_minutes'],2)

    def test_protocol_is_a_detached_settings_snapshot(self):
        # Runtime versions and file hashing do not affect the mutation contract.
        from unittest.mock import patch
        with patch.object(experiment,'source_hashes',return_value={}),patch.object(experiment,'runtime_versions',return_value={}):
            snapshot=experiment.protocol()
        original=experiment.SETTINGS['selection_rule']['maximum_inquiry_ratio']
        try:
            experiment.SETTINGS['selection_rule']['maximum_inquiry_ratio']=999
            self.assertEqual(snapshot['settings']['selection_rule']['maximum_inquiry_ratio'],original)
        finally:experiment.SETTINGS['selection_rule']['maximum_inquiry_ratio']=original

    def test_locked_protocol_survives_json_disk_roundtrip(self):
        snapshot=experiment.protocol()
        self.assertEqual(snapshot,json.loads(experiment._raw(snapshot)))


if __name__=='__main__':unittest.main()
