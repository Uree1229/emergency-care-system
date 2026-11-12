from copy import deepcopy
import unittest

from simulation import final_assignment_scenarios_20261028 as scenarios
from simulation.protected_augment_replay_20261029 import run_augmented_replay,audit_augment_replay


class ProtectedAugmentReplayTests(unittest.TestCase):
    def case(self,family,seed=77):
        return next(s for s in scenarios.build_stress_cases(seed) if s['family']==family)

    def test_large_batch_does_not_spend_budget_on_only_the_first_admission(self):
        for seed in (0,77,151):
            source=self.case('large_simultaneous',seed)
            result=run_augmented_replay(source);audit=audit_augment_replay(result,source_scenario=source)
            expected=max(0,len(source['patients'])-15)
            self.assertEqual(audit['branch_metrics']['G4']['unassigned'],expected)
            self.assertTrue(audit['passed'])

    def test_scarce_augmentation_keeps_parent_input_and_result_labels(self):
        source=self.case('scarce_greedy_trap');before=deepcopy(source)
        replay=run_augmented_replay(source);saved=deepcopy(replay)
        audit=audit_augment_replay(replay,source_scenario=source)
        self.assertEqual(audit['branch_metrics']['G4']['unassigned'],0)
        self.assertEqual(source,before);self.assertEqual(replay,saved)
        self.assertEqual(audit['actual_algorithm'],'G4')
        self.assertFalse(audit['performance_algorithm_reused'])

    def test_general_and_enroute_change_remain_auditable(self):
        for source in [scenarios.build_case(name,96) for name in scenarios.STRATA]+[self.case('resource_change_enroute')]:
            replay=run_augmented_replay(source);audit=audit_augment_replay(replay,source_scenario=source)
            self.assertTrue(audit['passed'])

    def test_audit_rejects_wrong_labels_schedule_and_altered_regret(self):
        source=self.case('scarce_greedy_trap');replay=run_augmented_replay(source)
        for mutation in ('label','schedule','regret'):
            bad=deepcopy(replay);branch=bad['branches'][0]
            if mutation=='label':branch['policy']['rule']='G3'
            elif mutation=='schedule':branch['execution_contract']['search_schedule']='different'
            else:
                record=next(r for r in branch['protected_records'] if r['protected_patient_ids'])
                pid=record['protected_patient_ids'][0]
                record['per_patient_regret'][pid]['positive_travel_regret_minutes']+=1
            with self.subTest(mutation=mutation),self.assertRaises(AssertionError):
                audit_augment_replay(bad,source_scenario=source)


if __name__=='__main__':unittest.main()
