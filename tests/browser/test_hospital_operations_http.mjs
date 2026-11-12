// Invoked by the dedicated Python fixture below; never point at a business DB.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import {HospitalOperations} from '../../hospital-ui/operations.mjs';
const [server,output]=process.argv.slice(2);
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(server)||!output)throw Error('Isolated loopback fixture and report path required');
const values=new Map(),storage={getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};
const requests=[];let loseExternal=true,loseCall=true,ids=0;
// Node 24's bundled fetch crashes on this host's short HTTP/1.0 keepalive
// connections. Use a fresh native HTTP connection; the production UI uses fetch.
const httpResponse=(url,opts={})=>new Promise((resolve,reject)=>{
 const headers={...(opts.headers||{}),Connection:'close'};if(opts.body)headers['Content-Length']=Buffer.byteLength(opts.body);
 const req=http.request(url,{method:opts.method||'GET',headers,agent:false},res=>{
  const chunks=[];res.on('data',chunk=>chunks.push(chunk));res.on('end',()=>{const body=Buffer.concat(chunks).toString('utf8');resolve({status:res.statusCode,ok:res.statusCode<400,headers:{get:name=>res.headers[name.toLowerCase()]},json:async()=>JSON.parse(body)});});res.on('error',reject);
 });req.on('error',reject);req.end(opts.body);
});
const request=async(ctx,path,opts={})=>{
 const response=await httpResponse(ctx.server+path,{...opts,headers:{'Content-Type':'application/json'}}),value=await response.json();
 requests.push({path,method:opts.method||'GET',status:response.status,body:opts.body?JSON.parse(opts.body):null});
 if(!response.ok){const error=Error(value.message||value.error);error.status=response.status;throw error;}
 if(path.endsWith('/external-arrivals')&&opts.method==='POST'&&loseExternal){loseExternal=false;throw Error('fixture loses successful registration response');}
 if(path.endsWith('/call-records')&&opts.method==='POST'&&loseCall){loseCall=false;throw Error('fixture loses successful call response');}
 return value;
};
const create=()=>{const c=new HospitalOperations({request,storage,makeId:()=>`http-${++ids}`});c.select({server,hospital:'OPS-H1',handler:'Synthetic desk',version:1});c.setPatient('OPS-P1');return c;};
const client=create();
const before=await request(client.context,'/api/patients/OPS-P1');
await client.loadCalls();assert.equal(client.calls.records.length,1);
const source=client.calls.records[0];await client.confirmCall(source,'confirm');await client.loadCalls();
assert.equal(client.calls.records[0].confirmation_status,'confirmed');
let after=await request(client.context,'/api/patients/OPS-P1');
assert.equal(after.destination_hospital_id,before.destination_hospital_id);assert.equal(after.state,before.state);assert.equal(after.inquiries.length,before.inquiries.length);
const now=Math.floor(Date.now()/1000);
await assert.rejects(client.createCall({call_started_at:now-5,call_ended_at:now-1,reported_outcome:'checking',counterparty_role:'paramedic',note:'<synthetic original>'}),e=>e.pending);
const reloaded=create();const duplicate=await reloaded.createCall({note:'must not replace original'});assert.equal(duplicate.value.duplicate,true);
const written=duplicate.value.record;
assert.equal(written.note,'<synthetic original>');
await reloaded.createCall({call_started_at:written.call_started_at,call_ended_at:written.call_ended_at,reported_outcome:'additional_info',counterparty_role:'paramedic',note:'synthetic correction',supersedes_id:written.id});
await reloaded.loadCalls();assert.equal(reloaded.calls.records.length,3);assert.ok(reloaded.calls.records.find(r=>r.id===written.id).superseded_by_id);
const started=await reloaded.startWork('request_review');assert.equal(started.value.activity.state,'OPEN');
await reloaded.loadWork();const open=reloaded.work.activities.find(a=>a.state==='OPEN');assert.ok(open);
const again=create();await again.loadWork();assert.ok(again.work.activities.some(a=>a.id===open.id&&a.state==='OPEN'));
await again.stopWork(again.work.activities.find(a=>a.id===open.id));await again.loadWork();assert.equal(again.work.activities.find(a=>a.id===open.id).state,'CLOSED');
await assert.rejects(again.registerExternal({resource_demands:{ed_bed:2},note:'synthetic walk-in'}),e=>e.pending);
const externalReload=create();const registered=await externalReload.registerExternal({resource_demands:{ed_bed:99},note:'must not replace original'});assert.equal(registered.value.duplicate,true);
await externalReload.loadExternal();assert.equal(externalReload.external.external_arrivals.length,1);
const external=externalReload.external.external_arrivals[0];assert.equal(external.applied_resource_demands.ed_bed,2);
await externalReload.dischargeExternal(external);await externalReload.loadExternal({state:'DISCHARGED'});assert.equal(externalReload.external.external_arrivals[0].state,'DISCHARGED');
const hospital=await request(externalReload.context,'/api/hospitals/OPS-H1');assert.equal(hospital.resources.find(r=>r.resource_type==='ed_bed').occupied,0);
const other=new HospitalOperations({request,storage,makeId:()=>`http-${++ids}`});other.select({server,hospital:'OPS-H2',handler:'Other synthetic desk',version:1});await other.loadCalls();assert.deepEqual(other.calls.records,[]);
await other.loadExternal();assert.deepEqual(other.external.external_arrivals,[]);
await client.loadProvenance();assert.equal(client.provenance.hospitals[0].hospital_id,'OPS-H1');
for(const path of ['/hospital/','/hospital/operations.mjs']){const r=await httpResponse(server+path);assert.equal(r.status,200);if(path.endsWith('mjs'))assert.ok(r.headers.get('content-type').includes('javascript'));}
after=await request(client.context,'/api/patients/OPS-P1');assert.equal(after.destination_hospital_id,before.destination_hospital_id);assert.equal(after.state,before.state);
fs.writeFileSync(output,JSON.stringify({passed:true,scope:'actual operations client over real loopback HTTP; synthetic dedicated DB only; no browser or phone',requests,checks:['record confirmation preserves clinical state','call lost-response exact retry','original-preserving correction','explicit work start-stop and reload recovery','external exact retry adds occupancy once','individual discharge releases only recorded occupancy','hospital scope isolation','provenance read','ES module MIME']},null,2));
console.log(JSON.stringify({passed:true,requests:requests.length}));
