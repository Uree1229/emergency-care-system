// 병원 이송 대화의 응답 유실 복구(H01) 회귀 검사. 실제 hospital-ui/index.html 스크립트를 VM과 최소 DOM 모형에서 실행한다.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';

const source=readFileSync(new URL('./hospital-ui/index.html',import.meta.url),'utf8').split('<script>')[1].split('</script>')[0].replaceAll("import('./","__dynamicImport('./");
const recoveryModule=()=>import('./hospital-ui/chat-recovery.mjs');
const settle=async()=>{for(let i=0;i<15;i++)await new Promise(resolve=>setImmediate(resolve));};
const memory=()=>{const data=new Map();return {data,get length(){return data.size;},key:i=>[...data.keys()][i]??null,getItem:k=>data.get(k)??null,setItem:(k,v)=>{data.set(k,String(v));},removeItem:k=>{data.delete(k);}};};
const pendingKeys=storage=>[...storage.data.keys()].filter(k=>k.startsWith('capstone.hospital.chat.'));
const patient=id=>({id,age:50,symptoms:`${id} 증상`,state:'IN_TRANSIT',transport_review:{},resource_demands:{},required_capabilities:[]});

// 서버의 send_message 규칙(conversations.py)을 따르는 합성 서버. 페이지를 새로 만들어도 유지된다.
class FakeServer {
  constructor(){this.rooms=new Map();this.posts=[];this.gets=[];this.next=[];this.incoming=['P1','P2'];}
  room(origin,hid,pid){const key=`${origin}|${hid}|${pid}`;if(!this.rooms.has(key))this.rooms.set(key,{canSend:true,session:7,state:'OPEN',messages:[]});return this.rooms.get(key);}
  send(room,body){
    const prior=room.messages.find(m=>m.client_message_id===body.client_message_id);
    if(prior){
      if(prior.body!==body.body||prior.sender_name!==body.sender_name||prior.hospital_session_id!==body.hospital_session_id)return [409,{error:'MESSAGE_CONFLICT',message:'같은 메시지 ID에 다른 내용이 있습니다'}];
      return [200,{message:prior,duplicate:true}];
    }
    if(room.state==='CLOSED')return [409,{error:'CONVERSATION_CLOSED',message:'종료된 이송 대화입니다'}];
    if(!room.canSend)return [403,{error:'NOT_PARTICIPANT',message:'현재 참여 중인 병원만 메시지를 보낼 수 있습니다'}];
    if(body.hospital_session_id!==room.session)return [409,{error:'STALE_SESSION',message:'참여 병원이 바뀌었습니다'}];
    const id=room.messages.length+1;
    const message={id:`message:${id}`,kind:'message',message_id:id,conversation_id:1,sender_role:'hospital',sender_name:body.sender_name,body:body.body,text:body.body,at:100,client_message_id:body.client_message_id,hospital_session_id:body.hospital_session_id,order_key:[1,1,id]};
    room.messages.push(message);
    return [200,{message,duplicate:false}];
  }
  async fetch(url,opts={}){
    const {pathname,origin,searchParams}=new URL(url);
    const reply=(status,body)=>({ok:status<400,status,json:async()=>body});
    const parts=pathname.split('/').map(decodeURIComponent);
    if(parts[4]==='patients'&&parts[6]==='messages'&&opts.method==='POST'){
      const body=JSON.parse(opts.body),raw=opts.body,room=this.room(origin,parts[3],parts[5]);
      this.posts.push({url,raw,body});
      const mode=this.next.shift();
      if(mode==='hold')return new Promise((resolve,reject)=>{this.held={resolve:()=>{const [s,b]=this.send(room,body);resolve(reply(s,b));},reject};});
      if(mode==='lost'){this.send(room,body);throw new TypeError('Failed to fetch');}
      if(mode==='offline')throw new TypeError('Failed to fetch');
      if(mode==='500')return reply(500,{message:'서버 오류'});
      const [status,payload]=this.send(room,body);
      if(mode==='broken-json')return {ok:true,status:200,json:async()=>{throw new SyntaxError('Unexpected end of JSON input');}};
      if(typeof mode==='function')return reply(status,mode(payload));
      return reply(status,payload);
    }
    if(parts[4]==='patients'&&parts[6]==='conversation'){
      if(this.offline)throw new TypeError('Failed to fetch');
      const room=this.room(origin,parts[3],parts[5]),after=Number(searchParams.get('message_cursor'));
      this.gets.push({url,message_cursor:after});
      return reply(200,{conversation:{state:room.state},active_hospital_session:room.canSend?{id:room.session}:null,can_send:room.canSend,items:room.messages.filter(m=>m.message_id>after),cursors:{event_id:0,message_id:room.messages.length},has_more:false,patient:patient(parts[5])});
    }
    if(pathname==='/api/hospitals')return reply(200,[{id:'H1',name:'가상 병원 1',source:'synthetic'},{id:'H2',name:'가상 병원 2',source:'synthetic'}]);
    if(pathname.endsWith('/inquiries'))return reply(200,{open:[],recent:[]});
    if(pathname.endsWith('/incoming'))return reply(200,{incoming:parts[3]==='H1'?this.incoming.map(patient):[],affected:[],planning:[]});
    return reply(200,{name:'가상 병원',source:'synthetic',resources:[],restrictions:[],capabilities:[],public_data:[]});
  }
}

