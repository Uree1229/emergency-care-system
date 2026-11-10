// 병원 메인의 임상변경 검토 발견(H02) 회귀 검사. 모듈 단위 검사와 실제 hospital-ui/index.html 스크립트 실행을 함께 한다.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {readReviewList,changeLines,reviewLink,reviewPanelMarkup,LIST_LIMIT} from './hospital-ui/review-discovery.mjs';

const source=readFileSync(new URL('./hospital-ui/index.html',import.meta.url),'utf8').split('<script>')[1].split('</script>')[0].replaceAll("import('./","__dynamicImport('./");
const settle=async()=>{for(let i=0;i<15;i++)await new Promise(resolve=>setImmediate(resolve));};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const review=(id,extra={})=>({id:`ER-${id}`,patient_id:`P${id}`,hospital_id:'H1',state:'PENDING',version:1,blockers:[],can_decide:true,monitoring_enabled:true,current_patient_version:3,decision_token:`token-${id}`,
  proposal:{current:{required_capabilities:['정형외과'],resource_demands:{ed_bed:1}},proposed:{required_capabilities:['정형외과','신경외과'],resource_demands:{ed_bed:1,special_team:1}},observation_changes:{consciousness:{previous:'alert',proposed:'impaired'}},reductions:[],unsupported_solver_resources:[]},...extra});

test('목록 검증: 대기 건만 고르고 다른 병원·형식 오류가 섞인 응답은 통째로 쓰지 않는다',()=>{
  const ok=readReviewList({reviews:[review(1),review(2,{state:'REJECTED'}),review(3,{state:'APPLIED_KEEP'})]},'H1');
  assert.deepEqual(ok.pending.map(r=>r.id),['ER-1']);assert.equal(ok.truncated,false);
  assert.match(readReviewList({reviews:[review(1),review(2,{hospital_id:'H2'})]},'H1').error,/다른 병원/);
  for(const bad of [null,{},{reviews:{}},{reviews:[{id:'x'}]},{reviews:[review(1,{blockers:'X'})]},{reviews:[review(1,{can_decide:'yes'})]},{reviews:[review(1,{proposal:null})]}])
    assert.match(readReviewList(bad,'H1').error,/형식/);
  const full=readReviewList({reviews:Array.from({length:LIST_LIMIT},(_,i)=>review(i,{state:'REJECTED'}))},'H1');
  assert.equal(full.truncated,true);assert.deepEqual(full.pending,[]);
});

test('변경 전후: 진료·자원·관찰 변화와 감소를 문장으로 보여 준다',()=>{
  assert.deepEqual(changeLines(review(1).proposal),['필요 진료: 정형외과 → 정형외과, 신경외과','필요 자원: 응급병상 1 → 응급병상 1 · 전문팀 1','의식: 명료 → 저하']);
  assert.deepEqual(changeLines({current:{},proposed:{},reductions:[],observation_changes:{breathing:{previous:null,proposed:'abnormal'},consciousness:{previous:'new-code',proposed:'unknown'}}}),['호흡: 미확인 → 이상','의식: new-code → 미확인']);
  const reduced=changeLines({...review(1).proposal,proposed:review(1).proposal.current,observation_changes:{},reductions:[{field:'priority'}]});
  assert.deepEqual(reduced,['감소하거나 빠지는 치료조건이 있습니다']);
  assert.deepEqual(changeLines({current:{},proposed:{},observation_changes:{},reductions:[]}),['치료조건 변화 없음 · 관찰 기록만 바뀜']);
});

test('검토 화면 링크는 서버 주소에 pid·hid만 담고 구급대 actor_id를 넣지 않는다',()=>{
  const link=new URL(reviewLink('http://127.0.0.1:18024','H1','P 1'));
  assert.equal(link.origin,'http://127.0.0.1:18024');assert.equal(link.pathname,'/patient/transport-change.html');
  assert.equal(link.searchParams.get('pid'),'P 1');assert.equal(link.searchParams.get('hid'),'H1');assert.equal(link.searchParams.has('actor_id'),false);
  assert.equal(reviewLink('javascript:alert(1)','H1','P1'),null);assert.equal(reviewLink('not a url','H1','P1'),null);
});

