/** Keep explicit resume/full links intact; new visits begin at the conversation. */
export function chooseParamedicEntry(search,hasSavedMainSession=false){
  const params=new URLSearchParams(search||'');
  if(params.get('view')==='full')return {kind:'main',field:false};
  if(params.get('voice_handoff')==='1'||params.get('flow')==='field'||params.has('pid')||params.has('sid')||params.has('actor_id')||hasSavedMainSession)
    return {kind:'main',field:true};
  return {kind:'conversation',url:'./quick-test.html?flow=field'};
}
if(typeof document!=='undefined'&&typeof window!=='undefined'){
  let saved=false;try{saved=!!sessionStorage.getItem('paramedic.voice.session');}catch{/* Failed storage never invents a draft. */}
  const next=chooseParamedicEntry(location.search,saved);
  if(next.kind==='conversation')location.replace(next.url);
  else{if(next.field)document.documentElement.dataset.fieldEntry='true';await import('./app.js');}
}