class El {
  constructor(page,id){this.page=page;this.id=id;this.dataset={};this.value='';this.hidden=false;this.disabled=false;this.readOnly=false;this.className='';this.textContent='';this.style={};this.listeners={};this.innerHTML='';this.scrollHeight=0;this.scrollTop=0;this.clientHeight=0;}
  addEventListener(type,fn){(this.listeners[type]??=[]).push(fn);}
  focus(){}
  setSelectionRange(){}
  querySelector(){return null;}
  querySelectorAll(){return [];}
  closest(){return null;}
  contains(){return false;}
  matches(){return false;}
}

class Page {
  constructor(server,storage,{search='?hid=H1&server=http://one',handler='담당A'}={}){
    this.server=server;this.storage=storage;this.els=new Map();
    this.document={activeElement:null,title:'',getElementById:id=>this.el(id),querySelector:()=>this.el('any'),querySelectorAll:()=>[]};
    this.context=vm.createContext({document:this.document,location:{search,pathname:'/hospital/',origin:'http://one'},localStorage:storage,URL,URLSearchParams,console,crypto:{randomUUID:()=>`id-${++Page.ids}`},
      fetch:(url,opts)=>server.fetch(url,opts),setInterval:()=>0,clearInterval(){},setTimeout:()=>0,clearTimeout(){},
      __dynamicImport:path=>path.includes('chat-recovery')?recoveryModule():new Promise(()=>{})});
    vm.runInContext(source,this.context);
    this.el('handler').value=handler;
  }
  el(id){if(!this.els.has(id))this.els.set(id,new El(this,id));return this.els.get(id);}
  eval(code){return vm.runInContext(code,this.context);}
  fire(id,type,event){for(const fn of this.el(id).listeners[type]||[])fn(event);}
  async poll(){await this.eval('poll()');await settle();}
  async openTab(){this.eval("setTab('incoming')");await settle();}
  async choose(pid){this.fire('incomingList','click',{target:{closest:s=>s==='[data-patient]'?{dataset:{patient:pid}}:null}});await settle();}
  write(text){this.el('chatBody').value=text;}
  submit(){this.fire('chatForm','submit',{preventDefault(){}});}
  async send(text){if(text!==undefined)this.write(text);this.submit();await settle();}
  get status(){return this.el('chatStatus').textContent;}
  get notice(){return this.el('notice').textContent;}
  get label(){return this.el('chatSend').textContent;}
}
Page.ids=0;

async function open({server=new FakeServer(),storage=memory(),pid='P1',handler='담당A',search}={}){
  const page=new Page(server,storage,{handler,search});
  await settle();await page.poll();await page.openTab();
  if(pid)await page.choose(pid);
  return {page,server,storage};
}

test('저장소: 대화 범위마다 한 건만 보관하고 범위가 다르면 돌려주지 않는다',async()=>{
  const {ChatRecoveryStore,newChatRequest}=await recoveryModule();
  const storage=memory(),store=new ChatRecoveryStore({storage,now:()=>1000});
  const scope={server:'http://one',hospital:'H1',patient:'P1'},request=newChatRequest({body:'본문',session:7,senderName:'담당A',makeId:()=>'id-1'});
  assert.deepEqual(request,{body:'본문',client_message_id:'id-1',hospital_session_id:7,sender_name:'담당A'});
  store.save(scope,request);
  assert.deepEqual(store.load(scope).request,request);
  for(const other of [{...scope,server:'http://two'},{...scope,hospital:'H2'},{...scope,patient:'P2'}])assert.equal(store.load(other),null);
  assert.deepEqual(store.list('http://one','H1').map(r=>r.patient),['P1']);
  assert.deepEqual(store.list('http://one','H2'),[]);
  assert.equal(store.clear(scope,'다른-id'),false);assert.notEqual(store.load(scope),null,'다른 요청 ID로는 지우지 않음');
  assert.equal(store.clear(scope,'id-1'),true);assert.equal(store.load(scope),null);assert.equal(storage.data.size,0);
});

