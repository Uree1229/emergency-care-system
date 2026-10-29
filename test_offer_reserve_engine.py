"""Seed-7 real workflow checks with every executed diagnostic preserved."""
from copy import deepcopy
import gzip
import json
from pathlib import Path
import unittest
from uuid import uuid4

from simulation.offer_reserve_engine import (IndependentOfferReserveReplay,
    CooperativeOfferReserveReplay,NAMESPACE_FIELDS,EXPERIMENT_ID,
    PUBLIC_OFFER_SPEC,PUBLIC_OFFER_FOUNDATION_SPEC,run_offer_reserve_branch)
from simulation.offer_reserve_policy import decide_offer_reserve
from simulation.offer_reserve_scenarios import make_scenario
from simulation.public_offer_engine import OfferReadyJointStage


DIAGNOSTICS=Path(__file__).resolve().parent/'.codex-build/offer-reserve-20261020/engine-diagnostics'


def save_raw(scenario,branch):
    DIAGNOSTICS.mkdir(parents=True,exist_ok=True)
    content=(json.dumps(dict(schema_version=1,experiment_id='offer_reserve_engine_diagnostic_20261020',
        scenario=deepcopy(scenario),branches=[branch],execution_order=[branch['id']]),
        ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n').encode('utf-8')
    path=DIAGNOSTICS/f'{scenario["family"]}-{branch["id"]}-{uuid4().hex}.json.gz'
    with path.open('xb') as stream:stream.write(gzip.compress(content,mtime=0))
    return path


class OfferReserveEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios={};cls.branches={};cls.paths={}
        for family in ('offer_reserve_ample_normal','public_offer_later_scarce_patient',
            'offer_reserve_greater_demand_future','offer_reserve_ample_future_outage','out_of_order'):
            scenario=make_scenario(7,family)
            branch=run_offer_reserve_branch(scenario)
            cls.paths[family]=save_raw(scenario,branch)
            cls.scenarios[family]=scenario;cls.branches[family]=branch

    def test_frozen_joint_stage_is_inherited_without_a_new_resolve_body(self):
        self.assertNotIn('resolve_batch',IndependentOfferReserveReplay.__dict__)
        self.assertNotIn('resolve_batch',CooperativeOfferReserveReplay.__dict__)
        for cls in (IndependentOfferReserveReplay,CooperativeOfferReserveReplay):
            self.assertIn(OfferReadyJointStage,cls.__mro__)
            names=[row.__name__ for row in cls.__mro__]
            self.assertLess(names.index('OfferReserveMixin'),names.index('PublicOfferMixin'))
            self.assertLess(names.index('OfferReadyJointStage'),names.index('JointPolicyReplay'))

    def test_actual_positive_has_a_literal_all_options_all_resources_proof(self):
        branch=self.branches['offer_reserve_ample_normal']
        rows=[row for row in branch['public_offer_records'] if row['decision']['eligible']]
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row['decision'],decide_offer_reserve(snapshot=row['snapshot']))
            self.assertTrue(row['decision']['base_decision']['eligible'])
            self.assertTrue(all(option['passed'] for option in row['decision']['option_checks']))
            for option in row['decision']['option_checks']:
                self.assertTrue(all(r['free']>=r['cohort_demand']+r['own_demand']
                    for r in option['resources'].values()))

    def test_original_22_minute_boundary_is_refused_on_current_quantity(self):
        rows=self.branches['public_offer_later_scarce_patient']['public_offer_records']
        blocked=[row for row in rows if row['decision']['base_decision']['eligible']]
        self.assertTrue(blocked)
        row=blocked[0]
        self.assertEqual(row['snapshot']['evaluated_t'],1.5)
        self.assertFalse(row['decision']['eligible'])
        proof=row['decision']['option_checks'][0]['resources']['ed_bed']
        self.assertEqual((proof['free'],proof['cohort_demand'],proof['required_free']),(2,2,4))
        self.assertNotIn('P03',[p['id'] for p in row['snapshot']['original_snapshot']['search_patients']])

    def test_future_event_twins_have_identical_actual_first_review_and_decision(self):
        names=('offer_reserve_ample_normal','offer_reserve_greater_demand_future',
               'offer_reserve_ample_future_outage')
        rows=[next(row for row in self.branches[name]['public_offer_records']
            if row['decision']['eligible']) for name in names]
        self.assertEqual(rows[0]['snapshot'],rows[1]['snapshot'])
        self.assertEqual(rows[0]['snapshot'],rows[2]['snapshot'])
        self.assertEqual(rows[0]['decision'],rows[1]['decision'])
        self.assertEqual(rows[0]['decision'],rows[2]['decision'])

    def test_original_marker_backup_lineage_and_deadlines_are_preserved(self):
        events=self.branches['offer_reserve_ample_normal']['ordered_emissions']
        marker=next(e for e in events if e['type']=='public_offer_ready')
        backup=next(e for e in events if e['type']=='backup_retained'
            and e['early_ready_event_sequence']==marker['event_sequence'])
        self.assertTrue(backup['pending'])
        sources={e['event_sequence']:e for e in events}
        for handle in backup['pending']:
            ask=sources[handle['ask_event_sequence']]
            self.assertEqual((handle['submitted_at'],handle['timeout_at']),
                (ask['submitted_at'],ask['timeout_at']))
        self.assertFalse(any(e['type'].startswith('offer_reserve_') for e in events))

    def test_stage_order_and_actual_option_order_are_unchanged(self):
        events=self.branches['offer_reserve_ample_normal']['ordered_emissions']
        for marker in (e for e in events if e['type']=='public_offer_ready'):
            at=[e for e in events if e['t']==marker['t']]
            first=lambda kind:min(e['event_sequence'] for e in at if e['type']==kind)
            self.assertLess(first('early_offer_cycle'),first('public_offer_cycle'))
            self.assertLess(marker['event_sequence'],first('opportunity_guard'))
            self.assertLess(first('opportunity_guard'),first('pending_opportunity_guard'))
            checks=[e['hospital'] for e in at if e['event_sequence']>marker['event_sequence']
                and e['type']=='information_check' and e.get('patient')==marker['patient']
                and e['purpose']=='allocation']
            self.assertEqual(checks[:len(marker['options'])],marker['options'])

    def test_literal_contract_distinguishes_new_rule_and_frozen_base(self):
        branch=self.branches['offer_reserve_ample_normal']
        namespace={k for k in branch if k.startswith('public_offer_') or k.startswith('offer_reserve_')}
        self.assertEqual(namespace,NAMESPACE_FIELDS)
        self.assertEqual(branch['comparison_id'],EXPERIMENT_ID)
        self.assertEqual(branch['public_offer_variant'],'cohort_reserve')
        self.assertEqual(branch['public_offer_spec'],PUBLIC_OFFER_SPEC)
        self.assertEqual(branch['public_offer_foundation_spec'],dict(PUBLIC_OFFER_FOUNDATION_SPEC,mode='cooperative_queue'))
        self.assertEqual(branch['public_offer_base_contract']['recovery_rank_selector'],'recovery_rank_normal')
        self.assertEqual(branch['offer_reserve_base_contract']['public_offer_spec']['variants'],['public_blocked'])
        self.assertTrue(branch['offer_reserve_base_contract']['descriptors_describe_frozen_base_not_actual_new_rule'])

    def test_normal_seven_inputs_original_clocks_and_no_new_reports(self):
        for family,branch in self.branches.items():
            self.assertFalse(branch['resource_updates_enabled'])
            for row in branch['inquiry_decisions']:
                self.assertEqual(len(row['inputs']),7)
                self.assertEqual(row['decision']['evidence']['policy'],'recovery_rank_normal')
            for event in branch['ordered_emissions']:
                if event['type']=='ask':
                    self.assertEqual(event['timeout_at']-event['submitted_at'],
                        self.scenarios[family].get('timeout_minutes',6))
        independent=self.branches['out_of_order']
        self.assertFalse(independent.get('cooperative_queue_enabled',False))
        self.assertEqual(independent['public_offer_foundation_spec']['mode'],'independent_replies')

    def test_each_diagnostic_is_saved_and_original_scenario_unchanged(self):
        for family,path in self.paths.items():
            raw=json.loads(gzip.decompress(path.read_bytes()))
            self.assertEqual(raw['branches'][0],self.branches[family])
            self.assertEqual(raw['scenario'],make_scenario(7,family))

    def test_invalid_allocation_and_variant_fail_before_execution(self):
        case=self.scenarios['offer_reserve_ample_normal']
        for kw in (dict(allocation='R1'),dict(variant='public_blocked')):
            with self.assertRaises(ValueError):run_offer_reserve_branch(case,**kw)


if __name__=='__main__':unittest.main()
