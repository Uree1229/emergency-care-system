"""Independent public pending ages, coordinate forecasts and copied groups."""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_wavecap_audit as audit
from test_inquiry_lab_horizon_policy import inputs,context
from test_inquiry_lab_cohort_wavecap_horizon import own_inputs

ROOT=Path(__file__).resolve().parent


def invoke(values):
    from inquiry_lab import cohort_wavecap_policy as policy
    return policy.decide(policy.NAME,**deepcopy(values))


@lru_cache(maxsize=1)
def group_fixture():
    from test_inquiry_lab_cohort_startslots_policy import initial_fixture
    from test_inquiry_lab_cohort_tailwait_policy import promote
    from inquiry_lab import cohort_wavecap_policy as policy
    args=promote(initial_fixture(count=4,hospitals=3),'eat_cohort_startslots_l15_f',audit.NAME)
    j,ms,ts,p,owners,parent=args
    with patch.object(policy.original,'decide_joint',return_value=deepcopy(parent)):
        result=policy.decide_joint(policy.NAME,members=ms,joint_context=j,tail_members=ts,priority_context=p,owner_context=owners)
    return j,ms,ts,p,owners,result


class WavecapIndependentAuditTests(unittest.TestCase):
    def test_own_and_planned_ages_include180_but_exclude181(self):
        own=[dict(submitted_at=0)]
        rows=audit.occupancy(dict(A=0,B=180,C=181),own,0)
        self.assertEqual([r['original_fresh_count'] for r in rows],[1,1,0])
        self.assertEqual([r['planned_fresh_count'] for r in rows],[1,2,2])
        self.assertEqual(audit.occupancy(dict(A=None),own,0),[])
        with self.assertRaises(ValueError):audit.occupancy(dict(A=True),own,0)

    def test_check_every_wave_and_allow_real_public_aging(self):
        old=[dict(submitted_at=-170)]*3
        rows=audit.occupancy(dict(A=0,B=0,C=15,D=15),old,0)
        self.assertEqual([r['total_fresh_count'] for r in rows],[5,4])
        rows=audit.occupancy(dict(A=0,B=15,C=15,D=15),[dict(submitted_at=-179)]*3,0)
        self.assertEqual([r['total_fresh_count'] for r in rows],[4,4])
        rows=audit.occupancy(dict(A=15,B=15),[dict(submitted_at=0)]*3,0)
        self.assertEqual(rows[0]['total_fresh_count'],5)

    def test_normalized_own_dedup_invalid_future_and_lastcontact_rules(self):
        values=own_inputs(1);original=deepcopy(values);q=values['patient_pending'][0]
        values['patient_pending']=[q,dict(q),dict(q,submitted_at=values['now']+1),{},dict(q,patient=True)]
        self.assertEqual(len(audit.normalized_inputs(values)['patient_pending']),1)
        self.assertEqual(len(audit.normalize_pending(values['patient_pending'],values['now'],own=True)),1)
        for last in (None,True,'1',float('nan'),float('inf'),values['now']+1):
            self.assertIsNone(audit.normalized_inputs(dict(values,last_contact_at=last))['last_contact_at'])
        self.assertEqual(audit.normalized_inputs(dict(values,last_contact_at=-1.))['last_contact_at'],-1.)
        self.assertEqual(original,own_inputs(1))
        with self.assertRaises(ValueError):audit.normalize_pending([dict(q,travel_seconds=-1)],values['now'],own=True)

    def test_independent_original_and_corrected_coordinate_search_match_real_planners(self):
        from inquiry_lab import horizon_policy as old,cohort_wavecap_horizon as new
        for values in (inputs(),own_inputs(1),own_inputs(3),own_inputs(4),own_inputs(1,age=181),
                own_inputs(3,now=1293,time_context=context(now=1293))):
            before=deepcopy(values)
            for capped,proposal in ((False,old.decide(old.NAME,**deepcopy(values))),
                    (True,new.decide(new.NAME,**deepcopy(values)))):
                expected,checked=audit.audit_H_search(values,proposal,every_wave_cap=capped)
                self.assertEqual(expected['send'],proposal['send']);self.assertEqual(checked['horizon_calls_checked'],1)
                if capped:self.assertTrue(all(r['total_fresh_count']<=4 for r in expected['occupancy']))
            self.assertEqual(values,before)

    def test_forced_choices_filtered_and_reset_rechecked_independently(self):
        pool=[dict(hospital='A',travel_seconds=2),dict(hospital='B',travel_seconds=1)]
        row,plan,reset=audit.force_initial_plan(dict(A=15,B=30),pool,lambda p:p['A']!=0,lambda p:0 if p['A']==0 else 10)
        self.assertEqual(row['hospital'],'B');self.assertEqual(plan,dict(A=15,B=0));self.assertFalse(reset)
        row,plan,reset=audit.force_initial_plan(dict(A=15,B=30),pool,lambda p:sum(x is not None for x in p.values())==1,lambda p:7)
        self.assertEqual(row['hospital'],'B');self.assertEqual(plan,dict(A=None,B=0));self.assertTrue(reset)
        with self.assertRaises(ValueError):audit.force_initial_plan(dict(A=15,B=30),pool,lambda p:False,lambda p:0)

    def test_genuine_fallback_activation_and_exact_copy_boundaries(self):
        triggered=0
        for values in (inputs(),inputs(last_contact_at=1000),own_inputs(1),own_inputs(3),own_inputs(4),
                own_inputs(1,age=181),own_inputs(4,max_new=0),own_inputs(3,last_contact_at=None)):
            result=invoke(values);before=deepcopy(result);counts=audit.audit_fallback(values,result)
            self.assertEqual(result,before);tag=result['evidence']['cohort_wavecap']
            self.assertEqual(counts['wavecap_fallback_calls_checked'],1);self.assertFalse(tag['next_review_changed'])
            triggered+=counts['wavecap_triggered_corrections_checked']
            if tag['evaluated']:self.assertTrue(all(r['total_fresh_count']<=4 for r in tag['resulting_occupancy']))
            else:
                value=deepcopy(result);value['evidence'].pop('cohort_wavecap');value['evidence']['policy']=audit.BASE_POLICY
                self.assertEqual(value,tag['original_tailwait_proposal'])
        self.assertGreater(triggered,0)

    def test_forged_old_H_final_plan_cannot_manufacture_activation(self):
        values=own_inputs(3);result=invoke(values);tag=result['evidence']['cohort_wavecap']
        for edit in (lambda t:t['original_tailwait_proposal']['evidence']['candidate_plan'][0].__setitem__('planned_offset_seconds',180),
            lambda t:t.__setitem__('original_plan_over_cap',not t['original_plan_over_cap']),
            lambda t:t.__setitem__('evaluated',not t['evaluated']),lambda t:t.__setitem__('normalized_own_pending_count',0),
            lambda t:t['source_sha256'].__setitem__('horizon_policy','x')):
            bad=deepcopy(result);edit(bad['evidence']['cohort_wavecap'])
            with self.assertRaises(ValueError):audit.audit_fallback(values,bad)

    def test_corrected_plan_occupancy_cost_source_and_current_action_tampers_rejected(self):
        values=own_inputs(4);result=invoke(values);self.assertTrue(result['evidence']['cohort_wavecap']['evaluated'])
        edits=(lambda t:t['resulting_occupancy'][0].__setitem__('total_fresh_count',0),
            lambda t:t['corrected_horizon_proposal']['evidence']['wavecap_plan'].__setitem__('forced_plan_reset',True),
            lambda t:t['corrected_horizon_proposal']['evidence']['wavecap_plan'].__setitem__('unrounded_planned_expected_cost',0),
            lambda t:t['corrected_horizon_proposal']['evidence']['candidate_plan'][0].__setitem__('planned_offset_seconds',0))
        for edit in edits:
            bad=deepcopy(result);edit(bad['evidence']['cohort_wavecap'])
            with self.assertRaises(ValueError):audit.audit_fallback(values,bad)
        for edit in (lambda o:o['send'].append('FOREIGN'),lambda o:o.__setitem__('next_review_at',values['now']+16)):
            bad=deepcopy(result);edit(bad)
            with self.assertRaises(ValueError):audit.audit_fallback(values,bad)

    def test_group_actions_full_Tailwait_proof_and_owner_sources_unchanged(self):
        j,ms,ts,p,owners,result=deepcopy(group_fixture());before=deepcopy(result)
        counts=audit.audit_pair(ms,j,ts,result['decisions'],result['evidence'],p,owners)
        self.assertEqual(result,before);self.assertEqual(counts['wavecap_groups_copied_checked'],1)
        for d in result['decisions']:self.assertEqual(d['evidence']['cohort_wavecap']['mode'],'pair_copied_tailwait')
        for edit in (lambda r:r['decisions'][0]['send'].append('FOREIGN'),
            lambda r:r['evidence']['original_tailwait_proposal']['decisions'][0]['send'].append('FOREIGN'),
            lambda r:r['evidence'].__setitem__('copied_tailwait_group',False)):
            bad=deepcopy(result);edit(bad)
            with self.assertRaises(ValueError):audit.audit_pair(ms,j,ts,bad['decisions'],bad['evidence'],p,owners)
        owners['inventory'].pop()
        with self.assertRaises(ValueError):audit.audit_pair(ms,j,ts,result['decisions'],result['evidence'],p,owners)

    def test_explicit_import_is_independent_and_does_not_patch_shared_helpers(self):
        code="import sys;from inquiry_lab import cohort_hedge_audit as h;o=h.owner_metrics;from inquiry_lab import cohort_wavecap_audit;assert h.owner_metrics is o;print([k for k in sys.modules if k.startswith('inquiry_lab.') and k.endswith(('_policy','_runner','_model','_context','_experiment'))])"
        result=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout.strip(),'[]')

    def test_complete_fallback_journal_trace_accounting_and_guard_scope(self):
        import test_inquiry_lab_horizon_audit as prior
        raw=prior.replay();branch=raw['branches'][0];branch['joint_groups']=[]
        for event in branch['db_events']:
            if event['type']=='inquiry_workflow_started':event['data']['policy_name']=audit.NAME
        for call,decision in zip(branch['policy_calls'],branch['db_decisions']):
            call['name']=decision['policy_name']=audit.NAME;call['workflow_snapshot']['job']['policy_name']=audit.NAME
            for candidate in call['inputs']['candidates']:candidate['resources_enough']=True
            result=invoke(call['inputs']);call['output']=result
            decision.update(reason=result['reason'],hospital_ids=deepcopy(result['send']),evidence=deepcopy(result['evidence']))
        branch.update(policy=audit.NAME,instrumentation=deepcopy(audit.NEW_INSTRUMENTATION));raw['arms']=[[audit.NAME,'immediate']]
        proof=audit.audit_replay(raw)['branch_audits'][0]
        self.assertTrue(proof['passed']);self.assertEqual(proof['wavecap_fallback_calls_checked'],1)
        self.assertEqual(proof['wavecap_unchanged_fallbacks_checked'],1)
        self.assertEqual(proof['wavecap_triggered_corrections_checked'],0)
        self.assertTrue(proof['actual_pending_fallback_H_search_independently_reconstructed'])
        self.assertIn('joint groups only',proof['parent_guard_flags_scope'])
        for edit in (lambda b:b['policy_calls'][0].__setitem__('db_event_prefix_max',999),
            lambda b:b['instrumentation'].__setitem__('base_horizon_preserved_for_fallback',True),
            lambda b:b['db_events'][2].__setitem__('hospital_id','FOREIGN')):
            bad=deepcopy(raw);edit(bad['branches'][0])
            with self.assertRaises(ValueError):audit.audit_replay(bad)

    def test_controls_delegate_to_accepted_Tailwait_and_normalizer_tamper_is_not_hidden(self):
        self.assertEqual(audit.control_audit.NAME,'eat_cohort_tailwait_l15_f')
        raw=dict(schema_version=1,arms=[['old','immediate']],branches=[dict(policy='old',rule='immediate',environment_sha256='x',snapshot_sha256='y')],environment_sha256='x',initial_snapshot_sha256='y');wanted=dict(passed=True,new_executions=0)
        with patch.object(audit.control_audit,'audit_replay',return_value=wanted) as call:self.assertIs(audit.audit_replay(raw),wanted)
        call.assert_called_once_with(raw)
        values=own_inputs(3);result=invoke(values);result['evidence']['cohort_wavecap']['contacted_owner']=False
        with self.assertRaises(ValueError):audit.audit_fallback(values,result)


if __name__=='__main__':unittest.main()