test('저장소: 쓰기 실패·확인 실패·저장소 없음은 오류이고 손상·만료 기록은 정리한다',async()=>{
  const {ChatRecoveryStore,ChatSaveError,PENDING_TTL_MS}=await recoveryModule();
  const scope={server:'http://one',hospital:'H1',patient:'P1'},request={body:'본문',client_message_id:'id-1',hospital_session_id:7,sender_name:'담당A'};
  const quota={...memory(),setItem(){throw new Error('QuotaExceeded');}};
  assert.throws(()=>new ChatRecoveryStore({storage:quota}).save(scope,request),ChatSaveError);
  const silent={...memory(),setItem(){},getItem:()=>null};
  assert.throws(()=>new ChatRecoveryStore({storage:silent}).save(scope,request),ChatSaveError);
  assert.throws(()=>new ChatRecoveryStore({storage:null}).save(scope,request),ChatSaveError);
  assert.equal(new ChatRecoveryStore({storage:null}).load(scope),null);assert.deepEqual(new ChatRecoveryStore({storage:null}).list('a','b'),[]);
  const storage=memory();let time=1000;const store=new ChatRecoveryStore({storage,now:()=>time});
  store.save(scope,request);
  storage.setItem(store.key(scope),'{깨진 값');assert.equal(store.load(scope),null);assert.equal(storage.data.size,0);
  store.save(scope,request);
  storage.setItem(store.key(scope),JSON.stringify({server:'http://two',hospital:'H1',patient:'P1',savedAt:1000,request}));assert.equal(store.load(scope),null,'키와 내용의 범위가 다르면 사용하지 않음');assert.equal(storage.data.size,0);
  store.save(scope,request);
  time+=PENDING_TTL_MS-1;assert.notEqual(store.load(scope),null);
  time+=2;assert.equal(store.load(scope),null);assert.equal(storage.data.size,0);
});

test('POST 전에 원요청을 저장하고 성공 확인 후 그 요청만 정리한다',async()=>{
  const {page,server,storage}=await open();
  server.next.push('hold');
  page.write('  CT 준비합니다  ');page.submit();await settle();
  assert.equal(server.posts.length,1);
  const stored=JSON.parse(storage.data.get(pendingKeys(storage)[0]));
  assert.equal(stored.server,'http://one');assert.equal(stored.hospital,'H1');assert.equal(stored.patient,'P1');
  assert.deepEqual(stored.request,server.posts[0].body,'POST 시점에 같은 요청이 이미 저장됨');
  assert.equal(stored.request.body,'CT 준비합니다');assert.equal(stored.request.hospital_session_id,7);assert.equal(stored.request.sender_name,'담당A');
  assert.match(server.posts[0].url,/^http:\/\/one\/api\/hospitals\/H1\/patients\/P1\/messages$/);
  server.held.resolve();await settle();
  assert.deepEqual(pendingKeys(storage),[]);assert.equal(page.el('chatBody').value,'');assert.match(page.notice,/메시지를 전송했습니다/);
  assert.match(page.el('chatLog').innerHTML,/CT 준비합니다/);
});

test('저장에 실패하면 POST를 시작하지 않고 입력을 남긴다',async()=>{
  const storage=memory();
  const {page,server}=await open({storage:{...storage,get length(){return 0;},key:()=>null,setItem(){throw new Error('QuotaExceeded');},getItem:()=>null,removeItem(){}}});
  await page.send('저장 안 될 메시지');
  assert.equal(server.posts.length,0);assert.equal(page.el('chatBody').value,'저장 안 될 메시지');assert.match(page.notice,/저장하지 못해/);assert.equal(page.el('notice').className,'err');
  assert.equal(page.label,'메시지 보내기');
});

test('응답 유실 → 새로고침 → 복귀 후 명시 재시도는 같은 요청을 보내고 duplicate로 확인한다',async()=>{
  const {page,server,storage}=await open();
  server.next.push('lost');await page.send('응답이 사라질 메시지');
  assert.equal(server.posts.length,1);assert.equal(server.room('http://one','H1','P1').messages.length,1,'서버에는 저장됨');
  assert.equal(page.label,'같은 메시지 재시도');assert.match(page.notice,/전송 결과를 확인하지 못했습니다/);assert.equal(pendingKeys(storage).length,1);
  const reloaded=new Page(server,storage);await settle();await reloaded.poll();await reloaded.openTab();
  assert.equal(server.posts.length,1,'새로고침만으로 자동 재전송하지 않음');
  await reloaded.choose('P1');
  assert.equal(reloaded.el('chatBody').value,'응답이 사라질 메시지');assert.equal(reloaded.el('chatBody').readOnly,true);assert.equal(reloaded.label,'같은 메시지 재시도');assert.equal(reloaded.el('chatSend').disabled,false);
  assert.equal(server.posts.length,1);
  await reloaded.send();
  assert.equal(server.posts.length,2);assert.equal(server.posts[1].raw,server.posts[0].raw,'본문·ID·구간·이름이 바이트까지 동일');assert.equal(server.posts[1].url,server.posts[0].url);
  assert.match(reloaded.notice,/이미 저장된 메시지의 전송을 확인했습니다/);assert.deepEqual(pendingKeys(storage),[]);assert.equal(reloaded.label,'메시지 보내기');assert.equal(reloaded.el('chatBody').value,'');
  assert.equal(server.room('http://one','H1','P1').messages.length,1,'중복 저장 없음');
});

