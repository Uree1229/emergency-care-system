// 병원 문의별 추가 질문 초안 분리 회귀 검사. 실제 hospital-ui/index.html 스크립트를 VM과 최소 DOM 모형에서 실행한다.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';

const source=readFileSync(new URL('./hospital-ui/index.html',import.meta.url),'utf8').split('<script>')[1].split('</script>')[0].replaceAll("import('./","__dynamicImport('./");
const settle=async()=>{for(let i=0;i<12;i++)await new Promise(resolve=>setImmediate(resolve));};
const inquiry=(id,symptoms,extra={})=>({id,state:'PENDING',version:1,attempts:1,created_at:100,elapsed_seconds:5,overdue:false,patient:{id:`P${id}`,age:40+id,symptoms,reporter_role:'transporter',priority_source:'unknown',effective_priority:4,resource_demands:{},required_capabilities:[],danger_signs:[]},...extra});

class El {
  constructor(page,id){this.page=page;this.id=id;this.dataset={};this.value='';this.hidden=false;this.disabled=false;this.className='';this.textContent='';this.style={};this.listeners={};this.html='';this.selectionStart=0;this.selectionEnd=0;}
  get innerHTML(){return this.html;}
  set innerHTML(html){this.html=html;this.page.rendered(this,html);}
  addEventListener(type,fn){(this.listeners[type]??=[]).push(fn);}
  focus(){this.page.document.activeElement=this;}
  setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;}
  querySelector(){return null;}
  querySelectorAll(selector){return selector==='details[open][data-keep]'?this.page.details.filter(d=>d.open):[];}
  closest(){return null;}
  contains(){return false;}
  matches(){return false;}
  scrollIntoView(){}
  getClientRects(){return [];}
}

