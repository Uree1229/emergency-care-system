"""Public age-limit release clock, unchanged actions and full source guards."""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_releasealign_audit as a
from test_inquiry_lab_horizon_policy import inputs,context

ROOT=Path(__file__).resolve().parent


def own_inputs(age=175,count=4,now=1175,**changes):
    own=[dict(hospital='OWN'+str(i),patient='P',submitted_at=now-age,travel_seconds=300+i) for i in range(count)]
    value=inputs(now=now,pending=[{k:v for k,v in q.items() if k!='travel_seconds'} for q in own],patient_pending=own,
        max_new=6,last_contact_at=now-age,time_context=context(now=now))
    value.update(changes);return value


def invoke(values):
    from inquiry_lab import cohort_releasealign_policy as p
    return p.decide(p.NAME,**deepcopy(values))


@lru_cache(maxsize=1)
def group():
    from test_inquiry_lab_cohort_startslots_policy import initial_fixture
    from test_inquiry_lab_cohort_tailwait_policy import promote
    from inquiry_lab import cohort_releasealign_policy as p
    j,ms,ts,pr,o,parent=promote(initial_fixture(count=4,hospitals=3),'eat_cohort_startslots_l15_f',a.NAME)
    with patch.object(p.original,'decide_joint',return_value=deepcopy(parent)):
        r=p.decide_joint(p.NAME,members=ms,joint_context=j,tail_members=ts,priority_context=pr,owner_context=o)
    return j,ms,ts,pr,o,r