test('담당자 표시명을 바꿔도 저장된 요청을 고치지 않고, 빈 이름이어도 재시도할 수 있다',async()=>{
  const {page,server,storage}=await open();
  server.next.push('lost');await page.send('이름 고정 메시지');
  const reloaded=new Page(server,storage,{handler:'담당B'});await settle();await reloaded.poll();await reloaded.openTab();await reloaded.choose('P1');
  assert.match(reloaded.status,/담당A/);
  await reloaded.send();
  assert.equal(server.posts[1].body.sender_name,'담당A');assert.equal(server.posts[1].raw,server.posts[0].raw);assert.equal(server.room('http://one','H1','P1').messages.length,1);
  const {page:second,server:s2,storage:st2}=await open();
  s2.next.push('offline');await second.send('이름 없이 재시도');
  second.el('handler').value='';
  await second.send();
  assert.equal(s2.posts.length,2);assert.equal(s2.posts[1].raw,s2.posts[0].raw);assert.deepEqual(pendingKeys(st2),[]);
});

test('다른 환자·병원·서버에서는 보관 요청을 보여 주지도 보내지도 않는다',async()=>{
  const {page,server,storage}=await open();
  server.next.push('offline');await page.send('P1 전용 메시지');
  await page.choose('P2');
  assert.equal(page.el('chatBody').value,'');assert.equal(page.label,'메시지 보내기');assert.equal(server.posts.length,1);
  await page.send('P2 새 메시지');
  assert.equal(server.posts.length,2);assert.match(server.posts[1].url,/patients\/P2\/messages$/);assert.notEqual(server.posts[1].body.client_message_id,server.posts[0].body.client_message_id);
  assert.equal(pendingKeys(storage).length,1,'P2 성공 후에도 P1 요청은 유지');
  await page.choose('P1');
  assert.equal(page.el('chatBody').value,'P1 전용 메시지');assert.equal(page.label,'같은 메시지 재시도');
  const otherHospital=new Page(server,storage,{search:'?hid=H2&server=http://one'});await settle();await otherHospital.poll();await otherHospital.openTab();await otherHospital.choose('P1');
  assert.equal(otherHospital.el('chatBody').value,'');assert.equal(otherHospital.label,'메시지 보내기');
  const otherServer=new Page(server,storage,{search:'?hid=H1&server=http://two'});await settle();await otherServer.poll();await otherServer.openTab();await otherServer.choose('P1');
  assert.equal(otherServer.el('chatBody').value,'');assert.equal(otherServer.label,'메시지 보내기');
  assert.equal(server.posts.length,2,'범위 이동으로 전송 없음');
});

test('참여가 끝나면 저장된 메시지의 재전송은 duplicate로 확인하고 새 메시지는 막는다',async()=>{
  const {page,server,storage}=await open();
  server.next.push('lost');await page.send('종료 직전 메시지');
  const room=server.room('http://one','H1','P1');room.canSend=false;room.state='CLOSED';
  const reloaded=new Page(server,storage);await settle();await reloaded.poll();await reloaded.openTab();await reloaded.choose('P1');
  assert.equal(reloaded.el('chatSend').disabled,false,'결과 미확인 요청은 종료 후에도 재시도 가능');
  await reloaded.send();
  assert.equal(server.posts.length,2);assert.equal(server.posts[1].raw,server.posts[0].raw);assert.match(reloaded.notice,/이미 저장된 메시지/);assert.deepEqual(pendingKeys(storage),[]);
  assert.equal(reloaded.el('chatSend').disabled,true);assert.equal(reloaded.el('chatBody').readOnly,true);
  await reloaded.send('종료 뒤 새 메시지');
  assert.equal(server.posts.length,2,'새 메시지 POST 없음');
});

test('참여 종료 전 저장되지 않은 요청의 재시도는 거절을 그대로 알리고 자동 우회하지 않는다',async()=>{
  const {page,server,storage}=await open();
  server.next.push('offline');await page.send('저장 안 된 메시지');
  const room=server.room('http://one','H1','P1');room.canSend=false;room.state='CLOSED';
  await page.send();
  assert.equal(server.posts.length,2);assert.match(page.notice,/종료된 이송 대화/);assert.equal(page.el('notice').className,'err');
  assert.deepEqual(pendingKeys(storage),[]);assert.equal(page.el('chatSend').disabled,true);assert.equal(page.el('chatBody').value,'저장 안 된 메시지');
  await settle();assert.equal(server.posts.length,2);
});

