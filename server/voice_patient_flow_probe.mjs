/** Actual app HTTP client + synthetic patient/hospital lifecycle. No UI or speech. */
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {VoiceClient} from '../paramedic-ui/voice-client.mjs';

const [server,output]=process.argv.slice(2);
assert.ok(server&&output,'Pass a private loopback fixture and output path');
assert.equal(new URL(server).hostname,'127.0.0.1');
const steps=[],requests=[];
let loseNextSubmit=false,uid=0,pid,inquiry;
const transport=async(url,options={})=>{
  assert.equal(new URL(url).origin,server,'No external requests');
  const path=new URL(url).pathname;
  requests.push({path,method:options.method||'GET'});
  const response=await fetch(url,options);
  if(loseNextSubmit&&path.endsWith('/submit')&&options.method==='POST'){
    loseNextSubmit=false;await response.arrayBuffer();
    throw new Error('QA fixture: response lost after committed submit');
  }
  return response;
};
const client=new VoiceClient({server,actorId:'VOICE-PATIENT-FLOW-QA',fetchImpl:transport});
const say=text=>client.write('transcripts',{text,mode:'auto',is_final:true,utterance_id:'flow-'+(++uid)});
const api=(path,body)=>client.api(path,body===undefined?'GET':'POST',body);
const patient=()=>api(`/api/patients/${pid}`);
const box=()=>api('/api/hospitals/VOICE-FLOW-H1/inquiries');
const respond=(action,extra={})=>api(`/api/inquiries/${inquiry.id}/respond`,{
  action,handled_by:'SYNTHETIC-DESK',expected_version:inquiry.version,...extra});
const hospitalPatient=async()=>{
  const inbox=await box();
  const item=[...inbox.open,...inbox.recent].find(q=>q.patient_id===pid);
  assert.ok(item);return item;
};
async function step(name,run){await run();steps.push({name,pass:true});}
const report={fixture:true,actual_speech_count:0,actual_hospital_requests:0,external_requests:0,
  scope:'Actual VoiceClient against a private local synthetic server. No frontend controls, microphone, routing API or real hospitals.',
  steps,requests};
