/** Server contract fixture. Imports the app client read-only; never opens a UI or microphone. */
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {VoiceClient} from '../paramedic-ui/voice-client.mjs';

const [server, output] = process.argv.slice(2);
assert.ok(server && output, 'Pass an isolated loopback server and report file');
assert.equal(new URL(server).hostname, '127.0.0.1');
const steps = [], requests = [];
let loseNextWrite = false;
const transport = async (url, options = {}) => {
  assert.equal(new URL(url).origin, server, 'No external requests');
  requests.push({path:new URL(url).pathname,method:options.method||'GET'});
  const response = await fetch(url, options);
  if (loseNextWrite && options.method === 'POST') {
    loseNextWrite = false;
    await response.arrayBuffer();
    throw new Error('QA fixture: response lost after the server committed');
  }
  return response;
};
const client = new VoiceClient({server,actorId:'CONNECTION-QA-NOT-REAL-AUDIO',fetchImpl:transport});
let uid = 0;
const say = text => client.write('transcripts',{text,mode:'auto',is_final:true,utterance_id:'utterance-'+(++uid)});
const answer = (question_id,text) => client.write('followup/answers',{
  question_id,text,profile:'voice_extended_v1',utterance_id:'answer-'+(++uid)});
async function step(name,run) {
  await run();
  steps.push({name,pass:true,version:client.session?.version});
}
const report = {fixture:true,actual_speech_count:0,external_requests:0,steps,requests,
  scope:'Current VoiceClient + real local HTTP; frontend controls and actual speech are not exercised.'};
try {
  await step('create_draft_with_actual_client',async()=>{
    await client.create();assert.equal(client.session.state,'DRAFT');
  });
  await step('one_long_final_utterance_produces_five_questions',async()=>{
    const result=await say('47세 남성 환자입니다 20분 전부터 가슴통증이 있습니다 2시간 후면 정상입니다 혈압 120 80 92회 산소 포화도 97% 체온 37.2도 고급 수입 회 혈당 105입니다 도와 주려는 없습니다');
    assert.equal(result.requested_mode,'auto');
    assert.deepEqual(client.session.followup.items.map(q=>q.id),['consciousness','breathing','blood_pressure','pulse','respiratory_rate']);
    assert.equal(client.session.utterances.length,1);
  });
  await step('guided_short_answers_advance_questions',async()=>{
    for (const [qid,text] of [['consciousness','명료'],['breathing','정상'],['blood_pressure','120에80'],['pulse','92회'],['respiratory_rate','20회']]) {
      assert.equal(client.session.followup.next_question.id,qid);
      const result=await answer(qid,text);assert.equal(result.answer.accepted,true);
    }
    assert.equal(client.session.followup.complete,true);
    assert.equal(client.session.fields.systolic_bp,120);
    assert.equal(client.session.fields.pulse,92);
  });
  await step('natural_status_change_keeps_previous_value',async()=>{
    const result=await say('혈압이 지금 100에 80까지 떨어졌어');
    assert.equal(result.command.action,'observe');
    const change=client.session.latest_changes.find(c=>c.field==='systolic_bp');
    assert.equal(change.previous,120);assert.equal(change.current,100);assert.equal(change.direction,'decreased');
  });
  await step('correction_and_readback_preserve_different_change_kind',async()=>{
    const result=await say('혈압 110에 70으로 수정해');
    assert.equal(result.command.action,'edit');
    assert.equal(client.session.field_metadata.systolic_bp.change_kind,'correction');
    const read=await client.readback('blood_pressure');
    assert.ok(read.speak_text.includes('110')&&read.speak_text.includes('70'));
  });
  await step('voice_confirmation_waits_for_manual_confirmation',async()=>{
    const result=await say('확인');
    assert.equal(result.operational_effect,'confirmation_requires_tap');
    assert.equal(client.session.confirmed,false);
    await client.write('confirm',{confirmed:true});
    assert.equal(client.session.confirmed,true);
  });
  await step('unvalued_change_preserves_old_value_and_reopens_question',async()=>{
    await say('혈압이 지금 떨어졌어');
    assert.equal(client.session.confirmed,false);
    assert.equal(client.session.fields.systolic_bp,110);
    assert.equal(client.session.field_metadata.systolic_bp.needs_clarification,true);
    assert.equal(client.session.followup.next_question.id,'blood_pressure');
    const read=await client.readback('blood_pressure');assert.ok(read.speak_text.includes('이전 관찰값'));
  });
  await step('undo_unavailable_answer_restores_recheck_instead_of_erasing_it',async()=>{
    await answer('blood_pressure','미측정');
    assert.equal(client.session.followup.collection_complete,true);
    await say('방금 거 취소');
    assert.equal(client.session.fields.systolic_bp,110);
    assert.equal(client.session.field_metadata.systolic_bp.needs_clarification,true);
    assert.equal(client.session.followup.next_question.id,'blood_pressure');
  });
  await step('undo_measurement_restores_prior_unavailable_status',async()=>{
    await answer('blood_pressure','미측정');
    await answer('blood_pressure','100에80');
    assert.equal(client.session.fields.systolic_bp,100);
    await say('방금 거 취소');
    assert.equal(client.session.fields.systolic_bp,110);
    const question=client.session.followup.items.find(q=>q.id==='blood_pressure');
    assert.equal(question.answer_status,'unavailable');assert.equal(question.askable,false);
  });
  await step('lost_answer_response_retries_exact_request_once',async()=>{
    const before=client.session.version;
    loseNextWrite=true;
    await assert.rejects(answer('blood_pressure','100에80'),error=>error.uncertain===true);
    const request=structuredClone(client.pending);
    assert.ok(request);
    const result=await client.retry();
    assert.equal(result.duplicate,true);assert.equal(client.pending,null);
    assert.equal(client.session.version,before+1);assert.equal(client.session.fields.systolic_bp,100);
    assert.equal(client.session.utterances.filter(u=>u.utterance_id===request.body.utterance_id).length,1);
  });
  await step('stale_client_does_not_overwrite_after_another_session_view_updates',async()=>{
    const stale=new VoiceClient({server,actorId:client.actorId});
    await stale.sync(client.session.id);
    await say('맥박이 지금 96으로 올랐어');
    await assert.rejects(stale.write('fields',{changes:{pulse:70},change_kind:'correction'},'PATCH'),error=>error.status===409);
    await stale.sync();assert.equal(stale.session.fields.pulse,96);
  });
  await step('final_manual_confirmation_and_reload_keep_data',async()=>{
    await client.write('confirm',{confirmed:true});
    const reloaded=new VoiceClient({server,actorId:client.actorId});await reloaded.sync(client.session.id);
    assert.equal(reloaded.session.confirmed,true);assert.equal(reloaded.session.fields.systolic_bp,100);
    assert.equal(reloaded.session.patient_id,null);assert.equal(reloaded.session.state,'DRAFT');
    report.final={session_id:reloaded.session.id,version:reloaded.session.version,confirmed:reloaded.session.confirmed,
      fields:reloaded.session.fields};
  });
  report.passed=steps.length;report.failed=0;
} catch (error) {
  report.passed=steps.length;report.failed=1;report.error=error.stack||String(error);process.exitCode=1;
} finally {
  await writeFile(output,JSON.stringify(report,null,2),'utf8');
  console.log(JSON.stringify({passed:report.passed,failed:report.failed,actual_speech_count:0,error:report.error||null}));
}
