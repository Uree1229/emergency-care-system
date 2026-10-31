(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const policies={R0:{label:'회신순',color:'#727e8c'},R1:{label:'거리순',color:'#3b77a5'},H1:{label:'공동 배정',color:'#77629d'},W1:{label:'대기 보호',color:'#0a726d'}};
  const shortTitles={'h1-improvement':'H1 개선과 전가','h1-worsening':'H1의 +67분','w1-waiting':'W1의 대기 보호','w1-transfer':'W1의 +83분','zero-capped-loss':'차이 0 · 성공 상실'};
  const endpointNames={selected:'선정',arrived:'도착',treated:'치료 시작',treatment_started:'치료 시작'};
  const number=value=>typeof value==='number'&&Number.isFinite(value)?value:null;
  const format=value=>number(value)==null?'미기록':Number.isInteger(value)?String(value):value.toFixed(2);
  const signed=value=>number(value)==null?'미산출':`${value>0?'+':''}${format(value)}`;
  const instant=value=>number(value)==null?'<span class="endpoint-missing">미기록</span>':`t${escape(format(value))}`;
  const data=globalThis.DEMO_CASEBOOK;
  let selected=0;

  for(const link of document.querySelectorAll('[data-map-scenario]')){
    const base=location.protocol==='file:'?'http://127.0.0.1:18045/':new URL('./',location.href).href;
    const url=new URL(base);url.searchParams.set('scenario',link.dataset.mapScenario);link.href=url.href;
  }
  $('connection-note').textContent=location.protocol==='file:'
    ?'파일로 열었습니다. 아래 연구 카드와 표는 오프라인으로 동작합니다. 지도 링크는 127.0.0.1:18045에 실행한 기존 뷰어가 필요합니다.'
    :'지도 링크는 지금 사용 중인 서버·포트를 유지합니다. 사건 번호는 지도에서 직접 이동해 확인하세요.';

  if(!data||data.schema_version!==1||!Array.isArray(data.cases)||!data.cases.length){
    $('data-error').hidden=false;$('data-error').textContent='저장된 사례 자료를 불러오지 못했습니다. demo_casebook_20261030.js가 이 HTML과 같은 폴더에 있는지 확인하세요.';
    $('case-panel').textContent='사례 자료가 필요합니다.';$('cohort-content').textContent='자료를 불러온 뒤 집계가 표시됩니다.';return;
  }

  function comparisonFor(item){
    if(item.comparison?.baseline&&item.comparison?.candidate)return item.comparison;
    return item.id.startsWith('h1-')?{baseline:'R1',candidate:'H1'}:{baseline:item.id==='zero-capped-loss'?'R0':'H1',candidate:'W1'};
  }

  function patientCard(patient,item){
    const comparison=comparisonFor(item),key=`${comparison.candidate}_minus_${comparison.baseline}`;
    const verified=patient.comparisons?.[key],delta=typeof verified==='number'?verified:number(verified?.delta_capped_minutes);
    const base=patient.endpoints[comparison.baseline],candidate=patient.endpoints[comparison.candidate];
    const lost=Array.isArray(verified?.success_lost)?verified.success_lost:[];
    const stateLost=lost.length?lost:['selected','arrived','treated'].filter(name=>base[name]&&!candidate[name]);
    const tone=stateLost.length||delta>0?'worsened':delta<0?'improved':'';
    const deltaLabel=stateLost.length?`${stateLost.map(name=>endpointNames[name]||name).join('·')} 기록 상실`:delta<0?'도착 제한값 감소':delta>0?'도착 제한값 증가':'도착 제한값 동일';
    const capMaximum=Math.max(1,...data.policies.map(code=>number(patient.endpoints[code]?.arrival_capped_minutes)??0));
    const bars=data.policies.map(code=>{
      const p=patient.endpoints[code],cap=number(p.arrival_capped_minutes),width=Math.max(0,Math.min(100,(cap??0)/capMaximum*100));
      return `<div class="bar-row"><span class="bar-label" style="color:${policies[code].color}">${escape(code)}</span><div class="bar-track" aria-hidden="true"><div class="bar-fill ${p.arrived?'':'capped'}" style="--bar-color:${policies[code].color};width:${width}%"></div></div><span class="bar-value ${p.arrived?'':'capped'}">${escape(format(cap))}분 · ${p.arrived?'도착':'미도착'}</span></div>`;
    }).join('');
    const rows=data.policies.map(code=>{
      const p=patient.endpoints[code];
      return `<tr><td style="color:${policies[code].color}">${escape(code)}</td><td>${instant(p.selected_t)}</td><td>${instant(p.arrive_t)}</td><td>${instant(p.treatment_start_t)}</td><td>${escape(format(p.arrival_capped_minutes))}분</td><td class="endpoint-state"><span>${p.selected?'선정 있음':'선정 없음'}</span><span>${p.arrived?'도착 있음':'도착 없음'}</span><span>${p.treated?'치료시작 있음':'치료시작 없음'}</span></td></tr>`;
    }).join('');
    return `<article class="patient-card"><div class="patient-title"><h4>환자 ${escape(patient.id)}</h4><span>실험 우선군 ${escape(patient.priority)}</span></div><p class="patient-context">발생 t${escape(patient.occur_t)} · 문의 시작 t${escape(patient.search_start_t)}</p><div class="delta-box ${tone}"><strong>${escape(signed(delta))}분</strong> · ${escape(deltaLabel)}<span class="delta-detail">${escape(comparison.candidate)} − ${escape(comparison.baseline)}${verified?.actual_arrival_delta_minutes!=null?` · 공통 도착 기록 차이 ${escape(signed(verified.actual_arrival_delta_minutes))}분`:stateLost.length?' · 도착/치료 성공 여부를 별도로 확인하세요.':''}</span></div><div class="bars" role="img" aria-label="${escape(patient.id)}의 정책별 도착 제한값. 미도착은 관찰 종료값이며 이어지는 표에 사건 기록을 표시합니다.">${bars}</div><div class="bar-legend"><span><i class="legend-box" aria-hidden="true"></i>도착 사건 있음</span><span><i class="legend-box capped" aria-hidden="true"></i>미도착 · 관찰 종료 제한값</span></div><div class="table-wrap"><table><caption class="skip-link">${escape(patient.id)}의 원본 합성 사건 시각과 성공 여부</caption><thead><tr><th>정책</th><th>선정</th><th>도착</th><th>치료시작</th><th>도착 제한값</th><th>사건 기록</th></tr></thead><tbody>${rows}</tbody></table></div><p class="table-note">t는 합성 시계의 절대 분입니다. 도착 제한값은 발생부터의 경과시간이며 미도착자는 관찰 종료까지 포함합니다. 막대는 이 환자 안에서 길이를 비교합니다.</p></article>`;
  }

  function renderCase(index,moveFocus=false){
    selected=index;const item=data.cases[index],comparison=comparisonFor(item);
    const sourceNumber=String(item.raw_path).match(/(\d+)\.json\.gz$/)?.[1]||'원본';
    const policyLegend=data.policies.map(code=>code+' '+policies[code].label).join(' · ');
    const buttons=[...$('case-tabs').querySelectorAll('[role=tab]')];buttons.forEach((button,i)=>{button.setAttribute('aria-selected',String(i===index));button.tabIndex=i===index?0:-1;});
    $('case-panel').setAttribute('aria-labelledby',`case-tab-${index}`);
    $('case-panel').innerHTML=`<div class="case-header"><div><p class="case-kicker">기존 개발 자료 · ${escape(item.profile_label||item.profile)} · 사례 ${escape(sourceNumber)}</p><h3>${escape(item.title)}</h3></div><div class="comparison-label">차이 = <b>${escape(comparison.candidate)} − ${escape(comparison.baseline)}</b></div></div><p class="case-description">${escape(item.explanation)}</p><p class="comparison-guide"><b>${escape(policyLegend)}</b><br>음수는 제한값 감소, 양수는 증가입니다. 시간차 0만으로 성공 여부가 같다고 판단하지 않습니다.</p><div class="patient-grid">${item.patients.map(p=>patientCard(p,item)).join('')}</div><div class="case-caveat"><b>함께 설명할 한계</b> · ${escape(item.caveat)}</div><details class="case-source"><summary>이 사례의 출처·해시</summary><p>원본: <code>${escape(item.raw_path)}</code></p><p>압축 원본 SHA-256: <code>${escape(item.raw_sha256)}</code></p><p>시나리오 SHA-256: <code>${escape(item.scenario_sha256)}</code></p><p>관찰 종료 t${escape(item.observation_end_t)} · 이미 관측한 개발 사례 · 실험 재실행 없이 발췌</p></details>`;
    if(moveFocus)buttons[index].focus();
  }

  $('case-tabs').innerHTML=data.cases.map((item,index)=>`<button type="button" id="case-tab-${index}" role="tab" aria-selected="false" aria-controls="case-panel" tabindex="-1" data-case-index="${index}">${escape(shortTitles[item.id]||item.title)}</button>`).join('');
  $('case-tabs').addEventListener('click',event=>{const button=event.target.closest('[data-case-index]');if(button)renderCase(Number(button.dataset.caseIndex));});
  $('case-tabs').addEventListener('keydown',event=>{
    if(!['ArrowRight','ArrowLeft','Home','End'].includes(event.key))return;
    event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?data.cases.length-1:(selected+(event.key==='ArrowRight'?1:-1)+data.cases.length)%data.cases.length;renderCase(next,true);
  });

  function cohort(){
    const fresh=data.fresh_summary,known=fresh.known_development||{};
    const fields=(pairs)=>pairs.map(([label,value])=>`<dt>${escape(label)}</dt><dd>${escape(value)}</dd>`).join('');
    const policyTable=phase=>{
      const study=data.study_summaries?.[phase];if(!study?.policies)return '';
      return `<details><summary>4정책의 실패·우선군4 수치</summary><div class="table-wrap"><table><thead><tr><th>정책</th><th>환자</th><th>미배정</th><th>미도착</th><th>치료시작 없음</th><th>우선군4 미배정/미도착</th></tr></thead><tbody>${data.policies.map(code=>{const row=study.policies[code];return `<tr><td>${escape(code)}</td><td>${escape(row.patients)}</td><td>${escape(row.unassigned)}</td><td>${escape(row.not_arrived)}</td><td>${escape(row.untreated)}</td><td>${escape(row.priority4?.unassigned)} / ${escape(row.priority4?.not_arrived)}</td></tr>`;}).join('')}</tbody></table></div><p class="table-note">각 정책에 같은 환자 코호트를 사용했습니다. 실패 수가 같아도 실패한 환자 ID가 같다는 뜻은 아닙니다.</p></details>`;
    };
    $('cohort-content').innerHTML=`<div class="cohort-grid"><article class="cohort-card"><div class="cohort-head"><h3>이미 본 개발·회귀</h3><span class="badge neutral">기존 36환경</span></div><p class="cohort-size">${escape(known.patients??603)}명 <small>/ 각 정책의 같은 환자</small></p><dl class="cohort-rows">${fields([['W1−H1 제한값 감소',`${known.improved_vs_H1??known.improved??3}명`],['W1−H1 제한값 증가',`${known.worsened_vs_H1??known.worsened??5}명`],['동일 제한값',`${known.equal??known.equal_vs_H1??595}명`],['H1 대비 개인 최대 증가',`${known.max_W1_minus_H1??83}분`]])}</dl><p class="cohort-note">한 사례를 개선했지만 다른 환자에게 지연이 옮겨갔습니다. 새 독립 검증의 성과에 합치지 않습니다.</p>${policyTable('development_known')}</article><article class="cohort-card"><div class="cohort-head"><h3>별도의 새 합성 검증</h3><span class="badge caution">${escape(fresh.environments)}환경</span></div><p class="cohort-size">${escape(fresh.patients)}명 <small>/ 각 정책의 같은 환자</small></p><dl class="cohort-rows">${fields([['H1·W1 관측 끝점 동일',`${fresh.H1_W1_endpoint_equal}명`],['W1의 H1 대비 개선',`${fresh.improved_vs_H1??0}명`],['W1의 R1 대비 개인 최대 증가',`${fresh.max_W1_minus_R1}분`],['우선군4의 R1 대비 최대 증가',`${fresh.priority4_max_W1_minus_R1}분`]])}</dl><div class="outcome-split" aria-hidden="true"><span class="arrived" style="width:${fresh.both_arrived/fresh.patients*100}%"></span><span class="not-arrived" style="width:${fresh.both_not_arrived/fresh.patients*100}%"></span></div><div class="split-caption"><span>양쪽 도착 ${escape(fresh.both_arrived)}명</span><span>양쪽 미도착 ${escape(fresh.both_not_arrived)}명</span></div><p class="cohort-note">끝점이 같다는 것은 모두 성공했다는 뜻이 아닙니다. 최대 ${escape(fresh.max_W1_minus_R1)}분의 R1 대비 악화는 H1에도 그대로 남아 있습니다.</p>${policyTable('validation_new')}</article></div><div class="cohort-callout"><b>최종 판단: W1 미채택 · 운영 기준 유지.</b> 개발 자료에서의 지연 전가와 새 검증에서의 개선 부재·기존 성공 기록 보호 실패를 함께 보존했습니다. 기술 검사 통과는 연구 보호조건 통과나 현장 효과의 증명이 아닙니다.</div>`;
  }

  function sources(){
    const origin=data.generated_from||{};let entries=Array.isArray(origin)?origin:Object.entries(origin).map(([name,value])=>({name,...(value&&typeof value==='object'?value:{value})}));
    $('source-content').innerHTML=`<p>표시 자료: <code>demo_casebook_20261030.js</code> · ${escape(data.id)} · schema ${escape(data.schema_version)}</p><div class="source-row"><b>원본 결합 정보</b><pre><code>${escape(JSON.stringify(entries,null,2))}</code></pre></div><p>${escape(typeof data.scope==='string'?data.scope:JSON.stringify(data.scope))}</p>`;
  }

  renderCase(0);cohort();sources();
  globalThis.demoHubDebug={selectCase:id=>{const index=data.cases.findIndex(item=>item.id===id);if(index<0)return false;renderCase(index);return true;},getState:()=>({caseId:data.cases[selected].id,caseCount:data.cases.length,offlineCore:true,experimentExecution:false,sourceId:data.id})};
})();