test('더블클릭은 POST 한 건만 만든다',async()=>{
  const {page,server}=await open();
  server.next.push('hold');
  page.write('한 번만');page.submit();page.submit();await settle();page.submit();await settle();
  assert.equal(server.posts.length,1);
  server.held.resolve();await settle();
  assert.equal(server.posts.length,1);assert.equal(server.room('http://one','H1','P1').messages.length,1);
});

for(const [name,arrange,pattern] of [
  ['STALE_SESSION',room=>{room.session=8;},/참여 병원이 바뀌었습니다/],
  ['NOT_PARTICIPANT',room=>{room.canSend=false;},/현재 참여 중인 병원만/],
  ['CONVERSATION_CLOSED',room=>{room.state='CLOSED';room.canSend=false;},/종료된 이송 대화/],
]){
  test(`재시도가 ${name}로 거절되면 재생하지 않고 내용을 남긴 채 최신 상태를 읽는다`,async()=>{
    const {page,server,storage}=await open();
    server.next.push('offline');await page.send('거절될 메시지');
    arrange(server.room('http://one','H1','P1'));
    const reads=server.gets.length;await page.send();
    assert.equal(server.posts.length,2);assert.equal(server.posts[1].raw,server.posts[0].raw);assert.ok(server.gets.length>reads,'최신 대화 재조회');
    assert.match(page.notice,pattern);assert.equal(page.el('notice').className,'err');assert.deepEqual(pendingKeys(storage),[]);assert.equal(page.label,'메시지 보내기');assert.equal(page.el('chatBody').value,'거절될 메시지');
    await settle();await page.poll();assert.equal(server.posts.length,2,'자동 재전송 없음');
  });
}

test('MESSAGE_CONFLICT는 같은 ID로 다시 보내지 않고 다음 전송은 새 ID를 쓴다',async()=>{
  const {page,server,storage}=await open();
  server.next.push('offline');await page.send('충돌할 메시지');
  const id=server.posts[0].body.client_message_id;
  server.room('http://one','H1','P1').messages.push({id:'message:1',kind:'message',message_id:1,sender_role:'hospital',sender_name:'담당A',body:'같은 ID의 다른 내용',text:'같은 ID의 다른 내용',at:1,client_message_id:id,hospital_session_id:7,order_key:[1,1,1]});
  await page.send();
  assert.match(page.notice,/같은 메시지 ID/);assert.deepEqual(pendingKeys(storage),[]);
  await page.send('수정해서 새로 보냄');
  assert.equal(server.posts.length,3);assert.notEqual(server.posts[2].body.client_message_id,id);
});

test('네트워크 오류와 5xx는 원래 내용으로 유지하고 입력을 고칠 수 없게 둔다',async()=>{
  for(const mode of ['offline','500']){
    const {page,server,storage}=await open();
    server.next.push(mode);await page.send('결과 미확인');
    assert.equal(pendingKeys(storage).length,1,mode);assert.equal(page.label,'같은 메시지 재시도');assert.equal(page.el('chatBody').readOnly,true);assert.equal(page.el('chatBody').value,'결과 미확인');
    assert.equal(page.el('notice').className,'err');
    await page.poll();await settle();assert.equal(server.posts.length,1,'자동 재전송 없음');
    page.write('바꾸려는 내용');await page.send();
    assert.equal(server.posts[1].raw,server.posts[0].raw,'입력창 값이 아니라 보관 요청을 전송');assert.equal(server.room('http://one','H1','P1').messages[0].body,'결과 미확인');
  }
});

test('대화 조회가 실패해도 결과 미확인 요청의 안내와 재시도 버튼을 유지한다',async()=>{
  const {page,server}=await open();
  server.next.push('offline');await page.send('오프라인 메시지');
  server.offline=true;await page.eval('pollChat()');await settle();
  assert.match(page.status,/전송 결과를 확인하지 못했습니다/);assert.equal(page.el('chatSend').disabled,false);assert.equal(page.label,'같은 메시지 재시도');
});

test('새 메시지는 전송 직전 조회에서 참여 구간이 바뀌면 저장도 전송도 하지 않는다',async()=>{
  const {page,server,storage}=await open();
  server.room('http://one','H1','P1').session=8;
  await page.send('구간이 바뀐 메시지');
  assert.equal(server.posts.length,0);assert.deepEqual(pendingKeys(storage),[]);assert.match(page.notice,/참여 상태가 바뀌었습니다/);assert.equal(page.el('chatBody').value,'구간이 바뀐 메시지');
});

