"""Full-raw rank provenance and mutation checks on small seeds only."""
from copy import deepcopy
import json
import subprocess
import sys
import unittest

from simulation import recovery_rank_audit as audit
from simulation.recovery_rank_engine import run_recovery_rank_branch
from simulation.recovery_dispatch_engine import run_recovery_dispatch_branch
from simulation.recovery_dispatch_scenarios import make_scenario
from simulation.recovery_rank_scenarios import make_scenario as make_rank_scenario


class RecoveryRankRawAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases={}
        for f in ('fresh_control','watch_outage_recovery','cooperative_probe_busy_admission',
                  'scarce_contact_concurrency_limits','recovery_dispatch_recovery_then_decline',
                  'recovery_dispatch_slow_reopen_maturity'):
            for v in audit.VARIANTS:
                s=make_scenario(7,f);cls.cases[f,v]=(s,run_recovery_rank_branch(s,variant=v))

    def fixture(self,variant='latched',family='recovery_dispatch_recovery_then_decline'):
        return deepcopy(self.cases[family,variant])

    def reject(self,s,b):
        with self.assertRaises((audit.RecoveryRankAuditError,ValueError,KeyError,TypeError,IndexError)):
            audit.audit_branch(s,b)

    def qualified(self,b):
        return next(r for r in b['recovery_rank_records'] if r['decision']['evidence']['recovery_rank']['rank_send'])

    def event(self,b,kind,**changes):
        e=next(e for e in b['ordered_emissions'] if e['type']==kind)
        for rows in (b['events'],b['ordered_emissions']):
            next(r for r in rows if r.get('event_sequence')==e['event_sequence']).update(deepcopy(changes))
        return e

    def test_complete_normal_and_latched_workflows(self):
        for key,pair in self.cases.items():
            with self.subTest(key=key):self.assertTrue(audit.audit_branch(*deepcopy(pair))['passed'])

    def test_actual_rank_and_reopen_have_separate_consumption(self):
        s,b=self.fixture();self.assertTrue(audit.audit_branch(s,b)['passed'])
        self.assertEqual(len(b['recovery_rank_consumptions']),1)
        self.assertEqual(len(b['recovery_dispatch_consumptions']),1)
        self.assertNotEqual(b['recovery_rank_consumptions'][0]['token_id'],b['recovery_dispatch_consumptions'][0]['token_id'])

    def test_normal_foundation_has_an_actual_rank_send_witness(self):
        s=make_rank_scenario(7,'recovery_rank_unvisited_recovery_alternative')
        b=run_recovery_rank_branch(s,variant='normal')
        checked=audit.audit_branch(s,b)
        self.assertTrue(checked['passed'])
        self.assertEqual(checked['counts']['recovery_rank_sent'],1)
        self.assertFalse(any(k.startswith('recovery_dispatch_') for k in b))

    def test_c1_seed8_full_rank_allocation(self):
        s=make_scenario(8,'recovery_dispatch_recovery_then_decline')
        for v in audit.VARIANTS:self.assertTrue(audit.audit_branch(s,run_recovery_rank_branch(s,variant=v,allocation='C1'))['passed'])

    def test_read_only(self):
        s,b=self.fixture();before=deepcopy((s,b));audit.audit_branch(s,b);self.assertEqual((s,b),before)

    def test_full_frozen_reference_delegation(self):
        s=make_scenario(8,'fresh_control');b=run_recovery_dispatch_branch(s,variant='latched')
        self.assertEqual(audit.audit_branch(s,b),audit.recovery.audit_branch(s,b))

    def test_every_rank_namespace_field_required(self):
        for k in audit.NAMESPACE_FIELDS:
            s,b=self.fixture();del b[k];self.reject(s,b)

    def test_candidate_cannot_hide_entire_namespace(self):
        s,b=self.fixture()
        for k in audit.NAMESPACE_FIELDS:del b[k]
        self.reject(s,b)

    def test_unknown_rank_namespace_rejected(self):
        s,b=self.fixture();b['recovery_rank_future_service']=1;self.reject(s,b)

    def test_actual_variant_selector_and_enabled_must_be_explicit(self):
        for k,v in (('recovery_rank_enabled',1),('recovery_rank_variant','eager'),('recovery_rank_selector','frontier')):
            s,b=self.fixture();b[k]=v;self.reject(s,b)

    def test_normal_cannot_invent_reopening_namespace(self):
        s,b=self.fixture('normal');b['recovery_dispatch_enabled']=False;self.reject(s,b)

    def test_old_actual_frontier_metadata_cannot_be_reintroduced(self):
        s,b=self.fixture();b['retry_dispatch_selector']='frontier';self.reject(s,b)

    def test_actual_subclass_is_honestly_named(self):
        s,b=self.fixture();b['recovery_rank_foundation_spec']['actual_selector_changed']=False;self.reject(s,b)

    def test_base_contract_preserved(self):
        s,b=self.fixture();b['recovery_rank_base_contract']['retry_dispatch_selector']='balanced';self.reject(s,b)

    def test_rank_contract_cannot_relax_original_work(self):
        s,b=self.fixture();b['recovery_rank_spec']['original_timeout_offer_ttl_fifo_readiness_resources_and_workers']=False;self.reject(s,b)

    def test_identity_is_actual_execution(self):
        s,b=self.fixture();b['policy']['name']='renamed';self.reject(s,b)

    def test_full_record_coverage_and_order(self):
        for k in ('recovery_rank_records','retry_dispatch_records'):
            s,b=self.fixture();b[k].pop();self.reject(s,b)
        s,b=self.fixture();b['recovery_rank_records'].reverse();self.reject(s,b)

    def test_unknown_future_rank_context(self):
        s,b=self.fixture();self.qualified(b)['rank_context']['future_reply']=.5;self.reject(s,b)

    def test_decline_is_actual_latest_contact(self):
        for k,v in (('ask_event_sequence',9999),('attempt',999),('condition_fingerprint','other')):
            s,b=self.fixture();self.qualified(b)['rank_context']['declines'][-1][k]=v;self.reject(s,b)

    def test_decline_export_cannot_erase_a_rejection(self):
        s,b=self.fixture();b['recovery_rank_declines'].pop();self.reject(s,b)

    def test_report_history_cannot_drop_a_loss(self):
        s,b=self.fixture();b['recovery_rank_resource_transitions'].pop();self.reject(s,b)

    def test_report_source_clock_reproduced(self):
        s,b=self.fixture();c=self.qualified(b)['rank_context']['transitions'][-1]
        c['after_resources'][next(iter(c['after_resources']))]['observed_at']=999;self.reject(s,b)

    def test_report_quantities_are_not_invented(self):
        s,b=self.fixture();c=self.qualified(b)['rank_context']['transitions'][-1]
        c['after_resources'][next(iter(c['after_resources']))]['used']+=1;self.reject(s,b)

    def test_before_report_provenance_bound(self):
        s,b=self.fixture();c=self.qualified(b)['rank_context']['transitions'][-1]
        c['before_report_event_sequences'][next(iter(c['before_report_event_sequences']))]=9999;self.reject(s,b)

    def test_valid_offer_ledger_cannot_be_invented(self):
        s,b=self.fixture();self.qualified(b)['rank_context']['valid_offers']=[dict(hospital='H01',expires_at=99)];self.reject(s,b)

    def test_contact_burden_never_disappears(self):
        s,b=self.fixture();b['retry_dispatch_contacts'].pop(0);self.reject(s,b)

    def test_current_public_universe_reconstructed(self):
        s,b=self.fixture();seq=self.qualified(b)['inquiry_event_sequence']
        next(r for r in b['retry_dispatch_records'] if r['inquiry_event_sequence']==seq)['context']['universe'].pop()
        self.reject(s,b)

    def test_last_contact_clock_not_renewed(self):
        s,b=self.fixture();seq=self.qualified(b)['inquiry_event_sequence']
        next(r for r in b['inquiry_decisions'] if r['event_sequence']==seq)['inputs']['last_contact_at']=None
        self.reject(s,b)

    def test_original_seven_inputs_cannot_receive_future_fields(self):
        s,b=self.fixture();seq=self.qualified(b)['inquiry_event_sequence']
        next(r for r in b['inquiry_decisions'] if r['event_sequence']==seq)['inputs']['future_worker_free_at']=9
        self.reject(s,b)

    def test_actual_kernel_output_reproduced(self):
        s,b=self.fixture();self.qualified(b)['decision']['next_review_at']+=15;self.reject(s,b)

    def test_frontier_counts_are_not_discounted_for_recovery(self):
        s,b=self.fixture();detail=self.qualified(b)['decision']['evidence']['recovery_rank']
        detail['base_frontier_decision']['evidence']['retry_dispatch']['contact_counts'][0]['contacts']=0
        self.reject(s,b)

    def test_rank_tier_cannot_promote_another_hospital(self):
        s,b=self.fixture();self.qualified(b)['decision']['evidence']['recovery_rank']['recovery_tier']=['H02'];self.reject(s,b)

    def test_actual_rank_ask_reference_required(self):
        s,b=self.fixture();b['recovery_rank_consumptions'][0]['ask_event_sequence']=9999;self.reject(s,b)

    def test_rank_selector_and_report_refs_bound(self):
        for k in ('inquiry_event_sequence','decline_event_sequence','report_event_sequence'):
            s,b=self.fixture();b['recovery_rank_consumptions'][0][k]=9999;self.reject(s,b)

    def test_consumed_condition_and_once_budget_bound(self):
        s,b=self.fixture();b['recovery_rank_consumptions'][0]['condition_fingerprint']='other';self.reject(s,b)
        s,b=self.fixture();b['recovery_rank_consumptions']*=2;self.reject(s,b)

    def test_latched_eligibility_independently_retained(self):
        s,b=self.fixture();b['recovery_dispatch_reopens'][0]['after_episode_id']+=1;self.reject(s,b)

    def test_normal_cannot_rename_original_episode_to_erase_budget(self):
        s,b=self.fixture('normal','fresh_control');pid='P01'
        for stream in (b['events'],b['ordered_emissions']):
            for e in stream:
                if e.get('patient')==pid and e['type']=='ask':e['inquiry_episode']+=1
        for row in b['inquiry_decisions']:
            if row['patient']==pid:row['inquiry_episode']+=1
        for row in b['retry_dispatch_records']:
            if row['context']['patient']['id']==pid:row['context']['episode']['id']+=1
        self.reject(s,b)

    def test_actual_worker_service_preserved(self):
        s,b=self.fixture();b['cooperative_job_ledger'][-1]['service_minutes']+=.25;self.reject(s,b)

    def test_original_report_wake_requires_source(self):
        s,b=self.fixture();self.event(b,'resource_event_review',report_event_sequence=9999);self.reject(s,b)

    def test_rank_metrics_are_real(self):
        s,b=self.fixture();b['metrics']['recovery_rank_sent']+=1;self.reject(s,b)

    def test_engine_solver_free_import(self):
        code=('import sys,json;import simulation.recovery_rank_audit;'
              'print(json.dumps([n for n in sys.modules if n.startswith("ortools") or '
              'n.startswith("simulation.") and n.endswith("_engine")]))')
        r=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(r.stdout),[])


if __name__=='__main__':unittest.main()