class Page {
  constructor({responses,search='?hid=H1&server=http://one'}={}){
    this.els=new Map();this.details=[];this.posts=[];this.pending=[];this.responses=responses;this.intervals=[];
    this.body=new El(this,'body');
    this.document={activeElement:this.body,title:'',getElementById:id=>this.el(id),querySelector:()=>this.el('any'),querySelectorAll:()=>[]};
    this.context=vm.createContext({document:this.document,location:{search,pathname:'/hospital/',origin:'http://one'},localStorage:{getItem:()=>null,setItem(){}},URL,URLSearchParams,console,
      fetch:(url,opts={})=>this.fetch(url,opts),setInterval:fn=>{this.intervals.push(fn);return 0;},clearInterval(){},setTimeout:()=>0,clearTimeout(){},__dynamicImport:()=>new Promise(()=>{})});
    vm.runInContext(source,this.context);
    this.el('handler').value='dev2';
  }
  el(id){
    if(id==='questionInput')return this.input||null;
    if(!this.els.has(id))this.els.set(id,new El(this,id));
    return this.els.get(id);
  }
  rendered(el,html){
    if(el.id!=='detail')return;
    if(this.document.activeElement===this.input)this.document.activeElement=this.body; // 실제 DOM처럼 교체된 입력의 포커스는 사라진다.
    this.input=html.includes('id="questionInput"')?new El(this,'questionInput'):null;
    this.details=[...html.matchAll(/<details\b[^>]*>/g)].map(([tag])=>({open:/\sopen\b/.test(tag),dataset:{keep:/data-keep="([^"]+)"/.exec(tag)?.[1]}}));
  }
  async fetch(url,opts){
    const {pathname,origin}=new URL(url);
    const json=(status,body)=>({ok:status<400,status,json:async()=>body});
    if(opts.method==='POST'){
      this.posts.push({url,path:pathname,body:JSON.parse(opts.body)});
      return new Promise((resolve,reject)=>this.pending.push({resolve:(status=200,body={ok:true})=>resolve(json(status,body)),reject}));
    }
    const data=this.responses[origin]?.[new URL(url).pathname.split('/')[3]]??this.responses[origin]?.H1??{open:[],recent:[]};
    if(pathname==='/api/hospitals')return json(200,[{id:'H1',name:'가상 병원 1',source:'synthetic'},{id:'H2',name:'가상 병원 2',source:'synthetic'}]);
    if(pathname.endsWith('/inquiries'))return json(200,data);
    if(pathname.endsWith('/incoming'))return json(200,{incoming:[],affected:[],planning:[]});
    return json(200,{name:'가상 병원',source:'synthetic',resources:[],restrictions:[],capabilities:[],public_data:[]});
  }
  eval(code){return vm.runInContext(code,this.context);}
  async poll(){await this.eval('poll()');await settle();}
  select(id){this.fire('openList','click',{target:{closest:s=>s==='[data-id]'?{dataset:{id:String(id)}}:null}});}
  act(act){this.fire('detail','click',{target:{closest:s=>s==='[data-act]'?{dataset:{act}}:null}});}
  reason(reason){this.fire('detail','click',{target:{closest:s=>s==='[data-reason]'?{dataset:{reason}}:null}});}
  type(text){this.input.value=text;this.fire('detail','input',{target:this.input});}
  fire(id,type,event){for(const fn of this.el(id).listeners[type]||[])fn(event);}
  get notice(){return this.el('notice').textContent;}
  get questionOpen(){return this.details.find(d=>d.dataset.keep==='question')?.open??null;}
  get focused(){return this.document.activeElement===this.input&&!!this.input;}
}

async function open(list=[inquiry(1,'A 증상'),inquiry(2,'B 증상')]){
  const responses={'http://one':{H1:{open:list,recent:[]}},'http://two':{H1:{open:[inquiry(1,'다른 서버 증상')],recent:[]}}};
  const page=new Page({responses});
  await settle();await page.poll();
  return {page,responses};
}
const changeList=(ctx,list,recent=[])=>{ctx.responses['http://one'].H1={open:list,recent};};

test('A의 초안은 B에 넘어가지 않고 A↔B 이동에서 각자 자기 문장만 복원한다',async()=>{
  const {page}=await open();
  page.select(1);page.input.focus();page.type('A-specific draft');
  page.select(2);
  assert.equal(page.input.value,'','B는 빈 질문창으로 시작');assert.equal(page.questionOpen,false,'B의 질문 영역은 접힌 채 시작');assert.equal(page.focused,false,'전환 시 이전 포커스를 복원하지 않음');
  page.type('B-specific draft');
  page.select(1);assert.equal(page.input.value,'A-specific draft');assert.equal(page.questionOpen,true);
  page.select(2);assert.equal(page.input.value,'B-specific draft');
  assert.equal(page.posts.length,0,'초안 이동만으로 전송하지 않음');
});

test('같은 문의의 자동 조회 재렌더에서 값·펼침·포커스·커서를 유지한다',async()=>{
  const ctx=await open();const {page}=ctx;
  page.select(1);page.details.find(d=>d.dataset.keep==='question').open=true;page.input.focus();page.type('작성 중인 문장');page.input.setSelectionRange(2,4);
  changeList(ctx,[inquiry(1,'A 증상',{state:'CHECKING'}),inquiry(2,'B 증상')]);await page.poll();
  assert.equal(page.input.value,'작성 중인 문장');assert.equal(page.questionOpen,true);assert.equal(page.focused,true);
  assert.deepEqual([page.input.selectionStart,page.input.selectionEnd],[2,4]);
});

test('같은 문의에서 사용자가 접은 질문 영역은 재렌더로 다시 펼쳐지지 않는다',async()=>{
  const {page}=await open();
  page.select(1);page.details.find(d=>d.dataset.keep==='question').open=true;page.type('초안');
  page.details.find(d=>d.dataset.keep==='question').open=false;page.eval('renderDetail()');
  assert.equal(page.questionOpen,false);assert.equal(page.input.value,'초안');
});

test('닫힌 문의·사라진 문의·빈 상세에서 초안이 다른 문의로 새지 않는다',async()=>{
  const ctx=await open();const {page}=ctx;
  page.select(1);page.type('A 초안');
  changeList(ctx,[inquiry(2,'B 증상')],[inquiry(1,'A 증상',{state:'EXPIRED'})]);await page.poll();
  assert.equal(page.input,null,'닫힌 문의에는 입력창이 없음');
  page.select(2);assert.equal(page.input.value,'');
  page.select(1);changeList(ctx,[inquiry(2,'B 증상')]);await page.poll();
  assert.equal(page.input,null,'사라진 문의는 빈 상세');assert.match(page.el('detail').innerHTML,/문의를 선택하세요/);
  page.select(2);assert.equal(page.input.value,'');
});

test('병원·서버 전환과 작업공간 초기화는 초안과 표시를 정리하고 같은 문의 번호도 섞이지 않는다',async()=>{
  const {page}=await open();
  page.select(1);page.type('서버1 초안');page.details.find(d=>d.dataset.keep==='question').open=true;
  page.el('server').value='http://two';page.fire('server','change',{});await settle();
  page.select(1);assert.match(page.el('detail').innerHTML,/다른 서버 증상/);assert.equal(page.input.value,'','같은 번호의 다른 서버 문의');assert.equal(page.questionOpen,false);
  page.type('서버2 초안');
  page.el('server').value='http://one';page.fire('server','change',{});await settle();
  page.select(1);assert.equal(page.input.value,'','되돌아와도 이전 초안이 따라오지 않음');
  page.type('병원 전환 전');
  page.el('hospital').value='H2';page.fire('hospital','change',{});await settle();
  assert.equal(page.input,null,'전환 직후 선택 해제');
  page.eval("resetWorkspace()");await settle();
  page.el('hospital').value='H1';page.fire('hospital','change',{});await settle();
  page.select(1);assert.equal(page.input.value,'');
});

test('보내기는 선택한 문의에만 POST하고 성공하면 제출한 초안만 지운다',async()=>{
  const {page}=await open();
  page.select(2);page.type('B 보존');
  page.select(1);page.type('  A 질문  ');page.act('question');
  assert.equal(page.posts.length,1);
  assert.equal(page.posts[0].path,'/api/inquiries/1/respond');
  assert.deepEqual({...page.posts[0].body},{action:'question',handled_by:'dev2',question:'A 질문'},'기존 요청 계약 유지');
  page.pending[0].resolve();await settle();
  assert.equal(page.input.value,'','제출한 A 초안 정리');assert.match(page.notice,/추가 질문을 보냈습니다/);
  page.select(2);assert.equal(page.input.value,'B 보존');
  assert.equal(page.posts.length,1);
});

test('전송 중 고쳐 쓴 내용은 성공 응답이 와도 지우지 않는다',async()=>{
  const {page}=await open();
  page.select(1);page.type('처음 문장');page.act('question');
  page.input.focus();page.type('처음 문장에 덧붙임');
  page.pending[0].resolve();await settle();
  assert.equal(page.input.value,'처음 문장에 덧붙임');assert.equal(page.focused,true);
});

test('전송 실패는 초안과 기존 오류 안내를 남기고 자동 재전송하지 않는다',async()=>{
  const {page}=await open();
  page.select(1);page.type('실패할 질문');page.act('question');
  page.pending[0].resolve(500,{message:'서버 오류'});await settle();
  assert.equal(page.input.value,'실패할 질문');assert.equal(page.notice,'서버 오류');assert.equal(page.el('notice').className,'err');
  await page.poll();assert.equal(page.posts.length,1);assert.equal(page.input.value,'실패할 질문');
});

test('응답 대기 중 B로 옮긴 뒤 실패해도 B를 건드리지 않고 A로 돌아오면 실패를 보여 준다',async()=>{
  const {page}=await open();
  page.select(1);page.type('A 질문');page.act('question');
  page.select(2);page.input.focus();page.type('B 작성 중');
  page.pending[0].reject(new TypeError('Failed to fetch'));await settle();
  assert.equal(page.input.value,'B 작성 중');assert.equal(page.focused,true,'B의 포커스 유지');assert.equal(page.notice,'','B 화면에 A 결과를 덮어쓰지 않음');
  assert.match(page.el('openList').innerHTML,/응답 실패/,'A의 실패를 목록에 표시');
  assert.equal(page.posts.length,1,'새 요청을 자동으로 보내지 않음');
  page.select(1);assert.equal(page.input.value,'A 질문');assert.equal(page.notice,'Failed to fetch');assert.equal(page.el('notice').className,'err');
  assert.doesNotMatch(page.el('openList').innerHTML,/응답 실패/);
});

test('응답 대기 중 B로 옮긴 뒤 성공해도 B의 알림·입력·불가 사유를 바꾸지 않는다',async()=>{
  const {page}=await open();
  page.select(1);page.type('A 질문');page.act('question');
  page.select(2);page.input.focus();page.type('B 작성 중');page.act('decline-open');page.reason('병상');
  page.pending[0].resolve();await settle();
  assert.equal(page.input.value,'B 작성 중');assert.equal(page.focused,true);assert.equal(page.notice,'');
  assert.equal(page.eval('declineReason'),'병상','B에서 고른 사유 유지');
  page.select(1);assert.equal(page.input.value,'','A 초안만 정리');assert.match(page.notice,/추가 질문을 보냈습니다/);
  page.select(2);assert.equal(page.input.value,'B 작성 중');
});