test('늦은 응답은 다른 선택의 화면을 바꾸지 않지만 보관 요청은 정리한다',async()=>{
  const {page,server,storage}=await open();
  server.next.push('hold');page.write('늦게 끝날 메시지');page.submit();await settle();
  page.eval("selectedPatientId='P2';selectedPatient=null;resetChat();");await page.eval('pollChat()');await settle();
  page.eval("notice('P2 안내',false)");page.write('P2 작성 중');
  server.held.resolve();await settle();
  assert.equal(page.notice,'P2 안내');assert.equal(page.el('chatBody').value,'P2 작성 중');assert.doesNotMatch(page.el('chatLog').innerHTML,/늦게 끝날 메시지/);
  assert.deepEqual(pendingKeys(storage),[],'성공 확인된 요청은 범위와 무관하게 정리');
});

test('늦은 실패는 다른 선택의 알림을 덮지 않고 원래 범위의 요청을 유지한다',async()=>{
  const {page,server,storage}=await open();
  server.next.push('hold');page.write('늦게 실패할 메시지');page.submit();await settle();
  page.eval("selectedPatientId='P2';selectedPatient=null;resetChat();");await page.eval('pollChat()');await settle();
  page.eval("notice('P2 안내',false)");
  server.held.reject(new TypeError('Failed to fetch'));await settle();
  assert.equal(page.notice,'P2 안내');assert.equal(page.label,'메시지 보내기');assert.equal(pendingKeys(storage).length,1);
  await page.choose('P1');assert.equal(page.el('chatBody').value,'늦게 실패할 메시지');assert.equal(page.label,'같은 메시지 재시도');
});

test('다른 환자를 고르면 이전 환자의 전송 안내를 남기지 않는다',async()=>{
  const {page}=await open();
  await page.send('P1 메시지');assert.match(page.notice,/메시지를 전송했습니다/);
  await page.choose('P2');assert.equal(page.notice,'');
});

test('POST 성공만으로 조회 커서를 앞당기지 않는다',async()=>{
  const {page,server}=await open();
  const before=page.eval('chat.messageCursor');
  server.next.push('hold');page.write('커서 확인');page.submit();await settle();
  server.held.resolve();await settle();
  assert.equal(page.eval('chat.messageCursor'),before,'POST 응답으로 커서 변경 없음');
  assert.match(page.el('chatLog').innerHTML,/커서 확인/);
  await page.eval('pollChat()');await settle();
  assert.equal(server.gets.at(-1).message_cursor,before,'다음 조회는 이전 커서로 시작');
  assert.equal(page.eval('chat.messageCursor'),1);
  assert.equal((page.el('chatLog').innerHTML.match(/커서 확인/g)||[]).length,1,'ID 기준으로 중복 표시 없음');
});

test('사람이 보관 요청을 삭제할 수 있고 그 뒤 전송은 새 ID를 쓴다',async()=>{
  const {page,server,storage}=await open();
  server.next.push('offline');await page.send('삭제할 요청');
  assert.equal(page.el('chatDiscard').hidden,false);
  page.fire('chatDiscard','click',{});await settle();
  assert.deepEqual(pendingKeys(storage),[]);assert.equal(page.label,'메시지 보내기');assert.equal(page.el('chatBody').value,'삭제할 요청','내용은 입력창에 남김');assert.equal(page.el('chatBody').readOnly,false);assert.equal(page.el('chatDiscard').hidden,true);
  assert.match(page.notice,/이미 저장됐을 수 있/);assert.equal(server.posts.length,1);
  await page.send();
  assert.equal(server.posts.length,2);assert.notEqual(server.posts[1].body.client_message_id,server.posts[0].body.client_message_id);
});

test('새로고침 뒤에도 미확인 요청을 찾을 수 있고 목록에 없는 환자도 복귀할 수 있다',async()=>{
  const {page,server,storage}=await open();
  server.next.push('lost');await page.send('목록 밖 환자 메시지');
  server.incoming=['P2'];const room=server.room('http://one','H1','P1');room.canSend=false;room.state='CLOSED';
  const reloaded=new Page(server,storage);await settle();await reloaded.poll();
  assert.equal(reloaded.el('chatPendingBadge').hidden,false);assert.equal(String(reloaded.el('chatPendingBadge').textContent),'1');
  await reloaded.openTab();
  assert.match(reloaded.el('incomingList').innerHTML,/data-patient="P1"/);assert.match(reloaded.el('incomingList').innerHTML,/전송 결과 미확인/);
  assert.equal(server.posts.length,1);
  await reloaded.choose('P1');assert.equal(reloaded.el('chatBody').value,'목록 밖 환자 메시지');
  await reloaded.send();
  assert.match(reloaded.notice,/이미 저장된 메시지/);assert.equal(reloaded.el('chatPendingBadge').hidden,true);assert.doesNotMatch(reloaded.el('incomingList').innerHTML,/data-patient="P1"/);
});

