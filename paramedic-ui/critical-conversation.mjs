/** Sequential, version-bound spoken review. No storage, clinical write or registration. */
const CONFIRM = new Set(['네','예','응','맞아','맞아요','맞습니다','확인했어','확인했어요','확인했습니다','네 맞아요','예 맞습니다']);
const CORRECT = new Set(['아니','아니요','아니오','아니야','틀렸어','틀렸어요','틀렸습니다','수정','수정할게요','수정하겠습니다','고칠게요']);
const REPEAT = new Set(['다시 읽어줘','다시 읽어주세요','다시 듣기']);
const STOP = new Set(['중지','중단','그만','여기까지','멈춰']);
const NEXT = new Set(['다음 단계','다음 단계로','계속','상세 검토로 이동']);
const copy = value => value == null ? value : JSON.parse(JSON.stringify(value));
const identifier = (value,maximum=128) => typeof value==='string' && value.length<=maximum && /^[A-Za-z0-9][A-Za-z0-9._:@-]*$/.test(value);
const positive = value => Number.isSafeInteger(value) && value>0;
const content = (value,maximum) => typeof value==='string' && !!value.trim() && value.length<=maximum;
const error = (code,message) => Object.assign(new Error(message),{code});

/** Full completed utterance only. A next-stage command never confirms an item. */
export function classifyConversationReply(text,stage='item') {
  if(typeof text!=='string' || !['item','next'].includes(stage)) return 'unknown';
  const phrase=text.trim().replace(/[.。!]$/u,'').trim();
  if(STOP.has(phrase)) return 'stop';
  if(REPEAT.has(phrase)) return 'repeat';
  if(stage==='next') return NEXT.has(phrase)?'next':'unknown';
  return CONFIRM.has(phrase)?'confirm':CORRECT.has(phrase)?'correct':'unknown';
}

function context(value) {
  if(!value || typeof value!=='object' || Array.isArray(value) ||
      !Number.isSafeInteger(value.patientEpoch) || value.patientEpoch<0 ||
      !identifier(value.actorId,80) || !identifier(value.sessionId) || !positive(value.version) ||
      value.state!=='DRAFT' || value.patientId!==null)
    throw error('INVALID_CONTEXT','미등록 상태의 같은 환자 초안에서 음성 확인을 시작해 주세요.');
  return {patientEpoch:value.patientEpoch,actorId:value.actorId,sessionId:value.sessionId,
    version:value.version,state:value.state,patientId:value.patientId};
}

function pending(value) {
  if(!Array.isArray(value) || value.length>50) throw error('INVALID_ITEMS','중요 항목 목록을 다시 확인해 주세요.');
  const items=value.map(item=>{
    if(!item || typeof item!=='object' || !identifier(item.id) || !content(item.label,200) ||
        !content(item.text,10000) || !content(item.signature,64000) || typeof item.confirmable!=='boolean')
      throw error('INVALID_ITEMS','중요 항목의 현재 값과 근거를 확인하지 못했습니다.');
    return {id:item.id,label:item.label,text:item.text,signature:item.signature,confirmable:item.confirmable};
  });
  if(new Set(items.map(item=>item.id)).size!==items.length) throw error('INVALID_ITEMS','중요 항목 식별자가 중복되었습니다.');
  return items;
}

const sameContext = (left,right) => JSON.stringify(left)===JSON.stringify(right);
const sameItem = (left,right) => !!left && !!right && left.id===right.id && left.signature===right.signature &&
  left.label===right.label && left.text===right.text && left.confirmable===right.confirmable;
const INITIAL = {phase:'idle',stage:'item',item:null,progress:{confirmed:0,total:0,position:0,remaining:0},
  heard:'',message:'음성 확인 시작을 누르면 항목을 읽고 답변을 듣습니다.',errorCode:null,active:false};