test('표시: 판단 가능 여부·감시·blocker를 구분하고 서버 값은 이스케이프한다',()=>{
  const html=reviewPanelMarkup({server:'http://one',hospital:'H1',pending:[
    review(1),review(2,{monitoring_enabled:false}),review(3,{can_decide:false,blockers:['UNSUPPORTED_SOLVER_RESOURCE','NEW_CODE']}),review(4,{patient_id:'<img src=x onerror=alert(1)>'})]},esc);
  assert.match(html,/검토 대기 <span class="count">4<\/span>/);
  assert.match(html,/판단 가능/);assert.match(html,/이송자 감시 꺼짐 · 유지·조정 불가, 반려만 가능/);assert.match(html,/재검토 필요 · 반려만 가능/);
  assert.match(html,/현재 모델 지원 외 자원이 있어 자동 반영할 수 없습니다/);assert.match(html,/<li>NEW_CODE<\/li>/);
  assert.doesNotMatch(html,/<img/);assert.match(html,/&lt;img src=x/);
  assert.match(html,/승인·조정만으로 목적지는 바뀌지 않습니다/);
  assert.match(reviewPanelMarkup({pending:[],truncated:true,error:'갱신 실패.',checkedAt:'오후 3:00:00'},esc),/최신 100건만[\s\S]*|갱신 실패\. 아래는 오후 3:00:00에 조회한 목록입니다/);
});

// ---- 실제 index.html 실행
class FakeServer {
  constructor(){this.reviews={H1:[review(1),review(2,{state:'APPLIED_KEEP'})],H2:[{...review(7),hospital_id:'H2',patient_id:'Q7',id:'ER-7'}]};this.held=[];this.hold=false;this.reviewGets=[];this.posts=[];this.fail=null;}
  async fetch(url,opts={}){
    const {pathname,origin}=new URL(url),parts=pathname.split('/').map(decodeURIComponent);
    const reply=(status,body)=>({ok:status<400,status,json:async()=>body});
    if(opts.method==='POST'){this.posts.push(url);return reply(500,{});}
    if(parts[4]==='transport-change-reviews'){
      const hid=parts[3],body={reviews:this.reviews[hid]||[]};this.reviewGets.push({origin,hid});
      if(this.fail)return reply(500,{message:this.fail});
      if(this.hold)return new Promise(resolve=>this.held.push({hid,resolve:(override)=>resolve(reply(200,override||body))}));
      return reply(200,body);
    }
    if(pathname==='/api/hospitals')return reply(200,[{id:'H1',name:'가상 병원 1',source:'synthetic'},{id:'H2',name:'가상 병원 2',source:'synthetic'}]);
    if(pathname.endsWith('/inquiries'))return reply(200,{open:[],recent:[]});
    if(pathname.endsWith('/incoming'))return reply(200,{incoming:parts[3]==='H1'?[{id:'P1',age:60,symptoms:'가상',state:'IN_TRANSIT',transport_review:{}},{id:'P2',age:61,symptoms:'가상',state:'IN_TRANSIT',transport_review:{}}]:[],affected:[],planning:[]});
    if(parts[4]==='patients'&&parts[6]==='conversation')return reply(200,{conversation:{state:'OPEN'},active_hospital_session:{id:1},can_send:true,items:[],cursors:{event_id:0,message_id:0},has_more:false});
    return reply(200,{name:'가상 병원',source:'synthetic',resources:[],restrictions:[],capabilities:[],public_data:[]});
  }
}
class El {
  constructor(id){this.id=id;this.dataset={};this.value='';this.hidden=false;this.disabled=false;this.textContent='';this.className='';this.style={};this.listeners={};this.innerHTML='';}
  addEventListener(type,fn){(this.listeners[type]??=[]).push(fn);}
  focus(){}setSelectionRange(){}querySelector(){return null;}querySelectorAll(){return [];}closest(){return null;}contains(){return false;}matches(){return false;}
}
class Page {
  constructor(server){
    this.server=server;this.els=new Map();
    const document={activeElement:null,title:'',getElementById:id=>this.el(id),querySelector:()=>this.el('any'),querySelectorAll:()=>[]};
    this.context=vm.createContext({document,location:{search:'?hid=H1&server=http://one',pathname:'/hospital/',origin:'http://one'},localStorage:{getItem:()=>null,setItem(){},removeItem(){},length:0,key:()=>null},URL,URLSearchParams,console,crypto:{randomUUID:()=>'id'},
      fetch:(url,opts)=>server.fetch(url,opts),setInterval:()=>0,clearInterval(){},setTimeout:()=>0,clearTimeout(){},
      __dynamicImport:path=>path.includes('review-discovery')?import('./hospital-ui/review-discovery.mjs'):new Promise(()=>{})});
    vm.runInContext(source,this.context);
  }
  el(id){if(!this.els.has(id))this.els.set(id,new El(id));return this.els.get(id);}
  eval(code){return vm.runInContext(code,this.context);}
  fire(id,type,event){for(const fn of this.el(id).listeners[type]||[])fn(event);}
  async poll(){await this.eval('poll()');await settle();}
  get panel(){return this.el('hospitalReviews');}
}
async function open(server=new FakeServer()){const page=new Page(server);await settle();await page.poll();return {page,server};}