test('만료된 요청은 보여 주지 않고 자동으로 보내지도 않는다',async()=>{
  const {PENDING_TTL_MS}=await recoveryModule();
  const {page,server,storage}=await open();
  server.next.push('offline');await page.send('오래된 요청');
  const key=pendingKeys(storage)[0],record=JSON.parse(storage.data.get(key));
  record.savedAt-=PENDING_TTL_MS+1000;storage.data.set(key,JSON.stringify(record));
  const reloaded=new Page(server,storage);await settle();await reloaded.poll();await reloaded.openTab();await reloaded.choose('P1');
  assert.equal(reloaded.el('chatBody').value,'');assert.equal(reloaded.label,'메시지 보내기');assert.deepEqual(pendingKeys(storage),[]);assert.equal(server.posts.length,1);
});

// 오래 열린 페이지의 메모리 복사본도 POST 직전에 영속 보관본과 대조해야 한다.
for(const change of ['expired','deleted','replaced','same-id-edited']){
  test(`이미 복원한 요청이 ${change} 상태가 되면 재전송하지 않고 현재 보관본을 표시한다`,async()=>{
    const {PENDING_TTL_MS}=await recoveryModule();
    const {page,server,storage}=await open();
    server.next.push('offline');await page.send('과거 원요청');
    const key=pendingKeys(storage)[0],record=JSON.parse(storage.getItem(key));
    if(change==='expired'){
      page.context.expiredNow=record.savedAt+PENDING_TTL_MS+1;
      page.eval('chatRecovery.store.now=()=>expiredNow');
      await page.openTab();
      assert.deepEqual(pendingKeys(storage),[],'화면 배지 갱신이 먼저 만료본을 지운 경우');
    }else if(change==='deleted')storage.removeItem(key);
    else{
      record.request.body='다른 창의 보관 요청';
      if(change==='replaced')record.request.client_message_id='other-window-id';
      storage.setItem(key,JSON.stringify(record));
    }
    assert.equal(page.eval('chatPending.body'),'과거 원요청','메모리에는 이전 내용이 남아 있음');
    await page.send();
    assert.equal(server.posts.length,1,'없거나 달라진 원요청을 POST하지 않음');
    assert.equal(server.room('http://one','H1','P1').messages.length,0);
    assert.match(page.notice,/만료|삭제|변경/);assert.equal(page.el('notice').className,'err');
    if(change==='replaced'||change==='same-id-edited'){
      assert.equal(page.el('chatBody').value,'다른 창의 보관 요청');assert.equal(page.label,'같은 메시지 재시도');
      await page.send();
      assert.equal(server.posts.length,2,'현재 보관본도 다음 명시 동작에서만 전송');
      assert.equal(server.posts[1].body.body,'다른 창의 보관 요청');
    }else{
      assert.equal(page.eval('chatPending'),null);assert.equal(page.label,'메시지 보내기');
      assert.equal(page.el('chatBody').value,'과거 원요청','자동으로 새 요청을 만들지 않고 원문은 확인용으로 남김');
    }
  });
}

test('재시도 전 대화 조회를 기다리는 동안 만료된 요청도 POST하지 않는다',async()=>{
  const {PENDING_TTL_MS}=await recoveryModule();
  const {page,server,storage}=await open();
  server.next.push('offline');await page.send('조회 중 만료');
  const record=JSON.parse(storage.getItem(pendingKeys(storage)[0]));
  const fetch=server.fetch.bind(server);
  server.fetch=async(url,opts)=>{
    const result=await fetch(url,opts);
    if(new URL(url).pathname.endsWith('/conversation')){
      page.context.expiredNow=record.savedAt+PENDING_TTL_MS+1;
      page.eval('chatRecovery.store.now=()=>expiredNow');
    }
    return result;
  };
  await page.send();assert.equal(server.posts.length,1);assert.match(page.notice,/만료|변경/);
});

for(const [label,mode] of [
  ['잘린 JSON','broken-json'],
  ['빈 응답',()=>({})],
  ['다른 메시지 ID',p=>({...p,message:{...p.message,client_message_id:'unrelated-id'}})],
  ['다른 본문',p=>({...p,message:{...p.message,body:'다른 메시지',text:'다른 메시지'}})],
  ['다른 참여 구간',p=>({...p,message:{...p.message,hospital_session_id:99}})],
  ['다른 표시 이름',p=>({...p,message:{...p.message,sender_name:'다른 담당자'}})],
  ['환자 역할',p=>({...p,message:{...p.message,sender_role:'patient'}})],
  ['잘못된 메시지 형식',p=>({...p,message:{...p.message,id:null,message_id:null}})],
]){
  test(`200 응답이 ${label}이면 원요청을 유지하고 새로고침 뒤 같은 ID로 확인한다`,async()=>{
    const {page,server,storage}=await open();
    server.next.push(mode);await page.send('응답 확인이 필요한 요청');
    const raw=server.posts[0].raw;
    assert.equal(server.room('http://one','H1','P1').messages.length,1,'실제 저장 뒤 응답만 손상');
    assert.equal(pendingKeys(storage).length,1,'전송 결과를 확인하기 전에는 원요청을 지우지 않음');
    assert.equal(JSON.stringify(JSON.parse(storage.getItem(pendingKeys(storage)[0])).request),raw);
    assert.match(page.notice,/전송 결과를 확인하지 못/);assert.equal(page.label,'같은 메시지 재시도');
    const reloaded=new Page(server,storage);await settle();await reloaded.poll();await reloaded.openTab();await reloaded.choose('P1');
    await reloaded.send();
    assert.equal(server.posts[1].raw,raw);assert.match(reloaded.notice,/이미 저장된 메시지/);
    assert.equal(server.room('http://one','H1','P1').messages.length,1);assert.deepEqual(pendingKeys(storage),[]);
  });
}

