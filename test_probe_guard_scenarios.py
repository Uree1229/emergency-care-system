"""Seed-7 preservation and realized guard-boundary witnesses; no validation use."""
from copy import deepcopy
import unittest

from simulation.probe_guard_engine import run_probe_guard_branch
from simulation.probe_guard_scenarios import (
    FAMILIES, ORIGINAL_FAMILIES, ADDITIONAL_FAMILIES, DEV_SEEDS, VALIDATION_SEEDS,
    BASE_FAMILIES, make_scenario, build_probe_guard_scenarios,
)
from simulation.recovery_rank_scenarios import make_scenario as old_case


class ProbeGuardScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases={family:make_scenario(7,family) for family in ADDITIONAL_FAMILIES}
        cls.branches={family:run_probe_guard_branch(case,allocation='H1',variant='renewed')
            for family,case in cls.cases.items()}

    def branch(self,suffix):
        return self.branches['probe_guard_'+suffix]

    def selected(self,suffix):
        rows=[r for r in self.branch(suffix)['probe_guard_records'] if r['shadow_decision']['send']]
        self.assertTrue(rows,'the original optional-probe selection must actually be reached')
        return rows[0]

    def test_manifest_keeps_all129_and_appends_six_boundaries(self):
        self.assertEqual((len(ORIGINAL_FAMILIES),len(ADDITIONAL_FAMILIES),len(FAMILIES)),(129,6,135))
        self.assertEqual(FAMILIES,ORIGINAL_FAMILIES+ADDITIONAL_FAMILIES)
        self.assertEqual(len(set(FAMILIES)),135)
        self.assertEqual(DEV_SEEDS,(44101,44102,44103))
        self.assertEqual(VALIDATION_SEEDS,tuple(range(48101,48107)))

    def test_every_inherited_small_input_is_completely_equal_and_detached(self):
        for family in ORIGINAL_FAMILIES:
            actual=make_scenario(7,family)
            self.assertEqual(actual,old_case(7,family))
            actual['family']='mutated'
            self.assertEqual(make_scenario(7,family),old_case(7,family))

    def test_new_cases_preserve_original_supply_routes_queue_and_priority(self):
        for family,case in self.cases.items():
            previous=old_case(7,BASE_FAMILIES[family])
            self.assertEqual([(h['id'],h['capacity'],h['lat'],h['lon']) for h in case['hospitals']],
                [(h['id'],h['capacity'],h['lat'],h['lon']) for h in previous['hospitals']])
            self.assertEqual(case['inquiry_queue'],previous['inquiry_queue'])
            for key in ('priority','treatment_minutes','location','ambulance_origin'):
                self.assertEqual(case['patients'][0][key],previous['patients'][0][key])
            self.assertFalse(case['experiment']['additional_resource_change_channel_enabled'])
            self.assertFalse(self.branches[family]['resource_updates_enabled'])
            self.assertEqual(case['resource_update_config'],previous['resource_update_config'])

    def test_positive_and_hidden_release_twins_have_identical_decision_information(self):
        positive=self.selected('renewed_fresh_negative')
        adverse=self.selected('hidden_recovery_adverse_twin')
        self.assertEqual(positive['snapshot'],adverse['snapshot'])
        self.assertEqual(positive['snapshot']['t'],1.5)
        self.assertEqual(positive['snapshot']['patient']['first_ask']['submitted_at'],0)
        for row in (positive,adverse):
            self.assertEqual(row['guard_decision']['action'],'veto')
            self.assertEqual(row['shadow_decision']['send'],['H01'])
        def received(branch):
            return [(e['t'],e['hospital'],e['observed_at'],e['resources'])
                for e in branch['ordered_emissions'] if e['type']=='resource_report_received' and e['t']<=1.5]
        self.assertEqual(received(self.branch('renewed_fresh_negative')),
            received(self.branch('hidden_recovery_adverse_twin')))

    def test_renewed_veto_is_strictly_after_real_first_ask_and_real_due(self):
        for suffix in ('renewed_fresh_negative','hidden_recovery_adverse_twin'):
            row=self.selected(suffix);snapshot=row['snapshot'];patient=snapshot['patient']
            selected=next(c for c in snapshot['candidates'] if c['hospital']==row['shadow_decision']['send'][0])
            bed=selected['resources']['ed_bed']
            self.assertEqual(bed['observed_at'],1)
            self.assertGreater(bed['observed_at'],patient['first_ask']['submitted_at'])
            self.assertGreaterEqual(snapshot['t'],patient['first_ask']['submitted_at']+.5)
            self.assertGreaterEqual(snapshot['t'],patient['last_contact_at']+.5)
            self.assertLess(bed['free'],patient['demand']['ed_bed'])

    def test_equal_and_old_sources_delegate_the_original_selected_probe(self):
        for suffix,source in (('same_observed_timestamp',0),('predated_late',0),('out_of_order',1)):
            row=self.selected(suffix);snapshot=row['snapshot']
            bed=next(c for c in snapshot['candidates'] if c['hospital']=='H01')['resources']['ed_bed']
            self.assertEqual(bed['observed_at'],source)
            self.assertLessEqual(bed['observed_at'],snapshot['patient']['first_ask']['submitted_at'])
            self.assertEqual(row['guard_decision']['action'],'delegate')
            actual=self.branch(suffix)['shortage_probe_records']
            self.assertTrue(any(r['snapshot']==snapshot and r['result']==row['shadow_decision'] for r in actual))

    def test_late_and_out_of_order_packets_really_arrive_before_the_due_review(self):
        for suffix in ('predated_late','out_of_order'):
            row=self.selected(suffix)
            receipt=2
            packets=[e for e in self.branch(suffix)['ordered_emissions'] if e['type']=='resource_report_received'
                and e['hospital']=='H01' and e['observed_at']==0 and e['source']=='feed' and e['t']==receipt]
            self.assertTrue(packets)
            self.assertLess(packets[-1]['event_sequence'],row['event_sequence'])
            if suffix=='out_of_order':
                self.assertFalse(packets[-1]['applied'])
            else:
                self.assertTrue(packets[-1]['applied'])

    def test_second_required_resource_keeps_the_original_probe_ineligible(self):
        rows=self.branch('required_second_resource')['probe_guard_records']
        witnesses=[r for r in rows if any(c['hospital']=='H01' and c['reason']=='OTHER_RESOURCE_SHORTAGE'
            for c in r['shadow_decision']['candidate_checks'])]
        self.assertTrue(witnesses)
        for row in witnesses:
            self.assertEqual(row['shadow_decision']['send'],[])
            self.assertEqual(row['guard_decision']['action'],'delegate')
        self.assertEqual(self.branch('required_second_resource')['metrics']['shortage_probe_sent'],0)

    def test_future_hidden_release_is_a_private_difference_not_guard_information(self):
        positive=self.cases['probe_guard_renewed_fresh_negative']
        adverse=self.cases['probe_guard_hidden_recovery_adverse_twin']
        self.assertEqual(positive['observation_config'],adverse['observation_config'])
        self.assertEqual(positive['response_matrix'],adverse['response_matrix'])
        self.assertEqual(positive['patients'],adverse['patients'])
        self.assertEqual(positive['hospitals'][0]['initial_occupancy'][0]['until'],8)
        self.assertEqual(adverse['hospitals'][0]['initial_occupancy'][0]['until'],2)
        for branch in (self.branch('renewed_fresh_negative'),self.branch('hidden_recovery_adverse_twin')):
            for row in branch['probe_guard_records']:
                self.assertNotIn('initial_occupancy',row['snapshot'])
                self.assertNotIn('next_free_at',row['snapshot'])

    def test_selection_validation_and_returned_values_are_detached(self):
        self.assertEqual(build_probe_guard_scenarios(7,[]),[])
        selected=list(reversed(ADDITIONAL_FAMILIES))
        self.assertEqual([c['family'] for c in build_probe_guard_scenarios(7,selected)],selected)
        case=deepcopy(self.cases[ADDITIONAL_FAMILIES[0]])
        case['hospitals'][0]['capacity']['ed_bed']=999
        self.assertNotEqual(make_scenario(7,ADDITIONAL_FAMILIES[0]),case)
        for seed in (True,False,-1,'7',7.0,None):
            with self.assertRaises(ValueError):build_probe_guard_scenarios(seed)
        for selection in ('bad',b'bad',3,[None],['bad'],[FAMILIES[0],FAMILIES[0]]):
            with self.assertRaises(ValueError):build_probe_guard_scenarios(7,selection)


if __name__=='__main__':unittest.main()
