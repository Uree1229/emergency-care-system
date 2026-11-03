/** Context narrows hypotheses; it never invents or chooses a clinical value. */
import {findVoiceTrigger} from './recording-control.mjs';

export const RECOGNITION_CONTEXT_REVISION='voice-recognition-context-20261103-v1';
export const RECOGNITION_CONTEXT_LIMITS=Object.freeze({alternatives:16,text:4000,totalText:20000});
const VOCABULARY=Object.freeze(['나이','성별','의식','호흡','혈압','수축기','이완기','맥박','심박수','산소포화도','체온','호흡수','혈당','발생 시각','분당','퍼센트','밀리미터 수은주','밀리그램 퍼 데시리터']);
const VALUE_HINT=/[0-9]|[일이삼사오육칠팔구십백]{2,}|(?:정상|비정상|명료|저하|반응\s*없|없음|있음)/u;
const invalid=()=>new Error('인식 후보의 범위나 형식을 확인하지 못했습니다. 원문을 직접 검토해 주세요.');
function text(value){if(typeof value!=='string'||!value.trim()||value.length>RECOGNITION_CONTEXT_LIMITS.text)throw invalid();}
function denseStrings(value){
  if(!Array.isArray(value)||Reflect.ownKeys(value).length!==value.length+1)throw invalid();
  for(let i=0;i<value.length;i++){
    const entry=Object.getOwnPropertyDescriptor(value,String(i));
    if(!entry?.enumerable||!Object.hasOwn(entry,'value'))throw invalid();
    text(entry.value);
  }
}
/** Validate, then detach the exact list used by API writes and their recovery. */
export function normalizeRecognitionAlternatives(source,value){
  text(source);denseStrings(value);
  if(!value.length||value.length>RECOGNITION_CONTEXT_LIMITS.alternatives)throw invalid();
  let total=source.length;const seen=new Set([source]);
  for(const candidate of value){if(seen.has(candidate))throw invalid();seen.add(candidate);total+=candidate.length;}
  if(total>RECOGNITION_CONTEXT_LIMITS.totalText)throw invalid();
  return [...value];
}
/** Only labels/units are added. Previous values and typical values are never hints. */
export function reportVocabulary(hints=[]){
  return [...new Set([...hints,...VOCABULARY].filter(item=>typeof item==='string').map(item=>item.trim()).filter(item=>item&&!VALUE_HINT.test(item)))].slice(0,50);
}
/** Each variant changes ONE recognizer fragment. No Cartesian/majority reconstruction. */
export function buildRecognitionContext(fragments,alternatives,{command=false,trigger=null}={}){
  try{
    denseStrings(fragments);if(!fragments.length)throw invalid();
    if(!Array.isArray(alternatives)||alternatives.length!==fragments.length)throw invalid();
    const raw=fragments.join(' ');text(raw);
    if(command){
      // A mode selection is not permission to select one uncertain correction.
      // Even equivalent-looking command variants remain a manual decision.
      for(let index=0;index<fragments.length;index++){
        const choices=alternatives[index];denseStrings(choices);
        if(!choices.length||choices[0]!==fragments[index]||choices.length>5)throw invalid();
        if(choices.some(candidate=>candidate!==fragments[index]))return {status:'review_required',reason:'command_alternative',text:raw,alternatives:[]};
      }
      return {status:'command_path',text:raw,alternatives:[]};
    }
    const originalControl=findVoiceTrigger(raw);
    if(trigger&&(!originalControl||originalControl.action!==trigger.action||originalControl.command!==trigger.command||originalControl.prefix!==trigger.prefix||originalControl.suffix!==trigger.suffix))throw invalid();
    if(!trigger&&originalControl){
      // Button-only/observe mode retains control words as text. It must not
      // silently discard numerical disagreement just because those words occur.
      for(let index=0;index<fragments.length;index++){
        const choices=alternatives[index];denseStrings(choices);
        if(!choices.length||choices[0]!==fragments[index]||choices.length>5)throw invalid();
        if(choices.some(candidate=>candidate!==fragments[index]))return {status:'review_required',reason:'control_alternative',text:raw,alternatives:[]};
      }
      return {status:'command_path',text:raw,alternatives:[]};
    }
    const source=trigger?originalControl.prefix:raw;
    const variants=[],seen=new Set([source]);let total=source.length;
    for(let index=0;index<fragments.length;index++){
      const choices=alternatives[index];denseStrings(choices);
      if(!choices.length||choices[0]!==fragments[index]||choices.length>5)throw invalid();
      for(const candidate of choices){
        if(candidate===fragments[index])continue;
        const rawVariant=fragments.map((part,i)=>i===index?candidate:part).join(' ');
        const control=findVoiceTrigger(rawVariant);
        if(trigger?(!control||control.action!==originalControl.action||control.command!==originalControl.command||control.suffix!==originalControl.suffix):!!control)return {status:'review_required',reason:'control_alternative',text:source,alternatives:[]};
        const variant=trigger?control.prefix:rawVariant;
        if(!seen.has(variant)){
          total+=variant.length;
          if(!variant.trim()||variants.length>=RECOGNITION_CONTEXT_LIMITS.alternatives||variant.length>RECOGNITION_CONTEXT_LIMITS.text||total>RECOGNITION_CONTEXT_LIMITS.totalText)throw invalid();
          seen.add(variant);variants.push(variant);
        }
      }
    }
    if(variants.length)normalizeRecognitionAlternatives(source,variants);
    return {status:variants.length?'ready':'single_hypothesis',text:source,alternatives:variants};
  }catch{return {status:'review_required',reason:'limits_or_shape',text:Array.isArray(fragments)&&fragments.every(value=>typeof value==='string')?fragments.join(' '):'',alternatives:[]};}
}