try{
  await step('long_report_and_guided_answer_create_reviewable_r1_draft',async()=>{
    await client.create();
    await say('47세 남성. 흉통. 의식 명료. 호흡 정상. 케이타스 2. 혈압 120에 80. 맥박 90회. 산소포화도 98퍼센트.');
    assert.equal(client.session.fields.pre_ktas,2);
    assert.equal(client.session.fields.avpu,'A');
    await client.write('followup/answers',{question_id:'temperature',text:'37.2도',utterance_id:'temperature-answer'});
    assert.equal(client.session.fields.temperature,37.2);
    assert.equal(client.session.patient_id,null);
  });
  await step('explicit_confirm_submit_and_lost_response_retry_create_one_patient',async()=>{
    await client.write('confirm',{confirmed:true});
    loseNextSubmit=true;
    await assert.rejects(client.write('submit',{
      context:{consent:true,lat:35.16,lon:126.85},start_inquiry:false}),e=>e.uncertain===true);
    assert.ok(client.pending);
    const retry=await client.retry();
    assert.equal(retry.duplicate,true);
    assert.equal(client.session.state,'SUBMITTED');pid=client.session.patient_id;
    assert.ok(pid);assert.equal(retry.patient.inquiries.length,0);
    assert.equal(retry.patient.extra.vitals.sbp,120);
    assert.equal(retry.patient.priority,4);
  });
  await step('candidate_query_returns_only_fixture_hospitals',async()=>{
    const list=await api(`/api/patients/${pid}/candidates`);
    assert.deepEqual(list.candidates.map(c=>c.hospital_id).sort(),['VOICE-FLOW-H1','VOICE-FLOW-H2']);
    const recommendation=await api(`/api/patients/${pid}/recommendation`);
    assert.ok(recommendation.availability);
  });
  await step('synthetic_hospital_sees_reviewed_values_and_request',async()=>{
    await api(`/api/patients/${pid}/inquiries`,{hospital_ids:['VOICE-FLOW-H1']});
    inquiry=await hospitalPatient();
    assert.equal(inquiry.state,'PENDING');assert.equal(inquiry.patient.extra.avpu,'A');
    assert.equal(inquiry.patient.extra.pre_ktas_level,2);
    assert.equal(inquiry.patient.extra.vitals.sbp,120);
    assert.equal(inquiry.patient.extra.vitals.hr,90);
  });
  await step('hospital_question_and_crew_answer_are_shared',async()=>{
    inquiry=(await respond('question',{question:'가상 시연: 증상 발생 시점을 확인해 주세요.'})).inquiry;
    const result=await api(`/api/patients/${pid}/inquiries/${inquiry.id}/answer`,{answer:'가상 시연: 20분 전입니다.'});
    assert.ok(result.inquiries.some(q=>q.answer==='가상 시연: 20분 전입니다.'));
    inquiry=await hospitalPatient();
    assert.equal(inquiry.answer,'가상 시연: 20분 전입니다.');
  });
  await step('hospital_acceptance_is_not_automatic_destination_confirmation',async()=>{
    inquiry=(await respond('accept')).inquiry;
    assert.equal(inquiry.state,'ACCEPTED');assert.equal((await patient()).state,'WAITING');
  });
  await step('new_voice_session_reads_current_patient_and_preserves_other_vitals',async()=>{
    await client.create(pid);
    assert.equal(client.session.fields.systolic_bp,120);assert.equal(client.session.fields.pulse,90);
    assert.equal(client.session.fields.pre_ktas,2);
    await say('맥박이 지금 96으로 올랐어');
    await client.write('confirm',{confirmed:true});
    const changed=await client.write('submit');
    assert.equal(changed.patient.extra.vitals.hr,96);
    assert.equal(changed.patient.extra.vitals.sbp,120);
    assert.equal(changed.patient.extra.vitals.dbp,80);
    assert.equal(changed.patient.extra.vitals.spo2,98);
    assert.equal(changed.patient.extra.vitals.temp,37.2);
    assert.equal(changed.patient.inquiries.find(q=>q.id===inquiry.id).state,'CANCELLED');
  });
  await step('hospital_is_notified_and_old_review_cannot_be_reused',async()=>{
    const events=await api(`/api/hospitals/VOICE-FLOW-H1/patients/${pid}/important-events?actor_id=SYNTHETIC-DESK`);
    assert.ok(events.items.some(e=>e.type==='patient_information_amended'));
    await api(`/api/patients/${pid}/inquiries`,{hospital_ids:['VOICE-FLOW-H1']});
    await assert.rejects(respond('accept'),e=>e.status===409);
    inquiry=await hospitalPatient();
    assert.equal(inquiry.patient.extra.vitals.hr,96);
    inquiry=(await respond('accept')).inquiry;assert.equal(inquiry.state,'ACCEPTED');
  });
  await step('destination_confirmation_and_transport_are_explicit',async()=>{
    const confirmed=await api(`/api/patients/${pid}/confirm`,{inquiry_id:inquiry.id});
    assert.equal(confirmed.state,'CONFIRMED');assert.equal(confirmed.destination_hospital_id,'VOICE-FLOW-H1');
    const moving=await api(`/api/patients/${pid}/depart`,{});
    assert.equal(moving.state,'IN_TRANSIT');
    const incoming=await api('/api/hospitals/VOICE-FLOW-H1/incoming');
    assert.ok(incoming.incoming.some(p=>p.id===pid));
  });
  await step('voice_draft_cannot_overwrite_transporting_patient',async()=>{
    await client.create(pid);await say('맥박이 지금 100으로 올랐어');
    await client.write('confirm',{confirmed:true});
    await assert.rejects(client.write('submit'),e=>e.status===409&&e.code==='INVALID_STATE');
    assert.equal(client.session.state,'DRAFT');assert.equal((await patient()).extra.vitals.hr,96);
  });
  await step('hospital_arrival_closes_the_transport_without_double_counting',async()=>{
    const arrived=await api(`/api/hospitals/VOICE-FLOW-H1/patients/${pid}/arrive`,{handled_by:'SYNTHETIC-DESK'});
    assert.equal(arrived.state,'ARRIVED');
    const hospital=await api('/api/hospitals/VOICE-FLOW-H1');
    const bed=hospital.resources.find(r=>r.resource_type==='ed_bed');
    assert.equal(bed.occupied,1);assert.equal(bed.expected,0);assert.equal(bed.free,1);
    report.final={patient_id:pid,state:arrived.state,version:arrived.version,
      destination_hospital_id:arrived.destination_hospital_id,vitals:arrived.extra.vitals};
  });
  report.passed=steps.length;report.failed=0;
}catch(error){report.passed=steps.length;report.failed=1;report.error=error.stack||String(error);process.exitCode=1;}
finally{
  await writeFile(output,JSON.stringify(report,null,2),'utf8');
  console.log(JSON.stringify({passed:report.passed,failed:report.failed,actual_speech_count:0,error:report.error||null}));
}
