"""Unchanged inherited inputs and visible boundary versus private future."""
from copy import deepcopy
import unittest

from simulation import offer_reserve_scenarios as new
from simulation import public_offer_scenarios as old


class OfferReserveScenariosTests(unittest.TestCase):
    def test_all_141_seed7_inherited_inputs_are_equal_and_detached(self):
        self.assertEqual(len(old.FAMILIES),141)
        for family in old.FAMILIES:
            with self.subTest(family=family):
                case=new.make_scenario(7,family)
                self.assertEqual(case,old.make_scenario(7,family))
                case['patients'][0]['priority']=999
                self.assertNotEqual(case,new.make_scenario(7,family))

    def test_new_eight_validate_and_generation_order_is_detached(self):
        cases=new.build_offer_reserve_scenarios(7,new.ADDITIONAL_FAMILIES)
        self.assertEqual(len(cases),8)
        reverse=new.build_offer_reserve_scenarios(7,reversed(new.ADDITIONAL_FAMILIES))
        self.assertEqual(cases,list(reversed(reverse)))
        for case in cases:
            self.assertEqual(case['experiment']['patient_count'],len(case['patients']))
            self.assertFalse(case['experiment']['additional_resource_change_channel_enabled'])

    def test_exact_and_below_reserve_environment_boundary(self):
        for family,capacity in (('offer_reserve_ample_normal',4),('offer_reserve_below_boundary',3)):
            case=new.make_scenario(7,family)
            h=next(h for h in case['hospitals'] if h['id']=='H03')
            self.assertEqual(h['capacity']['ed_bed'],capacity)
            self.assertEqual(h['initial_occupancy'],[dict(until=1,demand=dict(ed_bed=capacity))])
            self.assertEqual(case['patients'][0]['resource_demands'],{'ed_bed':2})

    def test_future_twins_keep_common_hospitals_patients_work_and_reports(self):
        base=new.make_scenario(7,'offer_reserve_ample_normal')
        for family in ('offer_reserve_same_demand_future','offer_reserve_greater_demand_future',
                       'offer_reserve_repeated_future','offer_reserve_ample_future_outage'):
            twin=new.make_scenario(7,family)
            self.assertEqual(base['hospitals'],twin['hospitals'])
            self.assertEqual(base['patients'],twin['patients'][:2])
            for field in ('observation_config','inquiry_queue'):
                self.assertEqual(base[field],twin[field])
            for pid,row in base['response_matrix'].items():
                self.assertEqual(row,twin['response_matrix'][pid])

    def test_future_and_current_h03_only_requests_are_explicit_environment(self):
        for family,times,demands in (
            ('offer_reserve_same_demand_future',[2],[2]),
            ('offer_reserve_greater_demand_future',[2],[3]),
            ('offer_reserve_repeated_future',[2,3],[2,2]),
            ('offer_reserve_simultaneous_current',[0],[2])):
            case=new.make_scenario(7,family)
            self.assertEqual([p['search_start_t'] for p in case['patients'][2:]],times)
            self.assertEqual([p['resource_demands']['ed_bed'] for p in case['patients'][2:]],demands)
            for patient in case['patients'][2:]:
                compatible=[h['id'] for h in case['hospitals']
                    if set(patient['required_capabilities'])<=set(h['capabilities'])]
                self.assertEqual(compatible,['H03'])

    def test_second_resource_and_future_outage_are_not_capacity_freebies(self):
        case=new.make_scenario(7,'offer_reserve_second_resource_failure')
        self.assertEqual(case['patients'][0]['resource_demands'],{'ed_bed':2,'special_team':1})
        h=next(h for h in case['hospitals'] if h['id']=='H03')
        self.assertEqual(h['capacity'],{'ed_bed':4,'special_team':1})
        case=new.make_scenario(7,'offer_reserve_ample_future_outage')
        self.assertEqual(case['exogenous_events'],[dict(type='resource_unavailable',t=4,until=6,
            hospital='H03',resource='ed_bed')])

    def test_invalid_arguments_are_rejected(self):
        for seed in (True,-1,'7',1.2):
            with self.assertRaises(ValueError):new.make_scenario(seed,new.ADDITIONAL_FAMILIES[0])
        for families in ('bad',['bad'],[new.FAMILIES[0]]*2,3):
            with self.assertRaises(ValueError):new.build_offer_reserve_scenarios(7,families)
        self.assertEqual(new.build_offer_reserve_scenarios(7,[]),[])


if __name__=='__main__':unittest.main()
