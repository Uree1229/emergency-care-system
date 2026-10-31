/** Experimental client control gate. It never saves, submits or stops a recorder. */
import {findVoiceTrigger} from './recording-control.mjs';

export const COMMAND_COMMIT_LIMITS=Object.freeze({rawText:16000,events:600,bytes:60000,text:1000,interimText:240});
const POLICIES=new Set(['deferred','immediate','observe']);
const clone=value=>JSON.parse(JSON.stringify(value));
const bytes=value=>new TextEncoder().encode(value).byteLength;
function invalid(){const error=new Error('명령 보류 정책이나 입력 형식을 확인하세요.');error.code='INVALID_COMMAND_COMMIT';throw error;}
function reasonCode(value){if(typeof value!=='string'||!value||!/^[A-Za-z0-9_-]{1,80}$/u.test(value))invalid();return value;}
function clipped(value,max){let result=value.slice(0,max);if(result&&/[\uD800-\uDBFF]/u.test(result.at(-1)))result=result.slice(0,-1);return {text:result,textTruncated:result.length!==value.length};}
function sameSpan(left,right){return !!left&&!!right&&['action','command','index','end'].every(key=>left[key]===right[key]);}

export class CommandCommitGate {
  constructor({mode='deferred',waitMs=800,now=Date.now,setTimer=globalThis.setTimeout,clearTimer=globalThis.clearTimeout,onCommit=null,onEvent=null}={}){
    if(!POLICIES.has(mode)||!Number.isInteger(waitMs)||waitMs<0||waitMs>10000||typeof now!=='function'||typeof setTimer!=='function'||typeof clearTimer!=='function'||onCommit!==null&&typeof onCommit!=='function'||onEvent!==null&&typeof onEvent!=='function')invalid();
    const initial=now();if(!Number.isFinite(initial))invalid();
    this.policy=mode;this.waitMs=waitMs;this._now=now;this._setTimer=setTimer;this._clearTimer=clearTimer;this._onCommit=onCommit;this._onEvent=onEvent;
    this._origin=initial;this._lastAt=0;this._raw='';this._finalCount=0;this._interim='';this._awaitingFinal=false;this._candidate=null;this._deadline=null;
    this._timer=null;this._generation=0;this._latched=false;this._closed=false;this._disposed=false;this._limited=false;this._events=[];this._eventBytes=0;this._interimEvents=0;this._dropped=0;
    this._finishLogged=false;this._endRequested=false;this._endPromise=null;this._resolveEnd=null;
  }
  get rawText(){return this._raw;}
  /** True after either a real commit or one observe-only would_commit decision. */
  get committed(){return this._latched;}
  get disposed(){return this._disposed;}
  get pending(){return !this._closed&&!this._latched&&this._candidate?{trigger:{...this._candidate},rawText:this._raw,finalCount:this._finalCount,deadlineAtMs:this._deadline,blocked:this._awaitingFinal}:null;}
  snapshot(){return {schema:1,policy:this.policy,waitMs:this.waitMs,events:this._events.map(event=>({...event})),dropped:this._dropped,limited:this._limited};}
  _at(){const value=this._now();if(!Number.isFinite(value))invalid();this._lastAt=Math.max(this._lastAt,0,Math.round(value-this._origin));return this._lastAt;}
  _notify(callback,value){try{const result=callback?.(value);if(result&&typeof result.then==='function')Promise.resolve(result).catch(()=>{});}catch{}}
  _event(type,details={},notify=true){
    const event={type,atMs:this._at(),...details},size=bytes(JSON.stringify(event))+1;
    const interimReserved=type==='interim'&&(this._eventBytes+size>36000||this._interimEvents>=400);
    if(interimReserved||this._events.length>=COMMAND_COMMIT_LIMITS.events||this._eventBytes+size>COMMAND_COMMIT_LIMITS.bytes-256){this._dropped=Math.min(Number.MAX_SAFE_INTEGER,this._dropped+1);return null;}
    this._events.push(event);this._eventBytes+=size;if(type==='interim')this._interimEvents++;
    if(notify)this._notify(this._onEvent,{...event});return {...event};
  }
  _triggerDetails(trigger,reason){return {action:trigger.action,command:trigger.command,index:trigger.index,end:trigger.end,reason};}
  _disarm(){const timer=this._timer;this._timer=null;this._deadline=null;this._generation++;if(timer!==null)try{this._clearTimer(timer);}catch{}}
  _finish(reason,notify=true){if(this._finishLogged)return null;this._finishLogged=true;return this._event('finish',{reason},notify);}
  _promise(){if(!this._endPromise)this._endPromise=new Promise(resolve=>{this._resolveEnd=resolve;});return this._endPromise;}
  _settle(){if(this._resolveEnd){const resolve=this._resolveEnd;this._resolveEnd=null;resolve(this.snapshot());}}
  _close(reason){
    if(this._closed){this._settle();return;}
    this._closed=true;this._disarm();this._candidate=null;
    const cancelled=this._event('cancel',{reason},false),finished=this._finish(reason,false);
    if(cancelled)this._notify(this._onEvent,cancelled);if(finished)this._notify(this._onEvent,finished);this._settle();
  }
  _arm(){
    if(this._closed||this._latched||this._limited||this._awaitingFinal||!this._candidate||this._timer!==null)return;
    const generation=++this._generation;this._deadline=this._at()+this.waitMs;
    let timer;try{timer=this._setTimer(()=>{if(generation!==this._generation||this._closed||this._latched||this._awaitingFinal||!this._candidate)return;this._timer=null;this._deadline=null;this._commit('quiet_window');},this.waitMs);}catch{this._close('timer_failed');return;}
    if(generation===this._generation&&!this._closed&&!this._latched){this._timer=timer;this._event('rearm',this._triggerDetails(this._candidate,'quiet_window'));}
    else try{this._clearTimer(timer);}catch{}
  }
  _commit(reason){
    if(this._closed||this._latched||this._limited||this._awaitingFinal||!this._candidate)return false;
    const trigger={...this._candidate};this._latched=true;this._disarm();this._candidate=null;
    const payload={trigger:{...trigger},rawText:this._raw,finalCount:this._finalCount,atMs:this._at(),reason};
    // Latch before either callback. The event is already in snapshots read by
    // a synchronous onCommit callback; observation notification follows it.
    const event=this._event(this.policy==='observe'?'would_commit':'commit',this._triggerDetails(trigger,reason),false);
    if(this.policy!=='observe')this._notify(this._onCommit,payload);
    if(event)this._notify(this._onEvent,event);this._settle();return true;
  }
  final(text){
    if(typeof text!=='string')invalid();if(this._closed||this._disposed)return this.snapshot();
    if(!text.trim()){this._event('final',{...clipped(text,COMMAND_COMMIT_LIMITS.text),reason:'empty_final'});return this.snapshot();}
    this._disarm();this._finalCount++;const combined=this._raw+(this._raw?' ':'')+text,previous=this._candidate;
    this._raw=combined.slice(0,COMMAND_COMMIT_LIMITS.rawText);this._interim='';this._awaitingFinal=false;
    if(combined.length>COMMAND_COMMIT_LIMITS.rawText)this._limited=true;
    const next=this._latched||this._limited?null:findVoiceTrigger(this._raw);this._candidate=next;
    this._event('final',clipped(text,COMMAND_COMMIT_LIMITS.text));
    if(this._closed||this._disposed)return this.snapshot();
    if(combined.length>COMMAND_COMMIT_LIMITS.rawText){this._limited=true;this._close('text_limit');return this.snapshot();}
    if(this._latched)return this.snapshot();
    if(!next){if(previous)this._event('cancel',{reason:'context_changed'});if(this._endRequested)this._close('no_candidate');return this.snapshot();}
    if(!sameSpan(previous,next))this._event('candidate',this._triggerDetails(next,'final'));
    if(this._closed||this._disposed||this._latched)return this.snapshot();
    if(this.policy==='immediate')this._commit('immediate');else this._arm();return this.snapshot();
  }
  interim(text){
    if(typeof text!=='string')invalid();if(this._closed||this._disposed)return this.snapshot();
    this._interim=text.slice(0,COMMAND_COMMIT_LIMITS.rawText);
    if(text.trim()){this._awaitingFinal=true;this._disarm();}
    if(text.length>COMMAND_COMMIT_LIMITS.rawText)this._limited=true;
    this._event('interim',clipped(text,COMMAND_COMMIT_LIMITS.interimText));
    if(this._closed||this._disposed||this._latched)return this.snapshot();
    if(this._limited){this._close('text_limit');return this.snapshot();}
    if(text.trim()){
      if(this._candidate){const combined=findVoiceTrigger(this._raw+(this._raw?' ':'')+text);if(!sameSpan(this._candidate,combined)){this._candidate=null;this._event('cancel',{reason:'interim_context'});}}
      if(this._endRequested)this._close('unresolved_interim');
    }
    // Clearing an interim does not prove its words vanished. Only a fresh
    // nonempty final can clear awaitingFinal and arm a candidate again.
    return this.snapshot();
  }
  end(reason='natural'){
    reasonCode(reason);const promise=this._promise();if(this._closed||this._disposed){this._settle();return promise;}
    this._endRequested=true;this._finish(reason);
    if(this._closed||this._disposed||this._latched){this._settle();return promise;}
    if(this._limited)this._close('text_limit');else if(this._awaitingFinal)this._close('unresolved_interim');else if(!this._candidate)this._close('no_candidate');else this._arm();
    return promise;
  }
  flush(reason='button'){
    reasonCode(reason);const promise=this._promise();if(this._closed||this._disposed){this._settle();return promise;}
    this._finish(reason);
    if(this._closed||this._disposed||this._latched){this._settle();return promise;}
    if(this._limited)this._close('text_limit');else if(this._awaitingFinal)this._close('unresolved_interim');else if(!this._candidate)this._close('no_candidate');else this._commit(reason);
    return promise;
  }
  /** Arrival of a parent-owned capture boundary, not evidence of hardware success. */
  mark(reason){if(this._closed||this._disposed)return this.snapshot();reasonCode(reason);this._event('capture',{reason});return this.snapshot();}
  cancel(reason='cancelled'){reasonCode(reason);this._close(reason);return this.snapshot();}
  dispose(){this._disposed=true;return this.cancel('disposed');}
}