test('다른 환자를 선택한 뒤 도착한 손상 성공 응답도 원래 범위의 요청을 지우지 않는다',async()=>{
  const {page,server,storage}=await open();
  const fetch=server.fetch.bind(server);
  server.fetch=async(url,opts={})=>{
    const result=await fetch(url,opts);
    return opts.method==='POST'?{...result,json:async()=>({})}:result;
  };
  server.next.push('hold');page.write('늦은 손상 응답');page.submit();await settle();
  page.eval("selectedPatientId='P2';selectedPatient=null;resetChat();");await page.eval('pollChat()');await settle();
  page.eval("notice('P2 안내',false)");page.write('P2 초안');
  server.held.resolve();await settle();
  assert.equal(page.notice,'P2 안내');assert.equal(page.el('chatBody').value,'P2 초안');assert.equal(pendingKeys(storage).length,1);
  await page.choose('P1');assert.equal(page.label,'같은 메시지 재시도');assert.equal(page.el('chatBody').value,'늦은 손상 응답');
});

for(const failure of ['throw','silent']){
  test(`보관 요청 삭제가 ${failure} 방식으로 실패하면 성공으로 표시하지 않고 재시도 상태를 유지한다`,async()=>{
    const {page,server,storage}=await open();
    server.next.push('offline');await page.send('지워지지 않은 요청');
    const remove=storage.removeItem;
    storage.removeItem=()=>{if(failure==='throw')throw new Error('SecurityError');};
    page.fire('chatDiscard','click',{});await settle();
    assert.equal(pendingKeys(storage).length,1);assert.equal(page.label,'같은 메시지 재시도');assert.equal(page.el('chatBody').readOnly,true);
    assert.equal(page.el('chatDiscard').hidden,false);assert.doesNotMatch(page.notice,/요청을 삭제했습니다/);
    assert.match(page.notice,/삭제하지 못|정리하지 못/);assert.equal(page.el('notice').className,'err');assert.equal(server.posts.length,1);
    storage.removeItem=remove;page.fire('chatDiscard','click',{});await settle();
    assert.deepEqual(pendingKeys(storage),[]);assert.equal(page.label,'메시지 보내기');
  });
}

test('전송을 확인해도 보관본 삭제가 실패하면 서버 결과와 정리 실패를 함께 알린다',async()=>{
  const {page,server,storage}=await open();
  const remove=storage.removeItem;storage.removeItem=()=>{throw new Error('SecurityError');};
  await page.send('서버 저장 후 정리 실패');
  const raw=server.posts[0].raw;
  assert.equal(server.room('http://one','H1','P1').messages.length,1);assert.equal(pendingKeys(storage).length,1);
  assert.match(page.el('chatLog').innerHTML,/서버 저장 후 정리 실패/);assert.match(page.notice,/보관.*정리.*못|삭제.*못/);
  assert.equal(page.el('notice').className,'err');assert.equal(page.label,'같은 메시지 재시도');
  storage.removeItem=remove;await page.send();
  assert.equal(server.posts[1].raw,raw);assert.match(page.notice,/이미 저장된 메시지/);assert.deepEqual(pendingKeys(storage),[]);
});

test('4xx로 거절되어도 보관본 정리가 실패하면 오류와 남은 보관 요청을 숨기지 않는다',async()=>{
  const {page,server,storage}=await open();
  server.next.push('offline');await page.send('거절 후 정리 실패');
  server.room('http://one','H1','P1').session=8;
  const remove=storage.removeItem;storage.removeItem=()=>{throw new Error('SecurityError');};
  await page.send();
  assert.equal(server.posts.length,2);assert.match(page.notice,/참여 병원이 바뀌었습니다/);assert.match(page.notice,/보관.*정리.*못|삭제.*못/);
  assert.equal(pendingKeys(storage).length,1);assert.equal(page.label,'같은 메시지 재시도');assert.equal(page.el('chatDiscard').hidden,false);
  await page.poll();assert.equal(server.posts.length,2,'자동 재전송 없음');
  storage.removeItem=remove;page.fire('chatDiscard','click',{});await settle();assert.deepEqual(pendingKeys(storage),[]);
});
