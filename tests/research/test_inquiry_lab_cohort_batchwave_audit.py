"""Independent complete CS reference, preserved edges and N-batch audit."""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from inquiry_lab import cohort_batchwave_audit as audit
from test_inquiry_lab_cohort_batchwave_policy import initial_fixture,fixture as pending_fixture,invoke

ROOT=Path(__file__).resolve().parents[2]


@lru_cache(maxsize=6)
def _fixture(kind='batch'):
    if kind=='batch':args=initial_fixture()
    elif kind=='singleton':args=initial_fixture(count=5)
    elif kind=='no_cover':args=initial_fixture(hospitals=3)
    elif kind=='one_tail':args=initial_fixture(count=3)
    elif kind=='no_tail':args=initial_fixture(count=2)
    elif kind=='pending':args=pending_fixture()[0]
    else:raise ValueError(kind)
    result=invoke(args);j,ms,ts,p,owners,_=args
    return j,ms,ts,p,owners,result


def fixture(kind='batch'):return deepcopy(_fixture(kind))


def check(data):
    j,ms,ts,p,owners,result=data
    return audit.audit_pair(ms,j,ts,result['decisions'],result['evidence'],p,owners)


class BatchWaveIndependentAuditTests(unittest.TestCase):
    def test_all_native_owners_and_every_reference_query_are_actual_outputs(self):
        data=fixture();before=deepcopy(data);counts=check(data);result=data[-1];e=result['evidence']
        self.assertEqual(data,before);self.assertTrue(e['batch_evaluated'])
        self.assertEqual(counts['batchwave_groups_evaluated'],1);self.assertEqual(counts['batchwave_actual_members_checked'],4)
        self.assertEqual(result['execution_member_indices'],list(range(4)));self.assertEqual(len(result['decisions']),4)
        records=e['reference']['owner_references'];self.assertEqual([d['send'] for d in result['decisions']],[r['reference_send'] for r in records])
        self.assertEqual(counts['batchwave_reference_queries_checked'],sum(len(r['reference_send']) for r in records))
        self.assertEqual(counts['batchwave_primary_dispatches_checked'],4)
        self.assertEqual(counts['batchwave_secondary_dispatches_checked'],counts['batchwave_reference_queries_checked']-4)
        self.assertFalse(e['actual_Tailwait_continuation_claim']);self.assertFalse(e['old_probability_wait_guards_protect_novel_batch'])

    def test_tail_pair_and_odd_singleton_reference_are_independently_rebuilt(self):
        data=fixture('singleton');counts=check(data);e=data[-1]['evidence'];records=e['reference']['owner_references']
        self.assertEqual(len(records),5);self.assertEqual(records[-1]['kind'],'frozen_H_reference_singleton')
        self.assertEqual(counts['batchwave_reference_CS_calls_checked'],1)
        self.assertEqual(counts['batchwave_singleton_H_searches_checked'],1)
        self.assertEqual(counts['batchwave_actual_members_checked'],5)

    def test_no_cover_and_structurally_outside_paths_copy_accepted_parent(self):
        for kind in ('no_cover','pending','one_tail','no_tail'):
            data=fixture(kind);counts=check(data);e=data[-1]['evidence'];self.assertEqual(counts['batchwave_groups_copied'],1)
            self.assertFalse(e['batch_evaluated']);self.assertEqual(len(data[-1]['decisions']),2)
            self.assertEqual(e['execution_member_indices'],[0,1]);self.assertEqual(e['dispatch_order'],[])
            for d,parent in zip(data[-1]['decisions'],e['original_tailwait_proposal']['decisions']):
                d=deepcopy(d);d['evidence'].pop('cohort_batchwave');d['evidence']['policy']=audit.BASE_POLICY;self.assertEqual(d,parent)
            if kind=='no_cover':self.assertEqual(counts['batchwave_groups_coverage_incomplete'],1)
            else:self.assertEqual(counts['batchwave_groups_scope_ineligible'],1)

    def test_single_dynamic_reference_anchor_source_hashes_and_CS_queries_cannot_be_changed(self):
        edits=(lambda r:r.__setitem__('stoploss_actual_anchor_index',True),
            lambda r:r.__setitem__('original_Stoploss_proposal_sha256','x'),
            lambda r:r.__setitem__('dynamic_reference_row_sha256','x'),
            lambda r:r['dynamic_reference_row']['projection']['steps'].pop(),
            lambda r:r['owner_references'].pop(),
            lambda r:r['owner_references'][-1]['reference_send'].pop(),
            lambda r:r.__setitem__('actual_Tailwait_continuation_claim',True))
        for edit in edits:
            data=fixture();edit(data[-1]['evidence']['reference'])
            with self.assertRaises(ValueError):check(data)

    def test_primary_edges_cannot_expand_beyond_reference_sets_or_change_CDF_costs(self):
        edits=(lambda a:a['primary_edges'][0].__setitem__('hospital','FOREIGN'),
            lambda a:a['primary_edges'][0]['metrics'].__setitem__('selection_probability',0),
            lambda a:a['primary_edges'][0].__setitem__('total_cost_seconds',0),
            lambda a:a['common_prefix_H_proposals'].pop(),
            lambda a:a['common_prefix_H_proposals'][0]['evidence']['candidate_plan'][0].__setitem__('reply_probability',0),
            lambda a:a.__setitem__('public_assumed_workers',2))
        for edit in edits:
            data=fixture();edit(data[-1]['evidence']['allocation'])
            with self.assertRaises(ValueError):check(data)

    def test_restricted_flow_augmentation_capacities_and_certificate_tampers_rejected(self):
        edits=(lambda c:c['edges'].pop(),lambda c:c['assignment'][0].__setitem__('owner_index',True),
            lambda c:c['hospital_capacities'][0].__setitem__('capacity',2),
            lambda c:c['augmentations'][0]['path'][0].__setitem__('residual_capacity_before',2),
            lambda c:c['augmentations'][0].__setitem__('shortest_distance_seconds',-1),
            lambda c:c['residual_potentials'].__setitem__(0,99),lambda c:c.__setitem__('exact_minimum_cost_certificate',True))
        for edit in edits:
            data=fixture();edit(data[-1]['evidence']['allocation']['coverage'])
            with self.assertRaises(ValueError):check(data)

    def test_dispatch_primary_phase_then_all_native_reference_extras_is_exact(self):
        data=fixture();check(data);e=data[-1]['evidence'];records=e['reference']['owner_references'];dispatch=e['dispatch_order']
        expected={(i,h) for i,r in enumerate(records) for h in r['reference_send']}
        self.assertEqual(len(dispatch),len(expected));self.assertEqual({(r['owner_index'],r['hospital']) for r in dispatch},expected)
        self.assertTrue(all(r['phase']=='matched_primary' for r in dispatch[:4]))
        self.assertTrue(all(r['phase']=='remaining_preserved_reference' for r in dispatch[4:]))
        for edit in (lambda e:e['dispatch_order'].reverse(),lambda e:e['dispatch_order'].pop(),
            lambda e:e['allocation']['dispatch_order'][0].__setitem__('reference_send_index',999),
            lambda e:e.__setitem__('reference_send_sets_and_counts_preserved',False)):
            bad=fixture();edit(bad[-1]['evidence'])
            with self.assertRaises(ValueError):check(bad)

    def test_N_output_coverage_order_clock_and_reference_only_tags_checked(self):
        for edit in (lambda r:r['decisions'].pop(),lambda r:r['decisions'].reverse(),
            lambda r:r['decisions'][-1]['send'].pop(),
            lambda r:r['decisions'][-1].__setitem__('next_review_at',0),
            lambda r:r['decisions'][0]['evidence']['cohort_batchwave'].__setitem__('original_reference_plan_reference_only',False),
            lambda r:r['evidence'].__setitem__('actual_entire_first_roster_commit',False)):
            data=fixture();edit(data[-1])
            with self.assertRaises(ValueError):check(data)

    def test_full_owner_sources_and_common_public_clocks_are_not_waived(self):
        for edit in (lambda d:d[4]['inventory'].pop(),
            lambda d:d[4]['inventory'][-1]['patient'].__setitem__('version',999),
            lambda d:d[2][-1]['workflow_snapshot']['apply_snapshot'].__setitem__('revision',999),
            lambda d:d[2][-1]['inputs']['time_context'].__setitem__('event_prefix_max',999)):
            data=fixture();edit(data)
            with self.assertRaises(ValueError):check(data)

    def test_actual_inquiry_id_order_is_independent_of_native_decision_order(self):
        data=fixture();j,ms,ts,p,owners,result=data;roster=ms+ts
        group=dict(members=ms,tail_members=ts,execution_member_indices=list(range(4)),now=j['now'],
            event_prefix_max=100,outputs=result['decisions'],evidence=result['evidence'],
            dispatch_inquiry_ids=list(range(1,len(result['dispatch_order'])+1)),
            database_write_phases=['native_decisions','ordered_queries','native_job_updates'],
            owner_source_snapshot=dict(rows=[dict(patient_id=m['patient_id'],job=dict(round_no=0)) for m in roster]))
        events=[];inquiries=[]
        for index,row in enumerate(result['dispatch_order'],1):
            events.append(dict(id=100+index,at=j['now'],type='inquiry_sent',patient_id=row['patient_id'],hospital_id=row['hospital'],data=dict(inquiry_id=index)))
            inquiries.append(dict(id=index,patient_id=row['patient_id'],hospital_id=row['hospital'],created_at=j['now']))
        query_count=len(events)
        for index,(member,decision) in enumerate(zip(roster,result['decisions'])):
            events.append(dict(id=101+query_count+index,at=j['now'],type='inquiry_workflow_batch',
                patient_id=member['patient_id'],data=dict(hospital_ids=decision['send'],
                    round_no=1,
                    next_evaluation_at=decision['next_review_at'],policy_name=audit.NAME,reason=decision['reason'])))
        self.assertEqual(audit.audit_dispatch_events(group,events,inquiries)['batchwave_actual_dispatch_events_checked'],query_count)
        bad=deepcopy(events);bad[0]['patient_id']=roster[-1]['patient_id']
        with self.assertRaises(ValueError):audit.audit_dispatch_events(group,bad,inquiries)
        bad=deepcopy(inquiries);bad[0]['created_at']-=1
        with self.assertRaises(ValueError):audit.audit_dispatch_events(group,events,bad)
        bad=deepcopy(events);bad[0]['data']['inquiry_id'],bad[1]['data']['inquiry_id']=bad[1]['data']['inquiry_id'],bad[0]['data']['inquiry_id']
        with self.assertRaises(ValueError):audit.audit_dispatch_events(group,bad,inquiries)

    def test_batch_native_N_call_links_keep_common_prefix_including_tail_indices(self):
        j,ms,ts,p,owners,result=fixture();roster=ms+ts
        group=dict(members=ms,tail_members=ts,evidence=result['evidence'],outputs=result['decisions'],now=j['now'],
            execution_mode='batchwave',execution_member_indices=list(range(4)),dispatch_order=result['dispatch_order'],
            event_prefix_max=j['event_prefix_max'],decision_prefix_max=j['decision_prefix_max'],
            db_decision_ids=list(range(j['decision_prefix_max']+1,j['decision_prefix_max']+5)))
        self.assertEqual(audit.audit_execution_identity(group,{j['event_prefix_max']})[1],list(range(4)))
        for index,member in enumerate(roster):
            call=dict(joint_member_index=index,patient_id=member['patient_id'],at=j['now']-audit.EPOCH,
                db_event_prefix_max=j['event_prefix_max'],inputs=member['inputs'],
                workflow_snapshot=member['workflow_snapshot'],output=result['decisions'][index])
            decision=dict(id=j['decision_prefix_max']+index+1)
            actual,dprefix=audit.audit_linked_call(group,call,decision,j['decision_prefix_max']+index)
            self.assertEqual(actual,member);self.assertEqual(dprefix,j['decision_prefix_max'])
            for edit in (lambda c:c.__setitem__('joint_member_index',True),
                lambda c:c.__setitem__('joint_member_index',4),lambda c:c.__setitem__('db_event_prefix_max',999)):
                bad=deepcopy(call);edit(bad)
                with self.assertRaises(ValueError):audit.audit_linked_call(group,bad,decision,j['decision_prefix_max']+index)
            with self.assertRaises(ValueError):audit.audit_linked_call(group,call,decision,j['decision_prefix_max']+index+1)
        for edit in (lambda g:g.__setitem__('execution_mode','copied_pair'),
            lambda g:g['execution_member_indices'].pop(),lambda g:g['db_decision_ids'].__setitem__(3,999),
            lambda g:g['execution_member_indices'].__setitem__(0,False)):
            bad=deepcopy(group);edit(bad)
            with self.assertRaises(ValueError):audit.audit_execution_identity(bad,{j['event_prefix_max']})

    def test_fallback_is_independent_horizon_with_only_reference_scoped_parent_guards(self):
        import test_inquiry_lab_horizon_audit as old
        raw=old.replay();branch=raw['branches'][0];branch['joint_groups']=[]
        for event in branch['db_events']:
            if event['type']=='inquiry_workflow_started':event['data']['policy_name']=audit.NAME
        for call,decision in zip(branch['policy_calls'],branch['db_decisions']):
            call['name']=decision['policy_name']=audit.NAME;call['workflow_snapshot']['job']['policy_name']=audit.NAME
            for candidate in call['inputs']['candidates']:candidate['resources_enough']=True
            e=call['output']['evidence'];e['policy']=audit.NAME
            e['joint_tail']=dict(schema_version=1,mode='horizon_fallback',guard_evaluated=False)
            e['joint_first']=dict(schema_version=1,mode='horizon_fallback',current_only=False)
            e['joint_budget']=dict(schema_version=1,mode='horizon_fallback',projection_evaluated=False)
            for key in ('cohort_success','cohort_dynamic','cohort_hedge','cohort_recourse','cohort_followplan',
                'cohort_stoploss','cohort_fullmenu','cohort_pareto','cohort_waitpareto','cohort_envelope',
                'cohort_pairscope','cohort_pairshield','cohort_tailwait','cohort_batchwave'):
                e[key]=dict(schema_version=1,mode='horizon_fallback',evaluated=False,intervened=False)
            e['cohort_batchwave']['original_reference_plan_reference_only']=False
            decision['evidence']=deepcopy(e)
        branch.update(policy=audit.NAME,instrumentation=deepcopy(audit.NEW_INSTRUMENTATION));raw['arms']=[[audit.NAME,'immediate']]
        proof=audit.audit_replay(raw)['branch_audits'][0]
        self.assertTrue(proof['passed']);self.assertFalse(proof['current_pair_only_execution_checked'])
        self.assertFalse(proof['native_query_submission_order_preserved']);self.assertTrue(proof['actual_hospital_FIFO_unchanged'])
        self.assertTrue(proof['embedded_Tailwait_reference_assertions']['every_owner_final_predicted_probability_guard_independently_checked'])
        self.assertNotIn('every_owner_final_predicted_probability_guard_independently_checked',proof)
        bad=deepcopy(raw);bad['branches'][0]['instrumentation']['protected_wait_first_selection']=True
        with self.assertRaises(ValueError):audit.audit_replay(bad)

    def test_old_controls_delegate_and_no_production_module_imported(self):
        raw=dict(schema_version=1,arms=[['old','immediate']],branches=[dict(policy='old',rule='immediate',environment_sha256='x',snapshot_sha256='y')],environment_sha256='x',initial_snapshot_sha256='y');wanted=dict(passed=True,new_executions=0)
        with patch.object(audit.control_audit,'audit_replay',return_value=wanted) as call:self.assertIs(audit.audit_replay(raw),wanted)
        call.assert_called_once_with(raw)
        code="import sys;from inquiry_lab import cohort_hedge_audit as h;o=h.owner_metrics;from inquiry_lab import cohort_batchwave_audit;assert h.owner_metrics is o;print([k for k in sys.modules if k.startswith('inquiry_lab.') and k.endswith(('_policy','_runner','_model','_context','_experiment'))])"
        result=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout.strip(),'[]')


if __name__=='__main__':unittest.main()
