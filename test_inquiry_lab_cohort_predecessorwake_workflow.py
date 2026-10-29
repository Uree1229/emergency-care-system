"""SQLite capture/consumption/rollback units; fixed stub decisions are explicit."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from server import db,inquiry_observations,service as physical
from inquiry_lab import cohort_predecessorwake_workflow as w,cohort_predecessorwake_service as s,cohort_predecessorwake_policy as p


def offers():
    return dict(candidates=[dict(hospital_id='H'+str(i),travel=dict(duration_seconds=100+i),resources_enough=True) for i in range(1,12)])


def parent_stub(send):
    """Workflow orchestration only; not an independently valid H-score proof."""
    def choose(name,**inputs):
        return dict(send=list(send),reason='synthetic_kernel',next_review_at=inputs['now']+15,
            evidence=dict(policy=name,sent_hospitals=list(send),candidate_plan=[],time_horizon=dict(context=deepcopy(inputs['time_context']))))
    return choose


class DBFixture(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parent/'.codex-build/eat-predecessorwake-20261028/tmp';root.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=root);self.path=Path(self.tmp.name)/'unit.db';self.conn=db.connect(self.path);db.init(self.conn)
        for i in range(1,12):
            physical.upsert_hospital(self.conn,dict(id='H'+str(i),name='H'+str(i),lat=35.1+i*.001,lon=127.1,capabilities=[],source='synthetic'))
            physical.set_resource(self.conn,1000,'H'+str(i),'ed_bed',100,0,source='hospital_input')
        s.create_patient(self.conn,1000,'P',dict(reporter_role='transporter',consent=True,symptoms='unit',required_capabilities=[],resource_demands={'ed_bed':1},
            lat=35.,lon=127.,auto_inquiry=True,inquiry_policy=p.NAME))

    def tearDown(self):self.conn.close();self.tmp.cleanup()

    def seed(self,count=3):
        snapshot,changed,wake=w._capture(self.conn,1000,'P');self.assertFalse(changed);self.assertIsNone(wake)
        with patch.object(p.original,'decide',side_effect=parent_stub(['H'+str(i) for i in range(1,count+1)])):
            result=w._apply(self.conn,1000,snapshot,offers(),inquiry_observations.capture(self.conn,1000,'P'))
        self.assertIsNotNone(result['record']);return result

    def decline(self,hospital='H1',at=1005):
        iid=self.conn.execute('SELECT id FROM inquiries WHERE patient_id=? AND hospital_id=?',('P',hospital)).fetchone()[0]
        return physical.respond(self.conn,at,iid,'decline','unit',reason='기타')

    def add_peer(self,hospital='H4',at=1001):
        physical.create_patient(self.conn,at,'PEER',dict(reporter_role='transporter',consent=True,symptoms='unit',required_capabilities=[],
            resource_demands={'ed_bed':1},lat=35.,lon=127.))
        with db.transaction(self.conn):physical._send_inquiries(self.conn,at,'PEER',[hospital],actor='unit_public_peer')

    def apply_wait(self,now,snapshot,wake):
        with patch.object(p.original,'decide',side_effect=parent_stub([])):
            return w._apply(self.conn,now,snapshot,offers(),inquiry_observations.capture(self.conn,now,'P'),wake_context=wake)

    def state(self):
        return {table:[tuple(row) for row in self.conn.execute('SELECT * FROM '+table+' ORDER BY 1')] for table in
            ('patients','inquiry_workflows','inquiries','inquiry_policy_decisions','events','planning_state')}


class PredecessorwakeWorkflowTests(DBFixture):
    def test_guard_blocked_send_commits_consumption_but_preserves_old_due_and_regular_send(self):
        self.seed();self.add_peer();self.decline();snapshot,_,wake=w._capture(self.conn,1005,'P')
        with patch.object(p.original,'decide',side_effect=parent_stub(['H4'])):
            result=w._apply(self.conn,1005,snapshot,offers(),inquiry_observations.capture(self.conn,1005,'P'),wake_context=wake)
        self.assertEqual(result['record']['output']['send'],[])
        self.assertEqual(result['record']['output']['evidence']['cohort_predecessorwake']['travel_guard']['rejected_hospital_ids'],['H4'])
        self.assertEqual(w._job(self.conn,'P')['next_evaluation_at'],1015)
        self.assertIsNone(w._capture(self.conn,1005,'P')[0])
        snapshot,_,wake=w._capture(self.conn,1015,'P');self.assertIsNone(wake)
        with patch.object(p.original,'decide',side_effect=parent_stub(['H4'])):
            regular=w._apply(self.conn,1015,snapshot,offers(),inquiry_observations.capture(self.conn,1015,'P'))
        self.assertEqual(regular['record']['output']['send'],['H4'])

    def test_empty_public_foreign_queue_recalls_farther_early_send_without_postponement(self):
        self.seed();self.decline();snapshot,_,wake=w._capture(self.conn,1005,'P')
        with patch.object(p.original,'decide',side_effect=parent_stub(['H4'])):
            result=w._apply(self.conn,1005,snapshot,offers(),inquiry_observations.capture(self.conn,1005,'P'),wake_context=wake)
        out=result['record']['output'];tag=out['evidence']['cohort_predecessorwake']
        self.assertEqual(out['send'],['H4'])
        self.assertEqual(tag['travel_guard']['original_travelwake_guard']['allowed_sends'],[])
        self.assertEqual(tag['original_travelwake_proposal']['send'],[])
        self.assertEqual(tag['original_declinewake_proposal']['send'],['H4'])
        self.assertEqual(tag['queue_exception_applied_hospital_ids'],['H4'])
        self.assertEqual(w._job(self.conn,'P')['next_evaluation_at'],1015)
        self.assertIsNone(w._capture(self.conn,1005,'P')[0])

    def test_travel_unknown_own_pending_omitted_by_H_input_still_blocks_early_send(self):
        self.seed();self.decline();snapshot,_,wake=w._capture(self.conn,1005,'P')
        missing=offers();missing['candidates']=[q for q in missing['candidates'] if q['hospital_id']!='H2']
        with patch.object(p.original,'decide',side_effect=parent_stub(['H4'])):
            result=w._apply(self.conn,1005,snapshot,missing,inquiry_observations.capture(self.conn,1005,'P'),wake_context=wake)
        self.assertEqual(result['record']['output']['send'],[])
        guard=result['record']['output']['evidence']['cohort_predecessorwake']['travel_guard']
        self.assertTrue(guard['unknown_fresh_pending_travel'])

    def test_new_decline_wakes_before_existing_review_and_no_send_consumes_once(self):
        self.seed();self.decline();snapshot,changed,wake=w._capture(self.conn,1005,'P')
        self.assertFalse(changed);self.assertIsNotNone(wake)
        result=self.apply_wait(1005,snapshot,wake);self.assertEqual(result['record']['output']['send'],[])
        self.assertEqual(result['record']['output']['next_review_at'],1015)
        self.assertEqual(w._job(self.conn,'P')['next_evaluation_at'],1015)
        again,_,again_wake=w._capture(self.conn,1005,'P');self.assertIsNone(again);self.assertIsNone(again_wake)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiry_policy_decisions').fetchone()[0],2)

    def test_same_second_later_event_id_can_wake_again_but_seen_event_cannot(self):
        self.seed();self.decline(at=1000);snapshot,_,wake=w._capture(self.conn,1000,'P');self.assertIsNotNone(wake)
        first=self.apply_wait(1000,snapshot,wake);prefix=first['record']['inputs']['time_context']['event_prefix_max']
        self.decline('H2',at=1000);snapshot,_,wake=w._capture(self.conn,1000,'P')
        self.assertIsNotNone(wake);self.assertTrue(all(row['event']['id']>prefix for row in wake['terminal_declines']))
        self.apply_wait(1000,snapshot,wake);self.assertIsNone(w._capture(self.conn,1000,'P')[0])

    def test_duplicate_terminal_click_does_not_create_a_new_wake_event(self):
        self.seed();first=self.decline();snapshot,_,wake=w._capture(self.conn,1005,'P');self.apply_wait(1005,snapshot,wake)
        count=self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        again=self.decline();self.assertTrue(again['duplicate'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0],count)
        self.assertIsNone(w._capture(self.conn,1005,'P')[0])

    def test_fractional_reply_and_old_review_are_not_floored(self):
        self.seed();self.conn.execute("UPDATE inquiry_workflows SET next_evaluation_at=1014.5,version=version+1 WHERE patient_id='P'")
        self.decline(at=1005.5)
        self.assertIsNone(w._capture(self.conn,1005,'P')[0])
        snapshot,_,wake=w._capture(self.conn,1006,'P');self.assertEqual(wake['terminal_declines'][0]['event']['at'],1005.5)
        result=self.apply_wait(1006,snapshot,wake)
        self.assertEqual(result['record']['output']['next_review_at'],1014.5)
        self.assertEqual(w._job(self.conn,'P')['next_evaluation_at'],1014.5)

    def test_all_own_closed_uses_original_normal_capture_without_new_trigger(self):
        self.seed(count=1);self.decline();snapshot,changed,wake=w._capture(self.conn,1005,'P')
        self.assertIsNotNone(snapshot);self.assertIsNone(wake)

    def test_event_only_append_invalidates_wake_without_consuming_a_decision(self):
        self.seed();self.decline();snapshot,_,wake=w._capture(self.conn,1005,'P');revision=w._revision(self.conn)
        physical.log(self.conn,1005,'unit','unrelated_public_event','P')
        self.assertEqual(w._revision(self.conn),revision)
        before=self.state();self.assertIsNone(self.apply_wait(1005,snapshot,wake));self.assertEqual(self.state(),before)
        self.assertIsNotNone(w._capture(self.conn,1005,'P')[2])

    def test_patient_job_or_inquiry_change_invalidates_snapshot_without_consumption(self):
        changes=("UPDATE patients SET version=version+1 WHERE id='P'", "UPDATE inquiry_workflows SET version=version+1 WHERE patient_id='P'",
            "UPDATE inquiries SET version=version+1 WHERE patient_id='P' AND hospital_id='H2'")
        for sql in changes:
            with self.subTest(sql=sql):
                snapshot,_,wake=w._capture(self.conn,1005,'P') if self.conn.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0] else (None,None,None)
                if snapshot is None:
                    self.seed();self.decline();snapshot,_,wake=w._capture(self.conn,1005,'P')
                self.conn.execute(sql);before=self.state();self.assertIsNone(self.apply_wait(1005,snapshot,wake));self.assertEqual(self.state(),before)

    def test_capture_and_apply_acceptance_priority_prevents_extra_send(self):
        self.seed();self.decline();snapshot,_,wake=w._capture(self.conn,1005,'P')
        iid=self.conn.execute("SELECT id FROM inquiries WHERE hospital_id='H2'").fetchone()[0]
        physical.respond(self.conn,1005,iid,'accept','unit')
        self.assertIsNone(self.apply_wait(1005,snapshot,wake))
        current,_,current_wake=w._capture(self.conn,1005,'P');self.assertIsNone(current);self.assertIsNone(current_wake)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiry_policy_decisions').fetchone()[0],1)

    def test_sql_failure_after_decision_insert_rolls_back_send_and_consumption(self):
        self.seed();self.decline();snapshot,_,wake=w._capture(self.conn,1005,'P');before=self.state()
        closer=offers();next(q for q in closer['candidates'] if q['hospital_id']=='H4')['travel']['duration_seconds']=50
        with patch.object(p.original,'decide',side_effect=parent_stub(['H4'])),patch.object(w.service,'_send_inquiries',side_effect=RuntimeError('unit SQL fail')):
            with self.assertRaisesRegex(RuntimeError,'SQL fail'):
                w._apply(self.conn,1005,snapshot,closer,inquiry_observations.capture(self.conn,1005,'P'),wake_context=wake)
        self.assertEqual(self.state(),before);self.assertIsNotNone(w._capture(self.conn,1005,'P')[2])

    def test_missing_or_malformed_actual_decision_prefix_does_not_guess(self):
        self.seed();self.decline();self.conn.execute("UPDATE inquiry_policy_decisions SET evidence='{}'")
        snapshot,_,wake=w._capture(self.conn,1005,'P');self.assertIsNone(snapshot);self.assertIsNone(wake)

    def test_deadline_disabled_and_contact_limit_keep_original_priority(self):
        self.seed(count=10);self.decline();self.assertIsNone(w._capture(self.conn,1005,'P')[0])
        self.conn.execute("UPDATE inquiry_workflows SET enabled=0 WHERE patient_id='P'")
        self.assertIsNone(w._capture(self.conn,1005,'P')[0])
        self.conn.execute("UPDATE inquiry_workflows SET enabled=1,deadline_at=1005 WHERE patient_id='P'")
        self.assertIsNone(w._capture(self.conn,1005,'P')[0])

    def test_checking_first_and_late_timeout_do_not_hide_new_terminal_declines(self):
        self.seed();qid=self.conn.execute("SELECT id FROM inquiries WHERE hospital_id='H1'").fetchone()[0]
        physical.respond(self.conn,1001,qid,'checking','unit');self.decline('H1',1005)
        ledger=inquiry_observations.capture(self.conn,1005,'P')
        self.assertEqual(next(x for x in ledger['observations'] if x['hospital']=='H1')['outcome'],'reply')
        snapshot,_,wake=w._capture(self.conn,1005,'P');self.assertIsNotNone(wake);self.apply_wait(1005,snapshot,wake)
        self.conn.execute("UPDATE inquiry_workflows SET next_evaluation_at=1195,version=version+1 WHERE patient_id='P'")
        self.decline('H2',1181)
        ledger=inquiry_observations.capture(self.conn,1181,'P')
        self.assertEqual(next(x for x in ledger['observations'] if x['hospital']=='H2')['outcome'],'timeout')
        self.assertIsNotNone(w._capture(self.conn,1181,'P')[2])

    def test_estimation_failure_does_not_record_or_consume_a_decline(self):
        self.seed();self.decline();before=self.conn.execute('SELECT COUNT(*) FROM inquiry_policy_decisions').fetchone()[0];records=[]
        previous=w._job(self.conn,'P')
        with patch.object(w.service,'candidates',side_effect=RuntimeError('unit estimator failed')):
            w.run_once(self.path,object(),lambda:1005,records=records)
        self.assertEqual(records,[]);self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiry_policy_decisions').fetchone()[0],before)
        self.assertEqual(w._job(self.conn,'P')['state'],'ERROR')

    def test_apply_valid_acceptance_precedes_wake_validation_or_policy_evaluation(self):
        self.seed();self.decline();_,_,wake=w._capture(self.conn,1005,'P')
        qid=self.conn.execute("SELECT id FROM inquiries WHERE hospital_id='H2'").fetchone()[0]
        physical.respond(self.conn,1005,qid,'accept','unit');job=w._job(self.conn,'P');patient=physical._patient(self.conn,'P')
        fresh=dict(patient_id='P',patient_version=patient['version'],job_version=job['version'],revision=w._revision(self.conn),now=1005)
        with patch.object(p.original,'decide',side_effect=AssertionError('must not evaluate after valid acceptance')):
            result=w._apply(self.conn,1005,fresh,offers(),inquiry_observations.capture(self.conn,1005,'P'),wake_context=wake)
        self.assertIsNone(result['record']);self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiry_policy_decisions').fetchone()[0],1)

    def test_wake_clock_drift_is_stale_without_error_or_consumption_and_can_recapture(self):
        self.seed();self.decline();snapshot,_,wake=w._capture(self.conn,1005,'P');before=self.state()
        result=self.apply_wait(1006,snapshot,wake)
        self.assertIsNone(result);self.assertEqual(self.state(),before)
        next_snapshot,_,next_wake=w._capture(self.conn,1006,'P')
        self.assertIsNotNone(next_snapshot);self.assertIsNotNone(next_wake)
        self.assertNotEqual(w._job(self.conn,'P')['state'],'ERROR')

    def test_run_once_drifting_clock_does_not_turn_normal_wake_into_error(self):
        self.seed();self.decline();before=self.state();records=[];times=iter((1005,1005,1006,1006))
        with patch.object(w.service,'candidates',return_value=offers()),patch.object(w,'_failed',side_effect=AssertionError('clock drift is not a policy error')) as failed:
            w.run_once(self.path,object(),lambda:next(times),records=records)
        failed.assert_not_called();self.assertEqual(records,[]);self.assertEqual(self.state(),before)

    def test_nonwaiting_patient_preempts_new_decline_and_stops_without_a_decision(self):
        self.seed();self.decline()
        for state in ('CANCELLED','CONFIRMED'):
            self.conn.execute("UPDATE inquiry_workflows SET enabled=1,state='RUNNING',version=version+1 WHERE patient_id='P'")
            self.conn.execute('UPDATE patients SET state=?,version=version+1 WHERE id=?',(state,'P'))
            count=self.conn.execute('SELECT COUNT(*) FROM inquiry_policy_decisions').fetchone()[0]
            snapshot,changed,wake=w._capture(self.conn,1005,'P')
            self.assertIsNone(snapshot);self.assertIsNone(wake);self.assertTrue(changed)
            self.assertFalse(w._job(self.conn,'P')['enabled']);self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiry_policy_decisions').fetchone()[0],count)

    def test_foreign_active_job_is_rejected_before_any_local_workflow_write(self):
        self.seed();self.decline()
        physical.create_patient(self.conn,1000,'FOREIGN',dict(reporter_role='transporter',consent=True,symptoms='unit',required_capabilities=[],
            resource_demands={'ed_bed':1},lat=35.,lon=127.,auto_inquiry=True,inquiry_policy='fixed_3'))
        before=self.state();records=[]
        with patch.object(w,'_failed',side_effect=AssertionError('scope rejection must not mark a foreign job ERROR')) as failed:
            with self.assertRaisesRegex(ValueError,'candidate active jobs'):w.run_once(self.path,object(),lambda:1005,records=records)
        failed.assert_not_called();self.assertEqual(self.state(),before);self.assertEqual(records,[])

    def test_ordinary_forward_clock_is_allowed_but_future_capture_cannot_leak_backwards(self):
        self.seed();job=w._job(self.conn,'P');patient=physical._patient(self.conn,'P')
        snapshot=dict(patient_id='P',patient_version=patient['version'],job_version=job['version'],revision=w._revision(self.conn),now=1005)
        with self.assertRaises(ValueError):w._time_context(self.conn,1004,snapshot,job)
        value,_=w._time_context(self.conn,1006,snapshot,job);self.assertEqual(value['evaluation_end_at'],1300)


class PublicPredecessorWorkflowTests(DBFixture):
    def predecessor(self,pid='PEER',hospital='H1',sent=990):
        physical.create_patient(self.conn,sent,pid,dict(reporter_role='transporter',consent=True,symptoms='unit',required_capabilities=[],
            resource_demands={'ed_bed':1},lat=35.,lon=127.))
        with db.transaction(self.conn):physical._send_inquiries(self.conn,sent,pid,[hospital],actor='unit_public_predecessor')
        return self.conn.execute('SELECT id FROM inquiries WHERE patient_id=? AND hospital_id=?',(pid,hospital)).fetchone()[0]

    def reply(self,iid,action='decline',at=1005):
        return physical.respond(self.conn,at,iid,action,'unit_public_reply',reason='기타' if action=='decline' else None)

    def test_strict_foreign_decline_wakes_and_no_send_consumes_once(self):
        iid=self.predecessor();self.seed();self.reply(iid)
        snapshot,changed,wake=w._capture(self.conn,1005,'P')
        self.assertFalse(changed);self.assertIsNotNone(snapshot);self.assertEqual(wake['rule'],w.context.predecessor.RULE)
        self.assertEqual(wake['terminal_predecessors'][0]['inquiry']['id'],iid)
        result=self.apply_wait(1005,snapshot,wake);tag=result['record']['output']['evidence']['cohort_predecessorwake']
        self.assertEqual(tag['wake_kind'],'public_strict_predecessor_terminal_reply')
        self.assertEqual(tag['public_predecessor_context'],wake)
        self.assertEqual(w._job(self.conn,'P')['next_evaluation_at'],1015)
        self.assertIsNone(w._capture(self.conn,1005,'P')[0]);self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM inquiry_policy_decisions').fetchone()[0],2)

    def test_accepted_then_confirmed_foreign_patient_remains_valid_public_terminal_source(self):
        iid=self.predecessor();self.seed();self.reply(iid,'accept')
        physical.confirm_destination(self.conn,1005,'PEER',iid,actor='unit_human')
        snapshot,_,wake=w._capture(self.conn,1005,'P')
        self.assertIsNotNone(snapshot);self.assertEqual(wake['terminal_predecessors'][0]['inquiry']['state'],'CONFIRMED')
        self.assertEqual(wake['terminal_predecessors'][0]['event']['data']['action'],'accept')

    def test_same_clock_foreign_send_is_not_a_strict_predecessor(self):
        iid=self.predecessor(sent=1000);self.seed();self.reply(iid)
        snapshot,_,wake=w._capture(self.conn,1005,'P');self.assertIsNone(snapshot);self.assertIsNone(wake)

    def test_foreign_checking_or_unrelated_hospital_does_not_wake(self):
        iid=self.predecessor(hospital='H4');self.seed();self.reply(iid)
        self.assertIsNone(w._capture(self.conn,1005,'P')[0])

    def test_foreign_nonterminal_checking_is_not_a_completed_queue_source(self):
        iid=self.predecessor();self.seed();self.reply(iid,'checking')
        self.assertIsNone(w._capture(self.conn,1005,'P')[0])

    def test_own_decline_has_priority_and_consumes_shared_prefix_before_foreign_retry(self):
        iid=self.predecessor();self.seed();self.reply(iid);self.decline('H2')
        snapshot,_,wake=w._capture(self.conn,1005,'P');self.assertEqual(wake['rule'],w.context.WAKE_RULE)
        result=self.apply_wait(1005,snapshot,wake)
        self.assertEqual(result['record']['output']['evidence']['cohort_predecessorwake']['wake_kind'],'own_terminal_decline')
        self.assertIsNone(w._capture(self.conn,1005,'P')[0])

    def test_foreign_row_event_drift_and_clock_drift_discard_without_consuming(self):
        iid=self.predecessor();self.seed();self.reply(iid);snapshot,_,wake=w._capture(self.conn,1005,'P');before=self.state()
        self.assertIsNone(self.apply_wait(1006,snapshot,wake));self.assertEqual(self.state(),before)
        physical.log(self.conn,1005,'unit','unit_public_append','PEER')
        before=self.state();self.assertIsNone(self.apply_wait(1005,snapshot,wake));self.assertEqual(self.state(),before)
        self.assertIsNotNone(w._capture(self.conn,1005,'P')[2])

    def test_new_same_second_foreign_event_after_commit_can_wake_again(self):
        first=self.predecessor();second=self.predecessor(pid='PEER2',hospital='H2',sent=995)
        self.seed();self.reply(first);snapshot,_,wake=w._capture(self.conn,1005,'P');self.apply_wait(1005,snapshot,wake)
        self.reply(second);snapshot,_,wake=w._capture(self.conn,1005,'P')
        self.assertIsNotNone(wake);self.assertEqual([r['inquiry']['id'] for r in wake['terminal_predecessors']],[second])
        self.apply_wait(1005,snapshot,wake);self.assertIsNone(w._capture(self.conn,1005,'P')[0])

    def test_valid_own_acceptance_preempts_foreign_wake(self):
        iid=self.predecessor();self.seed();self.reply(iid)
        own=self.conn.execute("SELECT id FROM inquiries WHERE patient_id='P' AND hospital_id='H2'").fetchone()[0]
        self.reply(own,'accept');snapshot,changed,wake=w._capture(self.conn,1005,'P')
        self.assertIsNone(snapshot);self.assertTrue(changed);self.assertIsNone(wake)

    def test_genuine_H_can_receive_common_fields_for_foreign_wake(self):
        iid=self.predecessor();self.seed();self.reply(iid);snapshot,_,wake=w._capture(self.conn,1005,'P')
        result=w._apply(self.conn,1005,snapshot,offers(),inquiry_observations.capture(self.conn,1005,'P'),wake_context=wake)
        self.assertIsNotNone(result['record'])
        tag=result['record']['output']['evidence']['cohort_predecessorwake']
        self.assertEqual(tag['wake_kind'],'public_strict_predecessor_terminal_reply')
        self.assertEqual(tag['predecessor_helper_sha256'],w.context.PREDECESSOR_HELPER_SHA256)
        self.assertLessEqual(result['record']['output']['next_review_at'],1015)


if __name__=='__main__':unittest.main()