test('병원 메인에 현재 병원의 대기 검토만 보이고 목록 카드에도 표시한다',async()=>{
  const {page,server}=await open();
  assert.equal(page.panel.hidden,false);
  assert.match(page.panel.innerHTML,/data-review-id="ER-1"/);assert.doesNotMatch(page.panel.innerHTML,/ER-2/,'종료된 검토는 대기 목록에 없음');
  assert.match(page.panel.innerHTML,/필요 진료: 정형외과 → 정형외과, 신경외과/);
  assert.match(page.panel.innerHTML,/href="http:\/\/one\/patient\/transport-change.html\?pid=P1&amp;hid=H1"/);
  page.eval("setTab('incoming')");await settle();
  assert.match(page.el('incomingList').innerHTML,/data-patient="P1"[\s\S]*변경 검토 대기/);
  assert.doesNotMatch(page.el('incomingList').innerHTML.split('data-patient="P2"')[1],/변경 검토 대기/);
  assert.equal(server.posts.length,0,'발견 화면은 판단 요청을 보내지 않음');
});

test('대기 검토가 없으면 패널을 숨긴다',async()=>{
  const server=new FakeServer();server.reviews.H1=[review(2,{state:'REJECTED'})];
  const {page}=await open(server);
  assert.equal(page.panel.hidden,true);assert.equal(page.panel.innerHTML,'');
});

test('병원을 바꾸면 이전 병원 목록을 즉시 지우고 늦게 온 이전 응답을 버린다',async()=>{
  const {page,server}=await open();
  server.hold=true;await page.poll();assert.equal(server.held.at(-1).hid,'H1');
  page.el('hospital').value='H2';page.fire('hospital','change',{});await settle();
  assert.equal(page.panel.hidden,true,'전환 즉시 이전 병원 목록 제거');
  server.held.find(h=>h.hid==='H1').resolve();await settle();
  assert.equal(page.panel.hidden,true,'늦게 온 H1 응답은 H2 화면에 표시하지 않음');assert.doesNotMatch(page.panel.innerHTML,/P1/);
  server.hold=false;await page.poll();
  assert.match(page.panel.innerHTML,/Q7/);assert.doesNotMatch(page.panel.innerHTML,/P1/);
});

test('먼저 시작한 조회가 늦게 끝나도 더 새 결과를 덮지 않는다',async()=>{
  const {page,server}=await open();
  server.hold=true;await page.poll();const older=server.held.at(-1);
  page.eval('reviewLoading=false');await page.poll();const newer=server.held.at(-1);
  newer.resolve({reviews:[review(5)]});await settle();
  assert.match(page.panel.innerHTML,/P5/);
  older.resolve({reviews:[review(1)]});await settle();
  assert.match(page.panel.innerHTML,/P5/);assert.doesNotMatch(page.panel.innerHTML,/data-review-id="ER-1"/);
});

test('다른 병원 검토가 섞인 응답은 표시하지 않고 이유를 알린다',async()=>{
  const server=new FakeServer();server.reviews.H1=[review(1),{...review(9),hospital_id:'H2'}];
  const {page}=await open(server);
  assert.equal(page.panel.hidden,false);assert.match(page.panel.innerHTML,/다른 병원의 검토가 포함된 응답이라 표시하지 않았습니다/);
  assert.doesNotMatch(page.panel.innerHTML,/data-review-id/);
});

test('조회 실패는 같은 병원의 마지막 목록을 시각과 함께 남기고 실패를 알린다',async()=>{
  const {page,server}=await open();
  server.fail='서버 오류';await page.poll();
  assert.match(page.panel.innerHTML,/검토 목록을 불러오지 못했습니다\. 서버 오류/);assert.match(page.panel.innerHTML,/에 조회한 목록입니다/);assert.match(page.panel.innerHTML,/data-review-id="ER-1"/);
  page.el('hospital').value='H2';page.fire('hospital','change',{});await settle();await page.poll();
  assert.doesNotMatch(page.panel.innerHTML,/ER-1/,'다른 병원으로 바꾸면 이전 병원 목록을 남기지 않음');assert.match(page.panel.innerHTML,/불러오지 못했습니다/);
});

test('환자 보기는 같은 환자를 도착 예정 탭에서 선택하고 그 환자의 대화를 연다',async()=>{
  const {page}=await open();
  page.fire('hospitalReviews','click',{target:{closest:s=>s==='[data-review-patient]'?{dataset:{reviewPatient:'P1'}}:null}});await settle();
  assert.equal(page.eval('activeTab'),'incoming');assert.equal(page.eval('selectedPatientId'),'P1');assert.equal(page.eval('selectedPatient?.id'),'P1');
  assert.equal(page.el('hospitalChat').hidden,false);
});
