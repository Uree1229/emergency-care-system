/** Recording commands are client controls, never clinical text or server commands. */
export const RECORDING_LIMITS = Object.freeze({toggle:180, hold:30, text:4000});

// Korean particles are not word boundaries: 종료하지 마, 중단점, 그만큼,
// and 추천해줘라고 must never become controls through a partial match.
const BOUNDARY = String.raw`[\s,，.。!！?？;；:：“”‘’"'「」『』〈〉《》()\[\]{}]`;
const STOP = String.raw`(?:(?:녹음|음성(?:\s*입력)?|말하기)\s*)?(?:그만|중단|종료)(?:\s*(?:해\s*주시겠어요|해\s*주십시오|해\s*주세요|해\s*줘요?|하겠습니다|할게요|할게|합니다|하자|하세요|해))?`;
const RECOMMEND = String.raw`병원\s*추천\s*해\s*(?:주세요|줘요?)`;
const HANDOFF_SUMMARY = String.raw`(?:병원\s*)?인계(?:용)?\s*요약\s*(?:해\s*(?:주세요|줘요?)|보여\s*(?:주세요|줘요?))`;
const NEW_PATIENT = String.raw`(?:새|다음)\s*환자\s*시작(?:\s*(?:해\s*주세요|해\s*줘요?|하겠습니다|할게요|할게|합니다|하세요|해))?`;
const PREVIOUS_PATIENT = String.raw`이전\s*환자(?:로|\s*기록으로)\s*돌아가(?:\s*(?:주세요|줘요?))?`;
const TRIGGER = String.raw`(?:^|${BOUNDARY})(${HANDOFF_SUMMARY}|${NEW_PATIENT}|${PREVIOUS_PATIENT}|${RECOMMEND}|${STOP})(?=$|${BOUNDARY})`;
const CLINICAL_END = /(?:CPR|심폐\s*소생술|소생술|처치|시술|수술|치료|투약|약물|투여|주입|수액|이송|검사|제세동|압박|환기|분만|경련|발작|출혈|통화|면담|산소\s*공급|산소|인공\s*호흡|호흡)(?:을|를|이|가|는|은)?\s*(?:(?:진행|시행|시도)(?:을|를)?\s*)?(?:(?:즉시|즉각|바로|일단|잠시만?|일시(?:적(?:으로)?)?|당분간|현재|이제|모두|지금|우선|완전히)\s*)*$/iu;
const REPORTED = /(?:환자|보호자|동승자|목격자)(?:가|이|는|은|분이|분은|분께서)?\s*(?:(?:이제|여기서|말하기를|진술|말을?|하던\s*말을)\s*)*$/u;
const META_BEFORE = /(?:명령어|트리거|단어|문구|표현|버튼)(?:가|는|은|을|를|인)?\s*[:：]?\s*$/u;
const NEGATIVE_BEFORE = /(?:아직(?:은)?|절대(?:로)?|함부로|임의로)\s*$/u;
const NOT_CONTROL_AFTER = /^(?:하지|하진|하지는|하지도|하시면|하면|하라는|하라고|하고|해도|해서|해\s*주지|주지|주시|주셔|주실|달라|말고|말라|마세요|마십시오|아니라|안\s*(?:해|하)|않|라고|이라고|라는|이라며|이라면서|했|하였|되|됐|됨|된|요청|보고|기록|표시|언급|의미|발언)/u;
const CLINICAL_AFTER = /^(?:후에?|뒤에?|이후|전에?|부터|까지|때|시각|시간|일시|여부|예정|상태)(?=$|[\s,，.。!！?？;；:：은는이가을를])/u;
const PATIENT_DESCRIPTION_AFTER = /^(?:(?:해\s*)?달라|지\s*마|안\s*(?:돼|되)|(?:시각|시간|일시|여부|예정|상태|명령어?|버튼|단어|문구|표현)(?=$|[\s,，.。!！?？;；:：은는이가을를입였되]))/u;
const OPEN_QUOTES = new Map([['“','”'],['‘','’'],['「','」'],['『','』'],['〈','〉'],['《','》'],['"','"'],["'","'"]]);

function insideQuote(text, end) {
  const stack=[];
  for(const char of text.slice(0,end)) {
    if(stack.length&&char===stack.at(-1))stack.pop();
    else if(OPEN_QUOTES.has(char))stack.push(OPEN_QUOTES.get(char));
  }
  return stack.length>0;
}

/**
 * Find the first explicit control in final STT. Both sides remain available to
 * the caller: save prefix first, and preserve suffix as unapplied speech.
 * This parser only classifies text; it never saves, submits, or runs an action.
 */
export function findVoiceTrigger(value, {isFinal=true}={}) {
  if(!isFinal||typeof value!=='string')return null;
  // A fresh iterator avoids mutable lastIndex leaking between STT callbacks.
  for(const match of value.matchAll(new RegExp(TRIGGER,'gu'))) {
    const command=match[1];
    const action=/^(?:병원\s*)?인계/u.test(command)?'handoff_summary':command.startsWith('병원')?'recommend':command.startsWith('이전')?'previous_patient':
      /^(?:새|다음)/u.test(command)?'new_patient':'stop';
    const index=match.index+match[0].indexOf(command),end=index+command.length;
    const prefix=value.slice(0,index).trimEnd(),suffix=value.slice(end).trimStart();
    const preceding=prefix.replace(/[\s,，.。!！?？;；:：]+$/u,'');
    if(insideQuote(value,index)||REPORTED.test(preceding)||META_BEFORE.test(preceding)||NEGATIVE_BEFORE.test(preceding))continue;
    const following=suffix.replace(/^[\s,，.。!！?？;；:：]+/u,'');
    if(NOT_CONTROL_AFTER.test(following))continue;
    if((action==='new_patient'||action==='previous_patient')&&PATIENT_DESCRIPTION_AFTER.test(following))continue;
    if(/^(?:종료|중단|그만)/u.test(command)&&(CLINICAL_END.test(preceding)||CLINICAL_AFTER.test(following)))continue;
    return {action,prefix,command,suffix,index,end};
  }
  return null;
}

/** Compatibility API: terminal stop commands only, with the original shape. */
export function splitStopCommand(value, options={}) {
  const result=findVoiceTrigger(value,options);
  if(!result||result.action!=='stop'||!/^[\s,.。!！?？;；]*$/u.test(result.suffix))return null;
  const {prefix,command,index,end}=result;
  return {prefix,command,index,end};
}
