"""Independent atomic-exchange metrics, all trials and source invariants."""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_swap_audit as a
from test_inquiry_lab_cohort_swap_model import own_inputs,shaped_plan,metric
from test_inquiry_lab_horizon_policy import inputs,context

ROOT=Path(__file__).resolve().parent


def invoke(values):
    from inquiry_lab import cohort_swap_policy as p
    return p.decide(p.NAME,**deepcopy(values))


@lru_cache(maxsize=1)
def group():
    from test_inquiry_lab_cohort_startslots_policy import initial_fixture
    from test_inquiry_lab_cohort_tailwait_policy import promote
    from inquiry_lab import cohort_swap_policy as p
    j,ms,ts,pr,o,parent=promote(initial_fixture(count=4,hospitals=3),'eat_cohort_startslots_l15_f',a.NAME)
    with patch.object(p.original,'decide_joint',return_value=deepcopy(parent)):
        r=p.decide_joint(p.NAME,members=ms,joint_context=j,tail_members=ts,priority_context=pr,owner_context=o)
    return j,ms,ts,pr,o,r


class SwapIndependentAuditTests(unittest.TestCase):
    def test_all_metrics_and_public_CDF_descriptors_match_independent_H_model(self):
        from inquiry_lab import cohort_swap_model as m,horizon_policy as H
        for values in (own_inputs(1),own_inputs(3),own_inputs(4),own_inputs(1,age=181),own_inputs(now=1293,time_context=context(now=1293))):
            old=H.decide(H.NAME,**deepcopy(values));before=deepcopy((values,old))
            actual=a.public_swap_model(values,old);expected=m.build_model(values,old)
            a.precise_same(actual,expected,'complete independent own/new source distributions')
            a.precise_same(a.swap_metrics(actual,actual['original_plan']),m.metrics(expected,expected['original_plan']),'all P/W/J/Q metrics')
            self.assertEqual((values,old),before)

    def test_complete_nine_atomic_trials_and_all_guard_fields_are_rebuilt(self):
        from inquiry_lab import cohort_swap_model as m
        values,old=shaped_plan();actual=a.evaluate_swaps(values,old);expected=m.evaluate(values,old)
        a.precise_same(actual,expected,'complete independent bounded exchange proof')
        self.assertEqual(len(actual['trials']),9)
        for trial in actual['trials']:
            for key in ('offset_histogram','planned_count','immediate_count'):
                self.assertEqual(trial['plan_summary'][key],actual['model']['original_plan_summary'][key])
            self.assertEqual(trial['planned_hospital_set_preserved'],trial['original_incoming_offset_seconds'] is not None)
            self.assertEqual(trial['admitted'],all(trial['checks'].values()))

    def test_selection_wait_band_then_JplusQ_then_native_pair_order(self):
        trials=[dict(trial_index=0,admitted=True,metrics=metric(wait=90+a.EPSILON/2,arrival=900)),
            dict(trial_index=1,admitted=True,metrics=metric(wait=90,arrival=901)),
            dict(trial_index=2,admitted=True,metrics=metric(wait=90+2*a.EPSILON,arrival=1))]
        chosen=a.choose_trials(trials);self.assertEqual(chosen['wait_band_trial_indices'],[0,1]);self.assertEqual(chosen['selected_trial_index'],0)
        trials[1]['metrics']=deepcopy(trials[0]['metrics']);self.assertEqual(a.choose_trials(trials)['cost_band_trial_indices'],[0,1])
        for r in trials:r['admitted']=False
        self.assertTrue(a.choose_trials(trials)['baseline_fallback'])

    def test_original_H_plan_is_independently_checked_and_old_future_relaxation_not_changed(self):
        from inquiry_lab import horizon_policy as H
        values=own_inputs(3);old=H.decide(H.NAME,**values);model=a.evaluate_swaps(values,old)
        self.assertTrue(model['original_future_cap_relaxation_unchanged'])
        self.assertTrue(model['one_exchange_only']);self.assertLessEqual(len(model['trials']),9)
        values,fake=shaped_plan();fake['evidence']['policy']=a.BASE_POLICY
        # It is useful for isolated exchange math, but is not a full original
        # Tailwait/H witness; the full fallback must reject source forgery.
        result=invoke(values);result['evidence']['cohort_swap']['original_tailwait_proposal']['evidence']['candidate_plan']=fake['evidence']['candidate_plan']
        with self.assertRaises(ValueError):a.audit_fallback(values,result)

    def test_genuine_pending_and_first_paths_have_complete_action_proofs(self):
        for values in (inputs(),own_inputs(1),own_inputs(3),own_inputs(4),own_inputs(1,age=181),own_inputs(last_contact_at=None)):
            result=invoke(values);before=deepcopy(result);counts=a.audit_fallback(values,result)
            self.assertEqual(result,before);self.assertEqual(counts['swap_original_H_searches_checked'],1)
            self.assertEqual(counts['swap_next_review_changes_checked'],0)
            if result['evidence']['cohort_swap']['evaluated']:self.assertLessEqual(counts['swap_trials_checked'],9)

    def test_projection_omission_metrics_admission_and_selection_tamper_rejected(self):
        values=own_inputs(1);result=invoke(values);self.assertTrue(result['evidence']['cohort_swap']['evaluated'])
        self.assertTrue(result['evidence']['cohort_swap']['swap_projection']['trials'])
        edits=(lambda p:p['trials'].pop(),lambda p:p['baseline_metrics'].__setitem__('selection_probability',0),
            lambda p:p['trials'][0]['metrics'].__setitem__('planned_contact_cost_seconds',0),
            lambda p:p['trials'][0].__setitem__('admitted',not p['trials'][0]['admitted']),
            lambda p:p['selection'].__setitem__('selected_trial_index',999),lambda p:p.__setitem__('original_future_cap_relaxation_unchanged',False))
        for edit in edits:
            bad=deepcopy(result);edit(bad['evidence']['cohort_swap']['swap_projection'])
            with self.assertRaises(ValueError):a.audit_fallback(values,bad)
        for edit in (lambda o:o['send'].append('FOREIGN'),lambda o:o.__setitem__('next_review_at',values['now']+1)):
            bad=deepcopy(result);edit(bad)
            with self.assertRaises(ValueError):a.audit_fallback(values,bad)

    def test_copied_group_keeps_full_Tailwait_owner_and_action_checks(self):
        j,ms,ts,p,o,result=deepcopy(group());counts=a.audit_pair(ms,j,ts,result['decisions'],result['evidence'],p,o)
        self.assertEqual(counts['swap_groups_copied_checked'],1)
        bad=deepcopy(result);bad['decisions'][0]['send'].append('FOREIGN')
        with self.assertRaises(ValueError):a.audit_pair(ms,j,ts,bad['decisions'],bad['evidence'],p,o)
        o['inventory'].pop()
        with self.assertRaises(ValueError):a.audit_pair(ms,j,ts,result['decisions'],result['evidence'],p,o)

    def test_complete_raw_fallback_journal_and_control_delegation(self):
        import test_inquiry_lab_horizon_audit as old
        raw=old.replay();b=raw['branches'][0];b['joint_groups']=[]
        for e in b['db_events']:
            if e['type']=='inquiry_workflow_started':e['data']['policy_name']=a.NAME
        for c,d in zip(b['policy_calls'],b['db_decisions']):
            c['name']=d['policy_name']=a.NAME;c['workflow_snapshot']['job']['policy_name']=a.NAME
            for row in c['inputs']['candidates']:row['resources_enough']=True
            c['output']=invoke(c['inputs']);d.update(reason=c['output']['reason'],hospital_ids=c['output']['send'],evidence=deepcopy(c['output']['evidence']))
        b.update(policy=a.NAME,instrumentation=deepcopy(a.NEW_INSTRUMENTATION));raw['arms']=[[a.NAME,'immediate']]
        proof=a.audit_replay(raw)['branch_audits'][0];self.assertTrue(proof['passed']);self.assertEqual(proof['swap_fallback_calls_checked'],1)
        self.assertEqual(a.control_audit.NAME,'eat_cohort_tailwait_l15_f')

    def test_pure_import_does_not_load_producer_or_mutate_shared_helpers(self):
        code="import sys;from inquiry_lab import cohort_hedge_audit as h;o=h.owner_metrics;from inquiry_lab import cohort_swap_audit;assert h.owner_metrics is o;print([k for k in sys.modules if k.startswith('inquiry_lab.') and k.endswith(('_policy','_runner','_model','_context','_diagnostic','_experiment'))])"
        result=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,check=True);self.assertEqual(result.stdout.strip(),'[]')


if __name__=='__main__':unittest.main()
