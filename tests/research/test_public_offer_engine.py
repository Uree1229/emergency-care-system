"""Seed-7 actual MRO, readiness order, and retained backup contracts."""
from copy import deepcopy
import gzip
import json
from pathlib import Path
import unittest
from uuid import uuid4

from simulation.public_offer_engine import (CooperativePublicOfferReplay,IndependentPublicOfferReplay,
    OfferReadyJointStage,NAMESPACE_FIELDS,EXPERIMENT_ID,run_public_offer_branch)
from simulation.public_offer_policy import decide_public_offer
from simulation.public_offer_scenarios import make_scenario
from simulation.recovery_rank_engine import run_recovery_rank_branch
from simulation.joint_policy_engine import JointPolicyReplay


DIAGNOSTICS=Path(__file__).resolve().parents[2]/'.codex-build/public-offer-20261020/engine-diagnostics'


def save_raw(scenario,branch):
    """Every small actual run is saved before test assertions or audit reuse."""
    DIAGNOSTICS.mkdir(parents=True,exist_ok=True)
    content=(json.dumps(dict(schema_version=1,experiment_id='public_offer_engine_diagnostic_20261020',
        scenario=deepcopy(scenario),branches=[branch],execution_order=[branch['id']]),
        ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n').encode('utf-8')
    target=DIAGNOSTICS/f'{scenario["family"]}-{branch["id"]}-{uuid4().hex}.json.gz'
    with target.open('xb') as stream:stream.write(gzip.compress(content,mtime=0))
    return target


class PublicOfferEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases={};cls.branches={};cls.paths={}
        for family in ('public_offer_fresh_shortage','public_offer_fresh_available_closer','out_of_order'):
            scenario=make_scenario(7,family);cls.cases[family]=scenario
            branch=run_public_offer_branch(scenario);cls.paths[family]=save_raw(scenario,branch)
            cls.branches[family]=branch
        cls.reference=run_recovery_rank_branch(cls.cases['out_of_order'],variant='normal')
        cls.reference_path=save_raw(cls.cases['out_of_order'],cls.reference)

    def test_mro_places_new_joint_stage_after_existing_work_and_before_joint(self):
        for cls in (IndependentPublicOfferReplay,CooperativePublicOfferReplay):
            names=[c.__name__ for c in cls.__mro__]
            self.assertLess(names.index('ShortageProbeReplay'),names.index('OfferReadyJointStage'))
            self.assertLess(names.index('DeadlineRecoveryReplay'),names.index('OfferReadyJointStage'))
            self.assertLess(names.index('OfferReadyJointStage'),names.index('JointPolicyReplay'))
            self.assertEqual(cls.resolve_batch.__qualname__,
                'CooperativeProbeReplay.resolve_batch' if cls is CooperativePublicOfferReplay else 'ShortageProbeReplay.resolve_batch')
        self.assertTrue(issubclass(OfferReadyJointStage,JointPolicyReplay))

    def test_positive_new_stage_has_real_current_shortage_proof(self):
        branch=self.branches['public_offer_fresh_shortage']
        positive=[r for r in branch['public_offer_records'] if r['decision']['eligible']]
        self.assertTrue(positive)
        row=positive[0]
        self.assertEqual(row['shadow_decision']['reason'],'NOT_DOMINANT')
        self.assertTrue(all(c['publicly_blocked'] for c in row['decision']['candidate_checks']))
        self.assertEqual(row['decision'],decide_public_offer(snapshot=row['snapshot']))

    def test_current_fresh_sufficient_closer_is_not_ignored(self):
        branch=self.branches['public_offer_fresh_available_closer']
        rows=[r for r in branch['public_offer_records'] if r['snapshot']['evaluated_t']==1]
        self.assertTrue(rows)
        self.assertEqual(rows[0]['decision']['reason'],'BETTER_STRUCTURAL_NOT_PUBLICLY_BLOCKED')

    def test_original_stages_precede_new_stage_and_all_guards_follow(self):
        branch=self.branches['public_offer_fresh_shortage'];events=branch['ordered_emissions']
        positive=next(r for r in branch['public_offer_records'] if r['decision']['eligible'])
        at=positive['snapshot']['evaluated_t'];current=[e for e in events if e['t']==at]
        seq=lambda kind:min(e['event_sequence'] for e in current if e['type']==kind)
        self.assertLess(seq('shortage_probe_cycle'),seq('early_offer_cycle'))
        self.assertLess(seq('early_offer_cycle'),seq('public_offer_cycle'))
        self.assertLess(seq('public_offer_ready'),seq('opportunity_guard'))
        self.assertLess(seq('opportunity_guard'),seq('pending_opportunity_guard'))
        self.assertLess(seq('pending_opportunity_guard'),seq('allocation_proposal'))

    def test_new_marker_retains_actual_pending_backup_with_original_deadline(self):
        branch=self.branches['public_offer_fresh_shortage'];events=branch['ordered_emissions']
        ready=next(e for e in events if e['type']=='public_offer_ready')
        backup=next(e for e in events if e['type']=='backup_retained'
            and e['early_ready_event_sequence']==ready['event_sequence'])
        self.assertEqual(backup['patient'],ready['patient'])
        self.assertTrue(backup['pending'])
        source={e['event_sequence']:e for e in events}
        for handle in backup['pending']:
            ask=source[handle['ask_event_sequence']]
            self.assertEqual((handle['submitted_at'],handle['timeout_at']),
                (ask['submitted_at'],ask['timeout_at']))
        self.assertFalse(any(e['type']=='early_offer_ready' and e.get('review_event_sequence')==ready['review_event_sequence'] for e in events))

    def test_new_options_follow_real_acceptance_order_and_allocation_checks(self):
        branch=self.branches['public_offer_fresh_shortage'];events=branch['ordered_emissions']
        for marker in (e for e in events if e['type']=='public_offer_ready'):
            checks=[e['hospital'] for e in events if e['t']==marker['t'] and e['event_sequence']>marker['event_sequence']
                and e['type']=='information_check' and e.get('patient')==marker['patient']
                and e['purpose']=='allocation']
            self.assertEqual(checks[:len(marker['options'])],marker['options'])

    def test_actual_identity_and_inherited_contract_are_separate(self):
        branch=self.branches['public_offer_fresh_shortage']
        self.assertEqual({k for k in branch if k.startswith('public_offer_')},NAMESPACE_FIELDS)
        self.assertEqual(branch['comparison_id'],EXPERIMENT_ID)
        self.assertTrue(branch['public_offer_foundation_spec']['actual_readiness_changed'])
        self.assertFalse(branch['public_offer_foundation_spec']['actual_normal_selector_changed'])
        self.assertEqual(branch['public_offer_base_contract']['recovery_rank_selector'],'recovery_rank_normal')
        self.assertNotIn('recovery_rank_foundation_spec',branch)
        self.assertNotIn('shortage_probe_spec',branch)

    def test_normal_selector_inputs_clocks_workers_and_reports_remain_original(self):
        branch=self.branches['public_offer_fresh_shortage']
        for row in branch['inquiry_decisions']:
            self.assertEqual(len(row['inputs']),7)
            self.assertEqual(row['decision']['evidence']['policy'],'recovery_rank_normal')
        for event in branch['ordered_emissions']:
            if event['type']=='ask':self.assertEqual(event['timeout_at']-event['submitted_at'],3)
        self.assertFalse(branch['resource_updates_enabled'])
        self.assertTrue(branch['cooperative_queue_enabled'])

    def test_independent_no_extra_ready_preserves_actual_outcomes(self):
        branch=self.branches['out_of_order']
        self.assertEqual(branch['public_offer_foundation_spec']['mode'],'independent_replies')
        self.assertFalse(any(e['type'].startswith('cooperative_') for e in branch['ordered_emissions']))
        self.assertEqual(branch['metrics']['public_offer_ready'],0)
        self.assertEqual(branch['patient_outcomes'],self.reference['patient_outcomes'])

    def test_new_cycles_and_review_accounting_are_literal_raw(self):
        for family,branch in self.branches.items():
            cycles=[e['t'] for e in branch['ordered_emissions'] if e['type']=='public_offer_cycle']
            self.assertEqual(cycles,[x/4 for x in range(int(self.cases[family]['max_minutes']*4)+1)])
            self.assertEqual(branch['metrics']['public_offer_reviews'],len(branch['public_offer_records']))

    def test_diagnostic_raw_is_saved_before_assertions_and_original_input_unchanged(self):
        for family,path in self.paths.items():
            raw=json.loads(gzip.decompress(path.read_bytes()))
            self.assertEqual(raw['scenario'],self.cases[family])
            self.assertEqual(raw['branches'][0],self.branches[family])
            self.assertEqual(self.cases[family],make_scenario(7,family))

    def test_invalid_rule_and_allocation_are_rejected_without_a_run(self):
        case=self.cases['public_offer_fresh_shortage']
        for kwargs in (dict(variant='pending_only'),dict(allocation='R1')):
            with self.assertRaises(ValueError):run_public_offer_branch(case,**kwargs)


if __name__=='__main__':unittest.main()
