import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {readDraftHandoff,writeDraftHandoff,DRAFT_HANDOFF_KEY} from '../../paramedic-ui/draft-handoff.mjs';

// Runs the production entry functions with the real handoff validator. No DOM,
// browser, microphone, HTTP request or patient operation is performed here.
const source=fs.readFileSync(new URL('../../server/static/flow-console-entry.mjs',import.meta.url),'utf8').replace(/^import .*;\r?\n/m,'').replace(/export /g,'');
const context=vm.createContext({URL,readDraftHandoff,writeDraftHandoff});vm.runInContext(source,context);
const parse=vm.runInContext('parseDraftEntry',context),open=vm.runInContext('openConsoleDraft',context);
const href='http://127.0.0.1:18033/flow-console-entry.html?sid=VS-ONE&actor_id=CREW-ONE&version=2';
const session=extra=>({id:'VS-ONE',actor_id:'CREW-ONE',version:2,state:'DRAFT',patient_id:null,...extra});
function fixture({record=session(),status=200}={}){
  const values=new Map(),calls=[],navigations=[];
  const storage={getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
  return {values,calls,navigations,storage,options:{href,storage,fetchImpl:async(url,options)=>{calls.push({url,options});return {ok:status===200,json:async()=>({session:record})};},navigate:url=>navigations.push(url)}};
}
test('entry validates one same-origin draft reference without clinical data in URL',()=>{
  assert.equal(parse(href).sid,'VS-ONE');
  for(const tail of ['&sid=VS-TWO','&server=https://outside.example','&text=patient','&version=3','&unknown=1'])assert.throws(()=>parse(href+tail));
  for(const invalid of [href.replace('version=2','version=0'),href.replace('VS-ONE','..%2Fpatients'),href.replace('CREW-ONE',''),href.replace('version=2','version=1e3')])assert.throws(()=>parse(invalid));
});
test('success reads the exact owner draft and stores only an existing draft handoff',async()=>{
  const h=fixture();assert.equal(await open(h.options),true);assert.equal(h.calls.length,1);assert.equal(h.calls[0].options.method,'GET');assert.match(h.calls[0].url,/VS-ONE\?actor_id=CREW-ONE$/);
  const result=readDraftHandoff(h.storage,'http://127.0.0.1:18033');assert.equal(result.status,'valid');assert.equal(result.payload.sessionId,'VS-ONE');assert.equal(result.payload.unappliedText,'');assert.equal(h.navigations[0],'/paramedic/?voice_handoff=1&flow=field');
});
test('a server-valid actor containing @ survives both entry and the real handoff validator',async()=>{
  const h=fixture({record:session({actor_id:'crew@one'})});h.options.href=href.replace('CREW-ONE','crew%40one');
  assert.equal(await open(h.options),true);assert.equal(readDraftHandoff(h.storage,'http://127.0.0.1:18033').payload.actorId,'crew@one');assert.equal(h.navigations.length,1);
});
for(const extra of [{id:'VS-OTHER'},{actor_id:'CREW-OTHER'},{state:'SUBMITTED'},{patient_id:'P-ONE'},{version:1},{version:2.5}])test('wrong session owner/state/version fails without local replacement '+JSON.stringify(extra),async()=>{
  const h=fixture({record:session(extra)});await assert.rejects(open(h.options));assert.equal(h.values.size,0);assert.equal(h.navigations.length,0);
});
test('server errors preserve the existing draft reference and never create a new one',async()=>{
  const h=fixture({status:503});await assert.rejects(open(h.options));assert.equal(h.calls.length,1);assert.equal(h.values.size,0);assert.equal(h.navigations.length,0);
});
test('unapplied handoff text is never overwritten by opening the console',async()=>{
  const h=fixture();writeDraftHandoff(h.storage,{serverOrigin:'http://127.0.0.1:18033',actorId:'CREW-ONE',sessionId:'VS-ONE',version:2,unappliedText:'synthetic unsent text',intent:'recommend'});const before=h.values.get(DRAFT_HANDOFF_KEY);
  await assert.rejects(open(h.options));assert.equal(h.calls.length,0);assert.equal(h.values.get(DRAFT_HANDOFF_KEY),before);assert.equal(h.navigations.length,0);
});
test('a local handoff changed while GET is delayed cannot be replaced',async()=>{
  const h=fixture();h.options.fetchImpl=async()=>{h.storage.setItem(DRAFT_HANDOFF_KEY,'changed by another frame');return {ok:true,json:async()=>({session:session()})};};
  await assert.rejects(open(h.options));assert.equal(h.storage.getItem(DRAFT_HANDOFF_KEY),'changed by another frame');assert.equal(h.navigations.length,0);
});
test('page exit invalidates a late GET before storage or navigation',async()=>{
  const h=fixture();h.options.isCurrent=()=>false;assert.equal(await open(h.options),false);assert.equal(h.values.size,0);assert.equal(h.navigations.length,0);
});
