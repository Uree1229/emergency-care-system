"""G3 full replay and independent rejection of altered policy evidence."""
from copy import deepcopy
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest

from simulation import final_assignment_scenarios_20261028 as scenarios
from simulation.protected_replay_20261029 import run_protected_replay,main
from simulation.protected_replay_audit_20261029 import audit_replay


class ProtectedReplayTests(unittest.TestCase):
    def case(self, family, seed=77):
        return next(s for s in scenarios.build_stress_cases(seed) if s['family']==family)

    def test_scarce_resource_augmentation_preserves_both_arrivals(self):
        source=self.case('scarce_greedy_trap')
        before=deepcopy(source)
        replay=run_protected_replay(source)
        audit=audit_replay(replay,source_scenario=source)
        self.assertTrue(audit['passed'])
        self.assertEqual(audit['branch_metrics']['G3']['unassigned'],0)
        self.assertEqual(audit['branch_metrics']['G3']['not_arrived'],0)
        self.assertGreater(audit['counters']['extra_current_admissions'],0)
        self.assertEqual(source,before)

    def test_general_current_snapshots_and_terminal_outcomes_are_auditable(self):
        for stratum in scenarios.STRATA:
            with self.subTest(stratum=stratum):
                source=scenarios.build_case(stratum,96)
                replay=run_protected_replay(source)
                audit=audit_replay(replay,source_scenario=source)
                self.assertEqual(len(audit['patient_vectors']['G3']),len(source['patients']))
                self.assertTrue(audit['passed'])
                self.assertTrue(all(d['rule']=='G3' for d in replay['branches'][0]['allocation_decisions']))

    def test_transit_change_uses_nearest_current_offer_without_global_guarantee(self):
        source=self.case('resource_change_enroute')
        replay=run_protected_replay(source)
        audit=audit_replay(replay,source_scenario=source)
        self.assertTrue(audit['passed'])
        self.assertEqual(audit['branch_metrics']['G3']['redirects'],1)
        self.assertFalse(replay['branches'][0]['execution_contract']['whole_episode_nonregression_guaranteed'])

    def test_auditor_rejects_removed_protected_patient_and_wrong_baseline(self):
        source=self.case('scarce_greedy_trap')
        replay=run_protected_replay(source)
        for mutation in ('remove','baseline','budget','global','objective','regret','count','priority','reason','events'):
            bad=deepcopy(replay)
            branch=bad['branches'][0]
            record=next(r for r in branch['protected_records'] if r['protected_patient_ids'])
            index=record['decision_index'];pid=record['protected_patient_ids'][0]
            if mutation=='remove':
                record['actual_plan'][pid]=None
                branch['allocation_decisions'][index]['assignments'][pid]=None
            elif mutation=='baseline':record['baseline_ready_order_r1_plan'][pid]=None
            elif mutation=='budget':record['work_units']=2049
            elif mutation=='global':record['whole_episode_nonregression_guaranteed']=True
            elif mutation=='objective':record['actual_objective'][2]+=1
            elif mutation=='regret':record['per_patient_regret'][pid]['positive_travel_regret_minutes']+=1
            elif mutation=='count':record['actual_assigned_count']+=1
            elif mutation=='priority':record['actual_priority4_assigned_count']+=1
            elif mutation=='reason':record['reason']='unknown'
            else:
                event=next(e for e in branch['events'] if e['type']=='commit' and e.get('patient')==pid and e['t']==record['t'])
                event['hospital']='not-an-offered-hospital'
            with self.subTest(mutation=mutation),self.assertRaises(AssertionError):
                audit_replay(bad,source_scenario=source)

    def test_source_binding_and_replay_input_are_immutable(self):
        source=self.case('abundant_control')
        replay=run_protected_replay(source)
        altered=deepcopy(source);altered['patients'][0]['priority']=1 if altered['patients'][0]['priority']!=1 else 4
        with self.assertRaises(AssertionError):audit_replay(replay,source_scenario=altered)

    def test_cli_exports_audited_candidate_and_preserves_existing_result(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'input.json';target=Path(directory)/'result.json'
            source.write_text(json.dumps(self.case('scarce_greedy_trap')),encoding='utf-8')
            args=['--input',str(source),'--output',str(target)]
            with redirect_stdout(io.StringIO()):self.assertEqual(main(args),0)
            saved=target.read_bytes()
            result=json.loads(saved)
            self.assertTrue(result['audit']['passed'])
            self.assertFalse(result['operational_adoption'])
            with redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):main(args)
            self.assertEqual(target.read_bytes(),saved)


if __name__=='__main__':unittest.main()
