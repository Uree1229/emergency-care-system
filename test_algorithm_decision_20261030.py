import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simulation import algorithm_decision_20261030 as decision


def endpoint(pid='P1',*,arrived=180,treated=180,commit=20,end=180):
    return dict(id=pid,priority=4,occur_t=54,search_start_t=60,observation_end_t=end,
        first_commit_t=commit,final_arrive_t=arrived,treatment_start_t=treated,
        selected=commit is not None,arrived=arrived is not None,inquiries=1,
        selection_elapsed_minutes=commit-60 if commit is not None else None,
        arrival_elapsed_minutes=arrived-54 if arrived is not None else None,
        selection_capped_minutes=(commit if commit is not None else end)-60,
        arrival_capped_minutes=(arrived if arrived is not None else end)-54)


def paired(a,b):return decision.pair(a,b,profile='fixture',raw_path='raw/test.json.gz',case_id='unit',baseline='R0')


class ClassificationTests(unittest.TestCase):
    def test_zero_capped_difference_does_not_hide_arrival_treatment_loss(self):
        r=paired(endpoint(),endpoint(arrived=None,treated=None))
        self.assertEqual(r['delta_capped_minutes'],0)
        self.assertEqual(r['time_direction'],'equal_capped')
        self.assertEqual(r['category'],'success_lost')
        self.assertEqual(r['success_lost'],['arrived','treated'])
        self.assertEqual(decision.stats([r])['success_lost']['arrived'],1)

    def test_both_censored_are_not_labeled_as_equal_success(self):
        r=paired(endpoint(arrived=None,treated=None),endpoint(arrived=None,treated=None))
        self.assertEqual(r['category'],'both_not_arrived');self.assertEqual(r['time_direction'],'equal_capped')

    def test_full_cohort_priority_loss_and_positive_worsening_are_separate(self):
        earlier=paired(endpoint(arrived=130,treated=130),endpoint(arrived=100,treated=100))
        later=paired(endpoint(arrived=100,treated=100),endpoint(arrived=167,treated=167))
        s=decision.stats([earlier,later])
        self.assertEqual(s['patients'],2);self.assertEqual(s['maximum_positive_capped_difference'],67)
        self.assertEqual(s['high_priority_maximum_positive_capped_difference'],67)
        self.assertEqual(sum(s['success_lost'].values()),0)

    def test_recorded_arrival_at_boundary_is_included_and_unable_revokes_arrival(self):
        raw=dict(original_scenario=dict(patients=[dict(id='P1',priority=4,occur_t=54,search_start_t=60)]),branches=[])
        events=[dict(t=80,type='commit',patient='P1'),dict(t=180,type='arrive',patient='P1'),dict(t=180,type='treatment_start',patient='P1')]
        for code in ('R0','R1','H1'):raw['branches'].append(dict(id=code,events=copy.deepcopy(events),metrics=dict(observation_end_t=180)))
        out=decision.reconstruct(raw);self.assertTrue(out['R0']['P1']['arrived'])
        raw['branches'][-1]['events'].append(dict(t=180,type='arrival_unable',patient='P1'))
        self.assertFalse(decision.reconstruct(raw)['H1']['P1']['arrived'])

    def test_unknown_branch_and_nonfinite_event_are_rejected(self):
        raw=dict(original_scenario=dict(patients=[]),branches=[])
        with self.assertRaises(ValueError):decision.reconstruct(raw)
        raw['branches']=[dict(id='R0',events=[dict(t=float('nan'),type='commit')],metrics=dict(observation_end_t=180))]
        with self.assertRaises(ValueError):decision.reconstruct(raw)

    def test_path_escape_and_duplicate_json_are_rejected(self):
        for name in ('../outside','C:/outside','/outside'):
            with self.assertRaises(ValueError):decision.safe_path(decision.ROOT,name)
        parent=decision.ROOT/'.codex-build/algorithm-decision-tests';parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as tmp:
            path=Path(tmp)/'duplicate.json';path.write_text('{"a":1,"a":2}',encoding='utf-8')
            with self.assertRaises(ValueError):decision.read(path)


class EvidenceTests(unittest.TestCase):
    def test_actual_frozen_raw_reconstruction_keeps_r0_loss_and_r1_denominators(self):
        registry=decision.read(decision.REGISTRY);report,rows=decision.load_evidence(registry)
        self.assertEqual(len(report['cases']),36);self.assertEqual(len(rows),1206)
        r1=decision.stats([r for r in rows if r['baseline']=='R1'])
        self.assertEqual(r1['time_direction'],{'equal_capped':542,'later_capped':34,'earlier_capped':27})
        self.assertEqual(r1['both_not_arrived'],40);self.assertEqual(r1['success_lost']['arrived'],0)
        r0=[r for r in rows if r['baseline']=='R0']
        self.assertEqual(decision.stats(r0)['success_lost']['arrived'],1)
        lost=next(r for r in r0 if r['success_lost'])
        self.assertEqual((lost['raw_path'],lost['patient_id'],lost['delta_capped_minutes']),('raw/027.json.gz','P19',0))

    def test_report_pin_prespecification_and_unvalidated_adoption_cannot_be_relabeled(self):
        registry=decision.read(decision.REGISTRY)
        variants=[]
        pin=copy.deepcopy(registry);first=next(iter(pin['pinned_evidence']));pin['pinned_evidence'][first]='0'*64;variants.append(pin)
        prior=copy.deepcopy(registry);prior['prespecified_adoption_test']=True;variants.append(prior)
        adopt=copy.deepcopy(registry);adopt['research_candidate']['H1']['adopted']=True;variants.append(adopt)
        for variant in variants:
            with self.assertRaises(ValueError):decision.load_evidence(variant)

    def test_existing_output_is_never_overwritten(self):
        with self.assertRaises(FileExistsError):decision.run(decision.ROOT/'outputs/final_evaluation_20261030/holdout-v1')

    def test_export_preserves_source_evidence_and_labels_censored_copy(self):
        registry=decision.read(decision.REGISTRY)
        source=decision.ROOT/registry['causal_case_document'];before=source.read_bytes()
        parent=decision.ROOT/'.codex-build/algorithm-decision-tests';parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as tmp:
            output=Path(tmp)/'new'
            result=decision.run(output)
            self.assertEqual(result['algorithm_executions'],0)
            self.assertFalse(result['runtime_policy_changed'])
            copied=(output/'causal-cases.md').read_text(encoding='utf-8')
            self.assertIn('도착 제한값 동일',copied);self.assertIn('양쪽 미도착 40명',copied)
            self.assertEqual(source.read_bytes(),before)
            manifest=decision.read(output/'manifest.json')
            for name,expected in manifest['files'].items():self.assertEqual(decision.file_hash(output/name),expected)


if __name__=='__main__':unittest.main()
