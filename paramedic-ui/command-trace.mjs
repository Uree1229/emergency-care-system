/** Local command timing observations. This is not an ASR accuracy certificate. */
export const COMMAND_TRACE_LIMITS=Object.freeze({events:600,bytes:60000,text:1000,waitMs:10000});
const TYPES=new Set(['final','interim','candidate','rearm','cancel','commit','would_commit','finish','capture']);
const ACTIONS=new Set(['stop','recommend','new_patient','previous_patient','handoff_summary']);
const MODES=new Set(['deferred','immediate','observe']);
const own=(value,key)=>Object.hasOwn(value,key);
const invalid=()=>{const error=new Error('음성 명령 시험 기록의 형식·시간·용량을 확인하세요.');error.code='INVALID_COMMAND_TRACE';throw error;};
const integer=value=>{if(!Number.isSafeInteger(value)||value<0)invalid();return value;};
function shape(value,required,optional=[]){
  if(!value||typeof value!=='object'||Array.isArray(value))invalid();
  const proto=Object.getPrototypeOf(value),ctor=proto&&Object.getOwnPropertyDescriptor(proto,'constructor');
  if(proto!==null&&!(Object.getPrototypeOf(proto)===null&&typeof ctor?.value==='function'&&ctor.value.name==='Object'))invalid();
  if(required.some(key=>!own(value,key)))invalid();
  for(const key of Reflect.ownKeys(value)){const descriptor=Object.getOwnPropertyDescriptor(value,key);if(typeof key!=='string'||![...required,...optional].includes(key)||!descriptor?.enumerable||!own(descriptor,'value'))invalid();}
}
export function normalizeCommandTrace(value){
  shape(value,['schema','policy','waitMs','events','dropped','limited']);
  if(value.schema!==1||!MODES.has(value.policy)||integer(value.waitMs)>COMMAND_TRACE_LIMITS.waitMs||typeof value.limited!=='boolean')invalid();
  if(!Array.isArray(value.events)||value.events.length>COMMAND_TRACE_LIMITS.events||Reflect.ownKeys(value.events).length!==value.events.length+1)invalid();
  let previous=0,commits=0;
  const events=[];
  for(let index=0;index<value.events.length;index++){
    const descriptor=Object.getOwnPropertyDescriptor(value.events,String(index));if(!descriptor||!own(descriptor,'value'))invalid();
    const event=descriptor.value;shape(event,['type','atMs'],['text','textTruncated','action','command','index','end','reason']);
    if(!TYPES.has(event.type)||integer(event.atMs)<previous)invalid();previous=event.atMs;
    const item={type:event.type,atMs:event.atMs};
    if(own(event,'text')){if(typeof event.text!=='string'||event.text.length>COMMAND_TRACE_LIMITS.text)invalid();item.text=event.text;}
    if(own(event,'textTruncated')){if(typeof event.textTruncated!=='boolean')invalid();item.textTruncated=event.textTruncated;}
    if(own(event,'action')){if(!ACTIONS.has(event.action))invalid();item.action=event.action;}
    if(own(event,'command')){if(typeof event.command!=='string'||event.command.length>100)invalid();item.command=event.command;}
    if(own(event,'index')!==own(event,'end'))invalid();
    if(own(event,'index')){if(integer(event.index)>16000||integer(event.end)>16000||event.end<event.index)invalid();item.index=event.index;item.end=event.end;}
    if(own(event,'reason')){if(typeof event.reason!=='string'||!/^[A-Za-z0-9_-]{1,80}$/u.test(event.reason))invalid();item.reason=event.reason;}
    if(event.type==='commit'||event.type==='would_commit'){
      if(++commits>1||!own(event,'action')||(value.policy==='observe')!==(event.type==='would_commit'))invalid();
    }
    events.push(item);
  }
  const result={schema:1,policy:value.policy,waitMs:value.waitMs,events,dropped:integer(value.dropped),limited:value.limited};
  if(new TextEncoder().encode(JSON.stringify(result)).byteLength>COMMAND_TRACE_LIMITS.bytes)invalid();return result;
}
