"""Generator contracts only: no allocations, model execution or raw replay reads."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import unittest

from simulation import final_assignment_scenarios_20261028 as s
from simulation.map_replay import validate_scenario
from simulation.online_allocation_scenarios import FAMILIES, build_online_scenarios

def canonical(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')

class FinalAssignmentScenarioTests(unittest.TestCase):
    def test_same_seed_and_stratum_are_byte_deterministic(self):
        for stratum in s.STRATA:
            seed=s.seed_for('unit',stratum,0)
            self.assertEqual(canonical(s.build_case(stratum,seed)),canonical(s.build_case(stratum,seed)))

    def test_seed_namespaces_are_disjoint_and_outside_old_small_ranges(self):
        seeds=[s.seed_for(split,stratum,i) for split in ('development','validation') for stratum in s.STRATA for i in range(100)]
        self.assertEqual(len(seeds),len(set(seeds)))
        self.assertTrue(all(seed>10**20 for seed in seeds))
        self.assertNotEqual(s.seed_for('development','stress',0),s.seed_for('validation','stress',0))

    def test_exact_input_types_and_invalid_names_rejected(self):
        for seed in (True,-1,1.0,'1',None):
            with self.assertRaises(ValueError):s.build_case('routine_single',seed)
            with self.assertRaises(ValueError):s.build_stress_cases(seed)
        for stratum in ('unknown',None,1):
            with self.assertRaises(ValueError):s.build_case(stratum,1)
        for split,stratum,index in [('old','surge',0),('unit','missing',0),('unit','surge',True),('unit','surge',-1)]:
            with self.assertRaises(ValueError):s.seed_for(split,stratum,index)

    def test_all_main_strata_obey_declared_size_and_validation_contracts(self):
        for stratum in s.STRATA:
            lo,hi=s.DISTRIBUTION['strata'][stratum]['patient_count']
            for index in range(12):
                case=s.build_case(stratum,s.seed_for('unit',stratum,index))
                validate_scenario(case)
                self.assertTrue(lo<=len(case['patients'])<=hi)
                self.assertTrue(5<=len(case['hospitals'])<=10)
                self.assertEqual(case['evaluation_track'],'main')
                self.assertFalse(case['experiment']['outcome_selection_used'])

    def test_stream_times_have_fixed_order_and_declared_gaps(self):
        case=s.build_case('routine_stream',s.seed_for('unit','routine_stream',5))
        times=[p['occur_t'] for p in case['patients']]
        self.assertEqual(times[0],0)
        self.assertTrue(all(2<=b-a<=8 for a,b in zip(times,times[1:])))
        for patient in case['patients']:
            self.assertEqual([patient[k]-patient['occur_t'] for k in ('dispatch_t','ambulance_arrive_t','search_start_t')],[1,4,6])

    def test_surge_has_only_declared_simultaneous_or_nonempty_wave_modes(self):
        modes=set()
        for index in range(20):
            case=s.build_case('surge',s.seed_for('unit','surge',index))
            mode=case['experiment']['arrival_generation'];modes.add(mode['mode'])
            times={p['occur_t'] for p in case['patients']}
            if mode['mode']=='simultaneous':self.assertEqual(times,{0})
            else:self.assertEqual(times,set(mode['wave_times']))
        self.assertEqual(modes,{'simultaneous','waves'})

    def test_initial_occupancy_never_exceeds_each_structural_capacity(self):
        for stratum in s.STRATA:
            case=s.build_case(stratum,s.seed_for('unit',stratum,7))
            for h in case['hospitals']:
                for resource,total in h['capacity'].items():
                    used=sum(row['demand'].get(resource,0) for row in h['initial_occupancy'])
                    self.assertLessEqual(used,total)
                self.assertTrue(all(5<=row['until']<=90 for row in h['initial_occupancy']))

    def test_main_has_no_outage_walkin_or_hidden_accept_override(self):
        case=s.build_case('surge',s.seed_for('unit','surge',8))
        self.assertEqual(case['exogenous_events'],[])
        self.assertEqual(case['inquiry_queue'],{'enabled':False})
        self.assertEqual(case['max_minutes'],180)
        self.assertNotIn('accept_probability',case)
        self.assertNotIn('response_acceptance_override',case)

    def test_all_potential_response_pairs_are_keyed_and_bounded(self):
        case=s.build_case('surge',s.seed_for('unit','surge',9))
        self.assertEqual(set(case['response_environment']),{p['id'] for p in case['patients']})
        hospitals={h['id'] for h in case['hospitals']}
        for rows in case['response_environment'].values():
            self.assertEqual(set(rows),hospitals)
            for value in rows.values():
                self.assertEqual(set(value),{'attempts'})
                self.assertEqual(len(value['attempts']),64)
                for attempt in value['attempts']:
                    self.assertEqual(set(attempt),{'delay','silent'})
                    self.assertIs(type(attempt['delay']),int)
                    self.assertTrue(1<=attempt['delay']<=5)
                    self.assertIs(type(attempt['silent']),bool)

    def test_hospital_identity_does_not_encode_distance_rank(self):
        case=s.build_case('surge',s.seed_for('unit','surge',10))
        radii=[(h['lat']-s.CENTER[0])**2+(h['lon']-s.CENTER[1])**2 for h in case['hospitals']]
        self.assertNotEqual(radii,sorted(radii))
        self.assertEqual(len(radii),len(set(radii)))

    def test_common_inquiry_contract_and_synthetic_provenance_are_explicit(self):
        case=s.build_case('routine_single',s.seed_for('unit','routine_single',3))
        self.assertEqual(case['experiment']['common_inquiry_settings'],dict(k=3,wait=2,notify=True))
        self.assertTrue(case['synthetic'])
        self.assertFalse(case['experiment']['distribution']['clinical_and_region_representativeness_claimed'])
        self.assertTrue(all(h['identity_source']=='synthetic' for h in case['hospitals']))

    def test_returned_values_do_not_mutate_generator_globals_or_next_case(self):
        before=deepcopy(s.DISTRIBUTION)
        seed=s.seed_for('unit','surge',4);case=s.build_case('surge',seed);original=canonical(case)
        case['experiment']['distribution']['hospital_count_uniform_inclusive'][0]=999
        case['hospitals'][0]['capacity']['ed_bed']=999
        self.assertEqual(s.DISTRIBUTION,before)
        self.assertEqual(canonical(s.build_case('surge',seed)),original)

    def test_global_random_state_is_untouched(self):
        before=random.getstate()
        s.build_case('routine_single',s.seed_for('unit','routine_single',12))
        s.build_stress_cases(s.seed_for('unit','stress',0))
        self.assertEqual(random.getstate(),before)

    def test_stress_all_nine_families_preserve_physical_input_separately(self):
        seed=s.seed_for('unit','stress',1)
        original=build_online_scenarios(seed);current=s.build_stress_cases(seed)
        self.assertEqual([c['family'] for c in current],list(FAMILIES))
        for old,new in zip(original,current):
            self.assertEqual(new['evaluation_track'],'stress')
            self.assertFalse(new['experiment']['main_aggregate_allowed'])
            for key in ('hospitals','patients','exogenous_events','response_environment','max_minutes','timeout_minutes','retry_minutes','accept_ttl_minutes'):
                self.assertEqual(new[key],old[key])

    def test_existing_generator_source_bytes_are_not_modified_by_generation(self):
        file=Path('simulation/online_allocation_scenarios.py')
        before=hashlib.sha256(file.read_bytes()).hexdigest()
        s.build_case('routine_stream',s.seed_for('unit','routine_stream',13))
        s.build_stress_cases(s.seed_for('unit','stress',2))
        self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(),before)

if __name__=='__main__':unittest.main(verbosity=2)
