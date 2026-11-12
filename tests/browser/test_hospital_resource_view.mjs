// 병원 자원 탭의 조회 전용 표시 회귀 검사(3차 멘토링: 자원 수동 입력 제거). 실제 hospital-ui/index.html 스크립트를 VM에서 실행한다.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';

const html=readFileSync(new URL('../../hospital-ui/index.html',import.meta.url),'utf8');
const source=html.split('<script>')[1].split('</script>')[0].replaceAll("import('./","__dynamicImport('./");
const settle=async()=>{for(let i=0;i<15;i++)await new Promise(resolve=>setImmediate(resolve));};
const resource=(type,extra={})=>({resource_type:type,capacity:12,occupied:8,expected:2,free:2,status:'available',version:3,updated_at:1700000000,stale:false,over_capacity:false,...extra});

class El {
  constructor(id){this.id=id;this.dataset={};this.value='';this.hidden=false;this.disabled=false;this.textContent='';this.className='';this.style={};this.listeners={};this.innerHTML='';
    if(id==='externalArrivalForm')this.elements={resource_type:{value:''},amount:{value:''},note:{value:''}};}
  addEventListener(type,fn){(this.listeners[type]??=[]).push(fn);}
  focus(){}setSelectionRange(){}querySelector(){return null;}querySelectorAll(){return [];}closest(){return null;}contains(){return false;}matches(){return false;}append(){}
}
class Page {
  constructor(hospital,{open=[]}={}){
    this.requests=[];this.els=new Map();
    const reply=(status,body)=>({ok:status<400,status,json:async()=>body});
    const fetch=async(url,opts={})=>{
      const {pathname}=new URL(url);this.requests.push({method:opts.method||'GET',pathname});
      if(pathname==='/api/hospitals')return reply(200,[{id:'H1',name:'가상 병원',source:hospital.source}]);
      if(pathname.endsWith('/inquiries'))return reply(200,{open,recent:[]});
      if(pathname.endsWith('/incoming'))return reply(200,{incoming:[],affected:[],planning:[]});
      if(pathname==='/api/hospitals/H1')return reply(200,hospital);
      return reply(200,{});
    };
    const document={activeElement:null,title:'',getElementById:id=>this.el(id),querySelector:()=>this.el('any'),querySelectorAll:()=>[]};
    this.context=vm.createContext({document,location:{search:'?hid=H1&server=http://one',pathname:'/hospital/',origin:'http://one'},localStorage:{getItem:()=>null,setItem(){},removeItem(){},length:0,key:()=>null},URL,URLSearchParams,console,crypto:{randomUUID:()=>'id'},
      fetch,setInterval:()=>0,clearInterval(){},setTimeout:()=>0,clearTimeout(){},__dynamicImport:()=>new Promise(()=>{})});
    vm.runInContext(source,this.context);
    this.el('handler').value='dev2';
  }
  el(id){if(!this.els.has(id))this.els.set(id,new El(id));return this.els.get(id);}
  eval(code){return vm.runInContext(code,this.context);}
  get detail(){return this.el('resourceDetail').innerHTML;}
}
const hospital=(extra={})=>({name:'가상 병원',source:'synthetic',resources:[resource('ed_bed'),resource('special_team',{capacity:3,occupied:3,expected:0,free:0,status:'limited',stale:true,over_capacity:true})],restrictions:[],capabilities:[],public_data:[],...extra});
async function open(h=hospital(),options){const page=new Page(h,options);await settle();await page.eval('poll()');await settle();page.eval("setTab('resources')");await settle();return page;}

test('자원 탭에는 수정 양식 없이 서버 값을 조회 전용으로 보여 준다',async()=>{
  const page=await open();
  assert.doesNotMatch(page.detail,/resource-form|name="capacity"|name="occupied"|name="status"|자원 저장/);
  assert.match(page.detail,/data-resource="ed_bed"[\s\S]*응급병상 <span class="tag t-blue">신규 여유 2<\/span>/);
  assert.match(page.detail,/<dt>총량<\/dt><dd>12<\/dd>[\s\S]*<dt>실제 점유<\/dt><dd>8<\/dd>[\s\S]*<dt>확정 이송 수요<\/dt><dd>2<\/dd>[\s\S]*<dt>정보 상태<\/dt><dd>확인됨<\/dd>/);
  assert.match(page.detail,/전문팀 <span class="tag t-high">신규 여유 0<\/span>[\s\S]*<dd>제한<\/dd>[\s\S]*오래된 정보 · 실제 점유가 총량을 초과함/);
  assert.match(html,/병원 자원은 조회만 합니다/);
});

test('여유를 알 수 없으면 미확인으로 표시한다',async()=>{
  const page=await open(hospital({resources:[resource('ed_bed',{free:null,status:'unknown'})]}));
  assert.match(page.detail,/<span class="tag t-gray">신규 여유 미확인<\/span>/);assert.match(page.detail,/<dd>미확인<\/dd>/);
});

test('자원 탭 조작으로는 자원 수정 요청(PUT)을 보내지 않는다',async()=>{
  const page=await open();
  const form={id:'',matches:s=>s==='.resource-form',dataset:{resource:'ed_bed',version:'3'},elements:{capacity:{value:'20'},occupied:{value:'0'},status:{value:'available'}}};
  for(const fn of page.el('resourceDetail').listeners.submit)fn({preventDefault(){},target:form});
  for(const fn of page.el('resourceDetail').listeners.input)fn({target:{closest:()=>null}});
  await settle();
  assert.equal(page.requests.filter(r=>r.method!=='GET').length,0);
  assert.equal(page.eval("typeof saveResource"),'undefined');
});

test('가상 병원의 시스템 밖 유입 시연 도구와 공개 병상 관측은 그대로 둔다',async()=>{
  const page=await open(hospital({public_data:[{endpoint:'nmc_rltm_beds',source_observed_at:1700000000,fetched_at:1700000100,stale:false,data:{resources:[{resource_type:'ed_bed',reported_available:5}]}}]}));
  assert.match(page.detail,/국립중앙의료원 공개 병상 관측[\s\S]*<td>응급병상<\/td><td>5<\/td>/);
  assert.match(page.detail,/id="externalArrivalForm"[\s\S]*가상 유입 등록/);
});

test('운영 자원이 없으면 안내만 보이고 공개 가용 수를 운영 값으로 바꾸지 않는다',async()=>{
  const page=await open(hospital({resources:[]}));
  assert.match(page.detail,/확인된 운영 자원이 아직 등록되지 않았습니다/);assert.doesNotMatch(page.detail,/resource-view/);
});

test('서버 기준 여유가 부족하면 수용 응답을 막고 이 화면에서 수정하지 않는다고 안내한다',async()=>{
  const inquiry={id:1,state:'PENDING',version:1,attempts:1,created_at:1,elapsed_seconds:5,overdue:false,patient:{id:'P1',age:50,symptoms:'가상',reporter_role:'transporter',priority_source:'unknown',effective_priority:4,resource_demands:{special_team:1},required_capabilities:[],danger_signs:[]}};
  const page=await open(hospital(),{open:[inquiry]});
  page.eval("setTab('inquiries');selectedId=1;render()");
  const detail=page.el('detail').innerHTML;
  assert.match(detail,/전문팀: 서버 기준 신규 여유 0, 필요 1라 수용 응답이 막혀 있습니다\. 실제 여유가 다르면 자원 정보 갱신이 필요하며, 이 화면에서는 수정하지 않습니다/);
  assert.match(detail,/data-act="accept"[^>]*disabled/);
});
