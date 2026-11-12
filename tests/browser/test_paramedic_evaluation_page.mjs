import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
import {setImmediate as nextTurn} from 'node:timers/promises';
import {noiseTestContext, captureTestContext, captureDescription} from '../../paramedic-ui/speech-test-context.mjs';
import {browserInputDiagnostics} from '../../paramedic-ui/audio-input-profile.mjs';

// The actual evaluation-page source and condition helpers run in a fake DOM.
// Capture callbacks and downloads are simulated and remain IN MEMORY ONLY.
// Mock "human_voice" exports below are not actual recordings and must never be
// written as a speech-evaluation dataset, scored as human audio, or sent to STT.
const source = fs.readFileSync(new URL('../../paramedic-ui/evaluate.js', import.meta.url), 'utf8')
  .replace(/^import .*;\r?\n/gm, '');
const html = fs.readFileSync(new URL('../../paramedic-ui/evaluate.html', import.meta.url), 'utf8');
const idEntries = [...html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"[^>]*>/g)];
const flush = async () => {for (let i=0;i<3;i++) await nextTurn();};

class Element {
  constructor(tag='div') {
    this.tagName=tag;this.children=[];this.textContent='';this.disabled=false;
    this.checked=false;this.hidden=false;this._value='';this.selectedIndex=0;
  }
  get options() {return this.children;}
  get value() {return this.tagName==='select'&&this.children.length ? this.children[this.selectedIndex]?.value : this._value;}
  set value(value) {
    const index=this.tagName==='select'?this.children.findIndex(c=>c.value===value):-1;
    if(index>=0)this.selectedIndex=index;else this._value=value;
  }
  replaceChildren(...children) {this.children=[...children];this.selectedIndex=0;}
  prepend(...children) {this.children.unshift(...children);}
  click() {this.onclick?.();}
}

async function harness() {
  const elements=Object.fromEntries(idEntries.map(m=>[m[2],new Element(m[1])]));
  const defaults={speaker:'S01',condition:'quiet','noise-kind':'unspecified','snr-basis':'unknown','record-count':'0'};
  for(const [id,value] of Object.entries(defaults)){
    if(id==='record-count')elements[id].textContent=value;else elements[id].value=value;
  }
  for(const m of idEntries)if(/\shidden(?:\s|>)/.test(m[0]))elements[m[2]].hidden=true;
  const downloads=[],fetches=[],captures=[],events={},clock={now:1000};let sequence=0;
  const corpus={schema_version:1,dataset_id:'FAKE-PAGE-TEST-ONLY',corpus_sha256:'fake-corpus-sha-for-vm-test',cases:[
    {id:'FAKE-C1',family:'measurements_arabic',mode:'dictation',reference_text:'혈압 120에 80, 맥박 92입니다.'},
    {id:'FAKE-C2',family:'negation',mode:'dictation',reference_text:'구토는 없습니다.'},
  ]};
  class FakeCapture {
    static capabilities(){return {browser:true,recording:false,tts:false};}
    constructor(callbacks){this.callbacks=callbacks;this.active=false;this.starts=0;this.inputDiagnostics=null;captures.push(this);}
    async start(mode){assert.equal(mode,'browser');this.starts++;this.active=true;this.inputDiagnostics=browserInputDiagnostics();this.callbacks.onState('listening');}
    async stop(){this.active=false;this.callbacks.onState('idle');return null;}
    abort(){this.active=false;this.callbacks.onState('idle');}
    stopSpeaking(){}
    final(text){this.callbacks.onFinal(text);}
    fail(code='network'){this.active=false;this.callbacks.onState('idle');this.callbacks.onError(Object.assign(new Error('simulated recognition failure'),{code}));}
  }
  class FakeBlob {constructor(parts,options){this.parts=parts;this.type=options.type;}}
  const context=vm.createContext({VoiceCapture:FakeCapture,requestId:()=>`fake-request-${++sequence}`,
    noiseTestContext,captureTestContext,captureDescription,
    document:{getElementById:id=>{assert.ok(elements[id],`unknown element: ${id}`);return elements[id];},createElement:tag=>new Element(tag)},
    window:{addEventListener:(name,callback)=>{events[name]=callback;}},
    Option:class extends Element{constructor(text,value){super('option');this.textContent=text;this.value=value;}},
    fetch:async(url,options)=>{fetches.push({url,options});assert.equal(url,'./evaluation-cases.json','no API/STT/network writes in the harness');return {ok:true,json:async()=>corpus};},
    Blob:FakeBlob,URL:{createObjectURL(blob){assert.equal(blob.type,'application/json');downloads.push(JSON.parse(blob.parts.join('')));return 'blob:fake-in-memory-only';},revokeObjectURL(){}},
    performance:{now:()=>++clock.now},setTimeout:()=>1,console});
  vm.runInContext(source,context,{filename:'evaluate.js'});await flush();
  const el=id=>elements[id],capture=captures[0];
  const finish=async(text='혈압 120에 80, 맥박 92입니다.')=>{capture.final(text);await el('eval-record').onclick();await flush();};
  const save=()=>{el('reference-verified').checked=true;el('reference-verified').onchange();el('save-recording').onclick();};
  const exportPackage=()=>{assert.equal(el('export-recordings').disabled,false);el('export-recordings').onclick();return downloads.at(-1);};
  return {el,capture,fetches,downloads,events,finish,save,exportPackage};
}

test('unknown SNR and distance remain null in the actual page export',async()=>{
  const h=await harness();await h.el('eval-record').onclick();await h.finish();h.save();
  const packet=h.exportPackage(),record=packet.recordings[0];
  assert.equal(packet.dataset_id,'FAKE-PAGE-TEST-ONLY');
  assert.equal(record.collection.noise_test.snr_db,null);
  assert.equal(record.collection.noise_test.snr_basis,'unknown');
  assert.equal(record.collection.noise_test.microphone_distance_cm,null);
  assert.equal(record.collection.noise_test.noise_asset_id,null);
  assert.equal(record.collection.actual_audio_file_saved,false);
  assert.equal(h.fetches.length,1);
});

test('measured and controlled-mix zero SNR preserve the attempt snapshot and browser route',async()=>{
  for(const basis of ['measured','controlled_mix']){
    const h=await harness();h.el('condition').value='background_noise';h.el('noise-kind').value='siren';
    h.el('noise-asset').value='siren-v1';h.el('pair-id').value='S01-P01';h.el('snr-db').value='0';
    h.el('snr-basis').value=basis;h.el('mic-distance').value='12.5';
    await h.el('eval-record').onclick();
    assert.equal(h.el('snr-db').disabled,true);assert.equal(h.el('noise-kind').disabled,true);
    // Even an out-of-band DOM edit after start must not rewrite this attempt's conditions.
    h.el('snr-db').value='20';h.el('pair-id').value='late-edit';h.el('noise-kind').value='none';
    await h.finish();h.save();const record=h.exportPackage().recordings[0];
    assert.equal(record.condition,'background_noise');
    assert.equal(record.collection.noise_test.snr_db,0);
    assert.equal(record.collection.noise_test.snr_basis,basis);
    assert.equal(record.collection.noise_test.noise_kind,'siren');
    assert.equal(record.collection.noise_test.noise_asset_id,'siren-v1');
    assert.equal(record.collection.noise_test.pair_id,'S01-P01');
    assert.equal(record.collection.noise_test.microphone_distance_cm,12.5);
    assert.equal(record.collection.audio_input.route,'browser_managed');
    assert.equal(record.collection.audio_input.app_preprocessing_applied,false);
    assert.deepEqual(record.collection.audio_input.applied,{});
    assert.match(h.el('eval-input-status').textContent,/확인할 수 없습니다/);
  }
});

test('an unsupported SNR claim is rejected before a microphone start or result creation',async()=>{
  const h=await harness();h.el('snr-db').value='0';h.el('snr-basis').value='unknown';
  await h.el('eval-record').onclick();await flush();
  assert.equal(h.capture.starts,0);assert.equal(h.capture.active,false);
  assert.equal(h.el('eval-error').hidden,false);
  assert.match(h.el('eval-error').textContent,/실측 또는 제어 혼합/);
  assert.equal(h.el('record-count').textContent,'0');assert.equal(h.el('export-recordings').disabled,true);
});

test('an engine error keeps partial raw finals and noise/input metadata, with an empty failed hypothesis',async()=>{
  const h=await harness();h.el('noise-kind').value='wind';h.el('pair-id').value='wind-pair';
  await h.el('eval-record').onclick();h.capture.final('혈압 120에 80');h.capture.fail('network');await flush();h.save();
  const record=h.exportPackage().recordings[0];
  assert.equal(record.status,'error');assert.equal(record.hypothesis,'');
  assert.equal(record.raw_hypothesis,'혈압 120에 80');
  assert.deepEqual(record.collection.received_final_fragments,['혈압 120에 80']);
  assert.equal(record.collection.recognition_error_code,'network');
  assert.equal(record.collection.noise_test.noise_kind,'wind');
  assert.equal(record.collection.noise_test.pair_id,'wind-pair');
  assert.equal(record.collection.audio_input.route,'browser_managed');
  assert.equal(record.collection.audio_input.app_preprocessing_applied,false);
  assert.equal(record.collection.actual_audio_file_saved,false);
});

test('verbatim-reference confirmation gates saving and a successful record cannot be saved twice',async()=>{
  const h=await harness();await h.el('eval-record').onclick();await h.finish();
  assert.equal(h.el('reference-verified').checked,false);assert.equal(h.el('save-recording').disabled,true);
  h.el('save-recording').onclick();
  assert.equal(h.el('record-count').textContent,'0');assert.equal(h.el('eval-log').children.length,0);
  assert.equal(h.el('export-recordings').disabled,true);
  h.save();h.el('save-recording').onclick();
  assert.equal(h.el('record-count').textContent,'1');assert.equal(h.el('eval-log').children.length,1);
  assert.equal(h.exportPackage().recordings.length,1);
});

test('the synthetic UI exercise remains explicitly synthetic and never captures or saves audio',async()=>{
  const h=await harness();h.el('simulate-result').onclick();
  assert.equal(h.capture.starts,0);assert.match(h.el('eval-status').textContent,/합성 예시/);
  assert.equal(h.el('reference-verified').checked,false);h.save();
  const record=h.exportPackage().recordings[0];
  assert.equal(record.source,'synthetic_corruption');assert.equal(record.engine,'synthetic-example');
  assert.equal(record.collection.actual_audio_file_saved,false);
  assert.equal(record.collection.audio_input,null);
  assert.equal(record.hypothesis,'혈압 120에 80, 맥박 92입니다.');
  assert.equal(h.capture.starts,0);assert.equal(h.fetches.length,1);
});