class ReleasealignIndependentAuditTests(unittest.TestCase):
    def test_advance_only_to_first_integer_strictly_after_inclusive180(self):
        values=own_inputs();before=deepcopy(values);result=invoke(values);counts=a.audit_fallback(values,result)
        tag=result['evidence']['cohort_releasealign'];self.assertEqual(values,before)
        self.assertEqual(tag['earliest_slot_release_at'],values['now']+6)
        self.assertEqual(result['next_review_at'],values['now']+6);self.assertTrue(tag['intervened'])
        self.assertEqual(counts['releasealign_advanced_reviews_checked'],1)
        values=own_inputs(age=180);result=invoke(values);a.audit_fallback(values,result)
        self.assertEqual(result['next_review_at'],values['now']+1)
        values=own_inputs(age=181);result=invoke(values);a.audit_fallback(values,result)
        self.assertFalse(result['evidence']['cohort_releasealign']['intervened'])

    def test_fractional_submission_uses_floor_plus_one_without_rounding_age(self):
        values=own_inputs(age=179.9);result=invoke(values);a.audit_fallback(values,result)
        self.assertEqual(result['next_review_at'],values['now']+1)
        self.assertGreater(result['next_review_at'],values['patient_pending'][0]['submitted_at']+180)

    def test_no_candidate_no_budget_not_contacted_or_not_exactlyfour_copies(self):
        for values in (inputs(),own_inputs(count=3),own_inputs(count=5),own_inputs(last_contact_at=None),
                own_inputs(last_contact_at=True),own_inputs(candidates=[]),own_inputs(max_new=0)):
            result=invoke(values);counts=a.audit_fallback(values,result);tag=result['evidence']['cohort_releasealign']
            self.assertFalse(tag['evaluated']);self.assertFalse(tag['intervened'])
            self.assertEqual(result['next_review_at'],tag['original_next_review_at'])
            self.assertEqual(counts['releasealign_advanced_reviews_checked'],0)

    def test_release_equal_or_after_next_review_A_H_is_not_selected(self):
        for values in (own_inputs(age=166),own_inputs(age=165),
                own_inputs(time_context=context(now=1175,automatic=1181)),
                own_inputs(now=1294,time_context=context(now=1294,created=1000))):
            result=invoke(values);a.audit_fallback(values,result);tag=result['evidence']['cohort_releasealign']
            self.assertFalse(tag['intervened']);self.assertEqual(result['next_review_at'],tag['original_next_review_at'])

    def test_original_send_plan_metrics_and_late_own_CDF_are_exact_copies(self):
        for values in (own_inputs(),own_inputs(age=181)):
            result=invoke(values);a.audit_fallback(values,result);old=result['evidence']['cohort_releasealign']['original_tailwait_proposal']
            copy=deepcopy(result);tag=copy['evidence'].pop('cohort_releasealign');copy['evidence']['policy']=a.BASE_POLICY
            copy['next_review_at']=tag['original_next_review_at'];self.assertEqual(copy,old)
            self.assertEqual(result['evidence']['expected_cost_without_new'],old['evidence']['expected_cost_without_new'])
            self.assertEqual(result['send'],old['send'])

    def test_duplicate_future_and_invalid_own_rows_do_not_create_fake_release(self):
        values=own_inputs();q=deepcopy(values['patient_pending'][0]);values['patient_pending']+=[q,dict(q,submitted_at=values['now']+1),{}]
        result=invoke(values);a.audit_fallback(values,result);tag=result['evidence']['cohort_releasealign']
        self.assertEqual(tag['normalized_own_pending_count'],4);self.assertEqual(tag['fresh_own_pending_count'],4)

    def test_clock_count_cutoff_and_original_source_tampers_rejected(self):
        values=own_inputs();result=invoke(values)
        for edit in (lambda t:t.__setitem__('earliest_slot_release_at',values['now']+5),
            lambda t:t.__setitem__('fresh_own_pending_count',3),lambda t:t.__setitem__('maximum_concurrent',5),
            lambda t:t.__setitem__('latest_allowed_new_inquiry_at',999999),
            lambda t:t['original_tailwait_proposal']['evidence']['candidate_plan'][0].__setitem__('planned_offset_seconds',0)):
            bad=deepcopy(result);edit(bad['evidence']['cohort_releasealign'])
            with self.assertRaises(ValueError):a.audit_fallback(values,bad)
        for edit in (lambda r:r.__setitem__('next_review_at',values['now']+5),
            lambda r:r['send'].append('FOREIGN'),lambda r:r['evidence'].__setitem__('planned_expected_cost',0)):
            bad=deepcopy(result);edit(bad)
            with self.assertRaises(ValueError):a.audit_fallback(values,bad)

    def test_full_Tailwait_group_copy_and_owner_provenance_unchanged(self):
        j,ms,ts,p,o,result=deepcopy(group());counts=a.audit_pair(ms,j,ts,result['decisions'],result['evidence'],p,o)
        self.assertEqual(counts['releasealign_groups_copied_checked'],1)
        bad=deepcopy(result);bad['decisions'][0]['next_review_at']-=1
        with self.assertRaises(ValueError):a.audit_pair(ms,j,ts,bad['decisions'],bad['evidence'],p,o)
        o['inventory'].pop()
        with self.assertRaises(ValueError):a.audit_pair(ms,j,ts,result['decisions'],result['evidence'],p,o)

    def test_complete_raw_copy_fallback_trace_and_pure_import(self):
        import test_inquiry_lab_horizon_audit as old
        raw=old.replay();b=raw['branches'][0];b['joint_groups']=[]
        for e in b['db_events']:
            if e['type']=='inquiry_workflow_started':e['data']['policy_name']=a.NAME
        for c,d in zip(b['policy_calls'],b['db_decisions']):
            c['name']=d['policy_name']=a.NAME;c['workflow_snapshot']['job']['policy_name']=a.NAME
            for row in c['inputs']['candidates']:row['resources_enough']=True
            c['output']=invoke(c['inputs']);d.update(reason=c['output']['reason'],hospital_ids=c['output']['send'],evidence=deepcopy(c['output']['evidence']))
        b.update(policy=a.NAME,instrumentation=deepcopy(a.NEW_INSTRUMENTATION));raw['arms']=[[a.NAME,'immediate']]
        proof=a.audit_replay(raw)['branch_audits'][0];self.assertTrue(proof['passed']);self.assertEqual(proof['releasealign_fallback_calls_checked'],1)
        code="import sys;from inquiry_lab import cohort_hedge_audit as h;o=h.owner_metrics;from inquiry_lab import cohort_releasealign_audit;assert h.owner_metrics is o;print([k for k in sys.modules if k.startswith('inquiry_lab.') and k.endswith(('_policy','_runner','_model','_context','_diagnostic','_experiment'))])"
        result=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,check=True);self.assertEqual(result.stdout.strip(),'[]')


if __name__=='__main__':unittest.main()