export class CriticalConversation {
  constructor({getContext,getPending,acknowledge,openEditor,goNext,audio,onState=()=>{}}={}) {
    for(const [name,value] of Object.entries({getContext,getPending,acknowledge,openEditor,goNext}))
      if(typeof value!=='function') throw new TypeError(name+' callback is required');
    if(!audio || ['capabilities','speak','listen','abort'].some(name=>typeof audio[name]!=='function'))
      throw new TypeError('Audio capabilities/speak/listen/abort are required');
    this._getContext=getContext;this._getPending=getPending;this._acknowledge=acknowledge;
    this._openEditor=openEditor;this._goNext=goNext;this._audio=audio;
    this._onState=typeof onState==='function'?onState:()=>{};
    this._generation=0;this._running=false;this._active=false;this._state=copy(INITIAL);
  }
  get active(){return this._active;}
  get state(){return copy({...this._state,active:this._active});}
  _publish(patch={}) {
    this._state={...this._state,...patch,active:this._active};
    try{this._onState(this.state);}catch{/* Rendering never changes confirmation semantics. */}
  }
  _abort(reason){try{this._audio.abort(reason);}catch{/* Local invalidation still wins. */}}
  _live(generation){return this._generation===generation;}
  _readContext(expected) {
    const latest=context(this._getContext());
    if(expected && !sameContext(latest,expected)) throw error('STALE_CONTEXT','환자·작성자·초안 버전이 바뀌었습니다. 최신 내용을 다시 확인해 주세요.');
    return latest;
  }
  _check(generation,expected,item=null,empty=false) {
    if(!this._live(generation)) throw error('ABORTED','음성 확인을 중지했습니다.');
    this._readContext(expected);
    const current=pending(this._getPending());
    if(item && !sameItem(current[0],item)) throw error('STALE_ITEM','확인할 항목이나 값이 바뀌었습니다. 현재 항목부터 다시 확인해 주세요.');
    if(empty && current.length) throw error('PENDING_CHANGED','아직 확인하지 않은 중요 항목이 있습니다. 현재 목록을 다시 확인해 주세요.');
    return current;
  }
  _pause(code,message,phase='paused') {
    this._active=false;this._abort(code);this._publish({phase,errorCode:code,message});
  }
  stop() {
    this._generation++;this._active=false;this._abort('stopped');
    this._publish({phase:'stopped',errorCode:null,message:'음성 확인을 멈췄습니다. 확인하지 않은 항목은 그대로 남아 있습니다.'});
    return this.state;
  }
  async _listen(generation,expected,item,stage) {
    this._publish({phase:'listening',stage,message:stage==='next'?'“다음 단계”라고 말하면 상세 검토로 이동합니다.':'읽은 항목에 대해 답변해 주세요.'});
    this._check(generation,expected,item,stage==='next');
    const answer=await this._audio.listen();
    this._check(generation,expected,item,stage==='next');
    if(!answer || typeof answer.text!=='string' || !Array.isArray(answer.finals) || !answer.finals.length ||
        answer.finals.some(value=>typeof value!=='string' || !value.trim()) || answer.text!==answer.finals.join(' '))
      throw error('INVALID_RESPONSE','완료된 음성 답변을 확인하지 못했습니다. 해당 항목은 확인하지 않았습니다.');
    this._publish({heard:answer.text});
    return classifyConversationReply(answer.text,stage);
  }
  async _edit(generation,expected,item,message) {
    this._check(generation,expected,item);
    this._active=false;this._abort('correction');
    this._publish({phase:'needs_correction',errorCode:null,message});
    // Root's existing editor is available only after this controller releases its lock.
    this._check(generation,expected,item);
    await this._openEditor(copy(item),copy(expected));
  }
  async start() {
    if(this._running || this._active) return this.state;
    const generation=++this._generation;this._running=true;this._active=true;
    this._state=copy(INITIAL);this._publish({phase:'checking',message:'현재 환자와 저장된 중요 항목을 확인하고 있습니다.'});
    let navigating=false;
    try {
      const expected=this._readContext();
      const capabilities=this._audio.capabilities();
      if(capabilities?.tts!==true || capabilities?.browser!==true)
        throw error('UNSUPPORTED','이 브라우저는 읽어주기와 음성 답변을 모두 지원하지 않습니다. 화면에서 확인해 주세요.');
      const initial=this._check(generation,expected);
      let confirmed=0;
      let introduced=false;
      const total=initial.length;
      while(this._live(generation)) {
        const current=this._check(generation,expected);
        const item=current[0]||null;
        this._publish({stage:item?'item':'next',item:copy(item),heard:'',
          progress:{confirmed,total,position:item?confirmed+1:confirmed,remaining:current.length},errorCode:null});
        if(!item) {
          this._publish({phase:'speaking',message:'중요 항목 확인을 마쳤습니다. 다음 단계로 이동할지 답변해 주세요.'});
          this._check(generation,expected,null,true);
          await this._audio.speak('확인을 마쳤습니다. 상세 검토로 이동하려면 다음 단계라고 말해 주세요.');
          this._check(generation,expected,null,true);
          const reply=await this._listen(generation,expected,null,'next');
          if(reply==='repeat')continue;
          if(reply==='stop'){this._pause(null,'이 화면에 머무릅니다. 확인한 내용은 현재 화면에서 유지됩니다.','stopped');break;}
          if(reply!=='next'){this._pause('UNKNOWN_REPLY','이동하지 않았습니다. 계속하려면 음성 확인을 다시 시작하고 “다음 단계”라고 말해 주세요.');break;}
          this._check(generation,expected,null,true);
          this._active=false;this._abort('navigation');
          this._publish({phase:'navigating',message:'같은 초안의 상세 검토로 이동합니다. 환자 등록은 실행하지 않습니다.'});
          this._check(generation,expected,null,true);
          navigating=true;
          const moved=await this._goNext(copy(expected));
          if(moved===false){navigating=false;throw error('NAVIGATION_FAILED','최신 초안에서 상세 검토 이동을 확인하지 못했습니다. 현재 기록을 다시 확인해 주세요.');}
          break;
        }
        if(!item.confirmable) {
          await this._edit(generation,expected,item,'이 항목은 값을 먼저 수정하거나 재확인해야 합니다. 수정 후 음성 확인을 다시 시작해 주세요.');
          break;
        }
        this._publish({phase:'speaking',message:item.label+' 내용을 읽고 있습니다. 읽기가 끝나면 답변해 주세요.'});
        this._check(generation,expected,item);
        const prompt=introduced?`${item.text}. 맞나요?`:`${item.text}. 맞으면 맞아요, 다르면 아니요, 다시 읽어줘 또는 중지라고 답하세요.`;
        introduced=true;
        await this._audio.speak(prompt);
        this._check(generation,expected,item);
        const reply=await this._listen(generation,expected,item,'item');
        if(reply==='repeat')continue;
        if(reply==='stop'){this._pause(null,'음성 확인을 멈췄습니다. 이 항목은 아직 확인하지 않았습니다.','stopped');break;}
        if(reply==='correct'){
          await this._edit(generation,expected,item,'해당 항목의 수정 화면을 열었습니다. 수정 후 음성 확인을 다시 시작해 주세요.');break;
        }
        if(reply!=='confirm'){this._pause('UNKNOWN_REPLY','답변을 확정하지 못해 멈췄습니다. 이 항목은 확인하지 않았습니다. 음성 확인을 다시 시작하거나 화면에서 확인해 주세요.');break;}
        this._check(generation,expected,item);
        this._publish({phase:'confirming',message:item.label+'의 최신 초안을 다시 확인하고 있습니다.'});
        this._check(generation,expected,item);
        const accepted=await this._acknowledge(copy(item),copy(expected));
        const after=this._check(generation,expected);
        if(accepted!==true)throw error('CONFIRMATION_FAILED','최신 초안에서 해당 항목을 확인하지 못했습니다. 다시 확인해 주세요.');
        if(after.some(value=>value.id===item.id))throw error('CONFIRMATION_NOT_APPLIED','항목 확인 결과가 반영되지 않아 멈췄습니다. 현재 목록을 다시 확인해 주세요.');
        if(after.length!==current.length-1 || after.some((value,index)=>!sameItem(value,current[index+1])))
          throw error('STALE_ITEM','다음 확인 항목이 바뀌었습니다. 최신 목록에서 다시 시작해 주세요.');
        confirmed++;
      }
    } catch(cause) {
      if(this._live(generation)) {
        const code=typeof cause?.code==='string'?cause.code:'REVIEW_ERROR';
        const message=typeof cause?.message==='string'?cause.message:'음성 확인을 완료하지 못했습니다. 현재 항목을 확인해 주세요.';
        this._pause(code,message);
      }
    } finally {
      if(this._live(generation)) {
        this._active=false;this._running=false;
        // A navigation callback owns the next screen. No new audio or repaint follows it.
        if(!navigating)this._publish();
      } else {
        this._running=false;
      }
    }
    return this.state;
  }
}
